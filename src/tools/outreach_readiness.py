"""Outreach Readiness tools for Investor Outbound MCP.

Tools 18-21 in the Phase 3 tool set:

    io_outreach_ready_contacts  — contacts with verified good emails only
    io_assess_contact_quality   — per-contact A/B/C/D quality grades + aggregate stats
    io_channel_coverage         — email/phone/LinkedIn breakdown per investor
    io_enrich_priorities        — LinkedIn-only contacts ranked for enrichment

Design notes
------------
- `outreach_ready_contacts` requires good_email=true, email_free=false,
  email_disposable=false, last_bounce_type IS NULL. Hard filters — no scoring.
- `assess_contact_quality` grades on a 4-tier scale using email_score and
  email_status. Returns per-contact grade dict plus aggregate grade counts.
- `channel_coverage` counts contacts with each channel per investor plus totals.
  Investor IDs are chunked to 100 per PostgREST query.
- `enrich_priorities` returns persons with linkedin_profile_url NOT NULL but
  email IS NULL, ranked by seniority score (is_senior from scoring.py).
- All tools accept `investor_ids` (list[int]) and chunk to 100 per query.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder

from src.helpers import error_response, tool_response
from src.scoring import is_senior, score_contact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 100  # max IDs per PostgREST in.(…) query

# Columns needed for outreach readiness filter
_READY_SELECT = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor,"
    "good_email,email_free,email_disposable,last_bounce_type,"
    "email_status,email_score"
)

# Columns needed for quality assessment (includes toxicity + bounce)
_QUALITY_SELECT = (
    "id,first_name,last_name,email,role,company_name,investor,"
    "good_email,email_status,email_score,email_toxicity,"
    "last_bounce_type,last_bounce_at,email_free,email_disposable"
)

# Columns needed for channel coverage (minimal — just presence flags)
_COVERAGE_SELECT = (
    "id,investor,email,phone,linkedin_profile_url"
)

# Columns needed for enrich priorities
_ENRICH_SELECT = (
    "id,first_name,last_name,role,company_name,investor,"
    "linkedin_profile_url,email,location"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_persons_for_investors(
    client: IOClient,
    investor_ids: list[int],
    select: str,
    extra_filters: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Query persons for multiple investor IDs, chunked to _CHUNK_SIZE.

    Optionally applies additional PostgREST filter params passed as raw
    (key, value) tuples (e.g. ("good_email", "eq.true")).

    Returns all rows across chunks, deduped by person id.
    """
    seen_ids: set[int] = set()
    all_rows: list[dict[str, Any]] = []

    for chunk_start in range(0, len(investor_ids), _CHUNK_SIZE):
        chunk = investor_ids[chunk_start : chunk_start + _CHUNK_SIZE]
        qb = QueryBuilder("persons").select(select).in_("investor", chunk).limit(1000)

        if extra_filters:
            for key, value in extra_filters:
                qb.raw(key, value)

        rows, _ = await client.query(qb, count=None)
        for row in rows:
            pid = row.get("id")
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_rows.append(row)

    return all_rows


def _grade_contact(row: dict[str, Any]) -> str:
    """Return a quality grade (A/B/C/D) for a single contact row.

    Grade rules
    -----------
    A — good_email=true AND email_score > 80
    B — email_status='deliverable' AND email_score > 50
    C — email_status='risky' OR (email exists AND email_score <= 50)
    D — email_status='undeliverable' OR last_bounce_type is not null
    D — no email at all

    Evaluated top-to-bottom; first matching grade wins.
    """
    email = row.get("email")
    good_email = row.get("good_email")
    status = (row.get("email_status") or "").lower()
    score = row.get("email_score")
    bounce = row.get("last_bounce_type")

    # Grade D: undeliverable or bounced
    if status == "undeliverable" or bounce is not None:
        return "D"

    # No email → D
    if not email:
        return "D"

    # Grade A: good_email + high score
    if good_email and score is not None and score > 80:
        return "A"

    # Grade B: deliverable + decent score
    if status == "deliverable" and score is not None and score > 50:
        return "B"

    # Grade C: risky or low/unknown score
    if status == "risky":
        return "C"
    if score is not None and score <= 50:
        return "C"

    # Default C for everything else with an email present
    return "C"


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all 4 outreach readiness tools with the MCP server."""

    # ------------------------------------------------------------------ #
    # Tool 18: io_outreach_ready_contacts
    # ------------------------------------------------------------------ #

    @mcp.tool(
        name="io_outreach_ready_contacts",
        description=(
            "Return contacts that are ready to receive outreach email. "
            "Hard filters: good_email=true, email_free=false, "
            "email_disposable=false, no recorded bounce (last_bounce_type IS NULL). "
            "Accepts a list of investor_ids. Chunks queries to 100 IDs at a time."
        ),
    )
    async def outreach_ready_contacts(
        investor_ids: list[int],
    ) -> str:
        """Return outreach-ready contacts for the given investor IDs.

        Args:
            investor_ids: List of investor primary-key IDs to query.

        Returns:
            JSON tool response with filtered contacts and a summary count.
        """
        if not investor_ids:
            return error_response(
                "VALIDATION_ERROR",
                "investor_ids must be a non-empty list of integers.",
            )

        try:
            # Apply hard email-quality filters at the PostgREST level
            extra_filters = [
                ("good_email", "eq.true"),
                ("email_free", "eq.false"),
                ("email_disposable", "eq.false"),
                ("last_bounce_type", "is.null"),
            ]
            rows = await _fetch_persons_for_investors(
                client, investor_ids, _READY_SELECT, extra_filters
            )
        except IOAuthError as exc:
            logger.error("Auth error in outreach_ready_contacts: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed.")
        except IOQueryError as exc:
            logger.error("Query error in outreach_ready_contacts: %s", exc)
            return error_response("QUERY_ERROR", "Bad query building outreach contacts.")
        except IOTransientError as exc:
            logger.error("Transient error in outreach_ready_contacts: %s", exc)
            return error_response("SERVER_ERROR", "Transient server error — retry.")
        except Exception as exc:
            logger.error("Unexpected error in outreach_ready_contacts: %s", exc)
            return error_response("SERVER_ERROR", "Unexpected error.")

        count = len(rows)
        return tool_response(
            data=rows,
            summary=(
                f"Found {count} outreach-ready contact{'s' if count != 1 else ''} "
                f"across {len(investor_ids)} investor{'s' if len(investor_ids) != 1 else ''}."
            ),
            next_actions=[
                "Call io_assess_contact_quality(investor_ids=...) to grade these contacts.",
                "Call io_channel_coverage(investor_ids=...) for phone/LinkedIn breakdown.",
            ],
        )

    # ------------------------------------------------------------------ #
    # Tool 19: io_assess_contact_quality
    # ------------------------------------------------------------------ #

    @mcp.tool(
        name="io_assess_contact_quality",
        description=(
            "Grade each contact A/B/C/D by email deliverability. "
            "A=good_email + score>80, B=deliverable + score>50, "
            "C=risky or score<=50, D=undeliverable/bounced/no-email. "
            "Returns per-contact grades and aggregate grade counts."
        ),
    )
    async def assess_contact_quality(
        investor_ids: list[int],
    ) -> str:
        """Assess email quality for all contacts across the given investor IDs.

        Args:
            investor_ids: List of investor primary-key IDs to query.

        Returns:
            JSON tool response with:
              - data.contacts: list of {id, name, role, email, grade}
              - data.stats: {A, B, C, D, total}
        """
        if not investor_ids:
            return error_response(
                "VALIDATION_ERROR",
                "investor_ids must be a non-empty list of integers.",
            )

        try:
            rows = await _fetch_persons_for_investors(
                client, investor_ids, _QUALITY_SELECT
            )
        except IOAuthError as exc:
            logger.error("Auth error in assess_contact_quality: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed.")
        except IOQueryError as exc:
            logger.error("Query error in assess_contact_quality: %s", exc)
            return error_response("QUERY_ERROR", "Bad query building quality assessment.")
        except IOTransientError as exc:
            logger.error("Transient error in assess_contact_quality: %s", exc)
            return error_response("SERVER_ERROR", "Transient server error — retry.")
        except Exception as exc:
            logger.error("Unexpected error in assess_contact_quality: %s", exc)
            return error_response("SERVER_ERROR", "Unexpected error.")

        grade_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}
        graded_contacts: list[dict[str, Any]] = []

        for row in rows:
            grade = _grade_contact(row)
            grade_counts[grade] += 1
            graded_contacts.append(
                {
                    "id": row.get("id"),
                    "name": f"{row.get('first_name', '') or ''} {row.get('last_name', '') or ''}".strip(),
                    "role": row.get("role"),
                    "email": row.get("email"),
                    "email_status": row.get("email_status"),
                    "email_score": row.get("email_score"),
                    "grade": grade,
                }
            )

        total = len(rows)
        a_pct = round(grade_counts["A"] / total * 100, 1) if total else 0

        return tool_response(
            data={"contacts": graded_contacts, "stats": {**grade_counts, "total": total}},
            summary=(
                f"Graded {total} contact{'s' if total != 1 else ''}: "
                f"{grade_counts['A']} A, {grade_counts['B']} B, "
                f"{grade_counts['C']} C, {grade_counts['D']} D "
                f"({a_pct}% top-tier)."
            ),
            next_actions=[
                "Call io_outreach_ready_contacts(investor_ids=...) to get only Grade A/B contacts.",
                "Call io_enrich_priorities(investor_ids=...) to find Grade D contacts with LinkedIn.",
            ],
        )

    # ------------------------------------------------------------------ #
    # Tool 20: io_channel_coverage
    # ------------------------------------------------------------------ #

    @mcp.tool(
        name="io_channel_coverage",
        description=(
            "For a list of investor_ids: count contacts with email, phone, "
            "LinkedIn, all three channels, and none. "
            "Returns breakdown per investor and rolled-up totals."
        ),
    )
    async def channel_coverage(
        investor_ids: list[int],
    ) -> str:
        """Compute channel coverage breakdown for the given investor IDs.

        Args:
            investor_ids: List of investor primary-key IDs to query.

        Returns:
            JSON tool response with:
              - data.per_investor: list of per-investor coverage dicts
              - data.totals: rolled-up counts across all investors
        """
        if not investor_ids:
            return error_response(
                "VALIDATION_ERROR",
                "investor_ids must be a non-empty list of integers.",
            )

        try:
            rows = await _fetch_persons_for_investors(
                client, investor_ids, _COVERAGE_SELECT
            )
        except IOAuthError as exc:
            logger.error("Auth error in channel_coverage: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed.")
        except IOQueryError as exc:
            logger.error("Query error in channel_coverage: %s", exc)
            return error_response("QUERY_ERROR", "Bad query building channel coverage.")
        except IOTransientError as exc:
            logger.error("Transient error in channel_coverage: %s", exc)
            return error_response("SERVER_ERROR", "Transient server error — retry.")
        except Exception as exc:
            logger.error("Unexpected error in channel_coverage: %s", exc)
            return error_response("SERVER_ERROR", "Unexpected error.")

        # Aggregate by investor_id
        investor_buckets: dict[int, dict[str, int]] = {}
        for row in rows:
            inv_id = row.get("investor")
            if inv_id not in investor_buckets:
                investor_buckets[inv_id] = {
                    "investor_id": inv_id,
                    "total_contacts": 0,
                    "with_email": 0,
                    "with_phone": 0,
                    "with_linkedin": 0,
                    "all_three": 0,
                    "none": 0,
                }
            bucket = investor_buckets[inv_id]
            bucket["total_contacts"] += 1

            has_email = bool(row.get("email"))
            has_phone = bool(row.get("phone"))
            has_linkedin = bool(row.get("linkedin_profile_url"))

            if has_email:
                bucket["with_email"] += 1
            if has_phone:
                bucket["with_phone"] += 1
            if has_linkedin:
                bucket["with_linkedin"] += 1
            if has_email and has_phone and has_linkedin:
                bucket["all_three"] += 1
            if not has_email and not has_phone and not has_linkedin:
                bucket["none"] += 1

        per_investor = list(investor_buckets.values())

        # Rolled-up totals
        total_contacts = sum(b["total_contacts"] for b in per_investor)
        totals = {
            "total_contacts": total_contacts,
            "with_email": sum(b["with_email"] for b in per_investor),
            "with_phone": sum(b["with_phone"] for b in per_investor),
            "with_linkedin": sum(b["with_linkedin"] for b in per_investor),
            "all_three": sum(b["all_three"] for b in per_investor),
            "none": sum(b["none"] for b in per_investor),
        }

        email_pct = round(totals["with_email"] / total_contacts * 100, 1) if total_contacts else 0

        return tool_response(
            data={"per_investor": per_investor, "totals": totals},
            summary=(
                f"{total_contacts} contact{'s' if total_contacts != 1 else ''} across "
                f"{len(per_investor)} investor{'s' if len(per_investor) != 1 else ''}: "
                f"{totals['with_email']} with email ({email_pct}%), "
                f"{totals['with_phone']} with phone, "
                f"{totals['with_linkedin']} with LinkedIn."
            ),
            next_actions=[
                "Call io_enrich_priorities(investor_ids=...) for contacts missing email but having LinkedIn.",
                "Call io_outreach_ready_contacts(investor_ids=...) for verified-deliverable contacts.",
            ],
        )

    # ------------------------------------------------------------------ #
    # Tool 21: io_enrich_priorities
    # ------------------------------------------------------------------ #

    @mcp.tool(
        name="io_enrich_priorities",
        description=(
            "Find contacts with a LinkedIn profile URL but no email — "
            "the best targets for enrichment. "
            "Results are ranked by seniority (senior titles first). "
            "Accepts a list of investor_ids."
        ),
    )
    async def enrich_priorities(
        investor_ids: list[int],
    ) -> str:
        """Return LinkedIn-only contacts ranked by seniority for enrichment.

        Args:
            investor_ids: List of investor primary-key IDs to query.

        Returns:
            JSON tool response with contacts ranked by seniority score,
            each record including linkedin_profile_url, role, and company.
        """
        if not investor_ids:
            return error_response(
                "VALIDATION_ERROR",
                "investor_ids must be a non-empty list of integers.",
            )

        try:
            # PostgREST filters: linkedin_profile_url is not null, email is null
            extra_filters = [
                ("linkedin_profile_url", "not.is.null"),
                ("email", "is.null"),
            ]
            rows = await _fetch_persons_for_investors(
                client, investor_ids, _ENRICH_SELECT, extra_filters
            )
        except IOAuthError as exc:
            logger.error("Auth error in enrich_priorities: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed.")
        except IOQueryError as exc:
            logger.error("Query error in enrich_priorities: %s", exc)
            return error_response("QUERY_ERROR", "Bad query building enrich priorities.")
        except IOTransientError as exc:
            logger.error("Transient error in enrich_priorities: %s", exc)
            return error_response("SERVER_ERROR", "Transient server error — retry.")
        except Exception as exc:
            logger.error("Unexpected error in enrich_priorities: %s", exc)
            return error_response("SERVER_ERROR", "Unexpected error.")

        # Score and rank by seniority — senior flag drives the sort
        ranked: list[dict[str, Any]] = []
        for row in rows:
            role = row.get("role") or ""
            senior = is_senior(role)
            seniority_score = score_contact(role)
            ranked.append(
                {
                    "id": row.get("id"),
                    "name": f"{row.get('first_name', '') or ''} {row.get('last_name', '') or ''}".strip(),
                    "role": row.get("role"),
                    "company_name": row.get("company_name"),
                    "linkedin_profile_url": row.get("linkedin_profile_url"),
                    "investor_id": row.get("investor"),
                    "is_senior": senior,
                    "seniority_score": seniority_score,
                }
            )

        # Sort: senior first, then by descending seniority_score
        ranked.sort(key=lambda r: (not r["is_senior"], -r["seniority_score"]))

        senior_count = sum(1 for r in ranked if r["is_senior"])
        total = len(ranked)

        return tool_response(
            data=ranked,
            summary=(
                f"{total} contact{'s' if total != 1 else ''} with LinkedIn but no email "
                f"across {len(investor_ids)} investor{'s' if len(investor_ids) != 1 else ''}. "
                f"{senior_count} senior-level ({total - senior_count} other). "
                "Ranked by seniority — enrich top contacts first."
            ),
            next_actions=[
                "Use the linkedin_profile_url values to enrich email via your enrichment provider.",
                "Call io_assess_contact_quality(investor_ids=...) after enrichment to verify deliverability.",
            ],
        )
