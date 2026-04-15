"""Deal matching tools — the highest-value tools in the MCP server.

Tools:
    match_deal          — Hero tool. Two-phase pipeline: broad investor pull + tight
                          contact-level 6-gate scoring. Replaces segment_v2.py logic.
    match_deal_stage    — Match investors by preferred_investment_types (Seed/Buyout/Growth).
    match_preferences   — Match investors by stated preferences only (industry, geography,
                          check size). No scoring.
    find_similar_investors — Embedding cosine similarity via ai_search_with_ideal_investor RPC.

Registration:
    This module exports register(mcp, client) called by server.py auto-discovery.
    Do NOT import this module directly.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder
from src.entities import (
    INVESTOR_SELECT_SUMMARY,
    format_investor_summary,
)
from src.helpers import error_response, tool_response
from src.scoring import passes_deal_relevance, score_contact
from src.sectors import (
    resolve_investment_types,
    resolve_investor_types,
    resolve_sectors,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum investor IDs to batch per PostgREST query (URL length safety)
_INVESTOR_BATCH_SIZE = 100
# Maximum persons to fetch per query
_PERSONS_BATCH_LIMIT = 5000
# Select string for persons in match_deal (need enough fields for scoring)
_PERSON_MATCH_SELECT = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor,"
    "email_status,email_score,good_email"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_investors_by_sector(
    client: IOClient,
    sector_codes: list[str],
    investor_type_values: list[str] | None,
    deal_size_m: float | None,
) -> list[dict]:
    """Query investors by sector overlap + investor type + check size."""
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ov("sectors_array", sector_codes)
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
    )
    if investor_type_values:
        qb.in_("primary_investor_type", investor_type_values)
    if deal_size_m is not None:
        qb.lte("check_size_min", deal_size_m)
        qb.gte("check_size_max", deal_size_m)
    qb.limit(5000)

    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_description(
    client: IOClient,
    keyword: str,
) -> list[dict]:
    """Query investors whose description ilike matches a keyword."""
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("description", f"*{keyword}*")
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
        .limit(2000)
    )
    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_name(
    client: IOClient,
    name: str,
) -> list[dict]:
    """Query investors whose name ilike matches.

    Short names (<4 chars) use word-start matching to avoid false positives
    (e.g., 'nea' matching 'lineage', 'cornea', etc.).
    """
    if len(name.strip()) < 4:
        pattern = f"{name}*"  # word-start only for short names
    else:
        pattern = f"*{name}*"  # substring for longer names
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("investors", pattern)
        .limit(500)
    )
    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_stage(
    client: IOClient,
    stage_values: list[str],
) -> list[dict]:
    """Query investors by preferred_investment_types ilike (text field)."""
    all_rows: list[dict] = []
    for stage in stage_values:
        qb = (
            QueryBuilder("investors")
            .select(INVESTOR_SELECT_SUMMARY)
            .ilike("preferred_investment_types", f"*{stage}*")
            .gt("contact_count", 0)
            .neq("investor_status", "Acquired/Merged")
            .limit(2000)
        )
        rows, _ = await client.query(qb, count=None)
        all_rows.extend(rows)
    return all_rows


async def _fetch_investors_by_type(
    client: IOClient,
    investor_type_values: list[str],
    deal_size_m: float | None,
) -> list[dict]:
    """Query investors by type only (no sector filter)."""
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .in_("primary_investor_type", investor_type_values)
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
    )
    if deal_size_m is not None:
        qb.lte("check_size_min", deal_size_m)
        qb.gte("check_size_max", deal_size_m)
    qb.limit(5000)

    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_persons_for_investors(
    client: IOClient,
    investor_ids: list[int],
) -> list[dict]:
    """Batch-fetch persons for a set of investor IDs.

    Chunks into batches of _INVESTOR_BATCH_SIZE to stay within URL length limits.
    """
    all_persons: list[dict] = []
    for i in range(0, len(investor_ids), _INVESTOR_BATCH_SIZE):
        batch = investor_ids[i : i + _INVESTOR_BATCH_SIZE]
        qb = (
            QueryBuilder("persons")
            .select(_PERSON_MATCH_SELECT)
            .in_("investor", batch)
            .limit(_PERSONS_BATCH_LIMIT)
        )
        rows, _ = await client.query(qb, count=None)
        all_persons.extend(rows)
    return all_persons


def _dedupe_investors(all_rows: list[dict]) -> dict[int, dict]:
    """Deduplicate investor rows by ID, returning a dict keyed by investor ID."""
    seen: dict[int, dict] = {}
    for row in all_rows:
        inv_id = row.get("id")
        if inv_id is not None and inv_id not in seen:
            seen[inv_id] = row
    return seen


def _score_and_gate_contacts(
    persons: list[dict],
    investor_map: dict[int, dict],
    role_keywords: list[str],
    firm_keywords: list[str],
    named_firms: list[str],
    expanded: bool,
    min_score: int,
) -> list[dict]:
    """Score each person and apply the 6-gate pipeline.

    Returns a list of dicts with person data + scoring metadata.
    """
    results: list[dict] = []
    for person in persons:
        role = person.get("role") or ""
        investor_id = person.get("investor")
        investor = investor_map.get(investor_id, {}) if investor_id else {}
        investor_name = investor.get("investors", "")
        company_name = person.get("company_name", "") or ""
        sectors_arr = investor.get("sectors_array") or []
        sectors_str = " ".join(sectors_arr) if isinstance(sectors_arr, list) else str(sectors_arr)

        # Compute score
        deal_kws = role_keywords  # role_keywords serve as deal keywords for scoring
        contact_score = score_contact(role, deal_kws)

        # 6-gate pipeline
        passes, reason = passes_deal_relevance(
            role=role,
            company_name=company_name,
            investor_name=investor_name,
            sectors_str=sectors_str,
            score=contact_score,
            role_keywords=role_keywords,
            firm_keywords=firm_keywords,
            named_firms=named_firms,
            expanded=expanded,
            min_score=min_score,
        )

        if not passes:
            continue

        results.append({
            **person,
            "_score": contact_score,
            "_match_path": reason,
            "_investor_name": investor_name,
            "_investor_type": investor.get("primary_investor_type"),
            "_sectors": sectors_str,
            "_check_size_min": investor.get("check_size_min"),
            "_check_size_max": investor.get("check_size_max"),
        })

    return results


def _cap_per_firm(
    results: list[dict],
    max_per_firm: int,
) -> list[dict]:
    """Group by investor, sort by score desc, take top N per firm."""
    by_firm: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        firm_key = r.get("_investor_name") or r.get("company_name") or "Unknown"
        by_firm[firm_key].append(r)

    capped: list[dict] = []
    for contacts in by_firm.values():
        contacts.sort(key=lambda x: x.get("_score", 0), reverse=True)
        capped.extend(contacts[:max_per_firm])

    # Final sort by score descending
    capped.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return capped


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, client: IOClient) -> None:
    """Register all deal matching tools on the MCP server."""

    # ------------------------------------------------------------------
    # Tool 1: match_deal (HERO TOOL)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def match_deal(
        role_keywords: list[str],
        firm_keywords: list[str],
        named_firms: list[str],
        sectors: list[str] | None = None,
        investor_types: list[str] | None = None,
        deal_size: float | None = None,
        geography: str | None = None,
        description_keywords: list[str] | None = None,
        deal_stage: str | None = None,
        expanded: bool = False,
        max_per_firm: int = 5,
        max_results: int = 1000,
        min_score: int = 20,
    ) -> str:
        """Find investors and scored contacts matching a specific deal.

        Two-phase pipeline proven on 206K contacts across 5 real deals:
        1. Broad investor pull via multiple PostgREST queries (sector overlap,
           investor type, description keywords, named firms, deal stage)
        2. Tight contact-level gating via 6-gate scoring pipeline

        Args:
            role_keywords: Keywords matched against contact role/title (e.g.,
                ["energy", "infrastructure", "buyout"]). Strongest signal.
            firm_keywords: Keywords matched against investor/company name (e.g.,
                ["energy", "renewable", "cleantech"]).
            named_firms: Exact firm names that always pass gating (e.g.,
                ["ares", "brookfield", "kkr"]). Case-insensitive substring match.
            sectors: Sector codes for broad pre-filter (e.g., ["energy",
                "infrastructure"]). Resolved via sector map. Pass null for
                family office or angel searches where sector data is sparse.
            investor_types: Investor type filter (e.g., ["pe", "vc",
                "family office"]). Resolved to DB enum values.
            deal_size: Deal size in USD. Divided by 1M internally for DB query
                (e.g., pass 70000000 for a $70M deal). Filters on check_size range.
            geography: Geographic preference filter (e.g., "United States").
            description_keywords: Keywords to search in investor descriptions
                (97% coverage). Each keyword triggers a separate ilike query.
            deal_stage: Deal stage for preferred_investment_types matching
                (e.g., "seed", "buyout", "growth"). Resolved via investment type map.
            expanded: When True, loosens Gate 6 to admit any senior investment
                professional (paths F1/F2). Use for niche deals.
            max_per_firm: Maximum contacts to return per investor firm (default 5).
            max_results: Maximum total contacts to return (default 1000).
            min_score: Minimum score threshold for Gate 1 (default 20).

        Returns:
            Ranked contacts with investor metadata, match path, and score.
            Contacts are included regardless of email availability.
        """
        try:
            # Convert deal_size from dollars to millions
            deal_size_m = deal_size / 1_000_000 if deal_size is not None else None

            # Resolve human-readable inputs to DB values
            sector_codes = resolve_sectors(sectors) if sectors else []
            investor_type_values = resolve_investor_types(investor_types) if investor_types else []
            stage_values = resolve_investment_types([deal_stage]) if deal_stage else []

            # ── Phase 1: Broad investor pull ──────────────────────────────
            all_investor_rows: list[dict] = []

            # 1a. Sector overlap query (skip if no sectors)
            if sector_codes:
                rows = await _fetch_investors_by_sector(
                    client, sector_codes, investor_type_values or None, deal_size_m,
                )
                all_investor_rows.extend(rows)

            # 1b. Description keyword queries
            if description_keywords:
                for kw in description_keywords:
                    rows = await _fetch_investors_by_description(client, kw)
                    all_investor_rows.extend(rows)

            # 1c. Named firm queries
            for name in named_firms:
                rows = await _fetch_investors_by_name(client, name)
                all_investor_rows.extend(rows)

            # 1d. Deal stage query
            if stage_values:
                rows = await _fetch_investors_by_stage(client, stage_values)
                all_investor_rows.extend(rows)

            # 1e. Type-only query when no sectors provided (family offices, angels)
            if not sector_codes and investor_type_values:
                rows = await _fetch_investors_by_type(
                    client, investor_type_values, deal_size_m,
                )
                all_investor_rows.extend(rows)

            # Deduplicate investors
            investor_map = _dedupe_investors(all_investor_rows)

            if not investor_map:
                return tool_response(
                    data={"contacts": [], "investors_scanned": 0, "contacts_scored": 0},
                    summary="No investors matched the search criteria.",
                    next_actions=[
                        "Try broader sectors or remove sector filter entirely",
                        "Add more named_firms or description_keywords",
                    ],
                )

            # ── Phase 2: Fetch persons + score/gate ───────────────────────
            investor_ids = list(investor_map.keys())
            all_persons = await _fetch_persons_for_investors(client, investor_ids)

            # Score and gate
            scored = _score_and_gate_contacts(
                persons=all_persons,
                investor_map=investor_map,
                role_keywords=role_keywords,
                firm_keywords=firm_keywords,
                named_firms=named_firms,
                expanded=expanded,
                min_score=min_score,
            )

            # Cap per firm
            capped = _cap_per_firm(scored, max_per_firm)

            # Global person-id dedup (same person can appear under multiple investors)
            seen_person_ids: set[int] = set()
            deduped: list[dict] = []
            for c in capped:
                pid = c.get("id")
                if pid and pid in seen_person_ids:
                    continue
                if pid:
                    seen_person_ids.add(pid)
                deduped.append(c)

            # Truncate to max_results
            final = deduped[:max_results]

            # Stats
            unique_firms = len(set(c.get("_investor_name", "") for c in final))
            with_email = sum(1 for c in final if c.get("email"))
            with_phone = sum(1 for c in final if c.get("phone"))
            with_linkedin = sum(1 for c in final if c.get("linkedin_profile_url"))

            summary_text = (
                f"{len(final)} contacts across {unique_firms} firms "
                f"(from {len(investor_map)} investors, {len(all_persons)} persons scored). "
                f"Email: {with_email}, Phone: {with_phone}, LinkedIn: {with_linkedin}."
            )

            return tool_response(
                data={
                    "contacts": final,
                    "stats": {
                        "total_contacts": len(final),
                        "unique_firms": unique_firms,
                        "investors_scanned": len(investor_map),
                        "persons_scored": len(all_persons),
                        "with_email": with_email,
                        "with_phone": with_phone,
                        "with_linkedin": with_linkedin,
                        "expanded_mode": expanded,
                        "min_score": min_score,
                        "max_per_firm": max_per_firm,
                    },
                },
                summary=summary_text,
                next_actions=[
                    a for a in [
                        "Use io_outreach_ready_contacts to filter to email-deliverable contacts only",
                        "Use io_find_similar_investors on top-matching investors to expand the list",
                        "Call with expanded=True to loosen matching" if not expanded else None,
                    ] if a is not None
                ],
            )
        except IOAuthError as exc:
            logger.warning("match_deal auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication expired — re-login required", str(exc))
        except IOQueryError as exc:
            logger.warning("match_deal query error: %s", exc)
            return error_response("QUERY_ERROR", str(exc))
        except IOTransientError as exc:
            logger.error("match_deal transient error: %s", exc)
            return error_response("SERVER_ERROR", "Temporary database error. Retry in a few seconds.")
        except Exception:
            logger.exception("unexpected error in match_deal")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ------------------------------------------------------------------
    # Tool 2: match_deal_stage
    # ------------------------------------------------------------------

    @mcp.tool()
    async def match_deal_stage(
        stage: str,
        investor_types: list[str] | None = None,
        geography: str | None = None,
        limit: int = 200,
    ) -> str:
        """Find investors by preferred investment stage (Seed, Buyout, Growth, etc.).

        Uses the preferred_investment_types field (84% coverage, 198K investors).
        This is a TEXT field searched via ilike, not an array.

        Args:
            stage: Investment stage to search for. Accepts human-readable names
                like "seed", "buyout", "growth", "series a", "m&a", "venture".
                Resolved to actual DB values via investment type map.
            investor_types: Optional investor type filter (e.g., ["vc", "pe"]).
            geography: Optional geography filter on preferred_geography.
            limit: Maximum results to return (default 200, max 5000).

        Returns:
            List of investor summaries matching the stage preference.
        """
        try:
            stage_values = resolve_investment_types([stage])
            if not stage_values:
                return error_response(
                    "VALIDATION_ERROR",
                    f"Unknown deal stage: '{stage}'. Try: seed, series a, growth, buyout, m&a, venture, debt.",
                )

            investor_type_values = resolve_investor_types(investor_types) if investor_types else []

            all_rows: list[dict] = []
            for sv in stage_values:
                qb = (
                    QueryBuilder("investors")
                    .select(INVESTOR_SELECT_SUMMARY)
                    .ilike("preferred_investment_types", f"*{sv}*")
                    .gt("contact_count", 0)
                    .neq("investor_status", "Acquired/Merged")
                )
                if investor_type_values:
                    qb.in_("primary_investor_type", investor_type_values)
                if geography:
                    qb.ilike("preferred_geography", f"*{geography}*")
                qb.limit(min(limit, 5000))

                rows, _ = await client.query(qb, count="estimated")
                all_rows.extend(rows)

            # Deduplicate
            deduped = _dedupe_investors(all_rows)
            investors = [format_investor_summary(v).model_dump() for v in deduped.values()]
            investors = investors[:limit]

            return tool_response(
                data=investors,
                summary=f"{len(investors)} investors matching stage '{stage}' ({', '.join(stage_values[:3])}).",
                next_actions=[
                    "Use io_match_deal with these results to score contacts",
                    "Use io_get_contacts(investor_id=ID) to fetch contacts for a specific investor",
                ],
            )
        except IOAuthError as exc:
            logger.warning("match_deal_stage auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication expired — re-login required", str(exc))
        except IOQueryError as exc:
            logger.warning("match_deal_stage query error: %s", exc)
            return error_response("QUERY_ERROR", str(exc))
        except IOTransientError as exc:
            logger.error("match_deal_stage transient error: %s", exc)
            return error_response("SERVER_ERROR", "Temporary database error. Retry in a few seconds.")
        except Exception:
            logger.exception("unexpected error in match_deal_stage")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ------------------------------------------------------------------
    # Tool 3: match_preferences
    # ------------------------------------------------------------------

    @mcp.tool()
    async def match_preferences(
        preferred_industry: str | None = None,
        preferred_geography: str | None = None,
        check_size_min: float | None = None,
        check_size_max: float | None = None,
        investor_types: list[str] | None = None,
        limit: int = 200,
    ) -> str:
        """Find investors by their stated preferences (no scoring pipeline).

        Queries ONLY the preference fields that investors self-report:
        preferred_industry, preferred_geography, and check_size range.
        This is a pure preference match with no contact-level gating.

        Args:
            preferred_industry: Industry preference substring match (e.g.,
                "Healthcare", "Technology"). Matched via ilike on preferred_industry.
            preferred_geography: Geography preference substring match (e.g.,
                "United States", "Europe"). Matched via ilike on preferred_geography.
            check_size_min: Minimum check size in MILLIONS USD (e.g., 5 for $5M).
                Already in DB units -- do NOT pass raw dollar amounts.
            check_size_max: Maximum check size in MILLIONS USD (e.g., 50 for $50M).
                Already in DB units -- do NOT pass raw dollar amounts.
            investor_types: Optional investor type filter (e.g., ["pe", "vc"]).
            limit: Maximum results to return (default 200, max 5000).

        Returns:
            List of investor summaries matching stated preferences.
        """
        try:
            if not any([preferred_industry, preferred_geography,
                        check_size_min is not None, check_size_max is not None,
                        investor_types]):
                return error_response(
                    "VALIDATION_ERROR",
                    "At least one preference filter is required: preferred_industry, "
                    "preferred_geography, check_size_min, check_size_max, or investor_types.",
                )

            investor_type_values = resolve_investor_types(investor_types) if investor_types else []

            qb = (
                QueryBuilder("investors")
                .select(INVESTOR_SELECT_SUMMARY)
                .gt("contact_count", 0)
                .neq("investor_status", "Acquired/Merged")
            )

            if preferred_industry:
                qb.ilike("preferred_industry", f"*{preferred_industry}*")
            if preferred_geography:
                qb.ilike("preferred_geography", f"*{preferred_geography}*")
            if check_size_min is not None:
                qb.gte("check_size_max", check_size_min)
            if check_size_max is not None:
                qb.lte("check_size_min", check_size_max)
            if investor_type_values:
                qb.in_("primary_investor_type", investor_type_values)

            qb.limit(min(limit, 5000))

            rows, total = await client.query(qb, count="estimated")

            investors = [format_investor_summary(row).model_dump() for row in rows]

            filters_desc = []
            if preferred_industry:
                filters_desc.append(f"industry='{preferred_industry}'")
            if preferred_geography:
                filters_desc.append(f"geography='{preferred_geography}'")
            if check_size_min is not None or check_size_max is not None:
                size_parts = []
                if check_size_min is not None:
                    size_parts.append(f"${check_size_min}M+")
                if check_size_max is not None:
                    size_parts.append(f"up to ${check_size_max}M")
                filters_desc.append(f"check size {' '.join(size_parts)}")

            return tool_response(
                data=investors,
                summary=(
                    f"{len(investors)} investors matching preferences "
                    f"({', '.join(filters_desc)}). "
                    f"Total estimated: {total or 'unknown'}."
                ),
                next_actions=[
                    "Use io_match_deal with role_keywords/firm_keywords to score contacts",
                    "Use io_get_contacts(investor_id=ID) for contacts at a specific investor",
                ],
            )
        except IOAuthError as exc:
            logger.warning("match_preferences auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication expired — re-login required", str(exc))
        except IOQueryError as exc:
            logger.warning("match_preferences query error: %s", exc)
            return error_response("QUERY_ERROR", str(exc))
        except IOTransientError as exc:
            logger.error("match_preferences transient error: %s", exc)
            return error_response("SERVER_ERROR", "Temporary database error. Retry in a few seconds.")
        except Exception:
            logger.exception("unexpected error in match_preferences")
            return error_response("SERVER_ERROR", "An unexpected error occurred")

    # ------------------------------------------------------------------
    # Tool 4: find_similar_investors
    # ------------------------------------------------------------------

    @mcp.tool()
    async def find_similar_investors(
        investor_id: int,
        limit: int = 50,
        investor_types: list[str] | None = None,
    ) -> str:
        """Find investors similar to a given investor using embedding similarity.

        Reads the source investor's 3072-dim embedding from investors_embeddings_3072,
        then passes it to the ai_search_with_ideal_investor RPC for cosine similarity
        ranking against 290K investor embeddings.

        Args:
            investor_id: The ID of the source investor to find similar ones for.
            limit: Maximum number of similar investors to return (default 50, max 5000).
            investor_types: Optional filter by investor type (e.g., ["vc", "pe"]).

        Returns:
            Ranked list of similar investors with similarity metadata.
        """
        try:
            # Step 1: Read the source investor's embedding
            qb = (
                QueryBuilder("investors_embeddings_3072")
                .select("investor_id,embedding")
                .eq("investor_id", investor_id)
                .limit(1)
            )
            rows, _ = await client.query(qb, count=None)

            if not rows:
                return error_response(
                    "NOT_FOUND",
                    f"No embedding found for investor_id={investor_id}. "
                    "Only ~290K of 234K investors have embeddings.",
                )

            # Parse the embedding from comma-separated text string
            embedding_text = rows[0].get("embedding", "")
            if not embedding_text:
                return error_response(
                    "NOT_FOUND",
                    f"Embedding for investor_id={investor_id} is empty.",
                )

            # Embedding is stored as text: "[0.1, 0.2, ...]" or "0.1, 0.2, ..."
            cleaned = embedding_text.strip().strip("[]")
            try:
                embedding_vector = [float(x.strip()) for x in cleaned.split(",")]
            except (ValueError, AttributeError) as exc:
                return error_response(
                    "QUERY_ERROR",
                    f"Failed to parse embedding for investor_id={investor_id}: {exc}",
                )

            # Step 2: Resolve investor types
            investor_type_values = resolve_investor_types(investor_types) if investor_types else None

            # Step 3: Call the AI search RPC
            # Valid params: query_embedding, search_limit, investor_types,
            # min_investment_amount, max_investment_amount.
            # target_investor_types does NOT exist in the DB function — omit it.
            rpc_body: dict[str, Any] = {
                "query_embedding": embedding_vector,
                "search_limit": min(limit, 5000),
                "investor_types": investor_type_values,
                "min_investment_amount": None,
                "max_investment_amount": None,
            }

            rpc_result = await client.rpc("ai_search_with_ideal_investor", rpc_body)

            if not rpc_result:
                return tool_response(
                    data=[],
                    summary=f"No similar investors found for investor_id={investor_id}.",
                )

            # Format results
            # The RPC returns 'distance' (cosine distance, lower = more similar),
            # not 'similarity'. Convert to similarity score: 1 - distance.
            similar = []
            for item in rpc_result[:limit]:
                raw_distance = item.get("distance")
                similarity_score = (
                    round(1.0 - float(raw_distance), 6) if raw_distance is not None else None
                )
                similar.append({
                    "investor_id": item.get("id") or item.get("investor_id"),
                    "name": item.get("investors") or item.get("name"),
                    "primary_investor_type": item.get("primary_investor_type"),
                    "similarity_score": similarity_score,
                    "cosine_distance": raw_distance,
                    "hq_location": item.get("hq_location"),
                    "check_size_min": item.get("check_size_min"),
                    "check_size_max": item.get("check_size_max"),
                    "contact_count": item.get("contact_count"),
                    "description": (item.get("description") or "")[:200] if item.get("description") else None,
                })

            return tool_response(
                data=similar,
                summary=(
                    f"{len(similar)} investors similar to investor_id={investor_id}. "
                    f"Top match: {similar[0].get('name', 'Unknown') if similar else 'N/A'}."
                ),
                next_actions=[
                    "Use io_match_deal with these investors' names as named_firms",
                    "Use io_get_contacts(investor_id=ID) for contacts at similar investors",
                ],
            )
        except IOAuthError as exc:
            logger.warning("find_similar_investors auth error: %s", exc)
            return error_response("AUTH_FAILED", "Authentication expired — re-login required", str(exc))
        except IOQueryError as exc:
            logger.warning("find_similar_investors query error: %s", exc)
            return error_response("QUERY_ERROR", str(exc))
        except IOTransientError as exc:
            logger.error("find_similar_investors transient error: %s", exc)
            return error_response("SERVER_ERROR", "Temporary database error. Retry in a few seconds.")
        except Exception:
            logger.exception("unexpected error in find_similar_investors")
            return error_response("SERVER_ERROR", "An unexpected error occurred")
