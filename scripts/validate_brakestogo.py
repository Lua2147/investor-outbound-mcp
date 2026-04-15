"""Phase 4, Deal 3 validation script — BrakesToGo ($2M SAFE, mobile brake repair).

Calls the same two-phase pipeline logic that `match_deal` uses (via direct imports,
not via the MCP server), then compares against the baseline CSV
(~/Desktop/Deal-Investor-Research/3-BrakesToGo/contacts/btg_FINAL.csv, 3,412 contacts).

Output: data/validation_brakestogo.json

Usage (from the investor-outbound-mcp directory):
    python3 scripts/validate_brakestogo.py

This is the hardest validation deal — $2M SAFE, niche keywords (franchise, auto).
expanded=True is CRITICAL to recover enough contacts.
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

# Make sure the package root is on sys.path when running as a script
_REPO_ROOT = Path(__file__).parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.client import IOClient
from src.scoring import passes_deal_relevance, score_contact
from src.sectors import resolve_investment_types, resolve_investor_types, resolve_sectors
from src.tools.deal_matching import (
    _cap_per_firm,
    _dedupe_investors,
    _fetch_investors_by_description,
    _fetch_investors_by_name,
    _fetch_investors_by_sector,
    _fetch_investors_by_stage,
    _fetch_investors_by_type,
    _fetch_persons_for_investors,
    _score_and_gate_contacts,
    _INVESTOR_BATCH_SIZE,
    _PERSONS_BATCH_LIMIT,
    _PERSON_MATCH_SELECT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("validate_brakestogo")

# ---------------------------------------------------------------------------
# BrakesToGo match_deal parameters (from task spec)
# ---------------------------------------------------------------------------

BTG_PARAMS = {
    "role_keywords": [
        "franchise", "consumer service", "home service", "auto", "automotive",
        "mobile service", "repair", "family office", "direct invest",
        "lower middle market", "buyout", "private equity", "growth equity",
        "consumer", "services", "operations",
    ],
    "firm_keywords": [
        "franchise", "auto", "automotive", "brake", "repair", "family office",
        "family capital", "consumer", "service", "home service",
    ],
    "named_firms": [
        "roark capital", "driven brands", "valvoline", "safelite",
        "neighborly", "authority brands", "blue sage", "suntx",
        "platinum equity", "concentric equity", "trp capital",
    ],
    "sectors": [],          # NULL — skip sector filter for family offices
    "investor_types": [
        "PE/Buyout", "Family Office - Single", "Venture Capital",
        "Growth/Expansion",
    ],
    "deal_size": 2_000_000,
    "expanded": True,       # CRITICAL for this deal
    "deal_stage": "seed",
    # tool defaults
    "max_per_firm": 5,
    "max_results": 1000,
    "min_score": 20,
}

BASELINE_CSV = Path.home() / "Desktop/Deal-Investor-Research/3-BrakesToGo/contacts/btg_FINAL.csv"
OUTPUT_PATH = _REPO_ROOT / "data" / "validation_brakestogo.json"


# ---------------------------------------------------------------------------
# Baseline loader
# ---------------------------------------------------------------------------

def load_baseline(csv_path: Path) -> list[dict]:
    """Load the baseline CSV and return a list of row dicts."""
    if not csv_path.exists():
        logger.warning("Baseline CSV not found at %s", csv_path)
        return []
    rows = []
    with csv_path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    logger.info("Loaded baseline: %d contacts from %s", len(rows), csv_path)
    return rows


def baseline_emails(baseline: list[dict]) -> set[str]:
    return {r["email"].lower() for r in baseline if r.get("email")}


def baseline_names(baseline: list[dict]) -> set[tuple[str, str]]:
    return {
        (r.get("first_name", "").lower().strip(), r.get("last_name", "").lower().strip())
        for r in baseline
        if r.get("first_name") and r.get("last_name")
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(client: IOClient) -> dict:
    params = BTG_PARAMS
    t0 = time.monotonic()

    deal_size_m = params["deal_size"] / 1_000_000  # → 2.0
    sector_codes = resolve_sectors(params["sectors"]) if params["sectors"] else []
    investor_type_values = resolve_investor_types(params["investor_types"]) if params["investor_types"] else []
    stage_values = resolve_investment_types([params["deal_stage"]]) if params["deal_stage"] else []

    logger.info("deal_size_m=%.1f sector_codes=%s investor_types=%s stage_values=%s",
                deal_size_m, sector_codes, investor_type_values, stage_values)

    # ── Phase 1: Broad investor pull ─────────────────────────────────────────
    all_investor_rows: list[dict] = []

    # 1a. Sector overlap (skip — sectors is empty for this deal)
    if sector_codes:
        rows = await _fetch_investors_by_sector(client, sector_codes, investor_type_values or None, deal_size_m)
        logger.info("1a sector: %d rows", len(rows))
        all_investor_rows.extend(rows)

    # 1b. Description keyword queries — use firm_keywords as description search terms
    description_keywords = params["firm_keywords"]
    for kw in description_keywords:
        rows = await _fetch_investors_by_description(client, kw)
        logger.info("1b description '%s': %d rows", kw, len(rows))
        all_investor_rows.extend(rows)

    # 1c. Named firm queries
    for name in params["named_firms"]:
        rows = await _fetch_investors_by_name(client, name)
        logger.info("1c named_firm '%s': %d rows", name, len(rows))
        all_investor_rows.extend(rows)

    # 1d. Deal stage query
    if stage_values:
        rows = await _fetch_investors_by_stage(client, stage_values)
        logger.info("1d stage (%s): %d rows", stage_values, len(rows))
        all_investor_rows.extend(rows)

    # 1e. Type-only query (no sectors for this deal — family offices + PE)
    if not sector_codes and investor_type_values:
        rows = await _fetch_investors_by_type(client, investor_type_values, deal_size_m)
        logger.info("1e type-only (%s): %d rows", investor_type_values, len(rows))
        all_investor_rows.extend(rows)

    raw_investor_count = len(all_investor_rows)
    investor_map = _dedupe_investors(all_investor_rows)
    logger.info("Phase 1 complete: %d raw rows → %d unique investors", raw_investor_count, len(investor_map))

    if not investor_map:
        return {"error": "No investors matched Phase 1 criteria"}

    # ── Phase 2: Fetch persons + score/gate ──────────────────────────────────
    investor_ids = list(investor_map.keys())
    logger.info("Fetching persons for %d investors (batch size %d)...", len(investor_ids), _INVESTOR_BATCH_SIZE)

    all_persons = await _fetch_persons_for_investors(client, investor_ids)
    logger.info("Persons fetched: %d total", len(all_persons))

    scored = _score_and_gate_contacts(
        persons=all_persons,
        investor_map=investor_map,
        role_keywords=params["role_keywords"],
        firm_keywords=params["firm_keywords"],
        named_firms=params["named_firms"],
        expanded=params["expanded"],
        min_score=params["min_score"],
    )
    logger.info("After scoring/gating: %d passed", len(scored))

    capped = _cap_per_firm(scored, params["max_per_firm"])
    final = capped[:params["max_results"]]
    logger.info("After per-firm cap (%d) and max_results (%d): %d", params["max_per_firm"], params["max_results"], len(final))

    elapsed = time.monotonic() - t0
    return {
        "contacts": final,
        "pipeline_stats": {
            "raw_investor_rows": raw_investor_count,
            "unique_investors": len(investor_map),
            "persons_fetched": len(all_persons),
            "passed_gating": len(scored),
            "after_firm_cap": len(capped),
            "final": len(final),
            "elapsed_seconds": round(elapsed, 2),
        },
    }


def analyse_match_paths(contacts: list[dict]) -> dict:
    """Break down contacts by which gate 6 path matched them."""
    path_counts: Counter = Counter()
    for c in contacts:
        path_counts[c.get("_match_path", "unknown")] += 1
    return dict(path_counts.most_common())


def analyse_investor_types(contacts: list[dict]) -> dict:
    type_counts: Counter = Counter()
    for c in contacts:
        type_counts[c.get("_investor_type", "unknown")] += 1
    return dict(type_counts.most_common())


def analyse_email_status(contacts: list[dict]) -> dict:
    status_counts: Counter = Counter()
    for c in contacts:
        status_counts[c.get("email_status", "unknown")] += 1
    return dict(status_counts.most_common())


def analyse_top_firms(contacts: list[dict], top_n: int = 20) -> list[dict]:
    firm_contacts: dict[str, list] = defaultdict(list)
    for c in contacts:
        firm = c.get("_investor_name") or c.get("company_name") or "Unknown"
        firm_contacts[firm].append(c)
    result = []
    for firm, conts in sorted(firm_contacts.items(), key=lambda x: -len(x[1])):
        result.append({
            "firm": firm,
            "count": len(conts),
            "investor_type": conts[0].get("_investor_type"),
            "top_roles": [c.get("role") for c in conts[:3]],
        })
    return result[:top_n]


def compare_with_baseline(
    new_contacts: list[dict],
    baseline: list[dict],
) -> dict:
    """Compare MCP results against the baseline extraction."""
    baseline_emails_set = baseline_emails(baseline)
    baseline_names_set = baseline_names(baseline)

    new_emails = {(c.get("email") or "").lower() for c in new_contacts if c.get("email")}
    new_names = {
        (
            (c.get("first_name") or "").lower().strip(),
            (c.get("last_name") or "").lower().strip(),
        )
        for c in new_contacts
        if c.get("first_name") and c.get("last_name")
    }

    overlap_by_email = baseline_emails_set & new_emails
    overlap_by_name = baseline_names_set & new_names

    # Coverage = what % of baseline do we recover
    email_coverage_pct = (
        round(len(overlap_by_email) / len(baseline_emails_set) * 100, 1)
        if baseline_emails_set else 0.0
    )
    name_coverage_pct = (
        round(len(overlap_by_name) / len(baseline_names_set) * 100, 1)
        if baseline_names_set else 0.0
    )

    # New contacts not in baseline
    new_only_emails = new_emails - baseline_emails_set
    new_only_names = new_names - baseline_names_set

    return {
        "baseline_count": len(baseline),
        "baseline_with_email": len(baseline_emails_set),
        "baseline_unique_names": len(baseline_names_set),
        "new_count": len(new_contacts),
        "new_with_email": len(new_emails),
        "overlap_by_email": len(overlap_by_email),
        "overlap_by_name": len(overlap_by_name),
        "email_coverage_pct": email_coverage_pct,
        "name_coverage_pct": name_coverage_pct,
        "new_only_emails": len(new_only_emails),
        "new_only_names": len(new_only_names),
        "verdict": _coverage_verdict(email_coverage_pct, name_coverage_pct, len(new_contacts), len(baseline)),
    }


def _coverage_verdict(email_pct: float, name_pct: float, new_count: int, baseline_count: int) -> str:
    if new_count == 0:
        return "FAIL: zero contacts returned"
    size_ratio = new_count / baseline_count if baseline_count else 0
    if email_pct >= 80 or name_pct >= 70:
        return "PASS: strong coverage"
    if email_pct >= 50 or name_pct >= 50 or size_ratio >= 0.5:
        return "PARTIAL: acceptable coverage — review top firms"
    return f"INVESTIGATE: low coverage (email={email_pct}%, name={name_pct}%, size_ratio={size_ratio:.2f})"


def build_sample_contacts(contacts: list[dict], n: int = 20) -> list[dict]:
    """Return n representative contacts with key fields for spot-checking."""
    sample = []
    for c in contacts[:n]:
        sample.append({
            "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
            "role": c.get("role"),
            "firm": c.get("_investor_name") or c.get("company_name"),
            "investor_type": c.get("_investor_type"),
            "email": c.get("email"),
            "email_status": c.get("email_status"),
            "linkedin": c.get("linkedin_profile_url"),
            "score": c.get("_score"),
            "match_path": c.get("_match_path"),
        })
    return sample


def _inject_credentials() -> None:
    """Inject IO_EMAIL / IO_PASSWORD from monorepo api_keys.json if not already set.

    The client.py _CONFIG_PATH resolves to the app-level config dir which doesn't exist.
    The actual credentials live at the monorepo root: config/api_keys.json.
    This function injects them into env vars so IOClient.from_env() finds them.
    """
    import os
    if os.environ.get("IO_EMAIL") and os.environ.get("IO_PASSWORD"):
        return  # already set

    monorepo_config = _REPO_ROOT.parents[1] / "config" / "api_keys.json"
    if not monorepo_config.exists():
        raise FileNotFoundError(
            f"api_keys.json not found at {monorepo_config}. "
            "Set IO_EMAIL and IO_PASSWORD env vars manually."
        )

    with monorepo_config.open() as fh:
        keys = json.load(fh)

    creds = keys.get("supabase_investor_outreach", {})
    email = creds.get("email")
    password = creds.get("password")
    if not email or not password:
        raise ValueError(
            "supabase_investor_outreach key missing email or password in api_keys.json"
        )

    os.environ["IO_EMAIL"] = email
    os.environ["IO_PASSWORD"] = password
    logger.info("Loaded IO credentials from %s", monorepo_config)


async def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _inject_credentials()

    logger.info("=== BrakesToGo Phase 4 Validation ===")
    logger.info("Parameters: %s", json.dumps(BTG_PARAMS, indent=2))

    # Load baseline
    baseline = load_baseline(BASELINE_CSV)

    # Run pipeline
    async with IOClient.from_env() as client:
        result = await run_pipeline(client)

    if "error" in result:
        logger.error("Pipeline failed: %s", result["error"])
        OUTPUT_PATH.write_text(json.dumps({"error": result["error"]}, indent=2))
        sys.exit(1)

    contacts = result["contacts"]
    pipeline_stats = result["pipeline_stats"]

    # Analyse results
    match_paths = analyse_match_paths(contacts)
    investor_type_breakdown = analyse_investor_types(contacts)
    email_breakdown = analyse_email_status(contacts)
    top_firms = analyse_top_firms(contacts, top_n=20)
    comparison = compare_with_baseline(contacts, baseline)
    sample = build_sample_contacts(contacts, n=25)

    # Score distribution
    scores = [c.get("_score", 0) for c in contacts]
    score_dist = {
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "mean": round(sum(scores) / len(scores), 1) if scores else None,
        "p50": sorted(scores)[len(scores) // 2] if scores else None,
        ">=50": sum(1 for s in scores if s >= 50),
        ">=35": sum(1 for s in scores if s >= 35),
        "20-34": sum(1 for s in scores if 20 <= s < 35),
    }

    # Key stats
    with_email = sum(1 for c in contacts if c.get("email"))
    with_phone = sum(1 for c in contacts if c.get("phone"))
    with_linkedin = sum(1 for c in contacts if c.get("linkedin_profile_url"))
    deliverable_emails = sum(1 for c in contacts if c.get("email_status") == "deliverable")
    unique_firms = len({c.get("_investor_name") or c.get("company_name") for c in contacts})

    # Build output document
    output = {
        "meta": {
            "deal": "BrakesToGo",
            "deal_description": "$2M SAFE | Mobile on-demand brake repair | 10+ yrs operating",
            "validation_phase": "Phase 4 — Deal 3",
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "params": BTG_PARAMS,
        "pipeline_stats": pipeline_stats,
        "contact_stats": {
            "total": len(contacts),
            "unique_firms": unique_firms,
            "with_email": with_email,
            "with_phone": with_phone,
            "with_linkedin": with_linkedin,
            "deliverable_emails": deliverable_emails,
        },
        "score_distribution": score_dist,
        "match_path_breakdown": match_paths,
        "investor_type_breakdown": investor_type_breakdown,
        "email_status_breakdown": email_breakdown,
        "top_firms": top_firms,
        "baseline_comparison": comparison,
        "sample_contacts": sample,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Written to %s", OUTPUT_PATH)

    # Print summary to stdout
    print("\n" + "=" * 60)
    print("BRAKESTOGO VALIDATION — RESULTS SUMMARY")
    print("=" * 60)
    print(f"Pipeline: {pipeline_stats['unique_investors']} investors → "
          f"{pipeline_stats['persons_fetched']} persons → "
          f"{pipeline_stats['passed_gating']} passed gating → "
          f"{pipeline_stats['final']} final")
    print(f"Elapsed: {pipeline_stats['elapsed_seconds']}s")
    print()
    print(f"Contacts: {len(contacts)} across {unique_firms} firms")
    print(f"  With email:    {with_email}")
    print(f"  Deliverable:   {deliverable_emails}")
    print(f"  With phone:    {with_phone}")
    print(f"  With LinkedIn: {with_linkedin}")
    print()
    print(f"Score distribution: min={score_dist['min']} max={score_dist['max']} "
          f"mean={score_dist['mean']} p50={score_dist['p50']}")
    print(f"  Score >=50: {score_dist['>=50']}")
    print(f"  Score >=35: {score_dist['>=35']}")
    print(f"  Score 20-34: {score_dist['20-34']}")
    print()
    print("Match paths:")
    for path, cnt in match_paths.items():
        print(f"  {path}: {cnt}")
    print()
    print("Investor types:")
    for itype, cnt in list(investor_type_breakdown.items())[:8]:
        print(f"  {itype}: {cnt}")
    print()
    print(f"Baseline comparison (baseline={comparison['baseline_count']} contacts):")
    print(f"  New contacts: {comparison['new_count']}")
    print(f"  Email overlap: {comparison['overlap_by_email']} ({comparison['email_coverage_pct']}% coverage)")
    print(f"  Name overlap:  {comparison['overlap_by_name']} ({comparison['name_coverage_pct']}% coverage)")
    print(f"  New only (email): {comparison['new_only_emails']}")
    print(f"  Verdict: {comparison['verdict']}")
    print()
    print("Top 10 firms:")
    for firm_data in top_firms[:10]:
        print(f"  [{firm_data['count']}] {firm_data['firm']} ({firm_data['investor_type']}) — "
              f"{firm_data['top_roles'][0] if firm_data['top_roles'] else ''}")
    print()
    print(f"Output: {OUTPUT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
