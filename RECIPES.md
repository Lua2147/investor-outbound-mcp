# Investor Outbound MCP — Recipes

Copy-paste tool call examples for common investor targeting scenarios. All
parameters come directly from Phase 4 validation against 5 live deals.
Numbers in "Expected output" sections are real — measured against the 234K
investor / 1.8M contact database.

**CRITICAL: The two-phase pipeline is always active.** `match_deal` never
returns raw sector-overlap results. Every contact passes a 6-gate scoring
pipeline (score threshold, role validation, junk filter, firm-name-as-role
detection, seniority/investment-function gate, 6-path deal relevance check)
before it reaches you. A call that returns 18,335 contacts passing gating is
real signal, not noise.

**check_size note:** The DB stores check sizes in MILLIONS USD. Pass raw
dollar amounts to `match_deal` (e.g., `deal_size=70000000`). The tool divides
by 1M internally. Pass pre-divided values (in millions) only to
`match_preferences` (`check_size_min`, `check_size_max`).

**Version:** Phase 4 validated | **Deals tested:** 5 | **Tool count:** 30

---

## Table of Contents

1. [Find investors for a $70M energy infrastructure buyout](#1-find-investors-for-a-70m-energy-infrastructure-buyout)
2. [Find VCs for a $7M healthcare SaaS Series A](#2-find-vcs-for-a-7m-healthcare-saas-series-a)
3. [Find family offices for a $2M SAFE](#3-find-family-offices-for-a-2m-safe)
4. [Find seed investors for auto-tech SaaS](#4-find-seed-investors-for-auto-tech-saas)
5. [Build LP list for a $250M multi-strategy fund](#5-build-lp-list-for-a-250m-multi-strategy-fund)
6. [Find 50 investors similar to a known firm](#6-find-50-investors-similar-to-a-known-firm)
7. [Get outreach-ready contacts for 100 investors](#7-get-outreach-ready-contacts-for-100-investors)
8. [Find coverage gaps across multiple active deals](#8-find-coverage-gaps-across-multiple-active-deals)
9. [Import conference attendee list and match](#9-import-conference-attendee-list-and-match)
10. [Clean stale contacts from an existing list](#10-clean-stale-contacts-from-an-existing-list)

---

## 1. Find investors for a $70M energy infrastructure buyout

**Scenario:** Doosan Grid Tech — $70-80M full buyout, battery energy storage
systems (BESS), utility-scale systems integrator with proprietary power plant
controller software. Target: Infrastructure PE firms, energy-focused growth
equity, power & utilities strategics.

**When to use this recipe:**
- Buyout deal with a clear energy / infrastructure thesis
- Named strategic acquirers exist (ArcLight, Ares, KKR, Brookfield, Macquarie)
- Deal size warrants filtering out seed-stage investors

### Tool call

```python
match_deal(
    role_keywords=[
        "energy", "infrastructure", "power", "utilities", "grid",
        "storage", "battery", "renewable", "buyout", "acquisition",
        "portfolio", "invest"
    ],
    firm_keywords=[
        "energy", "infrastructure", "power", "utility", "grid",
        "storage", "battery", "renewable", "cleantech", "climate"
    ],
    named_firms=[
        "ares", "brookfield", "macquarie", "kkr", "blackrock",
        "global infrastructure partners", "stonepeak", "eqt",
        "antin infrastructure", "ifm investors", "cdpq",
        "omers infrastructure", "energy capital partners",
        "arclight capital"
    ],
    sectors=["energy", "infrastructure", "green_energy", "clean_tech"],
    investor_types=["pe", "infrastructure", "asset manager"],
    deal_size=70000000,
    deal_stage="buyout",
    description_keywords=["energy storage", "battery", "renewable energy"],
    expanded=True,
    max_per_firm=5,
    max_results=1000,
)
```

### Expected output shape

```json
{
  "contacts": [...],
  "stats": {
    "total_contacts": 1000,
    "unique_firms": 505,
    "investors_scanned": 11946,
    "persons_scored": 148194,
    "with_email": 820,
    "with_phone": 641,
    "with_linkedin": 899,
    "expanded_mode": true,
    "min_score": 20,
    "max_per_firm": 5
  }
}
```

**Real Phase 4 numbers (expanded=True):** 18,335 contacts passing gating
before firm cap, capped to 1,000. 11,946 investors scanned, 148,194 persons
scored. 505 unique firms. 820 contacts with email. Top match paths: E (632),
A (161), C (95), F1 (94), B (18).

**Strict mode (expanded=False):** 11,044 contacts passing gating. 5,477
investors scanned, 42,518 persons scored. 492 unique firms. 808 contacts with
email. 18.0% email overlap with the segment_v2.py baseline (678 emails).

### Sample top contacts (from validation)

| Name | Role | Firm | Email | Score | Path |
|------|------|------|-------|-------|------|
| Justin Campeau | Senior Managing Director, Co-Portfolio Manager, Energy Infrastructure | Kayne Anderson Capital Advisors | [email] | 65 | A |
| Michael Schimmel | Senior Managing Director, Portfolio Manager, Energy Infrastructure Credit | Kayne Anderson Capital Advisors | [email] | 65 | A |
| Raj Agrawal | Partner, Global Head of Infrastructure | Kohlberg Kravis Roberts (KKR) | [email] | 65 | A |
| Rory O'Connor | Managing Director, Global Co-CIO & Head of Europe, Renewable Power | BlackRock | [email] | 65 | A |
| Kevin Nobels | Managing Director, Infrastructure and Energy Capital | Macquarie Group | [email] | 65 | A |

### Tips / gotchas

- `expanded=True` adds Path F1/F2 (senior professionals at any investment firm
  in scope), which nearly doubles the passing contact count (18,335 vs 11,044).
  Use it for niche infrastructure deals where tight keyword matching would miss
  generalist PE teams with active energy mandates.
- Path A = role keyword direct hit (strongest signal). Path E = keyword in
  investor name/description. Filter to `_match_path == "A"` for the highest
  conviction contacts if you need a shorter list.
- Daiwa Energy & Infrastructure returned 3 contacts with score 65 but null
  email. They still appear in the list — use `io_enrich_priorities` to find
  their LinkedIn URLs for manual outreach.
- description_keywords trigger separate ilike queries on the `description`
  field (97% coverage). "energy storage" returned 117 investors; "battery"
  returned 284. Each adds new firms the sector overlap query misses.
- `deal_size=70000000` filters on `check_size_min <= 70 AND check_size_max >= 70`
  in millions. Only 10% of investors have check size data — remove this filter
  if the result count is too low.

---

## 2. Find VCs for a $7M healthcare SaaS Series A

**Scenario:** IntraLogic Health Solutions — $7M Series A, MedTech SaaS,
RFID-based surgical instrument tracking. Target: Healthcare growth equity,
MedTech/hospital workflow strategics, healthcare-focused VC.

**When to use this recipe:**
- Series A / growth equity raise for a healthcare software company
- Named strategics exist (Stryker, Medtronic, Fortive/Censis)
- Need to surface both dedicated healthcare VCs and generalist growth equity
  with healthcare portfolio companies

### Tool call

```python
match_deal(
    role_keywords=[
        "healthcare", "medical", "life sciences", "medtech", "health tech",
        "surgical", "hospital", "biotech", "pharma", "health",
        "clinical", "digital health", "series a", "growth"
    ],
    firm_keywords=[
        "health", "medical", "life sciences", "medtech", "biotech",
        "pharma", "clinical", "surgical", "hospital", "wellness"
    ],
    named_firms=[
        "orbimed", "welsh carson", "bain capital", "thoma bravo",
        "vista equity", "venrock", "nea", "foresite", "8vc",
        "stryker", "medtronic", "steris", "fortive",
        "johnson & johnson", "becton dickinson", "intuitive surgical"
    ],
    sectors=["health_care", "healthtech", "biotech"],
    investor_types=["vc", "pe", "corporate venture capital", "growth"],
    deal_size=7000000,
    deal_stage="series a",
    description_keywords=["medtech", "health tech", "surgical"],
    expanded=False,
    max_per_firm=5,
    max_results=1000,
)
```

### Expected output shape

```json
{
  "contacts": [...],
  "stats": {
    "total_contacts": 1000,
    "unique_firms": 467,
    "investors_scanned": 8207,
    "persons_scored": 83565,
    "with_email": 786,
    "with_phone": 737,
    "with_linkedin": 884,
    "expanded_mode": false
  }
}
```

**Real Phase 4 numbers:** 7,910 contacts passing gating, capped to 1,000.
8,207 unique investors, 83,565 persons scored. 467 unique firms. 786 with
email (78.6% coverage). Score range 45-55, avg 45.5.

Named firm hit counts from validation: bain capital (11), nea (290), thoma
bravo (2), vista equity (3), venrock (4), foresite (3), 8vc (3), stryker (5),
medtronic (5), steris (3). "nea" returned 290 because the 3-letter name matches
many investors — use `named_firms=["nea"]` deliberately or the 4-char rule
switches to substring matching.

Match path breakdown: E (906), A (63), C (31). This deal is heavily path-E
dominated — most relevant contacts are at firms whose name/description contains
healthcare keywords, not contacts whose role title contains deal keywords. That
is expected for a Series A healthcare deal.

### Sample top contacts (from validation)

| Name | Role | Firm | Email | Score |
|------|------|------|-------|-------|
| Mark Carter | Managing Director, Co-Head, North America Healthcare Group | TA Associates Management | [email] | 55 |
| Josko Bobanovic | Partner, Industrial Biotech Strategy | Sofinnova Partners | [email] | 55 |
| Michael Krel | Partner, Industrial Biotechnology Strategy | Sofinnova Partners | [email] | 55 |
| Tunde Oshinowo | VP, Portfolio Growth, Healthcare | Sandbox Industries | [email] | 55 |
| Tina Huang | Managing Director of Healthcare Investment | Yonghua Capital | [email] | 55 |

### Tips / gotchas

- `nea` is 3 characters. The tool uses word-start matching (`nea*`) for names
  shorter than 4 chars, which avoids matching "lineage", "cornea", etc. You
  will still get all NEA entities.
- `search_descriptions(keyword="medtech")` returned 259 investors with
  "medtech" in description. Run this as a discovery pass first if you want
  firm names before committing to a full `match_deal` run (takes ~165s vs ~42s
  for a description-only scan).
- `deal_size=7000000` is a $7M deal. Only investors whose `check_size_min <=
  7.0` AND `check_size_max >= 7.0` pass the check size filter. Welsh Carson
  returned 0 hits — their check size is likely above $7M. Remove `deal_size`
  to include them.
- To surface the Fortive / Censis strategic angle specifically, add `"fortive"`
  and `"censis"` to `named_firms`. Fortive returned 0 in validation because
  their check size data exceeds the filter; dropping `deal_size` fixes this.

---

## 3. Find family offices for a $2M SAFE

**Scenario:** BrakesToGo — $2M SAFE, mobile on-demand brake repair, asset-light
shopless model, 10+ years operating. Target: Texas-based family offices,
lower middle market PE focused on home services / franchise / consumer services,
auto aftermarket strategics. NOT venture-stage investors.

**When to use this recipe:**
- Micro-deal ($1M-$5M equity or SAFE)
- Family office is the primary target (sector data sparse — use `expanded=True`)
- Specific named strategics exist (Roark Capital, Driven Brands, Valvoline)
- `sectors=[]` (no sector filter) because family office sector coverage is thin

### Tool call

```python
match_deal(
    role_keywords=[
        "franchise", "consumer service", "home service", "auto",
        "automotive", "mobile service", "repair", "family office",
        "direct invest", "lower middle market", "buyout",
        "private equity", "growth equity", "consumer", "services",
        "operations"
    ],
    firm_keywords=[
        "franchise", "auto", "automotive", "brake", "repair",
        "family office", "family capital", "consumer", "service",
        "home service"
    ],
    named_firms=[
        "roark capital", "driven brands", "valvoline", "safelite",
        "neighborly", "authority brands", "blue sage", "suntx",
        "platinum equity", "concentric equity", "trp capital"
    ],
    sectors=[],
    investor_types=[
        "PE/Buyout", "Family Office - Single", "Venture Capital",
        "Growth/Expansion"
    ],
    deal_size=2000000,
    expanded=True,
    deal_stage="seed",
    max_per_firm=5,
    max_results=1000,
    min_score=20,
)
```

### Expected output shape

```json
{
  "contacts": [...],
  "stats": {
    "total_contacts": 1000,
    "unique_firms": 560,
    "with_email": 873,
    "with_phone": 707,
    "with_linkedin": 834,
    "expanded_mode": true
  }
}
```

**Real Phase 4 numbers:** 23,214 raw investor rows, 15,470 unique investors.
219,518 persons fetched, 21,077 passing gating, 7,691 after firm cap, 1,000
final. 560 unique firms. 873 with email, 547 deliverable emails.

Score distribution: min 45, max 75, mean 50.1. 492 contacts scored ≥50. Match
path A (505), E (303), F1 (160), C (28), B (4). Path F1 contributed 160
contacts — these are senior investment professionals at family offices with
relevant sector keywords in the firm name, caught by the expanded loosening.

Investor type breakdown: PE/Buyout (252), Venture Capital (168),
Wealth Management/RIA (114), Family Office (78), Holding Company (65),
Investment Bank (50), Corporate Investor (41).

### Sample top contacts (from validation)

| Name | Role | Firm | Email | Score |
|------|------|------|-------|-------|
| Benjamin Prawdzik | VP, Consumer and Financial & Business Services Verticals, North America PE | Bain Capital | [email] | 75 |
| Charles Miller-Jones | Managing Director, Private Equity Services business unit | Partners Group | [email] | 75 |
| Nicolas Sanson | Managing Director & Head of Automotive & Mobility Investment Banking | Societe Generale CIB | [email] | 65 |
| Kirk Gutmann | Vice President Automotive Strategy | Bessemer Venture Partners | [email] | 65 |
| Vipul Amin | Managing Director, U.S. Buyout, Carlyle Equity Opportunity Fund | The Carlyle Group | [email] | 65 |

### Tips / gotchas

- `sectors=[]` is intentional. When sectors is empty the tool falls back to
  a type-only investor pull instead of a sector-overlap pull. Family offices
  have sparse sector tagging — a sector filter would drop 80%+ of them.
- `expanded=True` is mandatory for family office deals. Strict mode (expanded=
  False) would cut Path F1 (160 contacts) and miss many family office
  principals whose roles don't contain keyword hits.
- The tool returned contacts at Roark Capital (Kreg Nichols, 5 contacts, score
  65) even though the email was undeliverable. Contacts appear regardless of
  email status; use `io_outreach_ready_contacts` afterward to filter to
  deliverable-only for the send sequence.
- Baseline comparison was low (3.5% email overlap). This is expected — the
  segment_v2.py baseline for BrakesToGo was built with very different search
  logic. The MCP found 770 new emails not in the baseline.
- Add Texas geography filtering for family office prioritization:
  `io_search_investors(investor_types=["family office"], geography="Texas")`
  to build a focused FO sub-list first.

---

## 4. Find seed investors for auto-tech SaaS

**Scenario:** Grapeviine — $1.8M SAFE note, $5M cap, automotive dealer SaaS
for website lead conversion. Target: Seed/early-stage VC with auto-tech thesis,
angel investors, auto dealer software strategics (CDK Global, Cox Automotive).

**When to use this recipe:**
- Seed / pre-Series A deal
- Niche sector (auto-tech) with dedicated named VCs
- Strategics are the top prize (CDK Global, Cox Automotive, Toyota Ventures)
- Broad SaaS/software sector needed to capture generalist seed VCs

### Tool call

```python
match_deal(
    role_keywords=[
        "automotive", "auto tech", "autotech", "dealer", "mobility",
        "vehicle", "seed", "angel", "early stage", "venture",
        "saas", "software", "technology"
    ],
    firm_keywords=[
        "automotive", "auto", "dealer", "mobility", "vehicle",
        "seed", "angel", "accelerator", "incubator", "early stage"
    ],
    named_firms=[
        "automotive ventures", "autotech ventures", "toyota ventures",
        "gm ventures", "motus ventures", "cdk global", "cox automotive",
        "first round capital", "y combinator", "canvas ventures"
    ],
    sectors=["automotive", "software", "technology"],
    investor_types=[
        "Venture Capital", "Angel (individual)",
        "Corporate Venture Capital", "Accelerator/Incubator"
    ],
    deal_size=1800000,
    deal_stage="seed",
    expanded=False,
    max_per_firm=5,
    max_results=1000,
)
```

### Expected output shape

```json
{
  "contacts": [...],
  "stats": {
    "total_contacts": 1000,
    "unique_firms": 606,
    "with_email": 594,
    "with_phone": 537,
    "with_linkedin": 912
  }
}
```

**Real Phase 4 numbers (strict mode):** 18,887 raw investor rows, 11,431
unique investors, 119,762 persons fetched. Final 1,000 contacts across 606
unique firms. 594 with email, 912 with LinkedIn.

Score range 45-65. Match path breakdown: A (839), E (134), C (24), B (3). This
is the highest Path A concentration of all 5 deals — automotive and SaaS
keywords are distinctive enough that role-title matching dominates.

Investor type breakdown: Venture Capital (568), Accelerator/Incubator (157),
Corporate Venture Capital (62), PE/Buyout (36), Impact Investing (29).

Sector codes the tool resolves from `["automotive", "software", "technology"]`:
`automotive`, `digitaltechnology`, `enterprisesoftware`, `informationtechnology`,
`informationtechnologyservices`, `mobility`, `software`, `software_saas`,
`technology`, `transports`.

### Sample top contacts (from validation)

| Name | Role | Firm | Email | Score |
|------|------|------|-------|-------|
| Eliud Mungai | Pre-seed B2B SaaS Investor & Limited Partner | Startup Wise Guys | (null) | 65 |
| Jerry Lomax | Vice President at Chevron Technology Ventures | Chevron Technology Ventures | [email] | 65 |
| Jo Corkran | Co-CEO, Managing Partner, Deal Flow Lead NY | Golden Seeds Ventures | [email] | 65 |
| Danny Yoon | Venture Partner | FuturePlay | [email] | 55 |

### Tips / gotchas

- The `sectors` list triggers a sector-overlap pull from the 234K investors
  table. "automotive" as a sector code maps to the `automotive` tag (only ~20K
  investors) — combine with broader codes like `technology` and `software_saas`
  to avoid missing generalist seed VCs.
- Angel investors have lower sector tagging coverage. If your angel hit count
  is low, re-run with `sectors=[]` and `investor_types=["Angel (individual)"]`
  to do a type-only pull.
- `y combinator` and `first round capital` in `named_firms` will match by firm
  name substring (both > 4 chars). Their contacts will always pass the named-
  firm gate (Path B) regardless of role keywords.
- For corporate strategics (CDK, Cox Automotive), add `description_keywords=
  ["automotive software", "dealer management"]` to catch firms that describe
  themselves as automotive technology companies but are tagged under generic
  sectors.
- `expanded=False` is appropriate here — automotive keywords are specific enough
  that expanded mode would add noise from generalist firms.

---

## 5. Build LP list for a $250M multi-strategy fund

**Scenario:** Future Fund One — $250M fund raise, 60% NNN real estate + 20%
QSR (Swig) + 20% algorithmic Bitcoin. Target: Real estate LPs, bitcoin/crypto
allocators, wealth managers with alternatives appetite.

**When to use this recipe:**
- Fund raise targeting allocators rather than operating company investors
- Multi-asset mandate (need to cover real estate, crypto, and institutional
  wealth management in one pass)
- No deal_size filter (fund raise — check size not meaningful)

### Tool call

```python
match_deal(
    role_keywords=[
        "crypto", "bitcoin", "blockchain", "digital asset", "web3",
        "defi", "real estate", "reit", "net lease", "nnn",
        "triple net", "property", "commercial real estate",
        "franchise", "qsr", "wealth", "advisory", "capital markets",
        "alternatives", "allocation"
    ],
    firm_keywords=[
        "crypto", "bitcoin", "blockchain", "digital asset", "web3",
        "real estate", "reit", "realty", "property", "net lease",
        "wealth", "advisory"
    ],
    named_firms=[
        "galaxy digital", "grayscale", "pantera capital", "paradigm",
        "coinbase", "bitwise", "realty income", "spirit realty",
        "national retail properties", "caz investments",
        "evercore", "jll", "starwood", "tpg"
    ],
    sectors=["blockchain", "real estate"],
    investor_types=[
        "Venture Capital", "Hedge Fund", "Real Estate",
        "Family Office - Single", "PE/Buyout", "Wealth Management/RIA"
    ],
    deal_size=None,
    expanded=False,
    max_per_firm=5,
    max_results=1000,
)
```

### Expected output shape

```json
{
  "contacts": [...],
  "stats": {
    "total_contacts": 1000,
    "unique_firms": 536,
    "with_email": 912,
    "with_phone": 867,
    "with_linkedin": 895,
    "with_good_email": 572
  }
}
```

**Real Phase 4 numbers:** 10,239 raw investor rows, 8,313 unique investors,
76,374 persons fetched. 4,637 passing gating (before firm cap). Capped at
1,000. 536 unique firms. 912 with email (91.2% — highest of all 5 deals).

Match path breakdown: A (546), C (316), E (91), B (47). The large Path C share
(316) reflects contacts at sector-matched real estate and blockchain firms where
role keyword specificity was lower — their sector array placement put them in
scope, and they passed seniority/investment function gating.

Investor type distribution: Real Estate (645), PE/Buyout (204), Hedge Fund
(91), Wealth Management/RIA (28). Real estate investors dominate because the
`real_estate` sector code maps to 114K investors in the database.

Description-only scan results: "bitcoin" → 88 investors, "net lease" → 32
investors, "triple net" → 4 investors.

Baseline comparison: 4,637 contacts passing gating vs baseline of 6,916.
67.0% coverage (uncapped). PASS verdict.

### Sample top contacts (from validation)

| Name | Role | Firm | Email | Score |
|------|------|------|-------|-------|
| Brian Kim | Senior MD, Global COO Core+ Real Estate & Head of Acquisitions, BREIT | Blackstone | [email] | 75 |
| Cary Carpenter | MD, Commercial RE Capital Markets, Trading & Syndication | Starwood Property Trust | [email] | 75 |
| Coler Yoakam | Senior MD & Co-leader, Corporate Capital Markets & Single-Tenant Net Lease | JLL | [email] | 65 |
| Ryan Rohloff | Senior MD, Capital Markets Advisory | Evercore Group | [email] | 65 |
| Peter Sorrentino | Senior MD, Co-Lead Private Capital Markets & Head of Private Placements | Evercore Group | [email] | 65 |

### Tips / gotchas

- `deal_size=None` is intentional for fund raises. The check size filter
  (`check_size_min <= deal_size_m <= check_size_max`) only has 10% population
  coverage. For a $250M fund raise it would filter out most of the relevant
  real estate and PE investors. Omit it.
- The `sectors=["blockchain", "real estate"]` combination generates two separate
  sector overlap pulls. Real estate produces ~114K investor candidates; blockchain
  produces ~30K. The pipeline deduplicates before gating.
- The gate breakdown showed 42,152 contacts cut for "no deal relevance" and
  28,240 cut for score below minimum. This is the pipeline working correctly —
  the 76,374 raw persons included many junior staff at real estate firms.
- `search_descriptions(keyword="net lease")` returns 32 investors. Running this
  before the full `match_deal` call is a good way to identify named net-lease
  specialists to add to `named_firms`.
- For the Bitcoin/crypto LP angle specifically, supplement with
  `io_search_investors(investor_types=["hedge fund"], keyword="bitcoin")` to
  get a focused crypto-allocator sub-list.

---

## 6. Find 50 investors similar to a known firm

**Scenario:** You know ArcLight Capital Partners (investor_id=127852) is a
strong fit for the Doosan Grid Tech deal. Find 50 investors with similar
investment thesis, sector focus, and deal profile.

**When to use this recipe:**
- You have one confirmed good fit and want to expand the universe
- The deal is niche enough that keyword search alone is missing relevant firms
- You want to explore adjacent investor categories (e.g., infra PE → energy
  growth equity → energy debt)

### Step 1: Get the investor_id

If you know the name but not the ID, use `io_get_investor`:

```python
io_get_investor(name="arclight capital")
# Returns: investor_id=127852, type="Infrastructure", hq="Boston, MA"
```

### Step 2: Find similar investors

```python
find_similar_investors(
    investor_id=127852,
    limit=50,
    investor_types=None,  # None = all types; pass ["pe", "infrastructure"] to narrow
)
```

### Expected output shape

```json
[
  {
    "investor_id": 15329,
    "name": "Ultra Capital",
    "primary_investor_type": "Impact Investing",
    "similarity_score": 0.79,
    "cosine_distance": 0.21,
    "contact_count": 5,
    "hq_location": "Philadelphia, PA",
    "description": "..."
  },
  ...
]
```

**Real Phase 4 numbers:** ArcLight Capital (id=127852) returned 14 similar
investors within a 0.21-0.23 cosine distance range (similarity 0.77-0.79).
Note: only 291K of 234K investors have embeddings — the result set is bounded
by embedding availability, not the full 234K.

Top 5 similar investors from validation:

| Name | Type | Similarity | Location | Contacts |
|------|------|-----------|----------|---------|
| Ultra Capital | Impact Investing | 0.790 | Philadelphia, PA | 5 |
| Graylight Partners | PE/Buyout | 0.785 | San Francisco, CA | 2 |
| Argo Infrastructure Partners | Asset Manager | 0.784 | New York, NY | 9 |
| ArcLight Clean Transition II | SPAC | 0.782 | Boston, MA | 1 |
| Artemis Capital Partners | PE/Buyout | 0.782 | Boston, MA | 11 |

Other notable matches: SCF Partners (id=127988, 18 contacts), EnCap Investments
(id=128258, 33 contacts), Amberjack Capital Partners (id=131920, 9 contacts).

### Step 3: Feed similar investors into match_deal

```python
# Extract names from the similarity results
similar_firm_names = [
    "ultra capital", "argo infrastructure partners",
    "artemis capital partners", "scf partners",
    "encap investments", "amberjack capital partners",
    "arroyo energy investment partners", "shorelight partners"
]

# Add them as named_firms in your deal call
match_deal(
    role_keywords=["energy", "infrastructure", "power"],
    firm_keywords=["energy", "infrastructure"],
    named_firms=similar_firm_names,
    sectors=["energy", "infrastructure"],
    ...
)
```

### Tips / gotchas

- The embedding covers investor descriptions + sector/type metadata — it does
  NOT capture deal history or portfolio companies. Two infrastructure PE firms
  with different deal histories but similar descriptions will appear similar.
- `cosine_distance` (lower = more similar) is the raw RPC field. The tool
  converts it to `similarity_score = 1 - cosine_distance` for readability.
- ArcLight Clean Transition II (SPAC) appeared as #4 similar — expected, it
  is an ArcLight-branded vehicle. SPACs in the similarity results are typically
  affiliated entities or direct clones of the seed firm.
- 14 similar investors returned (vs 50 requested) for ArcLight. Low embedding
  density in the Infrastructure type category means fewer embeddings to compare
  against. Use `investor_types=None` to search all types and get more results.
- For the best expansion results, run `find_similar_investors` on the 3-5
  firms you already know are strong fits, union the results, then pass the
  combined names list to `match_deal` as `named_firms`.

---

## 7. Get outreach-ready contacts for 100 investors

**Scenario:** You have a list of 100 investor IDs from a `match_deal` call.
Before loading them into your outreach sequence, you want to filter to only
contacts with verified deliverable emails, then assess quality grades, and then
check channel coverage for investors where email is sparse.

**When to use this recipe:**
- Pre-launch quality check before loading into Instantly or HeyReach
- You need to know which contacts have phone/LinkedIn as email backup
- You want to grade the list (A/B/C/D) before deciding campaign strategy

### Step 1: Extract investor_ids from a match_deal result

```python
# After calling match_deal, collect unique investor IDs from the result
contacts = result["data"]["contacts"]
investor_ids = list({c["investor"] for c in contacts if c.get("investor")})
# Typically 467-606 unique IDs from a 1000-contact match_deal call
```

### Step 2: Get outreach-ready contacts (hard filters)

```python
io_outreach_ready_contacts(
    investor_ids=investor_ids[:100]  # chunk if needed; tool handles 100-ID batches internally
)
```

**Hard filters applied:** `good_email=true`, `email_free=false`,
`email_disposable=false`, `last_bounce_type IS NULL`.

### Expected output

Returns contacts only where all four conditions are met. For the Doosan deal
(820 contacts with email out of 1,000), expect ~500-600 passing all hard
filters. For IntraLogic (786 with email), expect ~400-500.

### Step 3: Assess quality grades

```python
io_assess_contact_quality(investor_ids=investor_ids[:100])
```

Returns per-contact grades (A/B/C/D) and aggregate counts:

| Grade | Criteria | Expected % |
|-------|----------|-----------|
| A | good_email=true AND email_score > 80 | ~30-40% of contacts with email |
| B | email_status='deliverable' AND email_score > 50 | ~15-20% |
| C | email_status='risky' OR email_score ≤ 50 | ~20-30% |
| D | undeliverable / bounced / no email | ~20-25% |

From IntraLogic validation: 643 contacts had `good_email=true` out of 786
with email (81.8% good-email rate within emailed contacts).

### Step 4: Check channel coverage for low-email investors

```python
io_channel_coverage(investor_ids=investor_ids[:100])
```

Returns per-investor counts of contacts with email, phone, and LinkedIn. Use
this to identify investors where email coverage is thin (< 2 contacts) but
LinkedIn is available — those are candidates for LinkedIn outreach via HeyReach.

### Tips / gotchas

- `io_outreach_ready_contacts` chunks investor_ids to 100 per PostgREST query
  internally. You can pass the full list (e.g., 500 IDs) — the tool handles
  chunking.
- Grade D contacts (undeliverable/bounced) still appear in `match_deal` results
  — the deal matching pipeline does not filter on email status. Always run this
  quality gate before uploading to a sending tool.
- BrakesToGo validation showed 547 deliverable emails out of 873 with email
  (62.7% deliverable rate). This is below the 78.6% seen in IntraLogic.
  Consumer service deals attract more personal email addresses with higher
  "unknown" rates.
- For contacts with null email but a LinkedIn URL, use `io_enrich_priorities`
  to get a ranked list sorted by seniority score — these are the highest ROI
  enrichment targets.

---

## 8. Find coverage gaps across multiple active deals

**Scenario:** You have 3 active deals (Doosan energy buyout, IntraLogic
healthcare SaaS, BrakesToGo consumer services). You want to: (a) find investors
that are relevant to 2+ deals so you can prioritize them across the pipeline,
and (b) identify which investor types and geographies are underrepresented in
each deal's coverage.

**When to use this recipe:**
- Multi-deal pipeline management
- Identifying high-value investors to cultivate across multiple mandates
- Finding blind spots before committing to an outreach sequence

### Part A: Cross-deal investors

```python
io_find_cross_deal_investors(
    deals=[
        {
            "label": "doosan_energy_buyout",
            "sectors": ["energy", "infrastructure"],
            "investor_types": ["pe", "infrastructure", "asset manager"],
            "description_keywords": ["energy storage"],
            "check_size_min_dollars": 50000000,
            "check_size_max_dollars": 600000000,
        },
        {
            "label": "intralogic_healthcare_saas",
            "sectors": ["health_care", "healthtech"],
            "investor_types": ["vc", "pe", "growth"],
            "description_keywords": ["medtech", "health tech"],
        },
        {
            "label": "brakestogo_consumer_safe",
            "sectors": [],
            "investor_types": ["pe", "family office"],
            "description_keywords": ["franchise", "consumer services"],
        },
    ],
    min_deal_matches=2,
)
```

Returns investors appearing in at least 2 of the 3 deal searches. Each result
includes `deal_match_count` and `matched_deals` so you know which combination
triggered the match. Investors matching all 3 are your highest-priority
relationship-building targets.

**What to expect:** Large generalist PE firms (Carlyle, KKR, Warburg Pincus,
Bain Capital) tend to appear across all 3 because they cover energy, healthcare,
and consumer verticals. Family offices with broad mandates appear in the
healthcare + consumer overlap.

### Part B: Coverage gap analysis (per deal)

```python
# Run for the Doosan deal
io_deal_coverage_gaps(
    sectors=["energy", "infrastructure"],
    investor_types=["pe", "infrastructure"],
    description_keywords=["energy storage"],
    check_size_min_dollars=50000000,
    check_size_max_dollars=600000000,
)
```

Returns three lists:
- `zero_investor_types`: Investor types that return 0 investors for this deal
- `zero_geographies`: Geographies with 0 investors for this deal
- `zero_sectors`: Sector codes with 0 investors for this deal

Plus `covered_*` lists with non-zero counts.

**What to expect for an energy/infrastructure PE deal:**
- `zero_investor_types`: likely Accelerator/Incubator, University, Not-for-Profit VC
- `zero_geographies`: likely smaller markets (Netherlands, Switzerland for an
  energy deal) or regions you haven't added geography filters for
- `covered_investor_types`: PE/Buyout, Infrastructure, Asset Manager, Hedge Fund

### Tips / gotchas

- `io_find_cross_deal_investors` does NOT run the full 6-gate gating pipeline.
  It queries investors (not contacts) by sector/type/keyword. Use it for
  investor-level prioritization, then run `match_deal` on the overlap firms to
  get scored contacts.
- `min_deal_matches=2` means "relevant to at least 2 deals." Use
  `min_deal_matches=3` for a tighter list when you have 3+ deals active.
- The tool accepts up to 10 deals per call. Beyond that, call it multiple times
  with subsets and union the results in your own code.
- `io_deal_coverage_gaps` probes 14 investor types, 10 geographies, and 15
  sectors (lightweight count queries, no full data fetch). The entire call runs
  in seconds rather than the 2-5 minute `match_deal` calls.
- If `zero_geographies` includes "United States" for an energy deal, it means
  no US investors survive your base filters PLUS the US geography constraint.
  This signals your `check_size` filter is too tight — US infrastructure PE
  firms often have wide check size ranges that don't match the DB's 10%
  populated field.

---

## 9. Import conference attendee list and match

**Scenario:** You have a list of 200 firm names from a conference attendee
sheet (e.g., Infrastructure Investor Global Summit). You want to find each
firm in the database, get their top contacts, and identify which ones are
relevant to an active deal.

**When to use this recipe:**
- Post-conference warm lead enrichment
- Existing relationship list where you have firm names but not structured data
- CRM import from any source where only company name is available

### Step 1: Batch lookup by firm name

```python
batch_firm_lookup(
    firm_names=[
        "arclight capital partners",
        "macquarie infrastructure",
        "ares management",
        "brookfield asset management",
        "energy capital partners",
        # ... up to 200 names
    ],
    max_contacts_per_firm=5,
)
```

The tool performs ilike substring matches for each name against the `investors`
table. Names >= 4 characters use substring matching; names < 4 use word-start
matching.

### Expected output shape

```json
{
  "matched_firms": [
    {
      "query_name": "arclight capital partners",
      "investor_id": 127852,
      "investor_name": "ArcLight Capital Partners",
      "investor_type": "Infrastructure",
      "hq_location": "Boston, MA",
      "contact_count": 9,
      "top_contacts": [
        {
          "id": 123456,
          "name": "...",
          "role": "Managing Partner",
          "email": "...",
          "score": 55
        }
      ]
    }
  ],
  "unmatched_firms": ["...", "..."],
  "stats": {
    "total_input": 200,
    "matched": 183,
    "unmatched": 17
  }
}
```

Typical match rate: 85-92% for professionally formatted firm names. Unmatched
entries are usually abbreviations, subsidiaries, or alternate brand names.

### Step 2: For unmatched firms, try email domain lookup

```python
# If you have attendee email addresses, try domain lookup instead
lookup_by_email_domain(domain="arclight.com")
# Returns all persons with email domain "arclight.com"
```

### Step 3: Score the matched contacts against your active deal

```python
# After batch_firm_lookup, extract investor IDs of matched firms
matched_ids = [m["investor_id"] for m in result["matched_firms"]]

# Run match_deal with these as named_firms (use the actual names returned)
matched_names = [m["investor_name"].lower() for m in result["matched_firms"][:50]]

match_deal(
    role_keywords=["energy", "infrastructure", "buyout"],
    firm_keywords=["energy", "infrastructure"],
    named_firms=matched_names,
    sectors=["energy"],
    investor_types=["pe", "infrastructure"],
    expanded=True,
)
```

### Tips / gotchas

- The tool processes firm names in chunks of 50 (internal limit). A 200-name
  list triggers 4 sequential PostgREST queries.
- Firm names with common words ("Capital", "Partners", "Group") will match
  multiple investors. The tool returns up to 5 investor matches per name query
  (`_BATCH_FIRM_INVESTOR_LIMIT=5`). Review the `investor_name` returned to
  confirm it is the right entity.
- Conference attendee lists often include fund names, management companies, and
  individual names mixed together. Pre-filter to firms only before calling
  `batch_firm_lookup`. Use `batch_person_lookup` for individual names.
- For matching individual attendees by email:
  ```python
  batch_person_lookup(
      emails=["[email]", "[email]", ...]
  )
  ```
- The top contacts returned by `batch_firm_lookup` are scored with
  `score_contact(role)` but NOT run through the full 6-gate pipeline. They are
  raw seniority-ranked contacts. If you need deal-relevance filtering, feed the
  investor IDs into `match_deal` as `named_firms`.

---

## 10. Clean stale contacts from an existing list

**Scenario:** You have a CSV of 2,000 contacts built 6 months ago. Before
your next campaign, you want to identify contacts whose email status is now
undeliverable, find recently-updated investor records, and surface LinkedIn-
only contacts as enrichment targets.

**When to use this recipe:**
- Pre-campaign hygiene check
- Quarterly refresh of an existing investor contact database
- Finding contacts that now have emails after being LinkedIn-only

### Step 1: Identify stale contacts by email

```python
# You have person IDs from your existing list
# Look up their current email status
batch_person_lookup(
    emails=["contact1@firm.com", "contact2@firm.com", ...],  # existing email list
)
```

The response includes current `email_status`, `good_email`, `email_score`, and
`last_bounce_type` for each matched contact. Contacts where `email_status =
"undeliverable"` or `last_bounce_type IS NOT NULL` should be suppressed.

### Step 2: Check full investor freshness

```python
io_investor_freshness(
    limit=50,
    investor_types=["pe", "infrastructure"],
)
```

Returns investors ordered by `updated_at DESC`. Recently updated investor
records often have refreshed contact lists — useful for finding new hires at
target firms.

### Step 3: Surface LinkedIn-only contacts for enrichment

```python
# For a set of investor IDs you care about
io_enrich_priorities(
    investor_ids=[127852, 127988, 128258, 131920, ...]  # your target firm IDs
)
```

Returns contacts with `linkedin_profile_url IS NOT NULL` but `email IS NULL`,
ranked by seniority score. These are the highest-ROI enrichment targets: you
can reach them via LinkedIn InMail while an enrichment service adds their email.

### Step 4: Grade the remaining contacts

```python
io_assess_contact_quality(investor_ids=[...])
```

Returns per-contact A/B/C/D grades. Suppress grade D, warm up grade A/B first,
treat grade C as secondary send.

### Email status values and what to do with them

| Status | Count (estimated) | Action |
|--------|-------------------|--------|
| deliverable | ~47% of all contacts | Send |
| unknown | ~47% | Test with a small batch first |
| undeliverable | ~3% | Suppress |
| risky | ~2% | Send with caution; watch bounce rate |

### Tips / gotchas

- `io_outreach_ready_contacts` applies `last_bounce_type IS NULL` as a hard
  filter. If a contact has bounced in a previous campaign and Instantly/Apollo
  recorded it in the `last_bounce_type` field, they will not appear. This is
  the correct behavior — do not override it.
- `email_score` is a numeric deliverability score (0-100). Contacts with
  `email_status="unknown"` but `email_score > 80` are usually safe to send to.
  Grade B catches this case.
- Contacts with `good_email=true` are the gold standard. From the IntraLogic
  validation, 643 out of 786 emailed contacts had `good_email=true` (81.8%).
  Focus your A/B test on the `good_email=false` cohort to measure risk.
- The `domain` column on the `persons` table stores the email domain (e.g.,
  `kaynecapital.com`). Use `lookup_by_email_domain(domain="kaynecapital.com")`
  to find all persons at a firm even if you only have one known email address.
- There is no "last contacted" or "replied" field in this database. Cadence
  tracking lives in your outreach tool (Instantly, HeyReach). Use this MCP for
  contact discovery and quality assessment; use your outreach tool for
  engagement tracking.

---

## Appendix A: match_deal parameter reference

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `role_keywords` | list[str] | required | Matched against contact role/title. Strongest signal. |
| `firm_keywords` | list[str] | required | Matched against investor/company name. |
| `named_firms` | list[str] | required | Always-pass firms. Case-insensitive substring match. |
| `sectors` | list[str] | None | Human-readable. Resolved to DB codes via sector map. Pass `[]` for family office searches. |
| `investor_types` | list[str] | None | Human-readable. Resolved to DB enum values. |
| `deal_size` | float | None | Raw dollar amount (e.g., 70000000). Divided by 1M internally. Only 10% of investors have check size populated — omit for fund raises. |
| `geography` | str | None | ilike on preferred_geography field (33% coverage). |
| `description_keywords` | list[str] | None | Each triggers a separate ilike query on description (97% coverage). Powerful supplement to sector filtering. |
| `deal_stage` | str | None | Human-readable. Resolves to preferred_investment_types values. Examples: "seed", "buyout", "growth", "series a". |
| `expanded` | bool | False | When True, loosens Gate 6 to admit any senior investment professional (Path F1/F2). Use for niche deals and family office searches. |
| `max_per_firm` | int | 5 | Maximum contacts returned per investor firm. |
| `max_results` | int | 1000 | Global cap on returned contacts. |
| `min_score` | int | 20 | Minimum score threshold for Gate 1. Lowering below 20 increases noise significantly. |

---

## Appendix B: Match path meanings

| Path | Meaning | Example contact |
|------|---------|----------------|
| A | role_keywords hit on contact role title | "Senior Managing Director, Energy Infrastructure" |
| B | named_firms hit — investor name matches a named firm | Any contact at KKR when "kkr" is in named_firms |
| C | firm_keywords hit on investor name | "Renewable Energy Capital" when "renewable" in firm_keywords |
| E | investor description/keyword match | Contact at firm whose description contains "energy storage" |
| F1 | expanded: senior investment professional (role-level) | "Managing Partner" at any in-scope firm |
| F2 | expanded: investment function general catch | "Investment Analyst" at a named firm |

Higher path priority order: A > B > C > E > F1 > F2. Filter to paths A and B
for maximum conviction. Paths E and F1 add volume at lower precision.

---

## Appendix C: Sector codes quick reference

Top 20 sector codes by investor population (for `sectors` parameter):

```
fin_services        453K investors    software_saas      138K
fin_invest          345K              real_estate        114K
technology          239K              private_equity     101K
business_services   180K              health_care         87K
agnostic            154K              ai_ml               83K
industrials          77K              fintech             65K
green_energy         64K              clean_tech          47K
energy               39K              edtech              39K
healthtech           37K              agritech            31K
blockchain           30K              biotech             29K
```

Human-readable inputs are resolved by `resolve_sectors()`. Common mappings:

| Human input | Resolved codes |
|-------------|----------------|
| "energy" | energy, green_energy, clean_tech |
| "healthcare" | health_care, healthtech, biotech |
| "software" | software_saas, technology, digitaltechnology |
| "automotive" | automotive, mobility, transports |
| "fintech" | fintech, fin_services, blockchain |
| "infrastructure" | infrastructure, energy, industrials |

---

## Appendix D: Performance benchmarks

From Phase 4 validation across 5 live deals:

| Deal | Investors scanned | Persons scored | Gating pass | Run time |
|------|-------------------|----------------|-------------|----------|
| Doosan (expanded) | 11,946 | 148,194 | 18,335 | 248s |
| Doosan (strict) | 5,477 | 42,518 | 11,044 | 84s |
| IntraLogic | 8,207 | 83,565 | 7,910 | 166s |
| BrakesToGo | 15,470 | 219,518 | 21,077 | 290s |
| Grapeviine | 11,431 | 119,762 | ~12,000 | 202s |
| FutureFundOne | 8,313 | 76,374 | 4,637 | 121s |

**Typical latency:** 2-5 minutes for a full `match_deal` call. This is a Supabase
PostgREST network round-trip across 1.8M contacts — it is not a caching issue.
Use `io_search_investors` or `search_descriptions` (1-45s) for quick discovery
passes before committing to a full `match_deal` run.
