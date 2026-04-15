"""Multi-Deal Intelligence tools — Tools 22–25.

Tools:
    io_find_cross_deal_investors  — investors that match 2+ deals from a set of criteria dicts
    io_deal_coverage_gaps         — blind spots: which investor_types / geographies / sectors
                                    return 0 results for a deal
    io_investor_funnel            — progressive filter counts to calibrate filter tightness
    io_deduplicate_across_deals   — persons appearing in 2+ per-deal contact lists

Registration:
    This module exports register(mcp, client) called by server.py auto-discovery.
    Do NOT edit src/server.py.

Design notes:
- All search_investors-style queries use count=estimated (not exact) for speed.
- check_size_min/max are stored as MILLIONS USD in the DB; inputs to funnel filters are
  expected in dollars and divided here.
- Cross-deal investor matching works by running one PostgREST query per deal and
  intersecting the returned investor ID sets — no RPC needed.
- io_deal_coverage_gaps iterates over candidate filter values and identifies which return
  empty result sets. This is a series of lightweight count-only queries.
- io_investor_funnel applies filters cumulatively: each step receives the constraint set
  from all previous steps plus the new constraint.
- io_deduplicate_across_deals works purely in Python — it compares the provided person_id
  lists and does NOT make any network calls.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOAuthError, IOClient, IOQueryError, IOTransientError, QueryBuilder
from src.entities import INVESTOR_SELECT_SUMMARY, format_investor_summary
from src.helpers import error_response, tool_response
from src.sectors import resolve_investor_types, resolve_sectors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_DEALS = 10
_MAX_INVESTOR_LIMIT = 5000  # per-deal investor pull cap

# Investor types probed by io_deal_coverage_gaps
_PROBE_INVESTOR_TYPES: list[str] = [
    "Venture Capital",
    "PE/Buyout",
    "Family Office",
    "Family Office - Single",
    "Angel (individual)",
    "Growth/Expansion",
    "Asset Manager",
    "Hedge Fund",
    "Corporate Venture Capital",
    "Wealth Management/RIA",
    "Investment Bank",
    "Holding Company",
    "Impact Investing",
    "Infrastructure",
]

# Geographies probed by io_deal_coverage_gaps
_PROBE_GEOGRAPHIES: list[str] = [
    "United States",
    "United Kingdom",
    "France",
    "Germany",
    "Canada",
    "Australia",
    "Netherlands",
    "Switzerland",
    "Singapore",
    "Japan",
]

# Sector codes probed by io_deal_coverage_gaps (top 20 from PLAN.md)
_PROBE_SECTORS: list[str] = [
    "financial services",
    "financial investments",
    "technology",
    "business services",
    "software",
    "real estate",
    "healthcare",
    "ai/ml",
    "industrials",
    "fintech",
    "energy",
    "edtech",
    "healthtech",
    "agritech",
    "biotech",
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _search_investors_for_criteria(
    client: IOClient,
    sectors: list[str] | None,
    investor_types: list[str] | None,
    description_keywords: list[str] | None,
    check_size_min_dollars: float | None = None,
    check_size_max_dollars: float | None = None,
    geography: str | None = None,
) -> list[dict]:
    """Run a PostgREST query for one deal's criteria, returning investor rows.

    Returns investor records (id + summary fields). All filters are additive
    (AND). At least one filter must be provided; if all are None/empty the
    caller should skip.
    """
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
    )

    if sectors:
        db_codes = resolve_sectors(sectors)
        if db_codes:
            qb = qb.ov("sectors_array", db_codes)

    if investor_types:
        db_types = resolve_investor_types(investor_types)
        if db_types:
            qb = qb.in_("primary_investor_type", db_types)

    if description_keywords:
        # Apply one ilike per keyword — PostgREST ANDs multiple column filters.
        # For multiple keywords we pick the first for broadness; callers that
        # need all keywords should pass a combined phrase instead.
        for kw in description_keywords:
            qb = qb.ilike("description", f"*{kw}*")

    if check_size_min_dollars is not None:
        qb = qb.gte("check_size_min", check_size_min_dollars / 1_000_000)
    if check_size_max_dollars is not None:
        qb = qb.lte("check_size_max", check_size_max_dollars / 1_000_000)

    if geography:
        qb = qb.ilike("hq_country_generated", f"*{geography}*")

    qb = qb.limit(_MAX_INVESTOR_LIMIT)
    rows, _ = await client.query(qb, count=None)
    return rows


async def _count_investors_for_filters(
    client: IOClient,
    sectors: list[str] | None,
    investor_types: list[str] | None,
    description_keywords: list[str] | None,
    check_size_min_dollars: float | None,
    check_size_max_dollars: float | None,
    geography: str | None,
    extra_investor_type: str | None = None,
    extra_geography: str | None = None,
    extra_sector: str | None = None,
) -> int | None:
    """Return the estimated count for a query (used in gap probing and funnel).

    Returns None when count is not available from the Content-Range header.
    """
    qb = (
        QueryBuilder("investors")
        .select("id")
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
    )

    if sectors:
        db_codes = resolve_sectors(sectors)
        if db_codes:
            qb = qb.ov("sectors_array", db_codes)

    if extra_sector:
        extra_codes = resolve_sectors([extra_sector])
        if extra_codes:
            qb = qb.ov("sectors_array", extra_codes)

    if investor_types:
        db_types = resolve_investor_types(investor_types)
        if db_types:
            qb = qb.in_("primary_investor_type", db_types)

    if extra_investor_type:
        qb = qb.eq("primary_investor_type", extra_investor_type)

    if description_keywords:
        for kw in description_keywords:
            qb = qb.ilike("description", f"*{kw}*")

    if check_size_min_dollars is not None:
        qb = qb.gte("check_size_min", check_size_min_dollars / 1_000_000)
    if check_size_max_dollars is not None:
        qb = qb.lte("check_size_max", check_size_max_dollars / 1_000_000)

    if geography:
        qb = qb.ilike("hq_country_generated", f"*{geography}*")

    if extra_geography:
        qb = qb.ilike("hq_country_generated", f"*{extra_geography}*")

    qb = qb.limit(1)
    _, total = await client.query(qb, count="estimated")
    return total


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all 4 Multi-Deal Intelligence tools on the FastMCP instance."""

    # ------------------------------------------------------------------
    # Tool 22: io_find_cross_deal_investors
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_find_cross_deal_investors(
        deals: list[dict[str, Any]],
        min_deal_matches: int = 2,
    ) -> str:
        """Find investors that match criteria from 2 or more deals simultaneously.

        Each element of ``deals`` is a criteria dict describing one deal. The
        tool runs one PostgREST investor query per deal, then returns the
        investors whose IDs appear in at least ``min_deal_matches`` result sets.

        This is useful for identifying investors to prioritise when managing a
        multi-deal pipeline — an investor appearing in 3 deal searches is a
        higher-priority target than one that appears in only 1.

        Args:
            deals: List of 2–10 criteria dicts. Each dict may contain any
                combination of:
                - ``sectors`` (list[str]): Human-readable sector names.
                - ``investor_types`` (list[str]): Human-readable investor type
                    names (e.g. ["pe", "family office"]).
                - ``description_keywords`` (list[str]): Keywords searched via
                    ilike on the investor description field.
                - ``check_size_min_dollars`` (float): Minimum check size in USD.
                - ``check_size_max_dollars`` (float): Maximum check size in USD.
                - ``geography`` (str): Geography filter (ilike on hq_country).
                - ``label`` (str): Optional human-readable label for this deal
                    (e.g. "Doosan PE buyout"). Returned in the output for
                    traceability. If omitted, defaults to "deal_N".
            min_deal_matches: Minimum number of deals an investor must appear
                in to be included in the results. Must be >= 2 (default 2).

        Returns:
            JSON string with ``data`` list of matched investors. Each entry
            includes the investor summary fields plus:
            - ``deal_match_count`` (int): Number of deals this investor matched.
            - ``matched_deals`` (list[str]): Labels of the matched deals.
        """
        if not deals or len(deals) < 2:
            return error_response(
                "VALIDATION_ERROR",
                "deals must contain at least 2 criteria dicts",
            )
        if len(deals) > _MAX_DEALS:
            return error_response(
                "VALIDATION_ERROR",
                f"deals must contain at most {_MAX_DEALS} criteria dicts",
            )
        min_deal_matches = max(2, min(min_deal_matches, len(deals)))

        # Assign labels
        labelled: list[tuple[str, dict]] = []
        for i, deal in enumerate(deals):
            label = deal.get("label") or f"deal_{i + 1}"
            labelled.append((label, deal))

        try:
            # id → list of matched deal labels
            match_map: dict[int, list[str]] = defaultdict(list)
            # id → investor row (store first occurrence)
            investor_rows: dict[int, dict] = {}

            for label, criteria in labelled:
                rows = await _search_investors_for_criteria(
                    client,
                    sectors=criteria.get("sectors"),
                    investor_types=criteria.get("investor_types"),
                    description_keywords=criteria.get("description_keywords"),
                    check_size_min_dollars=criteria.get("check_size_min_dollars"),
                    check_size_max_dollars=criteria.get("check_size_max_dollars"),
                    geography=criteria.get("geography"),
                )
                for row in rows:
                    inv_id = row.get("id")
                    if inv_id is None:
                        continue
                    match_map[inv_id].append(label)
                    if inv_id not in investor_rows:
                        investor_rows[inv_id] = row

        except IOAuthError as exc:
            logger.warning("io_find_cross_deal_investors: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_find_cross_deal_investors: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_find_cross_deal_investors: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception:
            logger.exception("io_find_cross_deal_investors: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        # Filter to investors matching min_deal_matches
        results: list[dict] = []
        for inv_id, matched_labels in match_map.items():
            if len(matched_labels) < min_deal_matches:
                continue
            row = investor_rows[inv_id]
            inv = format_investor_summary(row).model_dump()
            inv["deal_match_count"] = len(matched_labels)
            inv["matched_deals"] = sorted(set(matched_labels))
            results.append(inv)

        # Sort descending by deal_match_count, then by completeness_score
        results.sort(key=lambda r: (-r["deal_match_count"], -(r.get("completeness_score") or 0)))

        deal_labels = [label for label, _ in labelled]
        summary = (
            f"Found {len(results)} investors matching {min_deal_matches}+ of "
            f"{len(deals)} deals: {deal_labels}. "
            f"Top investor matches {results[0]['deal_match_count'] if results else 0} deals."
        )

        return tool_response(
            data=results,
            summary=summary,
            next_actions=[
                "Call io_get_contacts(investor_ids=[<id>]) to fetch contacts for a cross-deal investor",
                "Call io_get_investor(investor_id=<id>) for full profile",
            ],
        )

    # ------------------------------------------------------------------
    # Tool 23: io_deal_coverage_gaps
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_deal_coverage_gaps(
        sectors: list[str] | None = None,
        investor_types: list[str] | None = None,
        description_keywords: list[str] | None = None,
        check_size_min_dollars: float | None = None,
        check_size_max_dollars: float | None = None,
        geography: str | None = None,
    ) -> str:
        """Identify blind spots in a deal's investor coverage.

        For the given deal criteria, probes each candidate investor type,
        geography, and sector individually to find which ones return 0 results.
        Returns the gaps so the deal team knows which market segments are not
        covered.

        The base criteria (sectors, investor_types, description_keywords,
        check_size, geography) are used as the starting filter set. Each probe
        adds one additional constraint on top of the base and checks whether
        any investors survive.

        Args:
            sectors: Base sector filter for the deal (human-readable names).
            investor_types: Base investor type filter for the deal.
            description_keywords: Base description ilike keywords.
            check_size_min_dollars: Minimum check size in USD.
            check_size_max_dollars: Maximum check size in USD.
            geography: Base geography filter (ilike on hq_country).

        Returns:
            JSON string with ``data`` containing three lists:
            - ``zero_investor_types``: Investor type values with 0 results.
            - ``zero_geographies``: Geography names with 0 results.
            - ``zero_sectors``: Sector codes with 0 results.
            Plus ``covered_investor_types``, ``covered_geographies``,
            ``covered_sectors`` for the non-zero cases.
        """
        try:
            # Probe investor types
            zero_types: list[str] = []
            covered_types: list[dict] = []
            for inv_type in _PROBE_INVESTOR_TYPES:
                count = await _count_investors_for_filters(
                    client,
                    sectors=sectors,
                    investor_types=investor_types,
                    description_keywords=description_keywords,
                    check_size_min_dollars=check_size_min_dollars,
                    check_size_max_dollars=check_size_max_dollars,
                    geography=geography,
                    extra_investor_type=inv_type,
                )
                if count is None or count == 0:
                    zero_types.append(inv_type)
                else:
                    covered_types.append({"investor_type": inv_type, "count": count})

            # Probe geographies
            zero_geos: list[str] = []
            covered_geos: list[dict] = []
            for geo in _PROBE_GEOGRAPHIES:
                count = await _count_investors_for_filters(
                    client,
                    sectors=sectors,
                    investor_types=investor_types,
                    description_keywords=description_keywords,
                    check_size_min_dollars=check_size_min_dollars,
                    check_size_max_dollars=check_size_max_dollars,
                    geography=geography,
                    extra_geography=geo,
                )
                if count is None or count == 0:
                    zero_geos.append(geo)
                else:
                    covered_geos.append({"geography": geo, "count": count})

            # Probe sectors
            zero_sectors: list[str] = []
            covered_sectors: list[dict] = []
            for sector in _PROBE_SECTORS:
                count = await _count_investors_for_filters(
                    client,
                    sectors=sectors,
                    investor_types=investor_types,
                    description_keywords=description_keywords,
                    check_size_min_dollars=check_size_min_dollars,
                    check_size_max_dollars=check_size_max_dollars,
                    geography=geography,
                    extra_sector=sector,
                )
                if count is None or count == 0:
                    zero_sectors.append(sector)
                else:
                    covered_sectors.append({"sector": sector, "count": count})

        except IOAuthError as exc:
            logger.warning("io_deal_coverage_gaps: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_deal_coverage_gaps: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_deal_coverage_gaps: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception:
            logger.exception("io_deal_coverage_gaps: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        data: dict[str, Any] = {
            "zero_investor_types": zero_types,
            "covered_investor_types": covered_types,
            "zero_geographies": zero_geos,
            "covered_geographies": covered_geos,
            "zero_sectors": zero_sectors,
            "covered_sectors": covered_sectors,
        }

        summary = (
            f"Coverage gaps: {len(zero_types)} investor types, "
            f"{len(zero_geos)} geographies, {len(zero_sectors)} sectors return 0 results. "
            f"Covered: {len(covered_types)} types, {len(covered_geos)} geos, "
            f"{len(covered_sectors)} sectors."
        )

        return tool_response(
            data=data,
            summary=summary,
            next_actions=[
                "Review zero_investor_types to see which investor classes have no coverage",
                "Review zero_geographies to identify untapped geographic markets",
                "Call io_search_descriptions(keyword=<kw>) to find investors via description text",
            ],
        )

    # ------------------------------------------------------------------
    # Tool 24: io_investor_funnel
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_investor_funnel(
        filters: list[dict[str, Any]],
    ) -> str:
        """Apply progressive filters cumulatively and return the count at each step.

        Helps calibrate how tight a filter chain is. Each step in ``filters``
        adds one or more constraints on top of all previous steps. The tool
        returns the estimated investor count after applying the cumulative
        constraint set at each step.

        This reveals where in the funnel the most narrowing happens — useful
        for diagnosing over-filtering or under-filtering before running a full
        match_deal query.

        Args:
            filters: Ordered list of filter dicts. Each dict may contain any
                combination of:
                - ``sectors`` (list[str]): Sector names (resolved to DB codes).
                - ``investor_types`` (list[str]): Investor type names.
                - ``description_keywords`` (list[str]): ilike keywords on
                    investor description.
                - ``check_size_min_dollars`` (float): Minimum check size in USD.
                - ``check_size_max_dollars`` (float): Maximum check size in USD.
                - ``geography`` (str): Geography ilike filter.
                - ``label`` (str): Optional step label (e.g. "add PE/Buyout
                    type"). Defaults to "step_N".

                Constraints accumulate: step 2 adds to step 1's constraints,
                step 3 adds to step 2's, etc. Duplicate keys in later steps
                override earlier values for that key.

        Returns:
            JSON string with ``data`` list of funnel steps. Each step contains:
            - ``step`` (int): 1-indexed step number.
            - ``label`` (str): Step label.
            - ``count`` (int | null): Estimated investor count after this step.
            - ``cumulative_filters`` (dict): All active filters at this step.
            Plus ``total_narrowing``: the absolute drop from step 1 to last step.
        """
        if not filters:
            return error_response(
                "VALIDATION_ERROR",
                "filters must be a non-empty list of filter dicts",
            )

        try:
            steps: list[dict[str, Any]] = []
            cumulative: dict[str, Any] = {}

            for i, step_filter in enumerate(filters):
                label = step_filter.get("label") or f"step_{i + 1}"

                # Merge step filter into cumulative state
                # For list fields, extend rather than replace so sector/type
                # filters accumulate across steps.
                for key, value in step_filter.items():
                    if key == "label":
                        continue
                    if isinstance(value, list) and isinstance(cumulative.get(key), list):
                        # Extend list fields
                        existing = list(cumulative[key])
                        for v in value:
                            if v not in existing:
                                existing.append(v)
                        cumulative[key] = existing
                    else:
                        cumulative[key] = value

                count = await _count_investors_for_filters(
                    client,
                    sectors=cumulative.get("sectors"),
                    investor_types=cumulative.get("investor_types"),
                    description_keywords=cumulative.get("description_keywords"),
                    check_size_min_dollars=cumulative.get("check_size_min_dollars"),
                    check_size_max_dollars=cumulative.get("check_size_max_dollars"),
                    geography=cumulative.get("geography"),
                )

                steps.append(
                    {
                        "step": i + 1,
                        "label": label,
                        "count": count,
                        "cumulative_filters": dict(cumulative),
                    }
                )

        except IOAuthError as exc:
            logger.warning("io_investor_funnel: auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication failed — check IO_EMAIL/IO_PASSWORD")
        except IOQueryError as exc:
            logger.error("io_investor_funnel: query error: %s", exc)
            return error_response("QUERY_ERROR", "Database query failed", str(exc))
        except IOTransientError as exc:
            logger.error("io_investor_funnel: transient error: %s", exc)
            return error_response("SERVER_ERROR", "Transient database error — retry the request")
        except Exception:
            logger.exception("io_investor_funnel: unexpected error")
            return error_response("SERVER_ERROR", "Unexpected server error")

        first_count = steps[0]["count"] if steps else None
        last_count = steps[-1]["count"] if steps else None

        if first_count is not None and last_count is not None:
            total_narrowing = first_count - last_count
        else:
            total_narrowing = None

        summary = (
            f"{len(steps)}-step funnel: "
            f"{first_count or '?'} investors at step 1 → "
            f"{last_count or '?'} at final step "
            f"(narrowing: {total_narrowing or '?'})."
        )

        return tool_response(
            data={"steps": steps, "total_narrowing": total_narrowing},
            summary=summary,
            next_actions=[
                "If the funnel drops to 0 early, relax filters at that step",
                "Call io_deal_coverage_gaps() to identify which types/geos have no coverage",
                "Call io_find_cross_deal_investors() to find investors matching multiple deals",
            ],
        )

    # ------------------------------------------------------------------
    # Tool 25: io_deduplicate_across_deals
    # ------------------------------------------------------------------

    @mcp.tool()
    async def io_deduplicate_across_deals(
        deal_person_lists: list[dict[str, Any]],
    ) -> str:
        """Find persons appearing in multiple per-deal contact lists.

        Given a set of person_id lists (one per deal), identifies persons that
        appear in 2 or more lists. This is a pure Python operation — no network
        calls are made.

        Use this after running io_get_contacts or match_deal for each deal in
        a multi-deal pipeline to avoid contacting the same person multiple times
        with different deal pitches.

        Args:
            deal_person_lists: List of dicts, each describing one deal's
                contact list. Each dict must contain:
                - ``person_ids`` (list[int]): Person IDs from the deal's
                    contact retrieval. Must be non-empty.
                - ``label`` (str): Human-readable label for this deal
                    (e.g. "Doosan PE buyout"). Required for traceability.

                At least 2 dicts must be provided.

        Returns:
            JSON string with ``data`` containing:
            - ``duplicates`` (list): Persons appearing in 2+ deal lists. Each
                entry has:
                - ``person_id`` (int): The duplicated person ID.
                - ``deal_count`` (int): Number of deals this person appears in.
                - ``deal_labels`` (list[str]): Which deals contain this person.
            - ``total_unique_persons`` (int): Distinct person IDs across all
                lists.
            - ``total_input_persons`` (int): Total IDs including duplicates.
            - ``deduplication_rate`` (float | null): Fraction of input IDs that
                are duplicates (0.0–1.0).
        """
        if not deal_person_lists or len(deal_person_lists) < 2:
            return error_response(
                "VALIDATION_ERROR",
                "deal_person_lists must contain at least 2 deal dicts",
            )

        # Validate structure
        for i, entry in enumerate(deal_person_lists):
            if not isinstance(entry.get("person_ids"), list):
                return error_response(
                    "VALIDATION_ERROR",
                    f"deal_person_lists[{i}].person_ids must be a list of integers",
                )
            if not entry.get("label"):
                return error_response(
                    "VALIDATION_ERROR",
                    f"deal_person_lists[{i}].label is required",
                )

        # Build person_id → list of deal labels
        person_deal_map: dict[int, list[str]] = defaultdict(list)
        total_input = 0

        for entry in deal_person_lists:
            label: str = entry["label"]
            person_ids: list[int] = entry["person_ids"]
            total_input += len(person_ids)
            for pid in person_ids:
                if isinstance(pid, (int, float)) and not isinstance(pid, bool):
                    person_deal_map[int(pid)].append(label)

        total_unique = len(person_deal_map)

        # Find duplicates (appear in 2+ deals)
        duplicates: list[dict[str, Any]] = []
        for pid, labels in person_deal_map.items():
            if len(labels) >= 2:
                duplicates.append(
                    {
                        "person_id": pid,
                        "deal_count": len(labels),
                        "deal_labels": sorted(set(labels)),
                    }
                )

        # Sort by deal_count descending
        duplicates.sort(key=lambda d: -d["deal_count"])

        dedup_rate: float | None = None
        if total_input > 0:
            dedup_rate = round(len(duplicates) / total_input, 4)

        data: dict[str, Any] = {
            "duplicates": duplicates,
            "total_unique_persons": total_unique,
            "total_input_persons": total_input,
            "deduplication_rate": dedup_rate,
        }

        deal_labels = [e["label"] for e in deal_person_lists]
        summary = (
            f"Found {len(duplicates)} persons appearing in 2+ of {len(deal_person_lists)} deals "
            f"({deal_labels}). {total_unique} unique persons across {total_input} total entries."
        )

        return tool_response(
            data=data,
            summary=summary,
            next_actions=[
                "Review duplicates to decide contact sequencing (which deal to pitch first)",
                "Call io_get_contacts(investor_ids=[...]) to fetch fresh contact details",
            ],
        )
