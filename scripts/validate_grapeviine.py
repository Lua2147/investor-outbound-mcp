"""Phase 4 — Deal 4 Validation: Grapeviine ($1.8M SAFE, auto dealer SaaS).

Tests match_deal against live Supabase data and compares against the baseline
871-contact list at ~/Desktop/Deal-Investor-Research/4-Grapeviine/contacts/grapeviine_FINAL.csv.

Usage:
    .venv/bin/python scripts/validate_grapeviine.py
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.client import IOClient, _load_credentials  # noqa: E402
from src.scoring import passes_deal_relevance, score_contact  # noqa: E402
from src.sectors import resolve_investment_types, resolve_investor_types, resolve_sectors  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Deal 4 parameters ─────────────────────────────────────────────────────────
ROLE_KEYWORDS = [
    "automotive", "auto tech", "autotech", "dealer", "mobility", "vehicle",
    "seed", "angel", "early stage", "venture", "saas", "software", "technology",
]
FIRM_KEYWORDS = [
    "automotive", "auto", "dealer", "mobility", "vehicle",
    "seed", "angel", "accelerator", "incubator", "early stage",
]
NAMED_FIRMS = [
    "automotive ventures", "autotech ventures", "toyota ventures", "gm ventures",
    "motus ventures", "cdk global", "cox automotive", "first round capital",
    "y combinator", "canvas ventures",
]
SECTORS = ["automotive", "software", "technology"]
INVESTOR_TYPES = [
    "Venture Capital", "Angel (individual)",
    "Corporate Venture Capital", "Accelerator/Incubator",
]
DEAL_SIZE = 1_800_000  # $1.8M
DEAL_STAGE = "seed"

# ── paths ──────────────────────────────────────────────────────────────────────
BASELINE_CSV = Path(
    "data/baseline/4-Grapeviine/contacts/grapeviine_FINAL.csv"
)
OUTPUT_PATH = Path(__file__).parents[1] / "data" / "validation_grapeviine.json"

# ── constants (mirrors deal_matching.py) ──────────────────────────────────────
_INVESTOR_BATCH_SIZE = 100
_PERSONS_BATCH_LIMIT = 5000
_INVESTOR_SELECT_SUMMARY = (
    "id,investors,primary_investor_type,hq_location,check_size_min,check_size_max,"
    "sectors_array,description,capital_under_management,contact_count,investor_status,"
    "preferred_investment_types,preferred_geography,preferred_industry"
)
_PERSON_MATCH_SELECT = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor,"
    "email_status,email_score,good_email"
)


# ---------------------------------------------------------------------------
# Inline pipeline (mirrors deal_matching.py — validates the same logic)
# ---------------------------------------------------------------------------


async def _fetch_investors_by_sector(client, sector_codes, investor_type_values, deal_size_m):
    from src.client import QueryBuilder
    qb = (
        QueryBuilder("investors")
        .select(_INVESTOR_SELECT_SUMMARY)
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


async def _fetch_investors_by_description(client, keyword):
    from src.client import QueryBuilder
    qb = (
        QueryBuilder("investors")
        .select(_INVESTOR_SELECT_SUMMARY)
        .ilike("description", f"*{keyword}*")
        .gt("contact_count", 0)
        .neq("investor_status", "Acquired/Merged")
        .limit(2000)
    )
    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_name(client, name):
    from src.client import QueryBuilder
    qb = (
        QueryBuilder("investors")
        .select(_INVESTOR_SELECT_SUMMARY)
        .ilike("investors", f"*{name}*")
        .limit(500)
    )
    rows, _ = await client.query(qb, count=None)
    return rows


async def _fetch_investors_by_stage(client, stage_values):
    from src.client import QueryBuilder
    all_rows = []
    for stage in stage_values:
        qb = (
            QueryBuilder("investors")
            .select(_INVESTOR_SELECT_SUMMARY)
            .ilike("preferred_investment_types", f"*{stage}*")
            .gt("contact_count", 0)
            .neq("investor_status", "Acquired/Merged")
            .limit(2000)
        )
        rows, _ = await client.query(qb, count=None)
        all_rows.extend(rows)
    return all_rows


async def _fetch_investors_by_type(client, investor_type_values, deal_size_m):
    from src.client import QueryBuilder
    qb = (
        QueryBuilder("investors")
        .select(_INVESTOR_SELECT_SUMMARY)
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


async def _fetch_persons_for_investors(client, investor_ids):
    from src.client import QueryBuilder
    all_persons = []
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


def _dedupe_investors(all_rows):
    seen = {}
    for row in all_rows:
        inv_id = row.get("id")
        if inv_id is not None and inv_id not in seen:
            seen[inv_id] = row
    return seen


def _score_and_gate(persons, investor_map, expanded=False, min_score=20):
    results = []
    for person in persons:
        role = person.get("role") or ""
        investor_id = person.get("investor")
        investor = investor_map.get(investor_id, {}) if investor_id else {}
        investor_name = investor.get("investors", "")
        company_name = person.get("company_name", "") or ""
        sectors_arr = investor.get("sectors_array") or []
        sectors_str = " ".join(sectors_arr) if isinstance(sectors_arr, list) else str(sectors_arr)

        contact_score = score_contact(role, ROLE_KEYWORDS)

        passes, reason = passes_deal_relevance(
            role=role,
            company_name=company_name,
            investor_name=investor_name,
            sectors_str=sectors_str,
            score=contact_score,
            role_keywords=ROLE_KEYWORDS,
            firm_keywords=FIRM_KEYWORDS,
            named_firms=NAMED_FIRMS,
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


def _cap_per_firm(results, max_per_firm=5):
    by_firm = defaultdict(list)
    for r in results:
        firm_key = r.get("_investor_name") or r.get("company_name") or "Unknown"
        by_firm[firm_key].append(r)
    capped = []
    for contacts in by_firm.values():
        contacts.sort(key=lambda x: x.get("_score", 0), reverse=True)
        capped.extend(contacts[:max_per_firm])
    capped.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return capped


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------


def load_baseline(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        logger.warning("Baseline CSV not found: %s", csv_path)
        return []
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def baseline_stats(rows: list[dict]) -> dict:
    """Summarise baseline CSV."""
    firm_col = next(
        (k for k in (rows[0].keys() if rows else []) if "investor" in k.lower() or "company" in k.lower()),
        None,
    )
    firms = set(r.get(firm_col, "") for r in rows) if firm_col else set()
    with_email = sum(1 for r in rows if r.get("email", "").strip())
    types = Counter(r.get("_investor_type", r.get("investor_type", "Unknown")) for r in rows)
    match_paths = Counter(r.get("_match_path", "Unknown") for r in rows)
    return {
        "total_contacts": len(rows),
        "unique_firms": len(firms),
        "with_email": with_email,
        "top_investor_types": dict(types.most_common(8)),
        "top_match_paths": dict(match_paths.most_common(8)),
    }


# ---------------------------------------------------------------------------
# Overlap analysis
# ---------------------------------------------------------------------------


def compute_overlap(tool_results: list[dict], baseline: list[dict]) -> dict:
    """Compare tool results against baseline by investor name overlap."""
    tool_firms = set()
    for c in tool_results:
        name = (c.get("_investor_name") or c.get("company_name") or "").strip().lower()
        if name:
            tool_firms.add(name)

    baseline_firms = set()
    # Detect the firm column from baseline
    if baseline:
        firm_col = next(
            (k for k in baseline[0].keys() if "investor" in k.lower() or "company" in k.lower()),
            None,
        )
        for r in baseline:
            name = (r.get(firm_col, "") or "").strip().lower()
            if name:
                baseline_firms.add(name)

    overlap = tool_firms & baseline_firms
    tool_only = tool_firms - baseline_firms
    baseline_only = baseline_firms - tool_firms

    overlap_rate = len(overlap) / max(len(baseline_firms), 1) * 100
    return {
        "tool_firms": len(tool_firms),
        "baseline_firms": len(baseline_firms),
        "overlap_firms": len(overlap),
        "overlap_rate_pct": round(overlap_rate, 1),
        "tool_only_count": len(tool_only),
        "baseline_only_count": len(baseline_only),
        "overlap_sample": sorted(overlap)[:20],
        "baseline_only_sample": sorted(baseline_only)[:20],
        "tool_only_sample": sorted(tool_only)[:20],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_validation() -> dict:
    t_start = time.monotonic()
    logger.info("=== Grapeviine Phase 4 Validation ===")

    # Auth — _ensure_auth() is called lazily by query(), but do a warm-up query
    # to fail fast on bad credentials before starting the full pipeline.
    email, password = _load_credentials()
    client = IOClient(email=email, password=password)
    await client._ensure_auth()
    logger.info("Auth OK")

    # Resolve parameters
    sector_codes = resolve_sectors(SECTORS)
    investor_type_values = resolve_investor_types(INVESTOR_TYPES)
    stage_values = resolve_investment_types([DEAL_STAGE])
    deal_size_m = DEAL_SIZE / 1_000_000  # → 1.8

    logger.info("sector_codes=%s", sector_codes)
    logger.info("investor_type_values=%s", investor_type_values)
    logger.info("stage_values=%s", stage_values)
    logger.info("deal_size_m=%.2f", deal_size_m)

    # ── Phase 1: Broad investor pull ──────────────────────────────────────────
    all_investor_rows: list[dict] = []

    logger.info("Phase 1a: sector overlap query (%d sector codes)…", len(sector_codes))
    rows = await _fetch_investors_by_sector(client, sector_codes, investor_type_values, deal_size_m)
    logger.info("  → %d rows", len(rows))
    all_investor_rows.extend(rows)

    # Description keyword queries for the key auto-tech terms
    desc_keywords = ["automotive", "dealer", "dealership", "auto tech", "autotech", "mobility"]
    for kw in desc_keywords:
        logger.info("Phase 1b: description ilike '%s'…", kw)
        rows = await _fetch_investors_by_description(client, kw)
        logger.info("  → %d rows", len(rows))
        all_investor_rows.extend(rows)

    # Named firm queries
    logger.info("Phase 1c: named firm queries (%d firms)…", len(NAMED_FIRMS))
    for name in NAMED_FIRMS:
        rows = await _fetch_investors_by_name(client, name)
        logger.info("  %s → %d", name, len(rows))
        all_investor_rows.extend(rows)

    # Deal stage query
    logger.info("Phase 1d: deal stage query (%s)…", stage_values)
    rows = await _fetch_investors_by_stage(client, stage_values)
    logger.info("  → %d rows", len(rows))
    all_investor_rows.extend(rows)

    # Type-only fallback (since sectors are sparse for auto)
    if investor_type_values:
        logger.info("Phase 1e: type-only query…")
        rows = await _fetch_investors_by_type(client, investor_type_values, deal_size_m)
        logger.info("  → %d rows", len(rows))
        all_investor_rows.extend(rows)

    investor_map = _dedupe_investors(all_investor_rows)
    logger.info("Phase 1 complete: %d unique investors", len(investor_map))

    # ── Phase 2: Fetch persons ────────────────────────────────────────────────
    investor_ids = list(investor_map.keys())
    logger.info("Phase 2: fetching persons for %d investors…", len(investor_ids))
    all_persons = await _fetch_persons_for_investors(client, investor_ids)
    logger.info("  → %d persons fetched", len(all_persons))

    # ── Phase 3: Score and gate (strict mode) ────────────────────────────────
    logger.info("Phase 3 (strict): scoring and gating…")
    scored_strict = _score_and_gate(all_persons, investor_map, expanded=False, min_score=20)
    capped_strict = _cap_per_firm(scored_strict, max_per_firm=5)
    final_strict = capped_strict[:1000]
    logger.info("  strict: %d contacts passed", len(final_strict))

    # ── Phase 4: Score and gate (expanded mode) ───────────────────────────────
    logger.info("Phase 3 (expanded): scoring and gating…")
    scored_expanded = _score_and_gate(all_persons, investor_map, expanded=True, min_score=15)
    capped_expanded = _cap_per_firm(scored_expanded, max_per_firm=5)
    final_expanded = capped_expanded[:1000]
    logger.info("  expanded: %d contacts passed", len(final_expanded))

    # ── Baseline comparison ───────────────────────────────────────────────────
    baseline = load_baseline(BASELINE_CSV)
    logger.info("Baseline loaded: %d contacts", len(baseline))

    # Stats
    def tool_stats(results):
        unique_firms = len(set(c.get("_investor_name", "") for c in results))
        with_email = sum(1 for c in results if c.get("email"))
        with_phone = sum(1 for c in results if c.get("phone"))
        with_linkedin = sum(1 for c in results if c.get("linkedin_profile_url"))
        types = Counter(c.get("_investor_type", "Unknown") for c in results)
        paths = Counter(c.get("_match_path", "Unknown") for c in results)
        scores = sorted(set(c.get("_score", 0) for c in results))
        return {
            "total_contacts": len(results),
            "unique_firms": unique_firms,
            "with_email": with_email,
            "with_phone": with_phone,
            "with_linkedin": with_linkedin,
            "top_investor_types": dict(types.most_common(8)),
            "top_match_paths": dict(paths.most_common(10)),
            "score_range": [min(scores, default=0), max(scores, default=0)],
            "sample_contacts": [
                {k: v for k, v in c.items() if not k.startswith("_")} | {
                    "_score": c.get("_score"),
                    "_match_path": c.get("_match_path"),
                    "_investor_name": c.get("_investor_name"),
                    "_investor_type": c.get("_investor_type"),
                }
                for c in results[:25]
            ],
        }

    t_elapsed = time.monotonic() - t_start

    report = {
        "deal": "Grapeviine",
        "deal_description": "$1.8M SAFE | Auto dealer SaaS | Seed round",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(t_elapsed, 1),
        "parameters": {
            "role_keywords": ROLE_KEYWORDS,
            "firm_keywords": FIRM_KEYWORDS,
            "named_firms": NAMED_FIRMS,
            "sectors": SECTORS,
            "sector_codes_resolved": sector_codes,
            "investor_types": INVESTOR_TYPES,
            "investor_type_values_resolved": investor_type_values,
            "deal_size": DEAL_SIZE,
            "deal_size_m": deal_size_m,
            "deal_stage": DEAL_STAGE,
            "stage_values_resolved": stage_values,
        },
        "phase1_funnel": {
            "total_raw_investor_rows": len(all_investor_rows),
            "unique_investors_after_dedup": len(investor_map),
            "total_persons_fetched": len(all_persons),
        },
        "strict_mode": {
            "min_score": 20,
            "expanded": False,
            **tool_stats(final_strict),
        },
        "expanded_mode": {
            "min_score": 15,
            "expanded": True,
            **tool_stats(final_expanded),
        },
        "baseline": {
            "csv_path": str(BASELINE_CSV),
            **baseline_stats(baseline),
        },
        "overlap_strict": compute_overlap(final_strict, baseline),
        "overlap_expanded": compute_overlap(final_expanded, baseline),
        "diagnosis": {},
    }

    # ── Diagnosis ─────────────────────────────────────────────────────────────
    strict_count = report["strict_mode"]["total_contacts"]
    baseline_count = report["baseline"]["total_contacts"]
    overlap_rate = report["overlap_strict"]["overlap_rate_pct"]

    notes = []
    if strict_count < 100:
        notes.append(
            f"UNDER-MATCHING: strict mode returned only {strict_count} contacts vs "
            f"baseline {baseline_count}. Try expanded=True or broaden keywords."
        )
    elif strict_count > baseline_count * 3:
        notes.append(
            f"OVER-MATCHING: strict mode returned {strict_count} contacts vs baseline "
            f"{baseline_count}. Scoring thresholds may need tightening."
        )
    else:
        notes.append(
            f"VOLUME OK: {strict_count} contacts (strict) vs {baseline_count} baseline."
        )

    if overlap_rate < 30:
        notes.append(
            f"LOW FIRM OVERLAP: {overlap_rate}% firm overlap. Investor data may diverge "
            "from CapIQ baseline source (different universe)."
        )
    elif overlap_rate >= 60:
        notes.append(f"GOOD FIRM OVERLAP: {overlap_rate}% — results align with baseline firms.")
    else:
        notes.append(f"MODERATE FIRM OVERLAP: {overlap_rate}% — partial alignment.")

    report["diagnosis"]["notes"] = notes
    report["diagnosis"]["recommendation"] = (
        "Use expanded=True for Grapeviine — niche auto dealer SaaS seed deal "
        "benefits from looser matching to capture more generalist seed VCs."
        if strict_count < 200
        else "Strict mode yielding adequate volume. Review sample_contacts quality."
    )

    return report


def main():
    report = asyncio.run(run_validation())

    # Write to data/
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)

    logger.info("Report written to: %s", OUTPUT_PATH)

    # Print summary to stdout
    print("\n" + "=" * 70)
    print("GRAPEVIINE VALIDATION SUMMARY")
    print("=" * 70)
    print(f"Deal: {report['deal']} — {report['deal_description']}")
    print(f"Elapsed: {report['elapsed_seconds']}s")
    print()
    print("PHASE 1 FUNNEL")
    f = report["phase1_funnel"]
    print(f"  Raw investor rows:     {f['total_raw_investor_rows']:,}")
    print(f"  Unique investors:      {f['unique_investors_after_dedup']:,}")
    print(f"  Persons fetched:       {f['total_persons_fetched']:,}")
    print()
    print("STRICT MODE (min_score=20, expanded=False)")
    s = report["strict_mode"]
    print(f"  Contacts:              {s['total_contacts']:,}")
    print(f"  Unique firms:          {s['unique_firms']:,}")
    print(f"  With email:            {s['with_email']:,}")
    print(f"  With phone:            {s['with_phone']:,}")
    print(f"  With LinkedIn:         {s['with_linkedin']:,}")
    print(f"  Score range:           {s['score_range']}")
    print(f"  Top investor types:    {s['top_investor_types']}")
    print(f"  Top match paths:       {s['top_match_paths']}")
    print()
    print("EXPANDED MODE (min_score=15, expanded=True)")
    e = report["expanded_mode"]
    print(f"  Contacts:              {e['total_contacts']:,}")
    print(f"  Unique firms:          {e['unique_firms']:,}")
    print(f"  With email:            {e['with_email']:,}")
    print()
    print("BASELINE (grapeviine_FINAL.csv)")
    b = report["baseline"]
    print(f"  Total contacts:        {b['total_contacts']:,}")
    print(f"  Unique firms:          {b['unique_firms']:,}")
    print(f"  With email:            {b['with_email']:,}")
    print()
    print("FIRM OVERLAP (strict vs baseline)")
    o = report["overlap_strict"]
    print(f"  Tool firms:            {o['tool_firms']:,}")
    print(f"  Baseline firms:        {o['baseline_firms']:,}")
    print(f"  Overlap:               {o['overlap_firms']:,} ({o['overlap_rate_pct']}%)")
    print()
    print("DIAGNOSIS")
    for note in report["diagnosis"]["notes"]:
        print(f"  * {note}")
    print(f"  Recommendation: {report['diagnosis']['recommendation']}")
    print("=" * 70)
    print(f"\nFull report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
