"""Phase 4 Deal 2 Validation — IntraLogic Health Solutions ($7M Series A MedTech SaaS).

Tests:
    1. match_deal — full pipeline with IntraLogic criteria
    2. io_search_descriptions — "medtech" and "surgical instruments"

Writes results to:
    data/validation_intralogic.json

Run from the app root:
    uv run scripts/validate_intralogic.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
import time
from pathlib import Path

# Allow running from repo root or app root
_APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APP_ROOT))

from src.client import IOClient, IOAuthError, IOQueryError, IOTransientError, QueryBuilder
from src.scoring import passes_deal_relevance, score_contact
from src.sectors import resolve_investment_types, resolve_investor_types, resolve_sectors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# IntraLogic search criteria (Deal 2 — $7M Series A MedTech SaaS)
# ---------------------------------------------------------------------------

ROLE_KEYWORDS = [
    "healthcare", "medical", "medtech", "surgical", "hospital",
    "life science", "biotech", "pharma", "clinical", "health tech", "saas",
]

FIRM_KEYWORDS = [
    "health", "medical", "medtech", "surgical", "hospital",
    "life science", "biotech", "pharma", "clinical", "orbimed",
]

NAMED_FIRMS = [
    "orbimed", "welsh carson", "bain capital", "thoma bravo", "vista equity",
    "venrock", "nea", "foresite", "8vc", "stryker", "medtronic", "steris",
    "fortive",
]

SECTORS = ["healthcare", "biotech", "pharma"]

INVESTOR_TYPES = [
    "Venture Capital",
    "PE/Buyout",
    "Corporate Venture Capital",
    "Family Office - Single",
]

DEAL_SIZE = 7_000_000   # $7M Series A
DEAL_STAGE = "series_a"

DESCRIPTION_KEYWORDS = ["medtech", "surgical instruments"]

# Baseline from CapIQ segment_v2.py
BASELINE_CSV = Path("data/baseline/2-IntraLogic/contacts/intralogic_FINAL.csv")
OUTPUT_PATH = _APP_ROOT / "data" / "validation_intralogic.json"

# ---------------------------------------------------------------------------
# Constants (mirrors deal_matching.py)
# ---------------------------------------------------------------------------

_INVESTOR_BATCH_SIZE = 100
_PERSONS_BATCH_LIMIT = 5000
_PERSON_MATCH_SELECT = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor,"
    "email_status,email_score,good_email"
)
INVESTOR_SELECT_SUMMARY = (
    "id,investors,primary_investor_type,hq_location,sectors_array,"
    "check_size_min,check_size_max,contact_count,preferred_investment_types,"
    "description,completeness_score"
)

# ---------------------------------------------------------------------------
# Pipeline helpers (replicate deal_matching.py internals for standalone use)
# ---------------------------------------------------------------------------


async def _fetch_investors_by_sector(client: IOClient, sector_codes: list[str],
                                     investor_type_values: list[str] | None,
                                     deal_size_m: float | None) -> list[dict]:
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


async def _fetch_investors_by_description(client: IOClient, keyword: str) -> list[dict]:
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


async def _fetch_investors_by_name(client: IOClient, name: str) -> list[dict]:
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("investors", f"*{name}*")
        .limit(500)
    )
    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_stage(client: IOClient, stage_values: list[str]) -> list[dict]:
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


async def _fetch_persons_for_investors(client: IOClient, investor_ids: list[int]) -> list[dict]:
    all_persons: list[dict] = []
    for i in range(0, len(investor_ids), _INVESTOR_BATCH_SIZE):
        batch = investor_ids[i: i + _INVESTOR_BATCH_SIZE]
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
    seen: dict[int, dict] = {}
    for row in all_rows:
        inv_id = row.get("id")
        if inv_id is not None and inv_id not in seen:
            seen[inv_id] = row
    return seen


def _score_and_gate(
    persons: list[dict],
    investor_map: dict[int, dict],
    role_keywords: list[str],
    firm_keywords: list[str],
    named_firms: list[str],
    expanded: bool = False,
    min_score: int = 20,
) -> list[dict]:
    results: list[dict] = []
    for person in persons:
        role = person.get("role") or ""
        investor_id = person.get("investor")
        investor = investor_map.get(investor_id, {}) if investor_id else {}
        investor_name = investor.get("investors", "")
        company_name = person.get("company_name", "") or ""
        sectors_arr = investor.get("sectors_array") or []
        sectors_str = " ".join(sectors_arr) if isinstance(sectors_arr, list) else str(sectors_arr)

        contact_score = score_contact(role, role_keywords)

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


def _cap_per_firm(results: list[dict], max_per_firm: int = 5) -> list[dict]:
    from collections import defaultdict
    by_firm: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        firm_key = r.get("_investor_name") or r.get("company_name") or "Unknown"
        by_firm[firm_key].append(r)

    capped: list[dict] = []
    for contacts in by_firm.values():
        contacts.sort(key=lambda x: x.get("_score", 0), reverse=True)
        capped.extend(contacts[:max_per_firm])

    capped.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return capped


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------


def load_baseline(csv_path: Path) -> list[dict]:
    """Load the CapIQ baseline CSV into a list of dicts."""
    if not csv_path.exists():
        logger.warning("Baseline CSV not found at %s", csv_path)
        return []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# io_search_descriptions test (direct PostgREST — no MCP layer needed)
# ---------------------------------------------------------------------------


async def test_search_descriptions(client: IOClient, keyword: str) -> dict:
    """Run an ilike description search and return summary stats."""
    logger.info("io_search_descriptions: keyword=%r", keyword)
    t0 = time.perf_counter()

    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("description", f"*{keyword}*")
        .neq("investor_status", "Acquired/Merged")
        .gt("contact_count", 0)
        .order("completeness_score", ascending=False)
        .limit(200)
    )
    rows, total = await client.query(qb, count="estimated")
    elapsed = time.perf_counter() - t0

    names = [r.get("investors", "") for r in rows[:10]]
    types = {}
    for r in rows:
        t = r.get("primary_investor_type") or "Unknown"
        types[t] = types.get(t, 0) + 1

    return {
        "keyword": keyword,
        "rows_returned": len(rows),
        "estimated_total": total,
        "elapsed_s": round(elapsed, 2),
        "top_10_investors": names,
        "investor_type_breakdown": dict(sorted(types.items(), key=lambda x: -x[1])[:10]),
    }


# ---------------------------------------------------------------------------
# Main validation entrypoint
# ---------------------------------------------------------------------------


async def run_validation() -> None:
    logger.info("=== IntraLogic Phase 4 Validation START ===")

    baseline_rows = load_baseline(BASELINE_CSV)
    logger.info("Baseline contacts loaded: %d", len(baseline_rows))

    async with IOClient.from_env() as client:
        # ── Test 1: match_deal pipeline ────────────────────────────────────
        logger.info("--- match_deal pipeline ---")

        deal_size_m = DEAL_SIZE / 1_000_000  # → 7.0
        sector_codes = resolve_sectors(SECTORS)
        investor_type_values = resolve_investor_types(INVESTOR_TYPES)
        stage_values = resolve_investment_types([DEAL_STAGE])

        logger.info("Sector codes: %s", sector_codes)
        logger.info("Investor type values: %s", investor_type_values)
        logger.info("Stage values: %s", stage_values)

        t0 = time.perf_counter()
        all_investor_rows: list[dict] = []

        # 1a. Sector overlap
        logger.info("Phase 1a: sector overlap query (%s)...", sector_codes)
        rows = await _fetch_investors_by_sector(client, sector_codes, investor_type_values or None, deal_size_m)
        logger.info("  → %d rows", len(rows))
        all_investor_rows.extend(rows)

        # 1b. Description keywords (description_keywords param)
        for kw in DESCRIPTION_KEYWORDS:
            logger.info("Phase 1b: description ilike '%s'...", kw)
            rows = await _fetch_investors_by_description(client, kw)
            logger.info("  → %d rows", len(rows))
            all_investor_rows.extend(rows)

        # 1c. Named firms
        logger.info("Phase 1c: named firm queries (%d firms)...", len(NAMED_FIRMS))
        named_firm_counts: dict[str, int] = {}
        for name in NAMED_FIRMS:
            rows = await _fetch_investors_by_name(client, name)
            named_firm_counts[name] = len(rows)
            all_investor_rows.extend(rows)
        logger.info("  Named firm results: %s", named_firm_counts)

        # 1d. Deal stage
        if stage_values:
            logger.info("Phase 1d: deal stage query (%s)...", stage_values)
            rows = await _fetch_investors_by_stage(client, stage_values)
            logger.info("  → %d rows", len(rows))
            all_investor_rows.extend(rows)

        # Deduplicate
        investor_map = _dedupe_investors(all_investor_rows)
        phase1_elapsed = time.perf_counter() - t0
        logger.info(
            "Phase 1 complete: %d raw rows → %d unique investors  (%.1fs)",
            len(all_investor_rows), len(investor_map), phase1_elapsed,
        )

        # ── Phase 2: fetch persons ─────────────────────────────────────────
        logger.info("Phase 2: fetching persons for %d investors...", len(investor_map))
        t1 = time.perf_counter()
        investor_ids = list(investor_map.keys())
        all_persons = await _fetch_persons_for_investors(client, investor_ids)
        phase2_elapsed = time.perf_counter() - t1
        logger.info("  → %d persons  (%.1fs)", len(all_persons), phase2_elapsed)

        # ── Phase 2 scoring + gating ───────────────────────────────────────
        logger.info("Scoring and gating %d persons...", len(all_persons))
        t2 = time.perf_counter()
        scored = _score_and_gate(
            all_persons, investor_map,
            role_keywords=ROLE_KEYWORDS,
            firm_keywords=FIRM_KEYWORDS,
            named_firms=NAMED_FIRMS,
        )
        scoring_elapsed = time.perf_counter() - t2
        logger.info("  → %d passed gating  (%.1fs)", len(scored), scoring_elapsed)

        # Cap per firm
        final_contacts = _cap_per_firm(scored, max_per_firm=5)[:1000]
        total_elapsed = time.perf_counter() - t0

        # ── Compute stats ──────────────────────────────────────────────────
        unique_firms = len(set(c.get("_investor_name", "") for c in final_contacts))
        with_email = sum(1 for c in final_contacts if c.get("email"))
        with_phone = sum(1 for c in final_contacts if c.get("phone"))
        with_linkedin = sum(1 for c in final_contacts if c.get("linkedin_profile_url"))
        good_email = sum(1 for c in final_contacts if c.get("good_email"))

        # Investor type breakdown
        type_breakdown: dict[str, int] = {}
        for c in final_contacts:
            t_val = c.get("_investor_type") or "Unknown"
            type_breakdown[t_val] = type_breakdown.get(t_val, 0) + 1

        # Match path breakdown
        path_breakdown: dict[str, int] = {}
        for c in final_contacts:
            p = c.get("_match_path") or "Unknown"
            path_breakdown[p] = path_breakdown.get(p, 0) + 1

        # Score distribution
        scores = [c.get("_score", 0) for c in final_contacts]
        score_dist = {
            "min": min(scores) if scores else 0,
            "max": max(scores) if scores else 0,
            "avg": round(sum(scores) / len(scores), 1) if scores else 0,
            "gte_30": sum(1 for s in scores if s >= 30),
            "gte_40": sum(1 for s in scores if s >= 40),
            "gte_50": sum(1 for s in scores if s >= 50),
        }

        # Top 20 firms by contact count
        firm_counts: dict[str, int] = {}
        for c in final_contacts:
            f = c.get("_investor_name") or "Unknown"
            firm_counts[f] = firm_counts.get(f, 0) + 1
        top_firms = sorted(firm_counts.items(), key=lambda x: -x[1])[:20]

        # Overlap with baseline (by email)
        baseline_emails = {r.get("email", "").lower() for r in baseline_rows if r.get("email")}
        mcp_emails = {c.get("email", "").lower() for c in final_contacts if c.get("email")}
        overlap_count = len(baseline_emails & mcp_emails)
        mcp_only = len(mcp_emails - baseline_emails)
        baseline_only = len(baseline_emails - mcp_emails)

        match_deal_result = {
            "tool": "match_deal",
            "deal": "IntraLogic Health Solutions",
            "deal_size": DEAL_SIZE,
            "deal_stage": DEAL_STAGE,
            "timing": {
                "phase1_investors_s": round(phase1_elapsed, 2),
                "phase2_persons_s": round(phase2_elapsed, 2),
                "scoring_s": round(scoring_elapsed, 2),
                "total_s": round(total_elapsed, 2),
            },
            "pipeline_stats": {
                "raw_investor_rows": len(all_investor_rows),
                "unique_investors": len(investor_map),
                "total_persons_fetched": len(all_persons),
                "persons_passed_gating": len(scored),
                "final_contacts": len(final_contacts),
                "unique_firms": unique_firms,
            },
            "contact_stats": {
                "with_email": with_email,
                "with_phone": with_phone,
                "with_linkedin": with_linkedin,
                "good_email": good_email,
                "email_coverage_pct": round(with_email / len(final_contacts) * 100, 1) if final_contacts else 0,
            },
            "score_distribution": score_dist,
            "match_path_breakdown": dict(sorted(path_breakdown.items(), key=lambda x: -x[1])),
            "investor_type_breakdown": dict(sorted(type_breakdown.items(), key=lambda x: -x[1])),
            "top_20_firms": [{"firm": f, "contacts": n} for f, n in top_firms],
            "baseline_comparison": {
                "baseline_total": len(baseline_rows),
                "baseline_with_email": len(baseline_emails),
                "mcp_with_email": len(mcp_emails),
                "overlap": overlap_count,
                "mcp_only_new": mcp_only,
                "baseline_only_missed": baseline_only,
                "overlap_pct_of_baseline": round(overlap_count / len(baseline_emails) * 100, 1) if baseline_emails else 0,
            },
            "named_firm_hit_counts": named_firm_counts,
            "sample_top_contacts": [
                {k: v for k, v in c.items() if not k.startswith("_") or k in
                 ("_score", "_match_path", "_investor_name", "_investor_type")}
                for c in final_contacts[:10]
            ],
        }

        logger.info(
            "match_deal DONE: %d contacts / %d firms  email=%d phone=%d linkedin=%d  %.1fs total",
            len(final_contacts), unique_firms, with_email, with_phone, with_linkedin, total_elapsed,
        )

        # ── Test 2: io_search_descriptions ────────────────────────────────
        logger.info("--- io_search_descriptions tests ---")
        desc_results = []
        for kw in DESCRIPTION_KEYWORDS:
            result = await test_search_descriptions(client, kw)
            desc_results.append(result)
            logger.info(
                "  '%s': %d rows (est. total=%s) in %.2fs",
                kw, result["rows_returned"], result["estimated_total"], result["elapsed_s"],
            )

    # ── Write output ───────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "validation": "IntraLogic Health Solutions — Phase 4 Deal 2",
        "run_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "match_deal": match_deal_result,
        "search_descriptions": desc_results,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    logger.info("Results written to %s", OUTPUT_PATH)

    # ── Console summary ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY — IntraLogic Health Solutions (Deal 2)")
    print("=" * 70)
    ps = match_deal_result["pipeline_stats"]
    cs = match_deal_result["contact_stats"]
    bc = match_deal_result["baseline_comparison"]
    print(f"Pipeline:  {ps['raw_investor_rows']} raw → {ps['unique_investors']} investors → "
          f"{ps['total_persons_fetched']} persons → {ps['persons_passed_gating']} gated → "
          f"{ps['final_contacts']} final")
    print(f"Coverage:  email={cs['with_email']}  phone={cs['with_phone']}  "
          f"linkedin={cs['with_linkedin']}  good_email={cs['good_email']}")
    print(f"Timing:    {match_deal_result['timing']['total_s']}s total")
    print(f"Baseline:  {bc['baseline_total']} contacts  |  overlap={bc['overlap']}  "
          f"({bc['overlap_pct_of_baseline']}% of baseline)  |  new={bc['mcp_only_new']}")
    print()
    print("Investor type breakdown:")
    for itype, cnt in list(match_deal_result["investor_type_breakdown"].items())[:8]:
        print(f"  {itype:<45} {cnt}")
    print()
    print("Match path breakdown:")
    for path, cnt in match_deal_result["match_path_breakdown"].items():
        print(f"  {path:<45} {cnt}")
    print()
    print("io_search_descriptions:")
    for d in desc_results:
        print(f"  '{d['keyword']}': {d['rows_returned']} results  (est. total={d['estimated_total']})  {d['elapsed_s']}s")
    print("=" * 70)
    print(f"Full results: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(run_validation())
