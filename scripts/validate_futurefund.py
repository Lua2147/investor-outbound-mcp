#!/usr/bin/env python3
"""Phase 4, Deal 5 validation — Future Fund One.

Tests match_deal and search_descriptions logic live against the Investor
Outbound Supabase backend and compares results to the baseline
ffo_FINAL.csv (6,916 contacts).

Output: data/validation_futurefund.json
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
import time
from pathlib import Path

# Make src importable when run directly from repo root
REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.client import IOClient, QueryBuilder
from src.entities import INVESTOR_SELECT_SUMMARY
from src.scoring import passes_deal_relevance, score_contact
from src.sectors import resolve_investor_types, resolve_sectors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Deal 5 parameters (from task brief + INVESTOR_TARGETING_SOP.md) ──────────

ROLE_KEYWORDS = [
    "crypto", "bitcoin", "blockchain", "digital asset", "web3", "defi",
    "real estate", "reit", "net lease", "nnn", "triple net", "property",
    "commercial real estate", "franchise", "qsr", "wealth", "advisory",
    "capital markets", "alternatives", "allocation",
]

FIRM_KEYWORDS = [
    "crypto", "bitcoin", "blockchain", "digital asset", "web3", "real estate",
    "reit", "realty", "property", "net lease", "wealth", "advisory",
]

NAMED_FIRMS = [
    "galaxy digital", "grayscale", "pantera capital", "paradigm", "coinbase",
    "bitwise", "realty income", "spirit realty", "national retail properties",
    "caz investments", "evercore", "jll", "starwood", "tpg",
]

SECTORS = ["blockchain", "real estate"]

INVESTOR_TYPES = [
    "Venture Capital", "Hedge Fund", "Real Estate", "Family Office - Single",
    "PE/Buyout", "Wealth Management/RIA",
]

# search_descriptions test keywords (from task brief)
DESCRIPTION_KEYWORDS = ["bitcoin", "net lease", "triple net"]

# Baseline
BASELINE_PATH = Path(
    "data/baseline/5-FutureFundOne/contacts/ffo_FINAL.csv"
)
BASELINE_CONTACT_COUNT = 6916

# Output
OUTPUT_PATH = REPO_ROOT / "data" / "validation_futurefund.json"

# Internal constants (mirror match_deal defaults)
_INVESTOR_BATCH_SIZE = 100
_PERSONS_BATCH_LIMIT = 5000
_PERSON_SELECT = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor,"
    "email_status,email_score,good_email"
)


# ── Helpers (replicate match_deal internals for standalone validation) ────────


async def fetch_investors_by_sector(
    client: IOClient,
    sector_codes: list[str],
    investor_type_values: list[str],
) -> list[dict]:
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ov("sectors_array", sector_codes)
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
    )
    if investor_type_values:
        qb.in_("primary_investor_type", investor_type_values)
    qb.limit(5000)
    rows, total = await client.query(qb, count="estimated")
    logger.info("  sector query: %d rows (est. total %s)", len(rows), total)
    return rows


async def fetch_investors_by_description(
    client: IOClient,
    keyword: str,
) -> list[dict]:
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("description", f"*{keyword}*")
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
        .limit(2000)
    )
    rows, total = await client.query(qb, count="estimated")
    logger.info("  description ilike '%s': %d rows (est. %s)", keyword, len(rows), total)
    return rows


async def fetch_investors_by_name(
    client: IOClient,
    name: str,
) -> list[dict]:
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .ilike("investors", f"*{name}*")
        .limit(500)
    )
    rows, _ = await client.query(qb, count=None)
    if rows:
        logger.info("  named firm '%s': %d investor rows", name, len(rows))
    return rows


async def fetch_investors_by_type(
    client: IOClient,
    investor_type_values: list[str],
) -> list[dict]:
    qb = (
        QueryBuilder("investors")
        .select(INVESTOR_SELECT_SUMMARY)
        .in_("primary_investor_type", investor_type_values)
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
        .limit(5000)
    )
    rows, total = await client.query(qb, count="estimated")
    logger.info("  type-only query: %d rows (est. %s)", len(rows), total)
    return rows


async def fetch_persons_for_investors(
    client: IOClient,
    investor_ids: list[int],
) -> list[dict]:
    all_persons: list[dict] = []
    for i in range(0, len(investor_ids), _INVESTOR_BATCH_SIZE):
        batch = investor_ids[i: i + _INVESTOR_BATCH_SIZE]
        qb = (
            QueryBuilder("persons")
            .select(_PERSON_SELECT)
            .in_("investor", batch)
            .limit(_PERSONS_BATCH_LIMIT)
        )
        rows, _ = await client.query(qb, count=None)
        all_persons.extend(rows)
    return all_persons


def dedupe_investors(rows: list[dict]) -> dict[int, dict]:
    seen: dict[int, dict] = {}
    for row in rows:
        inv_id = row.get("id")
        if inv_id is not None and inv_id not in seen:
            seen[inv_id] = row
    return seen


def score_and_gate_contacts(
    persons: list[dict],
    investor_map: dict[int, dict],
    role_keywords: list[str],
    firm_keywords: list[str],
    named_firms: list[str],
    expanded: bool,
    min_score: int = 20,
) -> list[dict]:
    results: list[dict] = []
    gate_cuts: dict[str, int] = {}

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
            gate_cuts[reason] = gate_cuts.get(reason, 0) + 1
            continue

        results.append({
            **person,
            "_score": contact_score,
            "_match_path": reason,
            "_investor_name": investor_name,
            "_investor_type": investor.get("primary_investor_type"),
            "_sectors": sectors_str,
        })

    return results, gate_cuts


def cap_per_firm(results: list[dict], max_per_firm: int = 5) -> list[dict]:
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


def load_baseline(path: Path) -> dict:
    """Load baseline CSV and return summary stats."""
    if not path.exists():
        return {"error": f"File not found: {path}"}

    contacts: list[dict] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            contacts.append(row)

    firms = set(row.get("_investor_name", "") for row in contacts)
    with_email = sum(1 for r in contacts if r.get("email"))
    investor_types = {}
    for r in contacts:
        t = r.get("_investor_type", "Unknown") or "Unknown"
        investor_types[t] = investor_types.get(t, 0) + 1

    return {
        "total_contacts": len(contacts),
        "unique_firms": len(firms),
        "with_email": with_email,
        "top_investor_types": dict(sorted(investor_types.items(), key=lambda x: -x[1])[:10]),
    }


async def run_match_deal(client: IOClient) -> dict:
    """Run the full match_deal two-phase pipeline for Future Fund One."""
    logger.info("=== Phase 1: Broad investor pull ===")
    t0 = time.monotonic()

    sector_codes = resolve_sectors(SECTORS)
    investor_type_values = resolve_investor_types(INVESTOR_TYPES)
    logger.info("Resolved sectors: %s", sector_codes)
    logger.info("Resolved investor types: %d values", len(investor_type_values))

    all_investor_rows: list[dict] = []

    # 1a. Sector overlap
    logger.info("1a. Sector overlap query...")
    rows = await fetch_investors_by_sector(client, sector_codes, investor_type_values)
    all_investor_rows.extend(rows)

    # 1b. Description keywords: "bitcoin", "net lease", "triple net"
    logger.info("1b. Description keyword queries...")
    for kw in DESCRIPTION_KEYWORDS:
        rows = await fetch_investors_by_description(client, kw)
        all_investor_rows.extend(rows)

    # 1c. Named firms
    logger.info("1c. Named firm queries (%d firms)...", len(NAMED_FIRMS))
    for name in NAMED_FIRMS:
        rows = await fetch_investors_by_name(client, name)
        all_investor_rows.extend(rows)

    # 1d. Type-only fallback (because sectors can be sparse for family offices)
    logger.info("1d. Type-only investor query (family offices / wealth mgmt)...")
    rows = await fetch_investors_by_type(client, investor_type_values)
    all_investor_rows.extend(rows)

    investor_map = dedupe_investors(all_investor_rows)
    logger.info(
        "Phase 1 complete: %d raw rows → %d unique investors (%.1fs)",
        len(all_investor_rows),
        len(investor_map),
        time.monotonic() - t0,
    )

    # ── Phase 2: Fetch persons + score/gate ──────────────────────────────────
    logger.info("=== Phase 2: Fetch persons + score/gate ===")
    t1 = time.monotonic()

    investor_ids = list(investor_map.keys())
    logger.info("Fetching persons for %d investors in batches of %d...", len(investor_ids), _INVESTOR_BATCH_SIZE)
    all_persons = await fetch_persons_for_investors(client, investor_ids)
    logger.info("  fetched %d persons (%.1fs)", len(all_persons), time.monotonic() - t1)

    t2 = time.monotonic()
    logger.info("Scoring and gating %d persons...", len(all_persons))
    scored, gate_cuts = score_and_gate_contacts(
        persons=all_persons,
        investor_map=investor_map,
        role_keywords=ROLE_KEYWORDS,
        firm_keywords=FIRM_KEYWORDS,
        named_firms=NAMED_FIRMS,
        expanded=False,
        min_score=20,
    )
    logger.info("  %d passed gating (%.1fs)", len(scored), time.monotonic() - t2)

    capped = cap_per_firm(scored, max_per_firm=5)
    final = capped[:1000]

    unique_firms = len(set(c.get("_investor_name", "") for c in final))
    with_email = sum(1 for c in final if c.get("email"))
    with_phone = sum(1 for c in final if c.get("phone"))
    with_linkedin = sum(1 for c in final if c.get("linkedin_profile_url"))
    with_good_email = sum(1 for c in final if c.get("good_email"))

    # Match path distribution
    path_dist: dict[str, int] = {}
    for c in final:
        p = c.get("_match_path", "?")
        path_dist[p] = path_dist.get(p, 0) + 1

    # Score distribution
    scores = [c.get("_score", 0) for c in final]
    score_dist = {
        "min": min(scores) if scores else 0,
        "max": max(scores) if scores else 0,
        "p25": sorted(scores)[len(scores) // 4] if scores else 0,
        "p50": sorted(scores)[len(scores) // 2] if scores else 0,
        "p75": sorted(scores)[3 * len(scores) // 4] if scores else 0,
    }

    # Type distribution in final results
    type_dist: dict[str, int] = {}
    for c in final:
        t = c.get("_investor_type") or "Unknown"
        type_dist[t] = type_dist.get(t, 0) + 1

    # Top 10 firms by contact count
    firm_counts: dict[str, int] = {}
    for c in final:
        f = c.get("_investor_name") or "Unknown"
        firm_counts[f] = firm_counts.get(f, 0) + 1
    top_firms = dict(sorted(firm_counts.items(), key=lambda x: -x[1])[:10])

    elapsed = time.monotonic() - t0
    logger.info(
        "match_deal complete: %d contacts / %d firms in %.1fs",
        len(final), unique_firms, elapsed,
    )

    return {
        "tool": "match_deal",
        "params": {
            "role_keywords": ROLE_KEYWORDS,
            "firm_keywords": FIRM_KEYWORDS,
            "named_firms": NAMED_FIRMS,
            "sectors": SECTORS,
            "investor_types": INVESTOR_TYPES,
            "deal_size": None,
            "expanded": False,
        },
        "pipeline_stats": {
            "raw_investor_rows": len(all_investor_rows),
            "unique_investors_scanned": len(investor_map),
            "persons_fetched": len(all_persons),
            "persons_passing_gating": len(scored),
            "contacts_after_firm_cap": len(capped),
            "contacts_returned": len(final),
            "elapsed_seconds": round(elapsed, 1),
        },
        "gate_cut_breakdown": gate_cuts,
        "results": {
            "total_contacts": len(final),
            "unique_firms": unique_firms,
            "with_email": with_email,
            "with_phone": with_phone,
            "with_linkedin": with_linkedin,
            "with_good_email": with_good_email,
            "match_path_distribution": path_dist,
            "score_distribution": score_dist,
            "investor_type_distribution": dict(sorted(type_dist.items(), key=lambda x: -x[1])[:15]),
            "top_firms_by_contact_count": top_firms,
        },
        "sample_contacts": [
            {k: v for k, v in c.items() if not k.startswith("_") or k in ("_score", "_match_path", "_investor_name", "_investor_type")}
            for c in final[:20]
        ],
    }


async def run_search_descriptions(client: IOClient) -> dict:
    """Test search_descriptions for Future Fund One keywords."""
    logger.info("=== search_descriptions test ===")
    results: dict[str, dict] = {}

    for kw in DESCRIPTION_KEYWORDS:
        t0 = time.monotonic()
        qb = (
            QueryBuilder("investors")
            .select(INVESTOR_SELECT_SUMMARY)
            .ilike("description", f"*{kw}*")
            .gt("contact_count", 0)
            .neq("investor_status", "Acquired/Merged")
            .limit(2000)
        )
        rows, total = await client.query(qb, count="estimated")
        elapsed = time.monotonic() - t0

        # Type distribution in description results
        type_dist: dict[str, int] = {}
        for r in rows:
            t = r.get("primary_investor_type") or "Unknown"
            type_dist[t] = type_dist.get(t, 0) + 1

        results[kw] = {
            "keyword": kw,
            "investors_matched": len(rows),
            "total_estimated": total,
            "elapsed_seconds": round(elapsed, 2),
            "top_investor_types": dict(sorted(type_dist.items(), key=lambda x: -x[1])[:10]),
            "sample_investors": [
                {"name": r.get("investors"), "type": r.get("primary_investor_type"), "hq": r.get("hq_location")}
                for r in rows[:5]
            ],
        }
        logger.info(
            "  '%s' → %d investors (est. %s) in %.2fs",
            kw, len(rows), total, elapsed,
        )

    return {"tool": "search_descriptions", "results_by_keyword": results}


def compare_baseline(match_deal_results: dict, baseline_stats: dict) -> dict:
    """Compare match_deal output to ffo_FINAL.csv baseline."""
    mdr = match_deal_results.get("results", {})
    tool_contacts = mdr.get("total_contacts", 0)
    tool_firms = mdr.get("unique_firms", 0)
    baseline_contacts = baseline_stats.get("total_contacts", BASELINE_CONTACT_COUNT)
    baseline_firms = baseline_stats.get("unique_firms", 0)

    # The MCP tool caps at max_results=1000 and max_per_firm=5 by default
    # The baseline ran segment_v2.py with no cap. We compare coverage ratios.
    pipeline_stats = match_deal_results.get("pipeline_stats", {})
    uncapped_contacts = pipeline_stats.get("persons_passing_gating", 0)

    contact_coverage_pct = (
        (uncapped_contacts / baseline_contacts * 100) if baseline_contacts else 0
    )
    firm_coverage_pct = (
        (tool_firms / baseline_firms * 100) if baseline_firms else 0
    )

    verdict = "PASS" if contact_coverage_pct >= 60 else "INVESTIGATE"
    if uncapped_contacts > baseline_contacts * 2:
        verdict = "OVER-MATCHING — tune keywords or raise min_score"

    return {
        "baseline": baseline_stats,
        "tool_output": {
            "total_contacts_returned": tool_contacts,
            "unique_firms_in_capped_output": tool_firms,
            "contacts_passing_gating_before_cap": uncapped_contacts,
        },
        "coverage_vs_baseline": {
            "uncapped_contacts_vs_baseline_pct": round(contact_coverage_pct, 1),
            "firm_coverage_pct": round(firm_coverage_pct, 1),
            "note": (
                "uncapped_contacts = persons that pass 6-gate pipeline, "
                "before max_per_firm cap and max_results=1000 cap. "
                "This is the apples-to-apples comparison against segment_v2.py baseline."
            ),
        },
        "verdict": verdict,
    }


async def main() -> None:
    logger.info("Future Fund One — Phase 4 Validation")
    logger.info("Baseline: %s contacts in %s", BASELINE_CONTACT_COUNT, BASELINE_PATH.name)

    baseline_stats = load_baseline(BASELINE_PATH)
    logger.info("Baseline loaded: %s", baseline_stats)

    async with IOClient.from_env() as client:
        logger.info("Authenticated to Investor Outbound Supabase")

        match_deal_result = await run_match_deal(client)
        search_desc_result = await run_search_descriptions(client)

    comparison = compare_baseline(match_deal_result, baseline_stats)

    output = {
        "deal": "Future Fund One",
        "deal_size": "$250M fund raise",
        "strategy": "60% NNN real estate + 20% QSR (Swig) + 20% algorithmic Bitcoin",
        "validation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline": {
            "source": str(BASELINE_PATH),
            "stats": baseline_stats,
        },
        "match_deal": match_deal_result,
        "search_descriptions": search_desc_result,
        "comparison": comparison,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    logger.info("Results written to %s", OUTPUT_PATH)

    # Print summary to stdout
    print("\n" + "=" * 60)
    print("FUTURE FUND ONE — VALIDATION SUMMARY")
    print("=" * 60)
    ps = match_deal_result.get("pipeline_stats", {})
    r = match_deal_result.get("results", {})
    print(f"Investors scanned:          {ps.get('unique_investors_scanned', 0):,}")
    print(f"Persons fetched:            {ps.get('persons_fetched', 0):,}")
    print(f"Persons passing 6-gate:     {ps.get('persons_passing_gating', 0):,}")
    print(f"Contacts returned (capped): {r.get('total_contacts', 0):,}")
    print(f"Unique firms:               {r.get('unique_firms', 0):,}")
    print(f"With email:                 {r.get('with_email', 0):,}")
    print(f"With good_email:            {r.get('with_good_email', 0):,}")
    print(f"With LinkedIn:              {r.get('with_linkedin', 0):,}")
    print(f"\nBaseline (segment_v2.py):   {BASELINE_CONTACT_COUNT:,} contacts")
    cov = comparison.get("coverage_vs_baseline", {})
    print(f"Coverage vs baseline:       {cov.get('uncapped_contacts_vs_baseline_pct', 0):.1f}%")
    print(f"Verdict:                    {comparison.get('verdict', '?')}")

    print("\nMatch path distribution:")
    for path, count in sorted(r.get("match_path_distribution", {}).items(), key=lambda x: -x[1]):
        print(f"  Path {path}: {count:,}")

    print("\nGate cut breakdown:")
    for reason, count in sorted(match_deal_result.get("gate_cut_breakdown", {}).items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count:,}")

    print("\nTop investor types in results:")
    for t, count in list(r.get("investor_type_distribution", {}).items())[:8]:
        print(f"  {t}: {count:,}")

    print("\nTop firms by contact count:")
    for firm, count in list(r.get("top_firms_by_contact_count", {}).items())[:10]:
        print(f"  {firm}: {count}")

    print("\nsearch_descriptions results:")
    for kw, res in search_desc_result.get("results_by_keyword", {}).items():
        print(f"  '{kw}': {res['investors_matched']:,} investors (est. {res['total_estimated']})")

    print(f"\nFull results: {OUTPUT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
