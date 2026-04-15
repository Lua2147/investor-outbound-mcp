"""Export & Hygiene Tools (Tools 28–30).

Three tools for bulk export and data hygiene:
- Tool 28: io_export_contacts   — trigger CSV export via export2 edge fn, poll
                                   user_exports table via PostgREST GET until
                                   status="Ready", return signed download URL.
- Tool 29: io_stale_contact_check — find contacts with deliverability problems
                                   (bounced, low score, undeliverable).
- Tool 30: io_search_by_company_industry — filter persons by company_industry
                                   (ilike) and optional company_size bucket.

Design decisions:
- export2 polling uses PostgREST GET on user_exports (NOT WebSocket / realtime).
  We poll every 3 seconds for up to 60 seconds. If timeout, return partial status.
- stale_contact_check uses three OR-branch PostgREST queries (one per issue type)
  and merges results in Python to avoid complex OR syntax in PostgREST.
- search_by_company_industry uses ilike with wildcard wrapping so that e.g.
  "financial services" matches the LinkedIn-style label exactly.
- All three tools follow the standard register(mcp, client) pattern for
  auto-discovery by src/server.py.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder

from src.helpers import error_response, paginated_response, tool_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPORT_POLL_INTERVAL = 3.0   # seconds between PostgREST polls
_EXPORT_POLL_MAX = 60.0       # total seconds before timeout
_STALE_EMAIL_SCORE_THRESHOLD = 30
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

# Columns needed for stale-contact queries — full detail for actionable output
_STALE_SELECT = (
    "id,first_name,last_name,email,role,company_name,investor,"
    "email_status,email_score,last_bounce_type,last_bounce_at,"
    "linkedin_profile_url"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _person_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned-up dict for a person row (strip None-heavy fields)."""
    return {k: v for k, v in row.items() if v is not None}


# ---------------------------------------------------------------------------
# Register function (auto-discovered by src/server.py)
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all 3 Export & Hygiene tools on the FastMCP instance."""

    # ------------------------------------------------------------------
    # Tool 28: io_export_contacts
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_export_contacts(
        export_name: str,
        contacts_per_investor: int = 5,
        search_term: str | None = None,
        investment_types: list[str] | None = None,
        investor_types: list[str] | None = None,
        locations: list[str] | None = None,
        sectors: list[str] | None = None,
        fund_domicile: list[str] | None = None,
        investment_firm_min_size: int | None = None,
        investment_firm_max_size: int | None = None,
        limit_count: int = 1000,
    ) -> str:
        """Trigger a CSV export via the export2 edge function and return the download URL.

        Starts a background export job, polls the user_exports table every 3 seconds
        (via PostgREST GET — NOT WebSocket) for up to 60 seconds until the export
        status becomes "Ready", then calls the download-export edge function to get
        a temporary signed URL.

        If the export does not complete within 60 seconds, returns partial status with
        the export ID so the caller can poll manually.

        Args:
            export_name: Human-readable name for this export (shown in the UI).
            contacts_per_investor: Number of contacts per investor in the CSV (default 5).
            search_term: Optional keyword filter applied to investor names/descriptions.
            investment_types: Optional list of investment stage types (ilike strings).
            investor_types: Optional list of investor type strings.
            locations: Optional list of location strings.
            sectors: Optional list of sector codes.
            fund_domicile: Optional list of fund domicile strings.
            investment_firm_min_size: Optional min AUM filter.
            investment_firm_max_size: Optional max AUM filter.
            limit_count: Max records to include in the export (default 1000, max 10000).

        Returns:
            JSON string with:
                - status: "ready" | "timeout" | "failed"
                - download_url: Signed URL (present when status="ready")
                - export_id: UUID of the export job
                - export_name: Name passed by caller
                - poll_seconds: Time spent polling
                - message: Human-readable status message
        """
        # --- Step 1: Trigger export2 edge function ---
        body: dict[str, Any] = {
            "export_name": export_name,
            "contacts_per_investor": contacts_per_investor,
            "limit_count": limit_count,
            "offset_export": 0,
        }
        # Only include optional fields when provided — avoid sending nulls to the edge fn
        if search_term:
            body["search_term"] = search_term
        if investment_types:
            body["investment_types"] = investment_types
        if investor_types:
            body["investor_types"] = investor_types
        if locations:
            body["locations"] = locations
        if sectors:
            body["sectors"] = sectors
        if fund_domicile:
            body["fund_domicile"] = fund_domicile
        if investment_firm_min_size is not None:
            body["investment_firm_min_size"] = investment_firm_min_size
        if investment_firm_max_size is not None:
            body["investment_firm_max_size"] = investment_firm_max_size

        try:
            trigger_resp = await client.edge("export2", body, timeout=30.0)
        except IOAuthError as exc:
            logger.error("Auth error triggering export2: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — re-run after reconnecting.")
        except IOTransientError as exc:
            logger.error("Transient error triggering export2: %s", exc)
            return error_response("SERVER_ERROR", "Export trigger failed due to a transient server error.")
        except IOQueryError as exc:
            logger.error("Query error triggering export2: %s", exc)
            return error_response("QUERY_ERROR", f"Export trigger rejected: {str(exc)[:200]}")

        # The edge function returns an export ID. It may be under "id", "export_id",
        # or at top-level as a raw string — handle all shapes.
        export_id: str | None = None
        if isinstance(trigger_resp, dict):
            export_id = trigger_resp.get("id") or trigger_resp.get("export_id")
        elif isinstance(trigger_resp, str):
            export_id = trigger_resp

        if not export_id:
            logger.error("export2 returned no export ID: %r", trigger_resp)
            return error_response(
                "SERVER_ERROR",
                "Export job was created but no export ID was returned.",
                details={"raw_response": str(trigger_resp)[:400]},
            )

        logger.info("Export job started: id=%s name=%r", export_id, export_name)

        # --- Step 2: Poll user_exports table via PostgREST GET ---
        elapsed = 0.0
        export_row: dict[str, Any] | None = None

        while elapsed < _EXPORT_POLL_MAX:
            await asyncio.sleep(_EXPORT_POLL_INTERVAL)
            elapsed += _EXPORT_POLL_INTERVAL

            try:
                rows, _ = await client.query(
                    QueryBuilder("user_exports")
                    .select("id,status,file_url,created_at,updated_at")
                    .eq("id", export_id)
                    .limit(1),
                    count=None,
                )
            except (IOQueryError, IOTransientError) as exc:
                logger.warning("Poll attempt failed (%.0fs elapsed): %s", elapsed, exc)
                continue

            if rows:
                export_row = rows[0]
                status_val = (export_row.get("status") or "").lower()
                logger.debug("Export %s status=%r at %.0fs", export_id, status_val, elapsed)

                if status_val == "ready":
                    break
                if status_val in ("failed", "error", "cancelled"):
                    return error_response(
                        "SERVER_ERROR",
                        f"Export job failed with status '{status_val}'.",
                        details={"export_id": export_id, "row": export_row},
                    )

        # --- Timeout path ---
        if export_row is None or (export_row.get("status") or "").lower() != "ready":
            return tool_response(
                data={
                    "status": "timeout",
                    "export_id": export_id,
                    "export_name": export_name,
                    "poll_seconds": elapsed,
                    "last_status": export_row.get("status") if export_row else None,
                },
                summary=(
                    f"Export '{export_name}' did not complete within {int(_EXPORT_POLL_MAX)}s. "
                    f"Export ID: {export_id}. Check user_exports table for status."
                ),
                next_actions=[
                    f"Query user_exports table with id=eq.{export_id} to check status",
                    "Call io_export_contacts again with the same parameters",
                ],
            )

        # --- Step 3: Fetch signed download URL from download-export edge fn ---
        try:
            dl_resp = await client.edge("download-export", {"id": export_id}, timeout=30.0)
        except (IOAuthError, IOTransientError, IOQueryError) as exc:
            logger.error("Failed to fetch download URL for export %s: %s", export_id, exc)
            # Export is ready — return partial success with ID so caller can retry
            return tool_response(
                data={
                    "status": "ready_no_url",
                    "export_id": export_id,
                    "export_name": export_name,
                    "poll_seconds": elapsed,
                },
                summary=(
                    f"Export '{export_name}' is ready (ID: {export_id}) "
                    "but signed URL retrieval failed. Use export_id to re-fetch."
                ),
                next_actions=[
                    f"Call download-export edge function with {{\"id\": \"{export_id}\"}} to get URL"
                ],
            )

        download_url: str | None = None
        if isinstance(dl_resp, dict):
            download_url = dl_resp.get("url") or dl_resp.get("download_url")
        elif isinstance(dl_resp, str):
            download_url = dl_resp

        return tool_response(
            data={
                "status": "ready",
                "download_url": download_url,
                "export_id": export_id,
                "export_name": export_name,
                "poll_seconds": elapsed,
            },
            summary=(
                f"Export '{export_name}' is ready. "
                f"Download URL retrieved after {int(elapsed)}s of polling."
            ),
            next_actions=[
                "Download the CSV using the signed URL (expires shortly — download immediately)",
                "Call io_search_by_company_industry or io_stale_contact_check after import to filter contacts",
            ],
        )

    # ------------------------------------------------------------------
    # Tool 29: io_stale_contact_check
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_stale_contact_check(
        investor_ids: list[int] | None = None,
        sectors: list[str] | None = None,
        investor_types: list[str] | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> str:
        """Find contacts with deliverability problems across an investor set.

        Identifies contacts in three stale/problematic categories:
        - bounced: last_bounce_type is not null (hard or soft bounce recorded)
        - low_score: email_score < 30 (unreliable deliverability score)
        - undeliverable: email_status = "undeliverable"

        Contacts can appear in more than one category if multiple issues apply.
        Results are grouped by issue type with counts and sampled rows.

        Either investor_ids or filter parameters (sectors, investor_types) must
        be provided — the tool will not scan the full 1.8M persons table without
        scope.

        Args:
            investor_ids: Optional explicit list of investor IDs to scope the check.
                Chunked to 100 per PostgREST query.
            sectors: Optional sector code list — used to resolve investor IDs when
                investor_ids is not provided. Applied as ov (overlap) filter.
            investor_types: Optional investor type list — used alongside sectors to
                narrow the investor pre-filter.
            limit: Max contacts per issue category (default 100, max 500).

        Returns:
            JSON string with:
                - categories: dict with keys "bounced", "low_score", "undeliverable"
                  Each category has: count (int), sample (list of contact dicts).
                - total_flagged: total unique flagged contact IDs across all categories
                - investor_scope: number of investors checked (null if scanning directly)
                - summary: description of findings
        """
        limit = min(limit, _MAX_LIMIT)

        # --- Step 1: Resolve investor scope ---
        investor_id_set: set[int] | None = None

        if investor_ids:
            investor_id_set = set(investor_ids)
        elif sectors or investor_types:
            # Fetch investor IDs matching sector/type filters
            inv_qb = QueryBuilder("investors").select("id")
            if sectors:
                inv_qb = inv_qb.ov("sectors_array", sectors)
            if investor_types:
                # investor_types is matched one-at-a-time with ilike (no IN for text)
                # Use the first type as primary filter — caller should narrow upstream
                inv_qb = inv_qb.ilike("primary_investor_type", f"*{investor_types[0]}*")
            inv_qb = inv_qb.limit(5000)

            try:
                inv_rows, _ = await client.query(inv_qb, count=None)
            except (IOAuthError, IOQueryError, IOTransientError) as exc:
                logger.error("Failed to fetch investor IDs for stale check: %s", exc)
                return error_response("SERVER_ERROR", "Could not resolve investor scope.")

            investor_id_set = {r["id"] for r in inv_rows if r.get("id") is not None}
            if not investor_id_set:
                return tool_response(
                    data={
                        "categories": {
                            "bounced": {"count": 0, "sample": []},
                            "low_score": {"count": 0, "sample": []},
                            "undeliverable": {"count": 0, "sample": []},
                        },
                        "total_flagged": 0,
                        "investor_scope": 0,
                    },
                    summary="No investors matched the provided filters — no stale contacts to report.",
                )
        else:
            return error_response(
                "VALIDATION_ERROR",
                "Provide investor_ids or at least one of sectors/investor_types to scope the check.",
            )

        # --- Step 2: Fetch stale contacts in three independent queries ---
        # Each query runs against the persons table, filtered by investor FK if scoped.
        # We do NOT use OR syntax across columns in PostgREST — three separate queries.

        investor_list = sorted(investor_id_set) if investor_id_set else []
        _CHUNK = 100

        async def _fetch_stale_batch(qb_builder_fn: Any) -> list[dict[str, Any]]:
            """Fetch all rows using the provided query-builder factory, chunked by investor."""
            rows: list[dict[str, Any]] = []
            seen: set[int] = set()

            if investor_list:
                for i in range(0, len(investor_list), _CHUNK):
                    chunk = investor_list[i : i + _CHUNK]
                    qb = qb_builder_fn()
                    qb = qb.in_("investor", chunk).limit(limit)
                    try:
                        batch, _ = await client.query(qb, count=None)
                    except (IOQueryError, IOTransientError) as exc:
                        logger.warning("Stale batch query failed: %s", exc)
                        continue
                    for row in batch:
                        pid = row.get("id")
                        if pid not in seen:
                            seen.add(pid)
                            rows.append(row)
                            if len(rows) >= limit:
                                return rows
            else:
                qb = qb_builder_fn().limit(limit)
                try:
                    rows, _ = await client.query(qb, count=None)
                except (IOQueryError, IOTransientError) as exc:
                    logger.warning("Stale query failed (no investor scope): %s", exc)

            return rows

        def _bounced_qb() -> QueryBuilder:
            return (
                QueryBuilder("persons")
                .select(_STALE_SELECT)
                .not_is("last_bounce_type", "null")
            )

        def _low_score_qb() -> QueryBuilder:
            return (
                QueryBuilder("persons")
                .select(_STALE_SELECT)
                .lt("email_score", _STALE_EMAIL_SCORE_THRESHOLD)
            )

        def _undeliverable_qb() -> QueryBuilder:
            return (
                QueryBuilder("persons")
                .select(_STALE_SELECT)
                .eq("email_status", "undeliverable")
            )

        try:
            bounced_rows, low_score_rows, undeliverable_rows = await asyncio.gather(
                _fetch_stale_batch(_bounced_qb),
                _fetch_stale_batch(_low_score_qb),
                _fetch_stale_batch(_undeliverable_qb),
            )
        except Exception as exc:
            logger.error("Unexpected error in stale contact queries: %s", exc)
            return error_response("SERVER_ERROR", "Stale contact query failed unexpectedly.")

        # --- Step 3: Count unique flagged contacts ---
        all_flagged_ids: set[int] = set()
        for row in bounced_rows + low_score_rows + undeliverable_rows:
            pid = row.get("id")
            if pid is not None:
                all_flagged_ids.add(pid)

        bounced_sample = [_person_to_dict(r) for r in bounced_rows[:50]]
        low_score_sample = [_person_to_dict(r) for r in low_score_rows[:50]]
        undeliverable_sample = [_person_to_dict(r) for r in undeliverable_rows[:50]]

        total_flagged = len(all_flagged_ids)
        investor_scope = len(investor_id_set) if investor_id_set else None

        summary_parts = []
        if bounced_rows:
            summary_parts.append(f"{len(bounced_rows)} bounced")
        if low_score_rows:
            summary_parts.append(f"{len(low_score_rows)} low-score (<{_STALE_EMAIL_SCORE_THRESHOLD})")
        if undeliverable_rows:
            summary_parts.append(f"{len(undeliverable_rows)} undeliverable")

        if not summary_parts:
            summary = f"No stale contacts found across {investor_scope or 'all'} investors."
        else:
            summary = (
                f"Found {total_flagged} unique flagged contacts: "
                + ", ".join(summary_parts)
                + (f" (across {investor_scope} investors)" if investor_scope else "")
                + "."
            )

        return tool_response(
            data={
                "categories": {
                    "bounced": {
                        "count": len(bounced_rows),
                        "sample": bounced_sample,
                    },
                    "low_score": {
                        "count": len(low_score_rows),
                        "sample": low_score_sample,
                        "threshold": _STALE_EMAIL_SCORE_THRESHOLD,
                    },
                    "undeliverable": {
                        "count": len(undeliverable_rows),
                        "sample": undeliverable_sample,
                    },
                },
                "total_flagged": total_flagged,
                "investor_scope": investor_scope,
            },
            summary=summary,
            next_actions=[
                "Review bounced contacts for suppression list additions",
                "Call io_outreach_ready_contacts to get deliverable-only contacts for the same investor set",
            ]
            if total_flagged > 0
            else [],
        )

    # ------------------------------------------------------------------
    # Tool 30: io_search_by_company_industry
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_search_by_company_industry(
        company_industry: str,
        company_size: str | None = None,
        company_country: str | None = None,
        has_email: bool | None = None,
        limit: int = 50,
        page: int = 1,
    ) -> str:
        """Filter persons by LinkedIn-style company industry and optional company size.

        Uses ilike matching on the company_industry column (LinkedIn-style labels
        such as "financial services", "hospital & health care", "computer software",
        "investment management", "venture capital & private equity").

        Company size uses exact bucket matching against standard LinkedIn size buckets:
        "1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000",
        "5001-10000", "10001+".

        Args:
            company_industry: LinkedIn-style industry label to match (case-insensitive,
                partial match). Examples: "financial services", "hospital & health care",
                "computer software".
            company_size: Optional exact size bucket string. Must match the DB enum
                exactly: "1-10", "11-50", "51-200", "201-500", "501-1000",
                "1001-5000", "5001-10000", "10001+".
            company_country: Optional country filter on company_country column (ilike).
                Examples: "United States", "United Kingdom", "Canada".
            has_email: Optional filter: True = only persons with a non-null email,
                False = only persons without email, None = all (default).
            limit: Records per page (default 50, max 500).
            page: 1-indexed page number (default 1).

        Returns:
            Paginated JSON with persons matching the industry filter, each record
            containing contact details, email quality indicators, and company metadata.
        """
        if not company_industry or not company_industry.strip():
            return error_response(
                "VALIDATION_ERROR",
                "company_industry is required and cannot be blank.",
            )

        limit = max(1, min(limit, _MAX_LIMIT))
        page = max(1, page)
        offset = (page - 1) * limit

        qb = (
            QueryBuilder("persons")
            .select(
                "id,first_name,last_name,email,phone,role,company_name,"
                "company_industry,company_size,company_country,domain,"
                "linkedin_profile_url,email_status,email_score,good_email,investor"
            )
            .ilike("company_industry", f"*{company_industry.strip()}*")
        )

        if company_size:
            qb = qb.eq("company_size", company_size)

        if company_country:
            qb = qb.ilike("company_country", f"*{company_country.strip()}*")

        if has_email is True:
            qb = qb.not_is("email", "null")
        elif has_email is False:
            qb = qb.is_("email", "null")

        qb = qb.order("email_score", ascending=False).limit(limit).offset(offset)

        try:
            rows, total = await client.query(qb, count="estimated")
        except IOAuthError as exc:
            logger.error("Auth error in search_by_company_industry: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed.")
        except IOQueryError as exc:
            logger.error("Query error in search_by_company_industry: %s", exc)
            return error_response("QUERY_ERROR", f"Query failed: {str(exc)[:200]}")
        except IOTransientError as exc:
            logger.error("Transient error in search_by_company_industry: %s", exc)
            return error_response("SERVER_ERROR", "Transient server error — please retry.")

        persons = [_person_to_dict(r) for r in rows]

        size_clause = f", size bucket '{company_size}'" if company_size else ""
        country_clause = f" in {company_country}" if company_country else ""
        email_clause = " (with email)" if has_email is True else (" (no email)" if has_email is False else "")

        summary = (
            f"Found {'~' if total is None else ''}{total or len(persons)} persons "
            f"in '{company_industry}'{size_clause}{country_clause}{email_clause}. "
            f"Page {page} of results ({len(persons)} records)."
        )

        return paginated_response(
            data=persons,
            total=total,
            page=page,
            page_size=limit,
            summary=summary,
            next_actions=[
                f"Call io_search_by_company_industry with page={page + 1} for next page"
                if total and (page * limit) < total
                else "All pages retrieved",
                "Call io_get_contacts with investor_ids from these results to get scored contacts",
            ],
        )
