"""Phase 4 Deal 1 Validation: Doosan Grid Tech ($70-80M BESS buyout).

Tests match_deal, match_deal_stage, search_descriptions, and find_similar_investors
logic directly against live Supabase data. No MCP server required — imports
internal modules and constructs a real IOClient.

Baseline: ~/Desktop/Deal-Investor-Research/1-Doosan/contacts/doosan_FINAL.csv
          (4,454 contacts)

Results written to:
    apps/investor-outbound-mcp/data/validation_doosan.json
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

# ── ensure project root on sys.path ──────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ── inject credentials before importing src.client ───────────────────────────
# client.py resolves _CONFIG_PATH as parents[2]/config/api_keys.json which
# maps to apps/config/api_keys.json (wrong). Inject real path via env vars
# so _load_credentials() picks them up without patching the module.
_REAL_KEYS = Path("config/api_keys.json")
if _REAL_KEYS.exists() and not (os.environ.get("IO_EMAIL") and os.environ.get("IO_PASSWORD")):
    import json as _json
    _creds = _json.loads(_REAL_KEYS.read_text()).get("supabase_investor_outreach", {})
    if _creds.get("email") and _creds.get("password"):
        os.environ["IO_EMAIL"] = _creds["email"]
        os.environ["IO_PASSWORD"] = _creds["password"]

from src.client import IOClient, QueryBuilder  # noqa: E402
from src.tools.deal_matching import (  # noqa: E402
    _dedupe_investors,
    _fetch_investors_by_description,
    _fetch_investors_by_name,
    _fetch_investors_by_sector,
    _fetch_investors_by_stage,
    _fetch_persons_for_investors,
    _score_and_gate_contacts,
    _cap_per_firm,
)
from src.scoring import score_contact  # noqa: E402
from src.sectors import resolve_sectors, resolve_investor_types, resolve_investment_types  # noqa: E402
from src.entities import INVESTOR_SELECT_SUMMARY  # noqa: E402

# ── constants ─────────────────────────────────────────────────────────────────

BASELINE_CSV = Path("data/baseline/1-Doosan/contacts/doosan_FINAL.csv")
OUTPUT_JSON = PROJECT_ROOT / "data" / "validation_doosan.json"

# Doosan parameters (from the task spec)
ROLE_KEYWORDS = [
    "energy", "infrastructure", "power", "utility", "grid", "storage",
    "battery", "renewable", "cleantech", "bess",
]
FIRM_KEYWORDS = [
    "energy", "infrastructure", "power", "renewable", "cleantech", "climate",
    "solar", "wind", "battery", "storage", "grid", "sustainability",
    "transition", "green", "industrial",
]
NAMED_FIRMS = [
    "ares", "brookfield", "macquarie", "kkr", "blackrock", "stonepeak",
    "eqt", "antin", "kayne anderson", "daiwa energy", "arclight",
    "energy capital", "ls power", "invenergy",
]
SECTORS = ["energy", "cleantech", "infrastructure"]
INVESTOR_TYPES = [
    "PE/Buyout", "Venture Capital", "Infrastructure", "Corporate Venture Capital",
    "Impact Investing",
]
DEAL_SIZE_USD = 75_000_000  # midpoint of $70-80M range
DEAL_SIZE_M = DEAL_SIZE_USD / 1_000_000  # 75.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────


def load_baseline() -> tuple[set[str], set[str]]:
    """Load doosan_FINAL.csv and return (baseline_emails, baseline_firms)."""
    emails: set[str] = set()
    firms: set[str] = set()
    if not BASELINE_CSV.exists():
        log.warning("Baseline CSV not found: %s", BASELINE_CSV)
        return emails, firms

    with BASELINE_CSV.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            email = (row.get("email") or "").strip().lower()
            if email:
                emails.add(email)
            company = (row.get("company_name") or "").strip().lower()
            if company:
                firms.add(company)

    log.info("Baseline: %d emails, %d firms from %s", len(emails), len(firms), BASELINE_CSV.name)
    return emails, firms


def top10_contacts(contacts: list[dict]) -> list[dict]:
    """Return top 10 contacts with display fields only."""
    out = []
    for c in contacts[:10]:
        out.append({
            "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
            "role": c.get("role"),
            "firm": c.get("_investor_name") or c.get("company_name"),
            "email": c.get("email"),
            "score": c.get("_score"),
            "match_path": c.get("_match_path"),
            "investor_type": c.get("_investor_type"),
        })
    return out


def overlap_analysis(contacts: list[dict], baseline_emails: set[str], baseline_firms: set[str]) -> dict:
    """Compare MCP results against baseline CSV."""
    found_emails = {(c.get("email") or "").lower() for c in contacts if c.get("email")}
    found_firms = {
        (c.get("_investor_name") or c.get("company_name") or "").lower()
        for c in contacts
    }

    # Email overlap (exact)
    email_overlap = found_emails & baseline_emails
    # Firm overlap (partial: check if any baseline firm name is substring of MCP firm or vice versa)
    firm_overlap: set[str] = set()
    for mcp_firm in found_firms:
        for base_firm in baseline_firms:
            if mcp_firm and base_firm and (base_firm in mcp_firm or mcp_firm in base_firm):
                firm_overlap.add(mcp_firm)
                break

    return {
        "mcp_contacts": len(contacts),
        "mcp_firms": len(found_firms),
        "mcp_with_email": sum(1 for c in contacts if c.get("email")),
        "baseline_emails": len(baseline_emails),
        "baseline_firms": len(baseline_firms),
        "email_overlap": len(email_overlap),
        "firm_overlap": len(firm_overlap),
        "email_overlap_pct": round(len(email_overlap) / max(len(baseline_emails), 1) * 100, 1),
        "firm_overlap_pct": round(len(firm_overlap) / max(len(baseline_firms), 1) * 100, 1),
        "overlapping_emails": sorted(email_overlap)[:20],
        "overlapping_firms": sorted(firm_overlap)[:30],
    }


# ── test runners ──────────────────────────────────────────────────────────────


async def test1_match_deal(client: IOClient, baseline_emails: set[str], baseline_firms: set[str]) -> dict:
    """Test 1: Full match_deal pipeline for Doosan (expanded=True)."""
    log.info("=== Test 1: match_deal (expanded=True) ===")
    t0 = time.monotonic()

    sector_codes = resolve_sectors(SECTORS)
    investor_type_values = resolve_investor_types(INVESTOR_TYPES)
    stage_values = resolve_investment_types(["buyout"])

    log.info("Resolved sectors: %s", sector_codes[:8])
    log.info("Resolved investor types: %s", investor_type_values[:6])
    log.info("Resolved buyout stage values: %s", stage_values[:6])

    # Phase 1: broad investor pull
    all_rows: list[dict] = []

    log.info("1a. Sector overlap query...")
    rows = await _fetch_investors_by_sector(client, sector_codes, investor_type_values or None, DEAL_SIZE_M)
    log.info("    → %d investors", len(rows))
    all_rows.extend(rows)

    log.info("1b. Description keyword: 'energy storage'...")
    rows = await _fetch_investors_by_description(client, "energy storage")
    log.info("    → %d investors", len(rows))
    all_rows.extend(rows)

    log.info("1c. Description keyword: 'battery storage'...")
    rows = await _fetch_investors_by_description(client, "battery storage")
    log.info("    → %d investors", len(rows))
    all_rows.extend(rows)

    log.info("1d. Named firm queries (%d firms)...", len(NAMED_FIRMS))
    for name in NAMED_FIRMS:
        rows = await _fetch_investors_by_name(client, name)
        if rows:
            log.info("    '%s' → %d investors", name, len(rows))
        all_rows.extend(rows)

    log.info("1e. Deal stage (buyout) query...")
    rows = await _fetch_investors_by_stage(client, stage_values[:5])  # cap to first 5 to avoid timeout
    log.info("    → %d investors", len(rows))
    all_rows.extend(rows)

    investor_map = _dedupe_investors(all_rows)
    log.info("Deduped investor pool: %d unique investors", len(investor_map))

    # Phase 2: fetch persons + score
    log.info("Phase 2: fetching persons for %d investors...", len(investor_map))
    investor_ids = list(investor_map.keys())
    all_persons = await _fetch_persons_for_investors(client, investor_ids)
    log.info("Persons fetched: %d", len(all_persons))

    # Score and gate
    scored = _score_and_gate_contacts(
        persons=all_persons,
        investor_map=investor_map,
        role_keywords=ROLE_KEYWORDS,
        firm_keywords=FIRM_KEYWORDS,
        named_firms=NAMED_FIRMS,
        expanded=True,
        min_score=20,
    )
    log.info("Contacts passing gate (expanded=True): %d", len(scored))

    capped = _cap_per_firm(scored, max_per_firm=5)
    final = capped[:1000]

    elapsed = time.monotonic() - t0
    unique_firms = len(set(c.get("_investor_name", "") for c in final))
    with_email = sum(1 for c in final if c.get("email"))

    path_counts: dict[str, int] = {}
    for c in final:
        p = c.get("_match_path", "?")
        path_counts[p] = path_counts.get(p, 0) + 1

    result = {
        "test": "match_deal (expanded=True)",
        "elapsed_s": round(elapsed, 2),
        "investors_scanned": len(investor_map),
        "persons_scored": len(all_persons),
        "contacts_passing_gate": len(scored),
        "contacts_after_cap": len(final),
        "unique_firms": unique_firms,
        "with_email": with_email,
        "match_paths": path_counts,
        "top10": top10_contacts(final),
        "overlap": overlap_analysis(final, baseline_emails, baseline_firms),
    }

    log.info(
        "Test 1 done: %d contacts / %d firms / %d with email (%.1fs)",
        len(final), unique_firms, with_email, elapsed,
    )
    return result


async def test2_match_deal_strict(client: IOClient, baseline_emails: set[str], baseline_firms: set[str]) -> dict:
    """Test 1b: match_deal with expanded=False (strict mode) for comparison."""
    log.info("=== Test 1b: match_deal (expanded=False, strict) ===")
    t0 = time.monotonic()

    sector_codes = resolve_sectors(SECTORS)
    investor_type_values = resolve_investor_types(INVESTOR_TYPES)

    all_rows: list[dict] = []
    rows = await _fetch_investors_by_sector(client, sector_codes, investor_type_values or None, DEAL_SIZE_M)
    all_rows.extend(rows)
    for name in NAMED_FIRMS:
        rows = await _fetch_investors_by_name(client, name)
        all_rows.extend(rows)

    investor_map = _dedupe_investors(all_rows)
    investor_ids = list(investor_map.keys())
    all_persons = await _fetch_persons_for_investors(client, investor_ids)

    scored = _score_and_gate_contacts(
        persons=all_persons,
        investor_map=investor_map,
        role_keywords=ROLE_KEYWORDS,
        firm_keywords=FIRM_KEYWORDS,
        named_firms=NAMED_FIRMS,
        expanded=False,
        min_score=20,
    )
    capped = _cap_per_firm(scored, max_per_firm=5)
    final = capped[:1000]

    elapsed = time.monotonic() - t0
    unique_firms = len(set(c.get("_investor_name", "") for c in final))
    path_counts: dict[str, int] = {}
    for c in final:
        p = c.get("_match_path", "?")
        path_counts[p] = path_counts.get(p, 0) + 1

    result = {
        "test": "match_deal (expanded=False, strict)",
        "elapsed_s": round(elapsed, 2),
        "investors_scanned": len(investor_map),
        "persons_scored": len(all_persons),
        "contacts_passing_gate": len(scored),
        "contacts_after_cap": len(final),
        "unique_firms": unique_firms,
        "with_email": sum(1 for c in final if c.get("email")),
        "match_paths": path_counts,
        "top10": top10_contacts(final),
        "overlap": overlap_analysis(final, baseline_emails, baseline_firms),
    }

    log.info("Test 1b done: %d contacts / %d firms (%.1fs)", len(final), unique_firms, elapsed)
    return result


async def test3_match_deal_stage(client: IOClient) -> dict:
    """Test 2: match_deal_stage for 'buyout'."""
    log.info("=== Test 2: match_deal_stage (stage='buyout') ===")
    t0 = time.monotonic()

    stage_values = resolve_investment_types(["buyout"])
    log.info("Buyout stage values: %s", stage_values)

    from src.tools.deal_matching import _fetch_investors_by_stage
    from src.entities import format_investor_summary

    all_rows: list[dict] = []
    for sv in stage_values:
        qb = (
            QueryBuilder("investors")
            .select(INVESTOR_SELECT_SUMMARY)
            .ilike("preferred_investment_types", f"*{sv}*")
            .gt("contact_count", 0)
            .neq("investor_status", "Acquired/Merged")
            .limit(2000)
        )
        rows, _ = await client.query(qb, count="estimated")
        log.info("  stage '%s' → %d investors", sv, len(rows))
        all_rows.extend(rows)

    deduped = _dedupe_investors(all_rows)
    investors = list(deduped.values())[:200]

    elapsed = time.monotonic() - t0
    type_counts: dict[str, int] = {}
    for inv in investors:
        t = inv.get("primary_investor_type") or "Unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    top_investors = []
    for inv in investors[:20]:
        top_investors.append({
            "id": inv.get("id"),
            "name": inv.get("investors"),
            "type": inv.get("primary_investor_type"),
            "check_min": inv.get("check_size_min"),
            "check_max": inv.get("check_size_max"),
            "contact_count": inv.get("contact_count"),
            "sectors": inv.get("sectors_array", [])[:5] if inv.get("sectors_array") else [],
        })

    result = {
        "test": "match_deal_stage (stage='buyout')",
        "elapsed_s": round(elapsed, 2),
        "stage_values_used": stage_values,
        "total_investors_found": len(deduped),
        "investors_returned": len(investors),
        "investor_type_breakdown": dict(sorted(type_counts.items(), key=lambda x: -x[1])[:15]),
        "top20_investors": top_investors,
    }

    log.info("Test 2 done: %d unique investors (%.1fs)", len(deduped), elapsed)
    return result


async def test4_search_descriptions(client: IOClient) -> dict:
    """Test 3: search_descriptions with keyword='energy storage'."""
    log.info("=== Test 3: search_descriptions (keyword='energy storage') ===")
    t0 = time.monotonic()

    rows = await _fetch_investors_by_description(client, "energy storage")
    elapsed = time.monotonic() - t0

    type_counts: dict[str, int] = {}
    for inv in rows:
        t = inv.get("primary_investor_type") or "Unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    top_investors = []
    for inv in rows[:20]:
        top_investors.append({
            "id": inv.get("id"),
            "name": inv.get("investors"),
            "type": inv.get("primary_investor_type"),
            "contact_count": inv.get("contact_count"),
            "sectors": inv.get("sectors_array", [])[:5] if inv.get("sectors_array") else [],
        })

    result = {
        "test": "search_descriptions (keyword='energy storage')",
        "elapsed_s": round(elapsed, 2),
        "investors_found": len(rows),
        "investor_type_breakdown": dict(sorted(type_counts.items(), key=lambda x: -x[1])[:10]),
        "top20_investors": top_investors,
    }

    log.info("Test 3 done: %d investors matching 'energy storage' (%.1fs)", len(rows), elapsed)
    return result


async def test5_find_similar_investors(client: IOClient) -> dict:
    """Test 4: find_similar_investors seeded with ArcLight Capital Partners (id=127852).

    ArcLight is an energy infrastructure PE firm — ideal seed for a BESS buyout.
    Uses Kayne Anderson Capital Advisors as secondary seed for the named-firm test.

    Key discovery from live probe:
    - RPC returns 'distance' (cosine distance, lower = more similar), NOT 'similarity'
    - 'target_investor_types' is NOT a valid param — only: query_embedding, search_limit,
      investor_types, min_investment_amount, max_investment_amount
    """
    log.info("=== Test 4: find_similar_investors (seed: ArcLight Capital Partners) ===")
    t0 = time.monotonic()

    # Seed: ArcLight Capital Partners — energy infrastructure PE, 42 contacts, id=127852
    SEED_INVESTOR_ID = 127852
    SEED_INVESTOR_NAME = "ArcLight Capital Partners"

    # Step 1: Confirm seed exists
    qb = (
        QueryBuilder("investors")
        .select("id,investors,primary_investor_type,contact_count")
        .eq("id", SEED_INVESTOR_ID)
        .limit(1)
    )
    rows, _ = await client.query(qb, count=None)
    seed = rows[0] if rows else {"id": SEED_INVESTOR_ID, "investors": SEED_INVESTOR_NAME}
    log.info("Seed: id=%d '%s' type='%s' contacts=%d",
             seed["id"], seed.get("investors"), seed.get("primary_investor_type"), seed.get("contact_count", 0))

    # Step 2: Get embedding
    log.info("Looking up embedding for investor_id=%d...", SEED_INVESTOR_ID)
    emb_qb = (
        QueryBuilder("investors_embeddings_3072")
        .select("investor_id,embedding")
        .eq("investor_id", SEED_INVESTOR_ID)
        .limit(1)
    )
    emb_rows, _ = await client.query(emb_qb, count=None)

    if not emb_rows:
        return {
            "test": "find_similar_investors (seed: ArcLight Capital Partners)",
            "error": f"No embedding found for investor_id={SEED_INVESTOR_ID}",
        }

    embedding_text = emb_rows[0].get("embedding", "")
    cleaned = embedding_text.strip().strip("[]")
    embedding_vector = [float(x.strip()) for x in cleaned.split(",")]
    log.info("Embedding parsed: %d dimensions", len(embedding_vector))

    # Step 3: Call RPC (valid params only — no target_investor_types)
    rpc_body = {
        "query_embedding": embedding_vector,
        "search_limit": 20,
        "investor_types": None,
        "min_investment_amount": None,
        "max_investment_amount": None,
    }

    log.info("Calling ai_search_with_ideal_investor RPC...")
    rpc_result = await client.rpc("ai_search_with_ideal_investor", rpc_body)

    elapsed = time.monotonic() - t0

    # RPC returns 'distance' (cosine distance, 0=identical, lower=more similar)
    # Convert to similarity: 1 - distance
    similar = []
    for item in (rpc_result or [])[:20]:
        raw_distance = item.get("distance")
        similarity_score = round(1.0 - float(raw_distance), 4) if raw_distance is not None else None
        similar.append({
            "investor_id": item.get("id"),
            "name": item.get("investors"),
            "type": item.get("primary_investor_type"),
            "similarity_score": similarity_score,
            "cosine_distance": raw_distance,
            "contact_count": item.get("contact_count"),
            "hq_location": item.get("hq_location"),
        })

    # Exclude the seed itself from display
    similar_excl_seed = [s for s in similar if s["investor_id"] != SEED_INVESTOR_ID]

    result = {
        "test": "find_similar_investors (seed: ArcLight Capital Partners)",
        "elapsed_s": round(elapsed, 2),
        "seed_investor_id": SEED_INVESTOR_ID,
        "seed_investor_name": SEED_INVESTOR_NAME,
        "seed_investor_type": seed.get("primary_investor_type"),
        "embedding_dimensions": len(embedding_vector),
        "similar_investors_found": len(similar_excl_seed),
        "top20_similar": similar_excl_seed,
        "note": "RPC field is 'distance' (cosine, lower=more similar). similarity_score = 1 - distance.",
    }

    log.info("Test 4 done: %d similar investors found (%.1fs)", len(similar_excl_seed), elapsed)
    return result


# ── main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    log.info("Loading baseline CSV...")
    baseline_emails, baseline_firms = load_baseline()

    log.info("Connecting to Investor Outbound Supabase...")
    async with IOClient.from_env() as client:
        results = {}

        # Test 1: match_deal expanded
        try:
            results["test1_match_deal_expanded"] = await test1_match_deal(
                client, baseline_emails, baseline_firms
            )
        except Exception as exc:
            log.exception("Test 1 failed")
            results["test1_match_deal_expanded"] = {"error": str(exc)}

        # Test 1b: match_deal strict (for comparison)
        try:
            results["test1b_match_deal_strict"] = await test2_match_deal_strict(
                client, baseline_emails, baseline_firms
            )
        except Exception as exc:
            log.exception("Test 1b failed")
            results["test1b_match_deal_strict"] = {"error": str(exc)}

        # Test 2: match_deal_stage
        try:
            results["test2_match_deal_stage"] = await test3_match_deal_stage(client)
        except Exception as exc:
            log.exception("Test 2 failed")
            results["test2_match_deal_stage"] = {"error": str(exc)}

        # Test 3: search_descriptions
        try:
            results["test3_search_descriptions"] = await test4_search_descriptions(client)
        except Exception as exc:
            log.exception("Test 3 failed")
            results["test3_search_descriptions"] = {"error": str(exc)}

        # Test 4: find_similar_investors
        try:
            results["test4_find_similar_investors"] = await test5_find_similar_investors(client)
        except Exception as exc:
            log.exception("Test 4 failed")
            results["test4_find_similar_investors"] = {"error": str(exc)}

    # Write output
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON.open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    log.info("Results written to %s", OUTPUT_JSON)

    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY — Doosan Grid Tech ($70-80M BESS Buyout)")
    print("=" * 70)

    t1 = results.get("test1_match_deal_expanded", {})
    if "error" not in t1:
        print(f"\nTest 1 — match_deal (expanded=True):")
        print(f"  Investors scanned:   {t1.get('investors_scanned', 'N/A')}")
        print(f"  Persons scored:      {t1.get('persons_scored', 'N/A')}")
        print(f"  Contacts passing:    {t1.get('contacts_passing_gate', 'N/A')}")
        print(f"  After cap (5/firm):  {t1.get('contacts_after_cap', 'N/A')}")
        print(f"  Unique firms:        {t1.get('unique_firms', 'N/A')}")
        print(f"  With email:          {t1.get('with_email', 'N/A')}")
        print(f"  Match paths:         {t1.get('match_paths', {})}")
        ov = t1.get("overlap", {})
        print(f"  Baseline overlap:    {ov.get('email_overlap', 0)} emails "
              f"({ov.get('email_overlap_pct', 0)}%), "
              f"{ov.get('firm_overlap', 0)} firms "
              f"({ov.get('firm_overlap_pct', 0)}%)")
        print(f"  Elapsed:             {t1.get('elapsed_s', 'N/A')}s")
        print(f"\n  Top 10 contacts:")
        for i, c in enumerate(t1.get("top10", []), 1):
            email_flag = "[email]" if c.get("email") else "[no email]"
            print(f"    {i:2d}. {c.get('name', '?'):<30s}  {c.get('role', '?')[:45]:<45s}  "
                  f"{c.get('firm', '?'):<30s}  score={c.get('score', '?')}  {email_flag}")

    t1b = results.get("test1b_match_deal_strict", {})
    if "error" not in t1b:
        print(f"\nTest 1b — match_deal (expanded=False, strict):")
        print(f"  Contacts after cap:  {t1b.get('contacts_after_cap', 'N/A')}")
        print(f"  Unique firms:        {t1b.get('unique_firms', 'N/A')}")
        print(f"  With email:          {t1b.get('with_email', 'N/A')}")
        ov1b = t1b.get("overlap", {})
        print(f"  Baseline overlap:    {ov1b.get('email_overlap', 0)} emails "
              f"({ov1b.get('email_overlap_pct', 0)}%)")

    t2 = results.get("test2_match_deal_stage", {})
    if "error" not in t2:
        print(f"\nTest 2 — match_deal_stage (stage='buyout'):")
        print(f"  Investors found:     {t2.get('total_investors_found', 'N/A')}")
        print(f"  Stage values used:   {t2.get('stage_values_used', [])[:4]}")
        top_types = list((t2.get("investor_type_breakdown") or {}).items())[:5]
        print(f"  Top investor types:  {top_types}")

    t3 = results.get("test3_search_descriptions", {})
    if "error" not in t3:
        print(f"\nTest 3 — search_descriptions (keyword='energy storage'):")
        print(f"  Investors found:     {t3.get('investors_found', 'N/A')}")
        top_types3 = list((t3.get("investor_type_breakdown") or {}).items())[:5]
        print(f"  Top investor types:  {top_types3}")

    t4 = results.get("test4_find_similar_investors", {})
    if "error" not in t4:
        print(f"\nTest 4 — find_similar_investors (seed: Kayne Anderson):")
        print(f"  Seed investor:       {t4.get('seed_investor_name', 'N/A')} (id={t4.get('seed_investor_id', 'N/A')})")
        print(f"  Embedding dims:      {t4.get('embedding_dimensions', 'N/A')}")
        print(f"  Similar found:       {t4.get('similar_investors_found', 'N/A')}")
        print(f"  Top 5 similar:")
        for s in t4.get("top20_similar", [])[:5]:
            print(f"    {s.get('name', '?'):<35s}  sim={s.get('similarity', '?'):.4f}  type={s.get('type', '?')}")
    elif t4.get("error"):
        print(f"\nTest 4 — find_similar_investors: {t4.get('error')}")

    print(f"\nFull results: {OUTPUT_JSON}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
