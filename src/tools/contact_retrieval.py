"""Contact Retrieval tools for Investor Outbound MCP.

Tools 9-12 in the Phase 2 tool set:

    io_get_contacts        — scored + filtered contacts for one or more investors
    io_search_persons      — find people by name / email / company / role
    io_get_investor_team   — all persons at one investor, grouped by seniority tier
    io_find_decision_makers — senior investment professionals across multiple investors

Design notes
------------
- All tools return contacts regardless of email presence (caller enriches later).
- Scoring uses score_contact() then passes_deal_relevance() is NOT called here —
  these tools are retrieval, not deal-matching. The scoring is purely numeric
  (via score_contact) with junk + role-is-firm-name filtering applied explicitly.
- IDs are chunked to 100 per PostgREST query to stay well inside URL length limits.
- Investor name resolution uses ilike on the `investors` column (the name field).
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder
from src.entities.person import (
    PERSON_SELECT_DETAIL,
    PERSON_SELECT_SUMMARY,
    PersonSummary,
    format_summary as format_person_summary,
)
from src.helpers import error_response, paginated_response, tool_response
from src.scoring import (
    has_investment_function,
    is_junk_role,
    is_senior,
    role_is_firm_name,
    score_contact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 100  # max IDs per PostgREST in.(…) query

# Tier 1 title substrings for get_investor_team grouping
_TIER1_TITLES: list[str] = [
    "partner",
    "managing director",
    "general partner",
    "managing partner",
    "chief investment officer",
    "cio",
    "ceo",
    "chief executive",
    "founder",
    "co-founder",
]

# Tier 2 title substrings
_TIER2_TITLES: list[str] = [
    "vice president",
    "vp",
    "svp",
    "evp",
    "director",
    "principal",
    "senior vice president",
    "executive vice president",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _seniority_tier(role: str | None) -> int:
    """Return 1, 2, or 3 for the seniority tier of a role string.

    Tier 1: Partner / MD / GP / CIO / CEO / Founder
    Tier 2: VP / Director / SVP / EVP / Principal
    Tier 3: Everyone else
    """
    if not role:
        return 3
    role_l = role.lower()
    if any(t in role_l for t in _TIER1_TITLES):
        return 1
    if any(t in role_l for t in _TIER2_TITLES):
        return 2
    return 3


async def _fetch_persons_for_investors(
    client: IOClient,
    investor_ids: list[int],
    select: str,
) -> list[dict[str, Any]]:
    """Query the persons table for a list of investor IDs, chunked to _CHUNK_SIZE.

    Returns all rows across all chunks, deduped by person id.
    """
    seen_ids: set[int] = set()
    all_rows: list[dict[str, Any]] = []

    for chunk_start in range(0, len(investor_ids), _CHUNK_SIZE):
        chunk = investor_ids[chunk_start : chunk_start + _CHUNK_SIZE]
        qb = (
            QueryBuilder("persons")
            .select(select)
            .in_("investor", chunk)
            .limit(1000)
        )
        rows, _ = await client.query(qb, count=None)
        for row in rows:
            pid = row.get("id")
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_rows.append(row)

    return all_rows


async def _resolve_investor_name_to_ids(client: IOClient, name: str) -> list[int]:
    """Resolve an investor name string to a list of matching investor IDs.

    Uses case-insensitive partial match on the `investors` column.
    Returns up to 50 matching IDs.
    """
    qb = (
        QueryBuilder("investors")
        .select("id")
        .ilike("investors", f"*{name}*")
        .limit(50)
    )
    rows, _ = await client.query(qb, count=None)
    return [r["id"] for r in rows if r.get("id") is not None]


def _apply_contact_filters(
    rows: list[dict[str, Any]],
    deal_keywords: list[str] | None,
    max_per_firm: int,
) -> list[dict[str, Any]]:
    """Score, filter junk, cap per firm, and sort contacts.

    Args:
        rows: Raw person dicts from PostgREST (must include `role`,
              `company_name`, `investor` fields at minimum).
        deal_keywords: Optional keywords forwarded to score_contact().
        max_per_firm: Maximum contacts to return per investor (by `investor` FK).

    Returns:
        Filtered, scored list of person dicts sorted by score desc.
        A `_score` key is injected into each returned dict.
    """
    scored: list[tuple[int, dict[str, Any]]] = []

    for row in rows:
        role = row.get("role") or ""
        company = row.get("company_name") or ""
        # Use `investor` FK as the "investor name" for role_is_firm_name check
        # when the investor name is not available in the persons row.
        # We use company_name as a proxy since persons don't carry firm name.
        inv_name = company  # best available without a join

        if is_junk_role(role):
            continue
        if role_is_firm_name(role, company, inv_name):
            continue

        s = score_contact(role, deal_keywords)
        row["_score"] = s
        scored.append((s, row))

    # Sort descending by score
    scored.sort(key=lambda t: t[0], reverse=True)

    # Cap per firm (investor FK)
    firm_counts: dict[int | None, int] = {}
    results: list[dict[str, Any]] = []
    for _, row in scored:
        fk = row.get("investor")
        if firm_counts.get(fk, 0) < max_per_firm:
            firm_counts[fk] = firm_counts.get(fk, 0) + 1
            results.append(row)

    return results


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all contact retrieval tools with the MCP server."""

    # ── Tool 9: io_get_contacts ─────────────────────────────────────────────

    @mcp.tool(
        name="io_get_contacts",
        description=(
            "Retrieve scored and filtered contacts for one or more investors. "
            "Accepts either a list of investor IDs or a firm name to resolve. "
            "Applies score_contact() scoring, removes junk roles and firm-name-as-role "
            "entries, caps at max_per_firm per investor. Contacts WITHOUT email are "
            "included — callers can enrich them separately. "
            "Results are sorted by score descending."
        ),
    )
    async def io_get_contacts(
        investor_ids: list[int] | None = None,
        investor_name: str | None = None,
        deal_keywords: list[str] | None = None,
        max_per_firm: int = 5,
    ) -> str:
        """Get scored contacts for investor(s).

        Args:
            investor_ids: List of investor primary key IDs. Mutually exclusive with
                investor_name (investor_ids takes precedence when both are supplied).
            investor_name: Firm name string — resolved to IDs via ilike search before
                fetching contacts. Used when you don't know the exact numeric IDs.
            deal_keywords: Optional keywords matched against the contact's role for
                additional score boost (+10 per keyword hit).
            max_per_firm: Maximum contacts to return per investor. Default 5.
        """
        if not investor_ids and not investor_name:
            return error_response(
                "VALIDATION_ERROR",
                "Provide at least one of: investor_ids or investor_name",
            )

        try:
            resolved_ids: list[int] = list(investor_ids) if investor_ids else []

            if not resolved_ids and investor_name:
                resolved_ids = await _resolve_investor_name_to_ids(client, investor_name)
                if not resolved_ids:
                    return tool_response(
                        [],
                        f"No investors found matching '{investor_name}'",
                        next_actions=[
                            "Try io_search_investors with a broader keyword",
                        ],
                    )

            rows = await _fetch_persons_for_investors(
                client, resolved_ids, PERSON_SELECT_SUMMARY
            )

            contacts = _apply_contact_filters(rows, deal_keywords, max_per_firm)

            serialized = [PersonSummary.model_validate(c).model_dump() for c in contacts]
            # Carry the score through in the serialized output
            for i, row in enumerate(contacts):
                serialized[i]["_score"] = row.get("_score", 0)

            return tool_response(
                serialized,
                f"Returned {len(serialized)} scored contacts across "
                f"{len(resolved_ids)} investor(s)",
                next_actions=(
                    ["Call io_find_decision_makers to narrow to senior investment professionals"]
                    if serialized
                    else []
                ),
            )

        except IOAuthError as exc:
            logger.error("Auth error in io_get_contacts: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("Query error in io_get_contacts: %s", exc)
            return error_response("QUERY_ERROR", "Bad query — check investor IDs")
        except IOTransientError as exc:
            logger.error("Transient error in io_get_contacts: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error — please retry")
        except Exception:
            logger.exception("Unexpected error in io_get_contacts")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ── Tool 10: io_search_persons ──────────────────────────────────────────

    @mcp.tool(
        name="io_search_persons",
        description=(
            "Find people in the 1.8M persons table by any combination of: "
            "name (first or last), email (exact or partial), company name, or role. "
            "At least one filter must be provided. "
            "Returns paginated PersonSummary results."
        ),
    )
    async def io_search_persons(
        name: str | None = None,
        email: str | None = None,
        company: str | None = None,
        role: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> str:
        """Search for persons by name, email, company, or role.

        Args:
            name: Partial name match — searched against both first_name and
                last_name using ilike. Supply any part of the name.
            email: Email address. Supply a full address for exact match or a
                partial string (e.g. "@domain.com") for ilike domain search.
            company: Company/firm name — matched via ilike on company_name.
            role: Job title keyword — matched via ilike on role field.
            page: 1-indexed page number (default 1).
            page_size: Records per page (1–100, default 50).
        """
        if not any([name, email, company, role]):
            return error_response(
                "VALIDATION_ERROR",
                "Provide at least one search filter: name, email, company, or role",
            )

        page_size = max(1, min(page_size, 100))
        page = max(1, page)
        offset = (page - 1) * page_size

        try:
            # Email: exact match if it looks like a full address, else ilike
            email_exact = email and "@" in email and "." in email.split("@")[-1] and "*" not in email

            # We build multiple queries when name is supplied (first_name OR last_name)
            # PostgREST doesn't support OR across different columns natively in GET
            # params, so we issue up to two queries and merge.
            rows_first: list[dict] = []
            rows_last: list[dict] = []
            rows_other: list[dict] = []
            total_hint: int | None = None

            if name:
                # first_name match
                qb_first = QueryBuilder("persons").select(PERSON_SELECT_SUMMARY)
                qb_first = qb_first.ilike("first_name", f"*{name}*")
                if email:
                    qb_first = qb_first.eq("email", email) if email_exact else qb_first.ilike("email", f"*{email}*")
                if company:
                    qb_first = qb_first.ilike("company_name", f"*{company}*")
                if role:
                    qb_first = qb_first.ilike("role", f"*{role}*")
                qb_first = qb_first.limit(page_size).offset(offset)
                rows_first, total_hint = await client.query(qb_first)

                # last_name match
                qb_last = QueryBuilder("persons").select(PERSON_SELECT_SUMMARY)
                qb_last = qb_last.ilike("last_name", f"*{name}*")
                if email:
                    qb_last = qb_last.eq("email", email) if email_exact else qb_last.ilike("email", f"*{email}*")
                if company:
                    qb_last = qb_last.ilike("company_name", f"*{company}*")
                if role:
                    qb_last = qb_last.ilike("role", f"*{role}*")
                qb_last = qb_last.limit(page_size).offset(offset)
                rows_last, _ = await client.query(qb_last)

                # Merge, dedup by id
                seen: set[int] = set()
                for r in rows_first + rows_last:
                    pid = r.get("id")
                    if pid not in seen:
                        seen.add(pid)
                        rows_other.append(r)

            else:
                qb = QueryBuilder("persons").select(PERSON_SELECT_SUMMARY)
                if email:
                    qb = qb.eq("email", email) if email_exact else qb.ilike("email", f"*{email}*")
                if company:
                    qb = qb.ilike("company_name", f"*{company}*")
                if role:
                    qb = qb.ilike("role", f"*{role}*")
                qb = qb.limit(page_size).offset(offset)
                rows_other, total_hint = await client.query(qb)

            rows = rows_other[:page_size]
            summaries = [format_person_summary(r).model_dump() for r in rows]

            filters_used = [f for f, v in [("name", name), ("email", email), ("company", company), ("role", role)] if v]
            return paginated_response(
                summaries,
                total=total_hint,
                page=page,
                page_size=page_size,
                summary=f"Found {len(summaries)} persons matching {', '.join(filters_used)}",
            )

        except IOAuthError as exc:
            logger.error("Auth error in io_search_persons: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("Query error in io_search_persons: %s", exc)
            return error_response("QUERY_ERROR", f"Query failed: {exc}")
        except IOTransientError as exc:
            logger.error("Transient error in io_search_persons: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error — please retry")
        except Exception:
            logger.exception("Unexpected error in io_search_persons")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ── Tool 11: io_get_investor_team ───────────────────────────────────────

    @mcp.tool(
        name="io_get_investor_team",
        description=(
            "Retrieve all persons at a single investor, grouped into three seniority tiers. "
            "Tier 1: Partner / MD / GP / CIO / CEO / Founder. "
            "Tier 2: VP / Director / SVP / EVP / Principal. "
            "Tier 3: Everyone else. "
            "Includes per-tier channel coverage (count with email, phone, LinkedIn)."
        ),
    )
    async def io_get_investor_team(
        investor_id: int,
    ) -> str:
        """Get all persons at one investor, grouped by seniority tier.

        Args:
            investor_id: Primary key of the investor to look up.
        """
        try:
            qb = (
                QueryBuilder("persons")
                .select(PERSON_SELECT_DETAIL)
                .eq("investor", investor_id)
                .limit(500)
            )
            rows, _ = await client.query(qb, count=None)

            if not rows:
                return tool_response(
                    {"tiers": {"tier1": [], "tier2": [], "tier3": []}, "total": 0},
                    f"No persons found for investor {investor_id}",
                    next_actions=["Call io_get_investor(investor_id) to confirm the investor exists"],
                )

            # Group into tiers
            tiers: dict[str, list[dict[str, Any]]] = {"tier1": [], "tier2": [], "tier3": []}
            tier_map = {1: "tier1", 2: "tier2", 3: "tier3"}

            for row in rows:
                tier_key = tier_map[_seniority_tier(row.get("role"))]
                tiers[tier_key].append(row)

            def _coverage(tier_rows: list[dict[str, Any]]) -> dict[str, int]:
                return {
                    "total": len(tier_rows),
                    "with_email": sum(1 for r in tier_rows if r.get("email")),
                    "with_phone": sum(1 for r in tier_rows if r.get("phone")),
                    "with_linkedin": sum(1 for r in tier_rows if r.get("linkedin_profile_url")),
                }

            result = {
                "tiers": {
                    "tier1": {
                        "label": "Partner / MD / GP / CEO / Founder",
                        "coverage": _coverage(tiers["tier1"]),
                        "persons": tiers["tier1"],
                    },
                    "tier2": {
                        "label": "VP / Director / SVP / EVP / Principal",
                        "coverage": _coverage(tiers["tier2"]),
                        "persons": tiers["tier2"],
                    },
                    "tier3": {
                        "label": "Other staff",
                        "coverage": _coverage(tiers["tier3"]),
                        "persons": tiers["tier3"],
                    },
                },
                "total": len(rows),
            }

            return tool_response(
                result,
                f"Found {len(rows)} persons at investor {investor_id}: "
                f"{len(tiers['tier1'])} T1 / {len(tiers['tier2'])} T2 / {len(tiers['tier3'])} T3",
                next_actions=[
                    f"Call io_find_decision_makers(investor_ids=[{investor_id}]) "
                    "to filter to senior investment professionals only"
                ],
            )

        except IOAuthError as exc:
            logger.error("Auth error in io_get_investor_team: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("Query error in io_get_investor_team: %s", exc)
            return error_response("QUERY_ERROR", f"Query failed: {exc}")
        except IOTransientError as exc:
            logger.error("Transient error in io_get_investor_team: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error — please retry")
        except Exception:
            logger.exception("Unexpected error in io_get_investor_team")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ── Tool 12: io_find_decision_makers ────────────────────────────────────

    @mcp.tool(
        name="io_find_decision_makers",
        description=(
            "Across multiple investors, find ONLY senior investment professionals — "
            "people who are both is_senior() AND has_investment_function() and are "
            "not junk roles. Skips role-is-firm-name entries. "
            "Returns contacts sorted by score descending."
        ),
    )
    async def io_find_decision_makers(
        investor_ids: list[int],
        deal_keywords: list[str] | None = None,
        max_per_firm: int = 5,
    ) -> str:
        """Find senior investment professionals across multiple investors.

        Args:
            investor_ids: List of investor primary key IDs to search across.
            deal_keywords: Optional role-level keywords for score boosting.
            max_per_firm: Max contacts returned per investor. Default 5.
        """
        if not investor_ids:
            return error_response(
                "VALIDATION_ERROR",
                "investor_ids must be a non-empty list",
            )

        try:
            rows = await _fetch_persons_for_investors(
                client, investor_ids, PERSON_SELECT_SUMMARY
            )

            scored: list[tuple[int, dict[str, Any]]] = []
            for row in rows:
                role = row.get("role") or ""
                company = row.get("company_name") or ""

                # Hard gates: must be senior AND have investment function
                if not is_senior(role):
                    continue
                if not has_investment_function(role):
                    continue
                if is_junk_role(role):
                    continue
                if role_is_firm_name(role, company, company):
                    continue

                s = score_contact(role, deal_keywords)
                row["_score"] = s
                scored.append((s, row))

            # Sort by score desc
            scored.sort(key=lambda t: t[0], reverse=True)

            # Cap per firm
            firm_counts: dict[int | None, int] = {}
            results: list[dict[str, Any]] = []
            for _, row in scored:
                fk = row.get("investor")
                if firm_counts.get(fk, 0) < max_per_firm:
                    firm_counts[fk] = firm_counts.get(fk, 0) + 1
                    results.append(row)

            serialized = [PersonSummary.model_validate(r).model_dump() for r in results]
            for i, row in enumerate(results):
                serialized[i]["_score"] = row.get("_score", 0)

            return tool_response(
                serialized,
                f"Found {len(serialized)} senior investment professionals "
                f"across {len(investor_ids)} investor(s)",
                next_actions=(
                    [
                        "Call io_get_investor_team(investor_id=X) for full team view",
                        "Export contacts via io_export for outreach",
                    ]
                    if serialized
                    else [
                        "Try io_get_contacts with lower min_per_firm for broader coverage"
                    ]
                ),
            )

        except IOAuthError as exc:
            logger.error("Auth error in io_find_decision_makers: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("Query error in io_find_decision_makers: %s", exc)
            return error_response("QUERY_ERROR", f"Query failed: {exc}")
        except IOTransientError as exc:
            logger.error("Transient error in io_find_decision_makers: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error — please retry")
        except Exception:
            logger.exception("Unexpected error in io_find_decision_makers")
            return error_response("SERVER_ERROR", "An unexpected error occurred")
