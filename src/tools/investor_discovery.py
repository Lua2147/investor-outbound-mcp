"""Investor Discovery tools — Tool 5–8.

Tools:
    io_search_investors     — filter investors by type/sector/geography/check_size/keyword/status
    io_search_descriptions  — keyword search on investor description field (ilike)
    io_get_investor         — full profile by ID (int) or name (str ilike)
    io_investor_freshness   — recently updated investors ordered by updated_at desc

All tools are registered by calling ``register(mcp, client)`` — server.py auto-discovers this.

Design notes:
- check_size_min/max are stored as MILLIONS USD in DB. Tools accept dollar values and divide by 1M.
- preferred_investment_types is a TEXT string, use ilike only (cs operator fails on text type).
- Sector resolution: human names → DB codes via resolve_sectors().
- Investor type resolution: human names → DB enum values via resolve_investor_types().
- Default status filter: exclude "Acquired/Merged" (status eq.null or not eq.Acquired/Merged).
"""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder
from src.entities.investor import (
    INVESTOR_SELECT_DETAIL,
    INVESTOR_SELECT_SUMMARY,
    format_detail as format_investor_detail,
    format_summary as format_investor_summary,
)
from src.helpers import error_response, paginated_response, tool_response
from src.sectors import resolve_investor_types, resolve_sectors

logger = logging.getLogger(__name__)

# Status value that indicates a defunct/absorbed investor — excluded by default
_ACQUIRED_MERGED_STATUS = "Acquired/Merged"

# Default page size for list tools
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all 4 Investor Discovery tools on the FastMCP instance."""

    # ------------------------------------------------------------------
    # Tool 5: io_search_investors
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_search_investors(
        sectors: list[str] | None = None,
        investor_types: list[str] | None = None,
        geography: str | None = None,
        check_size_min_dollars: float | None = None,
        check_size_max_dollars: float | None = None,
        keyword: str | None = None,
        include_acquired: bool = False,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> str:
        """Search investors using structured filters.

        Returns a paginated list of investors matching all provided criteria.
        By default, investors with status "Acquired/Merged" are excluded.

        Args:
            sectors: Human-readable sector names (e.g. ["energy", "fintech"]).
                Resolved to DB sector codes via the sector map. Investors must
                overlap with at least one provided code.
            investor_types: Human-readable investor type names (e.g. ["vc",
                "family office", "pe"]). Resolved to primary_investor_type DB
                enum values. Investors matching any of the resolved types are
                returned.
            geography: Free-text geography filter. Applied as ilike on both
                ``hq_country_generated`` and ``hq_location``. Examples:
                "United States", "France", "Europe".
            check_size_min_dollars: Minimum check size in DOLLARS (e.g.
                5_000_000 for $5M). The tool divides by 1,000,000 before
                querying the DB (which stores values in MILLIONS).
            check_size_max_dollars: Maximum check size in DOLLARS. Applies an
                upper-bound filter on ``check_size_max``.
            keyword: Case-insensitive substring match on the investor name
                (``investors`` column). Example: "sequoia".
            include_acquired: If True, include investors with status
                "Acquired/Merged". Defaults to False.
            limit: Max records per page (1–200, default 50).
            offset: Zero-based record offset for pagination (default 0).

        Returns:
            JSON string with paginated envelope:
            ``{"data": [...], "meta": {"total", "page", "page_size",
            "has_more"}, "summary": "...", "next_actions": [...]}``.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        page = (offset // limit) + 1

        try:
            qb = QueryBuilder("investors").select(INVESTOR_SELECT_SUMMARY)

            # Sector filter — overlap on sectors_array
            if sectors:
                db_codes = resolve_sectors(sectors)
                if db_codes:
                    qb = qb.ov("sectors_array", db_codes)

            # Investor type filter — in() on primary_investor_type
            if investor_types:
                db_types = resolve_investor_types(investor_types)
                if db_types:
                    qb = qb.in_("primary_investor_type", db_types)

            # Geography filter — ilike on hq_country_generated
            # PostgREST supports only one ilike per column in a single request;
            # we use the more reliable generated country column as primary filter.
            if geography:
                qb = qb.ilike("hq_country_generated", f"*{geography}*")

            # Check size filters — values stored in MILLIONS, inputs in DOLLARS
            if check_size_min_dollars is not None:
                min_millions = check_size_min_dollars / 1_000_000
                qb = qb.gte("check_size_min", min_millions)
            if check_size_max_dollars is not None:
                max_millions = check_size_max_dollars / 1_000_000
                qb = qb.lte("check_size_max", max_millions)

            # Keyword search on investor name
            if keyword:
                qb = qb.ilike("investors", f"*{keyword}*")

            # Status filter — exclude Acquired/Merged by default
            if not include_acquired:
                qb = qb.neq("investor_status", _ACQUIRED_MERGED_STATUS)

            qb = qb.order("completeness_score", ascending=False).limit(limit).offset(offset)

            rows, total = await client.query(qb)

        except IOAuthError as exc:
            logger.warning("io_search_investors: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_search_investors: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed — check filter values", str(exc))
        except IOTransientError as exc:
            logger.error("io_search_investors: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception as exc:
            logger.exception("io_search_investors: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        investors = [format_investor_summary(r).model_dump() for r in rows]

        filter_parts: list[str] = []
        if sectors:
            filter_parts.append(f"sectors={sectors}")
        if investor_types:
            filter_parts.append(f"types={investor_types}")
        if geography:
            filter_parts.append(f"geography={geography!r}")
        if keyword:
            filter_parts.append(f"keyword={keyword!r}")
        filters_str = ", ".join(filter_parts) if filter_parts else "no filters"
        summary = f"Found {total or len(investors)} investors ({filters_str}), returning {len(investors)}"

        next_actions = [
            "Call io_get_investor(id=<id>) to fetch the full profile for a specific investor",
            "Call io_search_descriptions(keyword=<term>) to search by description text",
        ]

        return paginated_response(
            data=investors,
            total=total,
            page=page,
            page_size=limit,
            summary=summary,
            next_actions=next_actions,
        )

    # ------------------------------------------------------------------
    # Tool 6: io_search_descriptions
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_search_descriptions(
        keyword: str,
        investor_types: list[str] | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> str:
        """Keyword search on investor description text.

        297,000 investors have descriptions (97% coverage). Use this tool when
        the structured filters in io_search_investors are too broad — descriptions
        often capture niche strategies not reflected in sector tags.

        PERFORMANCE WARNING: This uses a case-insensitive substring scan
        (ilike) on a 234K-row text column. Expect 2–8 second response times.
        Prefer specific multi-word phrases over single common words to reduce
        scan volume.

        Args:
            keyword: Substring to search for in the ``description`` column
                (case-insensitive). Examples: "renewable energy storage",
                "franchise acquisitions", "medtech growth equity".
            investor_types: Optional list of human-readable investor type names
                to narrow results (e.g. ["pe", "family office"]). Applied as an
                additional filter alongside the description ilike.
            limit: Max records per page (1–200, default 50).
            offset: Zero-based record offset for pagination (default 0).

        Returns:
            JSON string with paginated investor list. Each record includes the
            ``description`` field so the caller can see why the investor matched.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        page = (offset // limit) + 1

        if not keyword or not keyword.strip():
            return error_response(
                "VALIDATION_ERROR",
                "keyword is required and must be a non-empty string",
            )

        # Select summary fields + description for context
        select = INVESTOR_SELECT_SUMMARY + ",description"

        try:
            qb = (
                QueryBuilder("investors")
                .select(select)
                .ilike("description", f"*{keyword.strip()}*")
                .neq("investor_status", _ACQUIRED_MERGED_STATUS)
            )

            if investor_types:
                db_types = resolve_investor_types(investor_types)
                if db_types:
                    qb = qb.in_("primary_investor_type", db_types)

            qb = qb.order("completeness_score", ascending=False).limit(limit).offset(offset)

            rows, total = await client.query(qb)

        except IOAuthError as exc:
            logger.warning("io_search_descriptions: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_search_descriptions: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_search_descriptions: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception as exc:
            logger.exception("io_search_descriptions: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        # Return summary + description snippet per investor
        results: list[dict[str, Any]] = []
        for r in rows:
            inv = format_investor_summary(r).model_dump()
            raw_desc: str | None = r.get("description")
            if raw_desc:
                # Truncate long descriptions — keep first 300 chars for context
                inv["description_snippet"] = raw_desc[:300] + ("…" if len(raw_desc) > 300 else "")
            else:
                inv["description_snippet"] = None
            results.append(inv)

        summary = (
            f"Found {total or len(results)} investors with description matching {keyword!r}, "
            f"returning {len(results)}. NOTE: ilike on text = sequential scan, may be slow."
        )
        next_actions = [
            "Call io_get_investor(id=<id>) for the full profile of a matched investor",
            "Narrow the search with io_search_investors() if too many results",
        ]

        return paginated_response(
            data=results,
            total=total,
            page=page,
            page_size=limit,
            summary=summary,
            next_actions=next_actions,
        )

    # ------------------------------------------------------------------
    # Tool 7: io_get_investor
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_get_investor(
        investor_id: int | None = None,
        name: str | None = None,
    ) -> str:
        """Fetch the full profile for a single investor by ID or name.

        Provide either ``investor_id`` (exact integer primary key, fastest) or
        ``name`` (case-insensitive substring — returns the best match by
        completeness_score when multiple investors share a similar name).

        At least one of ``investor_id`` or ``name`` must be provided.

        Args:
            investor_id: Integer primary key from the ``investors`` table.
            name: Investor/firm name. Matched with ilike — partial names work
                (e.g. "sequoia" matches "Sequoia Capital"). Returns the single
                best match ordered by completeness_score.

        Returns:
            JSON string with the full investor record (all 47 columns) wrapped
            in the standard tool response envelope.
        """
        if investor_id is None and (name is None or not name.strip()):
            return error_response(
                "VALIDATION_ERROR",
                "Provide investor_id (int) or name (str) — at least one is required",
            )

        try:
            if investor_id is not None:
                # ID lookup — use client cache (60s TTL)
                row = await client.get_investor_by_id(investor_id)
                if row is None:
                    return error_response(
                        "NOT_FOUND",
                        f"No investor found with id={investor_id}",
                    )
                detail = format_investor_detail(row)
                summary = f"Investor #{investor_id}: {detail.investors or 'Unknown'} ({detail.primary_investor_type})"
                return tool_response(
                    data=detail.model_dump(),
                    summary=summary,
                    next_actions=[
                        f"Call io_get_contacts(investor_ids=[{investor_id}]) to fetch contacts",
                        f"Call io_get_investor_team(investor_id={investor_id}) to see team structure",
                    ],
                )

            # Name lookup — ilike, best match by completeness_score
            qb = (
                QueryBuilder("investors")
                .select(INVESTOR_SELECT_DETAIL)
                .ilike("investors", f"*{name.strip()}*")  # type: ignore[union-attr]
                .order("completeness_score", ascending=False)
                .limit(1)
            )
            rows, _ = await client.query(qb, count=None)

            if not rows:
                return error_response(
                    "NOT_FOUND",
                    f"No investor found matching name {name!r}",
                )

            detail = format_investor_detail(rows[0])
            summary = f"Best match for {name!r}: {detail.investors} (id={detail.id}, type={detail.primary_investor_type})"
            return tool_response(
                data=detail.model_dump(),
                summary=summary,
                next_actions=[
                    f"Call io_get_contacts(investor_ids=[{detail.id}]) to fetch contacts",
                    "Call io_search_investors(keyword=...) if this is not the right firm",
                ],
            )

        except IOAuthError as exc:
            logger.warning("io_get_investor: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_get_investor: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_get_investor: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception as exc:
            logger.exception("io_get_investor: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

    # ------------------------------------------------------------------
    # Tool 8: io_investor_freshness
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_investor_freshness(
        sectors: list[str] | None = None,
        investor_types: list[str] | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> str:
        """Return recently updated investors ordered by last update timestamp.

        Useful for finding newly added investors or tracking data freshness.
        Ordered by ``updated_at`` descending (most recently updated first).

        Args:
            sectors: Optional list of human-readable sector names to narrow
                results (e.g. ["energy", "fintech"]).
            investor_types: Optional list of human-readable investor type names
                (e.g. ["vc", "family office"]).
            limit: Max records per page (1–200, default 50).
            offset: Zero-based record offset for pagination (default 0).

        Returns:
            JSON string with paginated investor list including the
            ``updated_at`` timestamp field.
        """
        limit = max(1, min(limit, _MAX_LIMIT))
        page = (offset // limit) + 1

        # Include updated_at in select so callers can see the freshness timestamp
        select = INVESTOR_SELECT_SUMMARY + ",updated_at"

        try:
            qb = (
                QueryBuilder("investors")
                .select(select)
                .not_is("updated_at", "null")
                .neq("investor_status", _ACQUIRED_MERGED_STATUS)
            )

            if sectors:
                db_codes = resolve_sectors(sectors)
                if db_codes:
                    qb = qb.ov("sectors_array", db_codes)

            if investor_types:
                db_types = resolve_investor_types(investor_types)
                if db_types:
                    qb = qb.in_("primary_investor_type", db_types)

            qb = qb.order("updated_at", ascending=False).limit(limit).offset(offset)

            rows, total = await client.query(qb)

        except IOAuthError as exc:
            logger.warning("io_investor_freshness: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_investor_freshness: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_investor_freshness: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception as exc:
            logger.exception("io_investor_freshness: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        investors: list[dict[str, Any]] = []
        for r in rows:
            inv = format_investor_summary(r).model_dump()
            inv["updated_at"] = r.get("updated_at")
            investors.append(inv)

        filter_parts: list[str] = []
        if sectors:
            filter_parts.append(f"sectors={sectors}")
        if investor_types:
            filter_parts.append(f"types={investor_types}")
        filters_str = ", ".join(filter_parts) if filter_parts else "all types"

        most_recent = investors[0]["updated_at"] if investors else "N/A"
        summary = (
            f"Returning {len(investors)} recently updated investors ({filters_str}). "
            f"Most recent: {most_recent}"
        )

        return paginated_response(
            data=investors,
            total=total,
            page=page,
            page_size=limit,
            summary=summary,
            next_actions=[
                "Call io_get_investor(investor_id=<id>) for full profile",
                "Call io_search_investors() for structured filtering",
            ],
        )
