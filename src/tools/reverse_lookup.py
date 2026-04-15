"""Reverse Lookup Tools (Tools 13–17).

Five tools for looking up investors and persons from partial identifiers:
- Tool 13: lookup_by_email_domain  — domain → all persons at that firm
- Tool 14: lookup_by_linkedin      — LinkedIn URL → single person detail
- Tool 15: reverse_company_lookup  — company name → investors grouped by FK
- Tool 16: batch_firm_lookup       — list of firm names → matched investors + top contacts
- Tool 17: batch_person_lookup     — list of emails or "first last" names → person records

Design decisions:
- Chunks: batch_firm_lookup → 50 names per query; batch_person_lookup → 100 per query.
- No scoring/gating on reverse lookups — the caller controls who they search for.
  score_contact is used in batch_firm_lookup to rank returned contacts, not to gate.
- person.domain column is the email domain (e.g. "kaynecapital.com"), not the full email.
- Nested joins (persons→investors) are BROKEN in this PostgREST schema (PGRST200).
  For reverse_company_lookup and batch_firm_lookup we group by investor FK in Python.
- preferred_investment_types is a TEXT string — ilike only, not cs operator.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder
from src.entities.investor import INVESTOR_SELECT_SUMMARY, format_summary as fmt_investor
from src.entities.person import (
    PERSON_SELECT_DETAIL,
    PERSON_SELECT_SUMMARY,
    format_detail as fmt_person_detail,
    format_summary as fmt_person_summary,
)
from src.helpers import error_response, tool_response
from src.scoring import score_contact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_BATCH_FIRM_CHUNK = 50       # max firm names per PostgREST ilike iteration
_BATCH_PERSON_CHUNK = 100    # max emails per in.() query
_BATCH_FIRM_MAX_PER_FIRM = 5  # top contacts to return per matched firm
_BATCH_FIRM_INVESTOR_LIMIT = 5  # max investor matches per firm name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    """Split *items* into sub-lists of at most *size* elements."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _top_contacts_for_investor(rows: list[dict], cap: int) -> list[dict]:
    """Score, sort, and cap person rows for a single investor.

    Args:
        rows: Raw PostgREST person records (any select shape).
        cap: Maximum contacts to return.

    Returns:
        Up to *cap* person dicts sorted by score descending.
    """
    scored = []
    for row in rows:
        role = row.get("role") or ""
        s = score_contact(role)
        scored.append((s, row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in scored[:cap]]


# ---------------------------------------------------------------------------
# Tool 13: lookup_by_email_domain
# ---------------------------------------------------------------------------


def _register_lookup_by_email_domain(mcp: FastMCP, client: IOClient) -> None:
    @mcp.tool()
    async def lookup_by_email_domain(domain: str) -> str:
        """Return all persons whose email domain matches the given domain.

        Useful for finding every contact at a known firm when you have the
        firm's email domain (e.g. "kaynecapital.com").

        Args:
            domain: Email domain to match, e.g. "kaynecapital.com".
                    Leading "@" is stripped automatically.

        Returns:
            JSON envelope with list of PersonSummary records enriched with the
            investor name from the investor FK (fetched separately due to
            broken nested joins). Includes persons with no email (domain field
            populated from data pipeline regardless of email presence).
        """
        # Normalise: strip leading @ if caller included it
        domain = domain.lstrip("@").strip().lower()
        if not domain:
            return error_response(
                "VALIDATION_ERROR",
                "domain must be a non-empty string, e.g. 'kaynecapital.com'",
            )

        try:
            rows, total = await client.query(
                QueryBuilder("persons")
                .select(PERSON_SELECT_SUMMARY)
                .eq("domain", domain)
                .order("role", ascending=True)
                .limit(500),
                count="estimated",
            )
        except IOAuthError as exc:
            logger.warning("lookup_by_email_domain auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("lookup_by_email_domain query error: %s", exc)
            return error_response("QUERY_ERROR", f"Bad query: {exc}")
        except IOTransientError as exc:
            logger.error("lookup_by_email_domain transient error: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error, please retry")

        if not rows:
            return tool_response(
                data=[],
                summary=f"No persons found at domain '{domain}'",
                next_actions=["Try reverse_company_lookup with the firm name instead"],
            )

        # Enrich with investor name via FK (nested joins broken → one lookup per
        # unique investor ID). Use the in-memory investor cache in IOClient.
        investor_ids: set[int] = {
            r["investor"] for r in rows if r.get("investor") is not None
        }
        investor_names: dict[int, str] = {}
        for inv_id in investor_ids:
            inv = await client.get_investor_by_id(inv_id)
            if inv:
                investor_names[inv_id] = inv.get("investors") or ""

        summaries = []
        for row in rows:
            ps = fmt_person_summary(row)
            entry = ps.model_dump()
            entry["investor_name"] = investor_names.get(row.get("investor"), "")  # type: ignore[arg-type]
            summaries.append(entry)

        return tool_response(
            data=summaries,
            summary=f"Found {len(summaries)} person(s) at domain '{domain}'"
            + (f" (estimated total: {total})" if total else ""),
            next_actions=[
                "Call lookup_by_linkedin(linkedin_url=...) for a specific person's full detail",
                "Call get_investor(investor_id=...) for the full investor profile",
            ],
        )


# ---------------------------------------------------------------------------
# Tool 14: lookup_by_linkedin
# ---------------------------------------------------------------------------


def _register_lookup_by_linkedin(mcp: FastMCP, client: IOClient) -> None:
    @mcp.tool()
    async def lookup_by_linkedin(linkedin_url: str) -> str:
        """Return the full PersonDetail record for a given LinkedIn profile URL.

        Useful for instant contact enrichment: paste a LinkedIn URL, get back
        email, phone, role, company, and email quality fields.

        Args:
            linkedin_url: Full LinkedIn profile URL.
                          Example: "https://www.linkedin.com/in/john-smith-abc123/"

        Returns:
            JSON envelope with a single PersonDetail record, or NOT_FOUND if
            the URL is not in the database.
        """
        url = linkedin_url.strip()
        if not url:
            return error_response("VALIDATION_ERROR", "linkedin_url must be non-empty")

        try:
            rows, _ = await client.query(
                QueryBuilder("persons")
                .select(PERSON_SELECT_DETAIL)
                .eq("linkedin_profile_url", url)
                .limit(1),
                count=None,
            )
        except IOAuthError as exc:
            logger.warning("lookup_by_linkedin auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("lookup_by_linkedin query error: %s", exc)
            return error_response("QUERY_ERROR", f"Bad query: {exc}")
        except IOTransientError as exc:
            logger.error("lookup_by_linkedin transient error: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error, please retry")

        if not rows:
            return error_response(
                "NOT_FOUND",
                f"No person found with LinkedIn URL: {url}",
                details={"hint": "URL must match exactly as stored, including trailing slash"},
            )

        person = fmt_person_detail(rows[0])
        return tool_response(
            data=person.model_dump(),
            summary=f"Found {person.first_name or ''} {person.last_name or ''} "
            f"({person.role or 'no role'}) at {person.company_name or 'unknown company'}",
            next_actions=[
                "Call lookup_by_email_domain(domain=...) to find colleagues at the same firm",
            ],
        )


# ---------------------------------------------------------------------------
# Tool 15: reverse_company_lookup
# ---------------------------------------------------------------------------


def _register_reverse_company_lookup(mcp: FastMCP, client: IOClient) -> None:
    @mcp.tool()
    async def reverse_company_lookup(company_name: str) -> str:
        """Find which investors have people from a given company.

        Queries persons by company_name ilike match, then groups results by
        investor FK to show which investor firms have contacts from that company.

        Useful for mapping an operator's advisors/alumni network to investors,
        or checking if a company is already tracked in the database.

        Args:
            company_name: Company name to search for (partial match, case-insensitive).
                          Example: "Sequoia" matches "Sequoia Capital", "Sequoia Heritage", etc.

        Returns:
            JSON envelope grouped by investor:
            [
              {
                "investor_id": 123,
                "investor_name": "Sequoia Capital",
                "contact_count": 5,
                "persons": [PersonSummary, ...]
              },
              ...
            ]
        """
        name = company_name.strip()
        if not name:
            return error_response("VALIDATION_ERROR", "company_name must be non-empty")

        try:
            rows, total = await client.query(
                QueryBuilder("persons")
                .select(PERSON_SELECT_SUMMARY)
                .ilike("company_name", f"*{name}*")
                .order("role", ascending=True)
                .limit(500),
                count="estimated",
            )
        except IOAuthError as exc:
            logger.warning("reverse_company_lookup auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed")
        except IOQueryError as exc:
            logger.error("reverse_company_lookup query error: %s", exc)
            return error_response("QUERY_ERROR", f"Bad query: {exc}")
        except IOTransientError as exc:
            logger.error("reverse_company_lookup transient error: %s", exc)
            return error_response("SERVER_ERROR", "Upstream error, please retry")

        if not rows:
            return tool_response(
                data=[],
                summary=f"No persons found with company matching '{name}'",
            )

        # Group by investor FK
        by_investor: dict[int | None, list[dict]] = defaultdict(list)
        for row in rows:
            by_investor[row.get("investor")].append(row)

        # Enrich investor names — fetch unique investor IDs from cache
        investor_ids: set[int] = {k for k in by_investor if k is not None}
        investor_names: dict[int, str] = {}
        for inv_id in investor_ids:
            inv = await client.get_investor_by_id(inv_id)
            if inv:
                investor_names[inv_id] = inv.get("investors") or ""

        # Build grouped output; sort largest groups first
        groups = []
        for inv_id, person_rows in sorted(
            by_investor.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            summaries = [fmt_person_summary(r).model_dump() for r in person_rows]
            groups.append(
                {
                    "investor_id": inv_id,
                    "investor_name": investor_names.get(inv_id, "") if inv_id else "",  # type: ignore[arg-type]
                    "contact_count": len(summaries),
                    "persons": summaries,
                }
            )

        unique_investors = sum(1 for g in groups if g["investor_id"] is not None)
        return tool_response(
            data=groups,
            summary=(
                f"Found {len(rows)} person(s) matching '{name}' "
                f"across {unique_investors} investor firm(s)"
            ),
            next_actions=[
                "Call get_investor(investor_id=...) for the full profile of any matched firm",
            ],
        )


# ---------------------------------------------------------------------------
# Tool 16: batch_firm_lookup
# ---------------------------------------------------------------------------


def _register_batch_firm_lookup(mcp: FastMCP, client: IOClient) -> None:
    @mcp.tool()
    async def batch_firm_lookup(firm_names: list[str]) -> str:
        """Look up multiple investor firms by name and return top contacts for each.

        For each firm name, queries the investors table with ilike matching, then
        fetches top-scored contacts for each matched investor. Useful for enriching
        a CRM list of target funds in bulk.

        Chunked to 50 names per iteration to stay within PostgREST URL length limits.
        Returns up to 5 investor matches per firm name and 5 contacts per investor.

        Args:
            firm_names: List of firm names to look up. Each name is matched with
                        ilike against investors.investors (the firm name column).
                        Example: ["Kayne Capital", "KKR", "Sequoia"]

        Returns:
            JSON envelope: list of per-firm results:
            [
              {
                "query": "Kayne Capital",
                "matched_investors": [InvestorSummary, ...],
                "top_contacts": [PersonSummary, ...]   # scored, top 5 across all matches
              },
              ...
            ]
            Firms with no match have matched_investors=[] and top_contacts=[].
        """
        clean_names = [n.strip() for n in firm_names if n and n.strip()]
        if not clean_names:
            return error_response("VALIDATION_ERROR", "firm_names must contain at least one non-empty string")

        results_by_query: dict[str, dict] = {
            n: {"query": n, "matched_investors": [], "top_contacts": []}
            for n in clean_names
        }

        # Process in chunks of _BATCH_FIRM_CHUNK (URL length management)
        # Each name needs its own ilike query — there's no PostgREST OR operator
        # across different rows, so we query once per name.
        for chunk in _chunk(clean_names, _BATCH_FIRM_CHUNK):
            for firm_name in chunk:
                try:
                    inv_rows, _ = await client.query(
                        QueryBuilder("investors")
                        .select(INVESTOR_SELECT_SUMMARY)
                        .ilike("investors", f"*{firm_name}*")
                        .limit(_BATCH_FIRM_INVESTOR_LIMIT),
                        count=None,
                    )
                except (IOAuthError, IOQueryError, IOTransientError) as exc:
                    logger.warning("batch_firm_lookup investor query error for '%s': %s", firm_name, exc)
                    continue

                if not inv_rows:
                    continue

                investor_summaries = [fmt_investor(r).model_dump() for r in inv_rows]
                results_by_query[firm_name]["matched_investors"] = investor_summaries

                # Collect top contacts across all matched investors
                investor_ids = [r["id"] for r in inv_rows if r.get("id")]
                all_contacts: list[dict] = []
                for inv_id in investor_ids:
                    try:
                        person_rows, _ = await client.query(
                            QueryBuilder("persons")
                            .select(PERSON_SELECT_SUMMARY)
                            .eq("investor", inv_id)
                            .limit(50),  # fetch pool for scoring
                            count=None,
                        )
                    except (IOAuthError, IOQueryError, IOTransientError) as exc:
                        logger.warning(
                            "batch_firm_lookup contacts query error for investor %d: %s", inv_id, exc
                        )
                        continue
                    all_contacts.extend(person_rows)

                top = _top_contacts_for_investor(all_contacts, _BATCH_FIRM_MAX_PER_FIRM)
                results_by_query[firm_name]["top_contacts"] = [
                    fmt_person_summary(r).model_dump() for r in top
                ]

        output = list(results_by_query.values())
        matched = sum(1 for r in output if r["matched_investors"])
        return tool_response(
            data=output,
            summary=f"Processed {len(clean_names)} firm name(s); {matched} matched in database",
            next_actions=[
                "Call get_investor(investor_id=...) for the full profile of any matched investor",
                "Call lookup_by_email_domain(domain=...) if you have the firm's email domain",
            ],
        )


# ---------------------------------------------------------------------------
# Tool 17: batch_person_lookup
# ---------------------------------------------------------------------------


def _register_batch_person_lookup(mcp: FastMCP, client: IOClient) -> None:
    @mcp.tool()
    async def batch_person_lookup(identifiers: list[str]) -> str:
        """Look up multiple persons by email address or full name.

        Accepts a mixed list of emails and "First Last" name strings. Emails are
        batched into in.() queries (100 per chunk). Name strings fall back to
        individual ilike queries (first_name + last_name split).

        Useful for CRM deduplication — paste a column of contact identifiers and
        get back matched records in one call.

        Args:
            identifiers: List of email addresses or "First Last" name strings.
                         Can be mixed. Examples:
                         ["john@kaynecapital.com", "Jane Smith", "bob@kkr.com"]

        Returns:
            JSON envelope:
            {
              "matched": [PersonSummary, ...],
              "unmatched_identifiers": ["identifier_that_returned_no_results", ...]
            }
        """
        clean = [i.strip() for i in identifiers if i and i.strip()]
        if not clean:
            return error_response("VALIDATION_ERROR", "identifiers must contain at least one non-empty string")

        emails = [i for i in clean if "@" in i]
        names = [i for i in clean if "@" not in i]

        matched: list[dict] = []
        matched_emails: set[str] = set()
        matched_name_keys: set[str] = set()

        # ── Email batch queries (chunked to _BATCH_PERSON_CHUNK) ──
        for chunk in _chunk(emails, _BATCH_PERSON_CHUNK):
            try:
                rows, _ = await client.query(
                    QueryBuilder("persons")
                    .select(PERSON_SELECT_SUMMARY)
                    .in_("email", chunk),
                    count=None,
                )
            except (IOAuthError, IOQueryError, IOTransientError) as exc:
                logger.warning("batch_person_lookup email query error: %s", exc)
                rows = []

            for row in rows:
                summary = fmt_person_summary(row)
                matched.append(summary.model_dump())
                if summary.email:
                    matched_emails.add(summary.email.lower())

        # ── Name queries (individual ilike per name) ──
        for name_str in names:
            parts = name_str.split(None, 1)  # split on first whitespace
            first = parts[0] if parts else ""
            last = parts[1] if len(parts) > 1 else ""

            try:
                if first and last:
                    # Query on both first and last name simultaneously
                    rows, _ = await client.query(
                        QueryBuilder("persons")
                        .select(PERSON_SELECT_SUMMARY)
                        .ilike("first_name", f"*{first}*")
                        .ilike("last_name", f"*{last}*")
                        .limit(10),
                        count=None,
                    )
                else:
                    # Single token — search first_name only
                    rows, _ = await client.query(
                        QueryBuilder("persons")
                        .select(PERSON_SELECT_SUMMARY)
                        .ilike("first_name", f"*{first}*")
                        .limit(10),
                        count=None,
                    )
            except (IOAuthError, IOQueryError, IOTransientError) as exc:
                logger.warning("batch_person_lookup name query error for '%s': %s", name_str, exc)
                rows = []

            if rows:
                name_key = name_str.lower()
                matched_name_keys.add(name_key)
                for row in rows:
                    matched.append(fmt_person_summary(row).model_dump())

        # ── Compute unmatched identifiers ──
        unmatched: list[str] = []
        for email in emails:
            if email.lower() not in matched_emails:
                unmatched.append(email)
        for name_str in names:
            if name_str.lower() not in matched_name_keys:
                unmatched.append(name_str)

        return tool_response(
            data={"matched": matched, "unmatched_identifiers": unmatched},
            summary=(
                f"Matched {len(matched)} person(s) from {len(clean)} identifier(s); "
                f"{len(unmatched)} unmatched"
            ),
            next_actions=[
                "Call lookup_by_linkedin(linkedin_url=...) for any person whose LinkedIn URL you have",
            ],
        )


# ---------------------------------------------------------------------------
# Registration entry point
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all Reverse Lookup tools with the MCP server.

    Called by src/server.py auto-discovery at startup.
    """
    _register_lookup_by_email_domain(mcp, client)
    _register_lookup_by_linkedin(mcp, client)
    _register_reverse_company_lookup(mcp, client)
    _register_batch_firm_lookup(mcp, client)
    _register_batch_person_lookup(mcp, client)
    logger.info("Registered 5 reverse lookup tools (tools 13–17)")
