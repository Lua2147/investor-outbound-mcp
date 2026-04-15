# Investor Outbound MCP — Integration Guide

Reference for agents and systems consuming IO MCP tools. Covers registration,
quick start, all 30 tools, response formats, deal-type workflows, filter values,
scoring mechanics, and known limitations.

---

## Table of Contents

1. [MCP Registration](#1-mcp-registration)
2. [Quick Start](#2-quick-start)
3. [Tool Groups](#3-tool-groups)
4. [Response Format](#4-response-format)
5. [Deal Matching Workflows](#5-deal-matching-workflows)
6. [Filter Reference](#6-filter-reference)
7. [Scoring Reference](#7-scoring-reference)
8. [Critical Notes](#8-critical-notes)
9. [Limitations](#9-limitations)

---

## 1. MCP Registration

### SSE mode (recommended — persistent server)

Add to `~/.claude.json` under the `mcpServers` key:

```json
{
  "mcpServers": {
    "investor-outbound": {
      "type": "sse",
      "url": "http://your-server:8770/sse"
    }
  }
}
```

The server binds `127.0.0.1` by default. Default port is **8770**.

### Starting the server

```bash
# SSE mode — default port 8770
cd /path/to/investor-outbound-mcp
python -m src.server --sse

# Custom port
python -m src.server --sse --port 8771

# stdio mode (for direct process launch, SSH tunnel, or testing)
python -m src.server
```

### Credentials

The server loads Supabase credentials at startup from environment variables or config:

```bash
# Preferred: environment variables
export IO_EMAIL="your@email.com"
export IO_PASSWORD="yourpassword"

# Fallback: config/api_keys.json key "supabase_investor_outreach"
```

JWT auth is handled internally — the client logs in once and auto-refreshes the
token on expiry. All tools will return `AUTH_FAILED` if credentials are missing or invalid.

### Tool naming

After registration all tools are available as `mcp__investor-outbound__<tool_name>` in Claude sessions.

---

## 2. Quick Start

Five calls that cover 80% of use cases:

### 2a. Match a deal (hero tool)

```python
mcp__investor-outbound__match_deal(
    role_keywords=["energy", "infrastructure", "renewable"],
    firm_keywords=["energy", "infrastructure", "power"],
    named_firms=["kkr", "brookfield", "blackrock"],
    sectors=["energy", "infrastructure"],
    investor_types=["pe", "infrastructure", "asset manager"],
    deal_size=70_000_000,
    deal_stage="buyout",
    max_per_firm=5,
    max_results=1000,
)
```

Runs a two-phase pipeline: broad investor pull (sector overlap + description keywords +
named firms + deal stage) then tight contact-level 6-gate scoring. Returns ranked
contacts with `_score`, `_match_path`, and investor metadata attached.

### 2b. Search investors

```python
mcp__investor-outbound__io_search_investors(
    sectors=["healthcare", "biotech"],
    investor_types=["vc", "family office"],
    geography="United States",
    limit=50,
)
```

Returns paginated investor summaries with pagination metadata.

### 2c. Get contacts for known investors

```python
mcp__investor-outbound__io_get_contacts(
    investor_ids=[12345, 67890],
    deal_keywords=["medtech", "growth"],
    max_per_firm=5,
)
```

Fetches persons for specific investor IDs, scores them, removes junk roles, caps
per firm, and returns sorted by score descending.

### 2d. Search investor descriptions

```python
mcp__investor-outbound__io_search_descriptions(
    keyword="franchise acquisitions",
    investor_types=["pe", "family office"],
    limit=50,
)
```

Searches the description field (97% coverage). Each result includes a 300-char
`description_snippet` showing why the investor matched.

### 2e. Find similar investors by embedding

```python
mcp__investor-outbound__find_similar_investors(
    investor_id=12345,
    limit=50,
    investor_types=["pe"],
)
```

Reads the source investor's 3072-dim embedding, passes it to the
`ai_search_with_ideal_investor` RPC, and returns investors ranked by cosine
similarity (higher `similarity_score` = more similar).

---

## 3. Tool Groups

### 3a. Deal Matching (Tools 1–4)

The highest-value tools. Tools 1–4 implement the two-phase pipeline proven on 206K contacts.

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `match_deal` | Hero tool. Two-phase: broad investor pull + 6-gate contact scoring. Contacts included regardless of email. | `role_keywords`, `firm_keywords`, `named_firms`, `sectors`, `investor_types`, `deal_size`, `deal_stage`, `expanded`, `max_per_firm`, `max_results`, `min_score` | `match_deal(role_keywords=["buyout"], firm_keywords=["pe"], named_firms=["kkr"], sectors=["industrials"], deal_stage="buyout")` |
| `match_deal_stage` | Investors filtered by `preferred_investment_types` ilike. No contact scoring. | `stage`, `investor_types`, `geography`, `limit` | `match_deal_stage(stage="seed", investor_types=["vc", "angel"])` |
| `match_preferences` | Investors filtered by stated preferences only: industry, geography, check size. No scoring. | `preferred_industry`, `preferred_geography`, `check_size_min`, `check_size_max`, `investor_types`, `limit` | `match_preferences(preferred_industry="Healthcare", preferred_geography="United States", check_size_min=5, check_size_max=50)` |
| `find_similar_investors` | Embedding cosine similarity via `ai_search_with_ideal_investor` RPC. 290K embeddings available. | `investor_id`, `limit`, `investor_types` | `find_similar_investors(investor_id=12345, limit=50)` |

**Important on `match_preferences` check size params**: `check_size_min` and `check_size_max`
are already in **MILLIONS USD**. Pass `5` for $5M, `50` for $50M. This differs from
`match_deal`'s `deal_size` param which accepts raw dollars.

---

### 3b. Investor Discovery (Tools 5–8)

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_search_investors` | Paginated investor search with structured filters. Ordered by completeness_score desc. | `sectors`, `investor_types`, `geography`, `check_size_min_dollars`, `check_size_max_dollars`, `keyword`, `include_acquired`, `limit`, `offset` | `io_search_investors(sectors=["fintech"], investor_types=["vc"], geography="United States", limit=50)` |
| `io_search_descriptions` | ilike search on investor description text (97% coverage). Returns description_snippet per result. 2–8s response time. | `keyword`, `investor_types`, `limit`, `offset` | `io_search_descriptions(keyword="medtech growth equity", limit=50)` |
| `io_get_investor` | Full investor profile (47 columns) by ID (exact) or name (ilike, best match by completeness_score). | `investor_id`, `name` | `io_get_investor(name="sequoia")` or `io_get_investor(investor_id=12345)` |
| `io_investor_freshness` | Recently updated investors, ordered by updated_at desc. | `sectors`, `investor_types`, `limit`, `offset` | `io_investor_freshness(sectors=["energy"], limit=50)` |

---

### 3c. Contact Retrieval (Tools 9–12)

All tools return contacts regardless of email presence. Scoring is numeric (via
`score_contact()`) with junk and firm-name-as-role filtering, but without the 6-gate
`passes_deal_relevance()` check. Use `match_deal` when you need deal-specific gating.

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_get_contacts` | Scored + filtered contacts for one or more investors. Accepts investor_ids or investor_name. | `investor_ids`, `investor_name`, `deal_keywords`, `max_per_firm` | `io_get_contacts(investor_ids=[12345, 67890], deal_keywords=["buyout"], max_per_firm=5)` |
| `io_search_persons` | Search 1.8M persons by name, email, company, or role. Returns paginated PersonSummary. At least one filter required. | `name`, `email`, `company`, `role`, `page`, `page_size` | `io_search_persons(company="blackrock", role="managing director")` |
| `io_get_investor_team` | All persons at a single investor grouped into 3 seniority tiers with per-tier channel coverage. | `investor_id` | `io_get_investor_team(investor_id=12345)` |
| `io_find_decision_makers` | Across multiple investors: senior investment professionals only. Must pass both `is_senior()` AND `has_investment_function()` gates. | `investor_ids`, `deal_keywords`, `max_per_firm` | `io_find_decision_makers(investor_ids=[12345, 67890], deal_keywords=["infrastructure"])` |

**Tier definitions for `io_get_investor_team`**:
- Tier 1: Partner / MD / GP / CIO / CEO / Founder
- Tier 2: VP / Director / SVP / EVP / Principal
- Tier 3: All other staff

---

### 3d. Reverse Lookup (Tools 13–17)

Lookup tools for enriching from partial identifiers. No deal-scoring applied.

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `lookup_by_email_domain` | All persons at a domain (e.g. "kaynecapital.com"). Returns investor_name via FK enrichment. | `domain` | `lookup_by_email_domain(domain="kaynecapital.com")` |
| `lookup_by_linkedin` | Full PersonDetail for an exact LinkedIn URL. Useful for instant enrichment. | `linkedin_url` | `lookup_by_linkedin(linkedin_url="https://www.linkedin.com/in/john-smith-abc123/")` |
| `reverse_company_lookup` | Find which investor firms have persons from a given company. Grouped by investor FK. | `company_name` | `reverse_company_lookup(company_name="McKinsey")` |
| `batch_firm_lookup` | Up to 50 firm names → matched investors + top 5 contacts per firm. For CRM enrichment. | `firm_names` | `batch_firm_lookup(firm_names=["KKR", "Blackstone", "Apollo"])` |
| `batch_person_lookup` | Mixed list of emails and "First Last" names → matched PersonSummary records. Emails use in() batching; names use ilike. | `identifiers` | `batch_person_lookup(identifiers=["john@kkr.com", "Jane Smith"])` |

---

### 3e. Outreach Readiness (Tools 18–21)

Tools for assessing and filtering contacts by email deliverability.

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_outreach_ready_contacts` | Hard-filtered contacts: good_email=true, email_free=false, email_disposable=false, last_bounce_type IS NULL. | `investor_ids` | `io_outreach_ready_contacts(investor_ids=[12345, 67890])` |
| `io_assess_contact_quality` | Grade each contact A/B/C/D. Returns per-contact grades + aggregate counts. | `investor_ids` | `io_assess_contact_quality(investor_ids=[12345])` |
| `io_channel_coverage` | Email/phone/LinkedIn breakdown per investor + rolled-up totals. | `investor_ids` | `io_channel_coverage(investor_ids=[12345, 67890])` |
| `io_enrich_priorities` | Persons with LinkedIn but no email, ranked by seniority. Best targets for enrichment. | `investor_ids` | `io_enrich_priorities(investor_ids=[12345])` |

**Grade thresholds**:
- A: `good_email=true` AND `email_score > 80`
- B: `email_status='deliverable'` AND `email_score > 50`
- C: `email_status='risky'` OR `email_score <= 50`
- D: `email_status='undeliverable'` OR bounce recorded OR no email

---

### 3f. Multi-Deal Intelligence (Tools 22–25)

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_find_cross_deal_investors` | Investors matching 2+ deals from a list of criteria dicts. Each criteria dict uses the same keys as `io_search_investors`. Returns `deal_match_count` and `matched_deals` per investor. | `deals`, `min_deal_matches` | See workflow in section 5. |
| `io_deal_coverage_gaps` | Probes 14 investor types, 10 geographies, 15 sectors individually. Returns zero-result dimensions as `zero_investor_types`, `zero_geographies`, `zero_sectors`. | `sectors`, `investor_types`, `description_keywords`, `check_size_min_dollars`, `check_size_max_dollars`, `geography` | `io_deal_coverage_gaps(sectors=["biotech"], investor_types=["vc"])` |
| `io_investor_funnel` | Progressive filter: each step accumulates constraints from all prior steps. Returns estimated investor count at each step. | `filters` (list of filter dicts with optional `label`) | See workflow in section 5. |
| `io_deduplicate_across_deals` | Pure Python — no network. Finds person IDs appearing in 2+ per-deal contact lists. | `deal_person_lists` (list of `{label, person_ids}` dicts) | `io_deduplicate_across_deals(deal_person_lists=[{"label": "deal1", "person_ids": [1,2,3]}, {"label": "deal2", "person_ids": [2,3,4]}])` |

---

### 3g. Analytics (Tools 26–27)

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_sector_landscape` | Investor type breakdown, top-10 country breakdown, check-size histogram, top-10 firms by contact count — all for one sector. | `sector`, `include_acquired` | `io_sector_landscape(sector="energy")` |
| `io_check_size_distribution` | Check size histogram + p25/p50/p75 percentiles in millions USD for sector + optional investor type. | `sector`, `investor_type`, `include_acquired` | `io_check_size_distribution(sector="healthcare", investor_type="family office")` |

Check-size histogram buckets: `<$1M`, `$1–5M`, `$5–25M`, `$25–100M`, `$100M–$1B`, `$1B+`

---

### 3h. Export and Hygiene (Tools 28–30)

| Tool | Description | Key Params | Example Call |
|------|-------------|------------|--------------|
| `io_export_contacts` | Trigger export2 edge function, poll user_exports table (3s interval, 60s max), return signed download URL. Status: "ready", "timeout", or "failed". | `export_name`, `contacts_per_investor`, `search_term`, `investment_types`, `investor_types`, `locations`, `sectors`, `limit_count` | `io_export_contacts(export_name="Q2 Energy Deals", sectors=["energy"], limit_count=1000)` |
| `io_stale_contact_check` | Find bounced, low-score, and undeliverable contacts. Three parallel queries. Requires investor_ids or sectors/investor_types scope. | `investor_ids`, `sectors`, `investor_types`, `limit` | `io_stale_contact_check(investor_ids=[12345, 67890])` |
| `io_search_by_company_industry` | Filter persons by LinkedIn-style company_industry (ilike) + optional company_size bucket + country + email presence. | `company_industry`, `company_size`, `company_country`, `has_email`, `limit`, `page` | `io_search_by_company_industry(company_industry="financial services", company_size="1001-5000", has_email=True)` |

---

## 4. Response Format

All tools return JSON strings. The LLM receives them as text — parse as JSON before
using field values.

### 4a. Standard tool response

Used by most tools. The `next_actions` key is present only when the tool has suggestions.

```json
{
  "data": <list | dict | scalar>,
  "summary": "1,234 contacts across 89 firms (from 456 investors, 12,345 persons scored).",
  "next_actions": [
    "Use io_outreach_ready_contacts to filter to email-deliverable contacts only",
    "Use io_find_similar_investors on top-matching investors to expand the list"
  ]
}
```

### 4b. Paginated response

Used by `io_search_investors`, `io_search_descriptions`, `io_search_persons`,
`io_investor_freshness`, and `io_search_by_company_industry`.

```json
{
  "data": [...],
  "meta": {
    "total": 4500,
    "page": 1,
    "page_size": 50,
    "has_more": true
  },
  "summary": "Found 4500 investors (sectors=['fintech']), returning 50",
  "next_actions": [...]
}
```

`total` may be `null` for large table scans where the count is not reliably available.
`has_more` is `null` when `total` is `null`.

### 4c. Stats response

Used by `io_sector_landscape` and `io_check_size_distribution`. No `next_actions`.

```json
{
  "data": {
    "sector": "energy",
    "total_investors": 1234,
    "by_investor_type": {"PE/Buyout": 312, "Infrastructure": 204, ...},
    "by_country_top10": {"United States": 480, ...},
    "check_size_histogram": {"<$1M": 45, "$1-5M": 210, ...},
    "top_firms_by_contacts": [...]
  },
  "summary": "Sector 'energy': 1234 investors total — 8 investor types, top country: United States"
}
```

### 4d. Error response

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "At least one preference filter is required.",
    "details": ["field: sectors — no matching DB codes found"]
  }
}
```

**Error codes**:

| Code | HTTP Equivalent | Meaning |
|------|----------------|---------|
| `AUTH_FAILED` | 401 | Supabase JWT expired or credentials invalid |
| `QUERY_ERROR` | 400 | Bad PostgREST query — check filter values |
| `RATE_LIMITED` | 429 | Too many requests |
| `SERVER_ERROR` | 5xx | Transient DB failure — retry in a few seconds |
| `VALIDATION_ERROR` | 400 | Bad tool input params |
| `NOT_FOUND` | 404 | Entity lookup returned no rows |
| `TIMEOUT` | 408 | Request exceeded deadline |

Always check for the `error` key before accessing `data`. When `error` is present, `data` is absent.

---

## 5. Deal Matching Workflows

### 5a. $70M+ Buyout — Doosan pattern

Infrastructure asset sale. Target: PE funds, infrastructure managers, and asset managers
with energy/infrastructure mandate. Use `expanded=True` because energy infrastructure is
a niche category where keyword coverage is sparse.

```python
# Step 1: Run the two-phase match
result = mcp__investor-outbound__match_deal(
    role_keywords=["energy", "infrastructure", "renewable", "power", "buyout"],
    firm_keywords=["energy", "infrastructure", "power", "renewable"],
    named_firms=["kkr", "brookfield", "blackrock", "macquarie", "ares"],
    sectors=["energy", "infrastructure"],
    investor_types=["pe", "infrastructure", "asset manager"],
    deal_size=70_000_000,
    deal_stage="buyout",
    description_keywords=["energy infrastructure", "renewable energy"],
    expanded=True,
    max_per_firm=5,
    max_results=1000,
)
# Expect: ~1000 contacts from ~500 firms, ~82% with email

# Step 2: Get only outreach-ready contacts from top investor IDs
top_investor_ids = [c["investor"] for c in result["data"]["contacts"][:200]]
ready = mcp__investor-outbound__io_outreach_ready_contacts(
    investor_ids=list(set(top_investor_ids))
)

# Step 3: Find similar investors to expand beyond the initial set
top_investor_id = result["data"]["contacts"][0]["investor"]
similar = mcp__investor-outbound__find_similar_investors(
    investor_id=top_investor_id,
    investor_types=["pe", "infrastructure"],
    limit=50,
)
```

**Validated result (Session 1)**: 11,946 investors scanned, 148,194 persons scored,
18,335 passing the gate, 1,000 returned (capped). Top match: Justin Campeau,
Senior Managing Director at Kayne Anderson Capital Advisors, score 65, path A.

---

### 5b. $7M Series A — IntraLogic pattern

B2B SaaS / logistics tech. Target: early-stage VC and growth equity funds.

```python
# Step 1: Match by stage and sector
result = mcp__investor-outbound__match_deal(
    role_keywords=["venture", "investment", "series a", "early stage"],
    firm_keywords=["logistics", "supply chain", "saas", "enterprise"],
    named_firms=["sequoia", "a16z", "bessemer", "tier1"],
    sectors=["software", "technology", "services"],
    investor_types=["vc", "growth equity"],
    deal_size=7_000_000,
    deal_stage="series a",
    description_keywords=["enterprise software", "logistics", "supply chain"],
    expanded=False,
    max_per_firm=3,
    max_results=500,
)

# Step 2: Run funnel diagnostics if result count is too low
funnel = mcp__investor-outbound__io_investor_funnel(
    filters=[
        {"sectors": ["software"], "label": "base: software sector"},
        {"investor_types": ["vc"], "label": "add VC type"},
        {"description_keywords": ["logistics"], "label": "add logistics desc"},
    ]
)
```

---

### 5c. $2M SAFE — BrakesToGo pattern

Early-stage consumer / automotive. Pre-revenue. Target: angels, accelerators, seed VCs.
**Use `expanded=True`** — angels have sparse sector data and minimal keyword overlap
in their roles.

```python
result = mcp__investor-outbound__match_deal(
    role_keywords=["angel", "seed", "early stage", "founder"],
    firm_keywords=["automotive", "mobility", "consumer", "hardware"],
    named_firms=["techstars", "y combinator", "500 startups"],
    sectors=["automotive", "consumer"],
    investor_types=["angel", "accelerator", "vc"],
    deal_size=2_000_000,
    deal_stage="seed",
    description_keywords=["seed investment", "angel investing", "early stage"],
    expanded=True,            # REQUIRED for angel/seed — sparse keyword coverage
    max_per_firm=3,
    min_score=15,             # Lower threshold for angels (shorter titles)
    max_results=500,
)
```

**Why `expanded=True` for small deals**: Angels often have generic titles like
"Angel Investor" or "Founder" with no deal keywords in their role. Without expanded,
gates F1/F2 are unavailable and most angels fall out at Gate 6.

---

### 5d. $1.8M Seed — Grapeviine pattern

Consumer tech / social. Target: angels, family offices with consumer focus, seed VCs.

```python
# Step 1: Stage-only match to get investor list
investors = mcp__investor-outbound__match_deal_stage(
    stage="seed",
    investor_types=["angel", "family office", "vc"],
    geography="United States",
    limit=200,
)

# Step 2: For top investors, get full team view
for inv in investors["data"][:10]:
    team = mcp__investor-outbound__io_get_investor_team(
        investor_id=inv["id"]
    )
    # Use tier1 (Partner/Founder level) for outreach

# Step 3: Preference match with check size bounds
pref_match = mcp__investor-outbound__match_preferences(
    preferred_industry="Consumer",
    preferred_geography="United States",
    check_size_min=0.5,    # $500K — already in MILLIONS
    check_size_max=3.0,    # $3M — already in MILLIONS
    investor_types=["angel", "family office"],
    limit=200,
)
```

---

### 5e. $250M Fund Raise — FutureFundOne pattern

PE fund raising from LPs. Target: pension funds, endowments, family offices, fund of funds.
No deal stage filter — this is a fund, not a company deal.

```python
# Step 1: LP-focused investor search
result = mcp__investor-outbound__match_deal(
    role_keywords=["fund investment", "portfolio", "allocation", "lp", "alternatives"],
    firm_keywords=["pension", "endowment", "foundation", "fund of funds"],
    named_firms=["calpers", "teachers", "ontario teachers", "gic"],
    sectors=["financial investments", "financial services"],
    investor_types=["pension", "endowment", "fund of funds", "sovereign wealth"],
    deal_size=250_000_000,
    description_keywords=["private equity allocation", "alternatives", "fund of funds"],
    expanded=False,
    max_per_firm=5,
    max_results=1000,
)

# Step 2: Cross-deal check — find investors appearing in multiple PE fund searches
cross = mcp__investor-outbound__io_find_cross_deal_investors(
    deals=[
        {
            "label": "FutureFundOne",
            "sectors": ["financial investments"],
            "investor_types": ["pension", "endowment"],
            "description_keywords": ["private equity allocation"],
        },
        {
            "label": "FutureFundOne_FoF",
            "investor_types": ["fund of funds"],
            "description_keywords": ["fund of funds"],
        },
    ],
    min_deal_matches=2,
)

# Step 3: Coverage gap analysis
gaps = mcp__investor-outbound__io_deal_coverage_gaps(
    sectors=["financial investments"],
    investor_types=["pension"],
    description_keywords=["private equity"],
)
```

---

## 6. Filter Reference

### 6a. Sector Codes (`sectors_array`)

120 unique codes. Pass human-readable names — resolved internally via `resolve_sectors()`.

**Full list of DB codes** (use these as-is or let the tool resolve from human names):

```
agnostic, agritech, ai_ml, artificialintelligence,
agriculture, agriculturaltechnology,
automotive, biotech, blockchain, business_products_and_services,
business_services, businessservices,
cannabis, clean_tech, cleantech, crypto, cybersecurity,
consumer, consumer_discr, consumerdiscr, consumerdiscretionary,
consumerproducts, consumer_staples, consumerstaples,
digitaltechnology, ecommerce, e_commerce, edtech, education,
energy, enterprisesoftware, esg_impact,
fin_invest, fin_services, fintech, fininvest, finservices,
financeinvestments, financialservices,
gaming_esports, gayenergysolarcleantechwindrenewables,
green_energy, greeninfrastructure,
health, health_care, healthcare, healthcaretechnology, healthtech,
holding, impactinvesting, industrial, industrials,
industrialtechnology, informationtechnology, informationtechnologyservices,
insurance, insurancetech,
manufacturing, media, media_tv, mediatelevision, mediatv,
mining, miningdevelopment, miningexploration,
mobility, multifamilyrealestate,
nanotechnology, newenergy,
oil_gas, pharma, private_equity, privateequity,
re, re_lending, real_estate, realestate, realestatedirectinvestments,
realestategplp, realestateindustrial/logistics, realestatelending,
realestateoffice, realestateretail, relending,
renewableenergy, right_energy, rightenergy, rightenergyoilgascoal,
services, software, software_saas,
space_tech, sustainableinfrastructure,
technology, transports, utilities,
venturecapital, water
```

**Human-readable aliases** (input → resolved DB codes):

| Input | Resolves To |
|-------|-------------|
| "energy" | energy, cleantech, clean_tech, renewableenergy, green_energy, newenergy, right_energy, rightenergy, gayenergysolarcleantechwindrenewables |
| "healthcare" | healthcare, health_care, health, healthtech, healthcaretechnology |
| "technology" | technology, digitaltechnology, informationtechnology, informationtechnologyservices |
| "software" | software, software_saas, enterprisesoftware |
| "ai/ml" | ai_ml, artificialintelligence |
| "fintech" | fintech |
| "financial services" | fin_services, finservices, financialservices |
| "financial investments" | fin_invest, fininvest, financeinvestments |
| "real estate" | realestate, real_estate, realestatedirectinvestments, realestategplp, realestateindustrial/logistics, realestatelending, realestateoffice, realestateretail, multifamilyrealestate, re, re_lending, relending |
| "industrial" | industrial, industrials, industrialtechnology, manufacturing |
| "private equity" | private_equity, privateequity, venturecapital |
| "blockchain" | blockchain, crypto |
| "services" | services, business_services, businessservices, business_products_and_services |

---

### 6b. Investor Types (`primary_investor_type`) — 52 values

Complete list of DB enum values (pass these directly, or use human-readable aliases):

```
Academic Institution, Accelerator/Incubator, Angel (individual), Angel Group,
Asset Manager, Asset Manager - Fund Manager, Bank, Corporate Development,
Corporate Investor, Corporate Venture Capital, Endowment Plan,
Family Office, Family Office - Multi, Family Office - Single,
Financial Advisor, Foundation, Fund Manager, Fund of Funds,
Fund of Hedge Funds Manager, Fundless Sponsor, Government,
Growth/Expansion, Hedge Fund, Holding Company, Impact Investing,
Infrastructure, Investment Bank, Investment Company,
Lender/Debt Provider, Limited Partner, Merchant Banking Firm,
Mutual Fund, Not-For-Profit Venture Capital, Other,
Other Private Equity, PE/Buyout, Private Equity, Private Equity Firm,
Private Equity Fund of Funds Manager, Private Sector Pension Fund,
Public Pension Fund, Real Estate, Real Estate Film (Investor),
Real Estate Firm, Real Estate Fund of Funds Manager,
Secondary Buyer, Sovereign Wealth Fund,
Special Purpose Acquisition Company (SPAC), University,
Venture Capital, Wealth Management/RIA, Wealth Manager
```

**Human-readable aliases** (partial list):

| Input | Resolves To |
|-------|-------------|
| "vc" / "venture capital" | Venture Capital |
| "pe" / "private equity" / "buyout" | PE/Buyout, Private Equity, Private Equity Firm |
| "family office" | Family Office, Family Office - Single, Family Office - Multi |
| "sfo" | Family Office - Single |
| "mfo" | Family Office - Multi |
| "ria" / "wealth management" | Wealth Management/RIA, Wealth Manager |
| "hedge fund" / "hf" | Hedge Fund |
| "angel" | Angel (individual), Angel Group |
| "asset manager" | Asset Manager, Asset Manager - Fund Manager |
| "growth" / "growth equity" | Growth/Expansion |
| "cvc" / "corporate vc" | Corporate Venture Capital |
| "lender" / "debt" | Lender/Debt Provider |
| "pension" | Private Sector Pension Fund, Public Pension Fund |
| "endowment" | Endowment Plan |
| "sovereign wealth" / "swf" | Sovereign Wealth Fund |
| "fund of funds" / "fof" | Fund of Funds |
| "secondary" / "secondaries" | Secondary Buyer |
| "fundless sponsor" / "search fund" | Fundless Sponsor |
| "spac" | Special Purpose Acquisition Company (SPAC) |
| "impact" / "esg" | Impact Investing |
| "infrastructure" | Infrastructure |
| "corporate" / "strategic" | Corporate Investor, Corporate Development |
| "holding company" / "holdco" | Holding Company |
| "bank" | Bank |
| "investment bank" | Investment Bank |
| "accelerator" / "incubator" | Accelerator/Incubator |

---

### 6c. Investment Types (`investment_types_array` / `preferred_investment_types`) — 78 values

Complete list of DB values:

```
Accelerator/Incubator, Acquisition Financing, Add-on, Angel (individual),
Asset Acquisition, Asset Divestiture (Corporate), Balanced,
Bankruptcy: Admin/Reorg, Bankruptcy: Liquidation, Bonds, Bonds (Convertible),
Bridge, Buyout, Buyout/LBO, Capital Spending, Capitalization, Carveout, CLO,
Co-investment, Convertible Debt, Corporate, Corporate Asset Purchase,
Corporate Divestiture, Debt - General, Debt Refinancing, Debt Repayment,
Distressed Acquisition, Distressed Debt, Dividend Recapitalization,
Early Stage, Early Stage VC, Equity For Service, Expansion / Late Stage,
Fund of Funds, General Corporate Purpose, Grant, Growth,
Hospital/Healthcare Facility, Hotel, IPO, Joint Venture,
Late Stage Venture, Later Stage VC, Leveraged Recapitalization, Loan,
Management Buy-In, Management Buyout, Merger/Acquisition, Mezzanine,
Natural Resources, PE Growth/Expansion, PIPE, Privatization,
Project Financing, Public to Private, Public-Private Partnership,
Real Estate, Recapitalization, Sale-Lease back facility,
Secondaries, Secondary Buyer, Secured, Secured Debt, Seed, Seed Round,
Senior Debt, Special Situations, Spin-Off, Start-up, Subordinated,
Subordinated Debt, Timber, Turnaround, University Spin-Out,
Unsecured Debt, Venture (General), Venture Debt, Working Capital
```

**Human-readable aliases** (partial list):

| Input | Resolves To |
|-------|-------------|
| "seed" | Seed Round, Seed, Angel (individual), Start-up |
| "series a" / "early stage" | Early Stage VC, Early Stage, Venture (General) |
| "series b" | Later Stage VC, Venture (General), Growth |
| "growth" / "growth equity" | Growth, PE Growth/Expansion |
| "buyout" / "lbo" | Buyout/LBO, Buyout, Management Buyout |
| "m&a" | Merger/Acquisition, Add-on, Acquisition Financing, Carveout |
| "venture" / "vc" | Venture (General), Early Stage VC, Later Stage VC |
| "debt" | Debt - General, Mezzanine, Venture Debt, Secured Debt, Senior Debt, Subordinated Debt |
| "convertible" | Convertible Debt, Bonds (Convertible) |
| "distressed" | Distressed Debt, Distressed Acquisition, Turnaround, Special Situations |
| "restructuring" | Bankruptcy: Admin/Reorg, Recapitalization, Turnaround |
| "fundraise" / "capital raise" | Venture (General), Growth, PE Growth/Expansion, Early Stage VC, Later Stage VC |
| "pipe" | PIPE |
| "secondaries" | Secondaries |
| "project finance" / "infrastructure" | Project Financing, Natural Resources |

**Deal-stage presets** (pass as `deal_stage` param to `match_deal` and `match_deal_stage`):

| Preset key | Values |
|------------|--------|
| `seed` | Seed Round, Seed, Angel (individual), Start-up, Accelerator/Incubator |
| `series_a` | Early Stage VC, Early Stage, Venture (General) |
| `growth` | Growth, Later Stage VC, Late Stage Venture, PE Growth/Expansion, Expansion / Late Stage, Venture (General) |
| `buyout` | Buyout/LBO, Buyout, Management Buyout, Management Buy-In, Merger/Acquisition, Add-on, Carveout, Acquisition Financing, Corporate Divestiture, Asset Acquisition, Public to Private |
| `fund_raise` | Venture (General), Early Stage VC, Later Stage VC, Growth, PE Growth/Expansion, Expansion / Late Stage, Seed Round, Seed, Start-up |

---

### 6d. Investor Status (`investor_status`)

```
"Actively Seeking New Investments"   — active; included by default
null                                  — status unknown; included by default
"Acquired/Merged"                    — defunct; excluded by default in all tools
```

The `include_acquired` parameter (default `False`) controls whether `Acquired/Merged`
investors are included. All tools that scan investors exclude them by default.

---

### 6e. Email Status (`email_status`)

| Value | Coverage | Meaning |
|-------|----------|---------|
| `"deliverable"` | ~47% | Email confirmed deliverable |
| `"unknown"` | ~47% | Deliverability not verified |
| `"undeliverable"` | ~3% | Known bad address |
| `"risky"` | ~2% | Deliverable but risky (catch-all, spam trap risk) |

The `good_email` boolean field (derived, always present) indicates the email passed
all quality checks. Use `good_email=true` as the strictest filter.

---

## 7. Scoring Reference

### 7a. Score computation (`score_contact()`)

Each contact gets a numeric relevance score before gate evaluation:

| Signal | Points |
|--------|--------|
| Top seniority (Partner / MD / GP / CIO / CEO / Founder / Co-founder / Chief Executive) | +30 |
| Mid seniority (VP / Director / SVP / EVP / Principal / Head of) | +20 |
| Junior investment (Analyst / Associate) | +5 |
| Investment function present in role (see list below) | +15 |
| Each matching deal keyword in role | +10 |
| Junk role match (67 patterns — see Gate 3) | −30 |
| Empty or very short role (<3 chars) | −5 |

**Investment function substrings** (triggers the +15 bonus):
`investment, investor, deal, portfolio, acquisition, corporate development,
corp dev, m&a, capital, fund manager, private equity, venture, buyout,
origination, sourcing, underwriting, business development, strategy, strategic`

**Contact data bonus** (added separately via `contact_data_bonus()`):
- Email present: +3
- LinkedIn present: +2
- Phone present: +1

### 7b. Score level interpretation

| Score | Typical profile | Outreach priority |
|-------|----------------|------------------|
| 55–65 | Senior MD/Partner with role keyword hit | Top tier |
| 45–54 | Partner/MD with investment function | High |
| 35–44 | VP/Director with investment function | Medium-high |
| 25–34 | VP/Director or senior role + sector match | Medium |
| 15–24 | Director or analyst-level investment role | Low |
| < 15 | Marginal or failed gate | Excluded (default min_score=20) |

### 7c. The 6-gate pipeline (`passes_deal_relevance()`)

Applied by `match_deal` and `io_find_decision_makers`. All 6 gates must pass:

**Gate 1 — Minimum score**: `score >= min_score` (default 20)

**Gate 2 — Real role**: `len(role.strip()) >= 3`

**Gate 3 — Not junk**: Role must not match any of 67 junk patterns including:
`human resources, payroll, recruiting, administrative, office manager,
executive assistant, help desk, customer service, software engineer, data scientist,
it manager, it support, marketing coordinator, social media, content writer,
account manager, account executive, compliance officer, paralegal, accountant,
bookkeeper, intern, student, nurse, physician, teacher, professor, product manager,
project manager, database administrator, graphic designer, legal counsel,
marketing manager, warehouse, shipping, janitor, security guard`

**Gate 4 — Role is not firm name**: Role field must not be the firm name repeated.
Detects bad CRM data where company name was populated as job title.

**Gate 5 — Senior OR investment function**: Must pass at least one of `is_senior()` or
`has_investment_function()`.

**Gate 6 — Deal relevance** — 7 alternative paths (first match wins):

| Path | Condition | Strength |
|------|-----------|---------|
| A | Role contains a `role_keyword` | Strongest signal |
| B | Named target firm (in `named_firms`) + senior OR investment function | Strong |
| C | Firm name matches `firm_keywords` + has investment function | Medium |
| D | Firm name matches `firm_keywords` + is senior + score >= 35 | Medium |
| E | Sectors match `firm_keywords` + is senior + investment function + score >= 35 | Weak |
| F1 | `expanded=True` + is senior + has investment function | Fallback (expanded only) |
| F2 | `expanded=True` + has investment function + score >= 30 | Fallback (expanded only) |

The `_match_path` field in each contact record identifies which path caused the contact
to pass (A–F2). Use this for quality analysis — path A contacts are highest signal.

### 7d. When to use `expanded=True`

Set `expanded=True` when:
- Deal sectors have sparse DB coverage (e.g., cannabis, space, novel consumer)
- Targeting angel investors (short/generic titles)
- Targeting family offices (often no investment keywords in role)
- Initial run returns fewer than 100 contacts with paths A–D only
- Niche deals where `role_keywords` and `firm_keywords` have low keyword overlap

---

## 8. Critical Notes

### check_size units mismatch

This is the most common source of incorrect results.

```
DB storage:   check_size_min / check_size_max are in MILLIONS USD
              $10M deal → stored as 10
              $1B deal → stored as 1000
              $50B deal → stored as 50000

match_deal:   deal_size param is in DOLLARS
              Pass 70_000_000 for a $70M deal
              The tool divides by 1,000,000 internally before querying

match_preferences:  check_size_min / check_size_max are in MILLIONS
              Pass 5 for $5M, not 5_000_000
              DO NOT apply the dollar convention here

io_search_investors:  check_size_min_dollars / check_size_max_dollars are in DOLLARS
              Pass 5_000_000 for $5M
              The tool divides by 1,000,000 internally
```

Summary: `match_deal(deal_size=)` and `io_search_investors(check_size_*_dollars=)` accept
dollars. `match_preferences(check_size_min=, check_size_max=)` accepts millions directly.

### preferred_investment_types is TEXT not array

The `preferred_investment_types` column is a TEXT string, not an array. The `cs`
(array contains) operator will fail with:
```
operator does not exist: text @> unknown
```

Always use `ilike` with wildcard wrapping. The tools handle this internally — callers
should never construct raw PostgREST queries on this column with `cs`.

### Nested PostgREST joins are broken

The `persons → investors` foreign key relationship is NOT exposed in the PostgREST
schema. Any query attempting:
```
persons?select=*,investors!inner(*)
```
will return `PGRST200: Could not find a relationship`.

All tools use the two-step pattern:
1. Query `investors` table → collect investor IDs
2. Query `persons` table with `investor` FK in `in.()` filter

This is the only supported approach.

### sectors_tsv FTS is unreliable

The `sectors_tsv` full-text search column works for some keywords (`fintech`) and
returns 0 results for others (`energy`, `healthcare`). Do not use `sectors_tsv` as a
primary filter. All tools use `sectors_array` overlap (`ov`) instead.

### Contacts without email are included by default

`match_deal`, `io_get_contacts`, and all contact retrieval tools return contacts
regardless of email presence. This is intentional — LinkedIn-only contacts are
valuable for enrichment.

To get email-only contacts:
- `io_outreach_ready_contacts` — hard-filtered to verified deliverable emails
- `io_assess_contact_quality` — grades all contacts; use grade A/B subset
- `io_search_persons(email="@")` — any contact with a non-empty email

### Short firm names in `named_firms`

Names shorter than 4 characters use `name*` (prefix only) to avoid false positives.
For example, `"nea"` would match `"lineage"` and `"cornea"` with a substring search.
Names 4+ characters use `*name*` (full substring).

### Embedding coverage

Only ~290K of 234K investors have 3072-dim embeddings in `investors_embeddings_3072`.
`find_similar_investors` returns `NOT_FOUND` for investors outside this set.
New embedding generation is currently unavailable (OpenAI quota exceeded on the IO side).

### Export polling

`io_export_contacts` polls via PostgREST GET (not WebSocket). It polls every 3 seconds
for up to 60 seconds. For large exports, the status may be `"timeout"` — use the
returned `export_id` to poll manually via the `user_exports` table.

---

## 9. Limitations

The IO database contains investor and contact data only. It does **not** contain:

| What is missing | Why it matters |
|----------------|---------------|
| Deal history | Cannot tell whether an investor has completed a deal in a sector before |
| Portfolio company list | Cannot identify existing portfolio overlap with a target company |
| Fund vintages | Cannot determine a fund's current deployment stage |
| LP lists | Cannot identify who has committed capital to a specific fund |
| Deal flow / pipeline | No information on deals investors are currently evaluating |
| AUM as numeric | `capital_under_management` is a free-text field ("$500M"); not queryable numerically |
| Preferred industry as structured array | `preferred_industry` is a comma-delimited text field; use ilike, not array operators |
| Geography as structured enum | `preferred_geography` is free text; values range from "United States" to "US" to "San Francisco, California" |
| contact freshness | No `last_seen` or LinkedIn activity timestamp; contact data may be months old |
| Email bounce history beyond a flag | `last_bounce_type` indicates a bounce occurred; no bounce date or bounce count in most records |
| investors_scraped_data | Table exists but has 0 rows |
| extracted_industries | Unpopulated; 0 rows |
| corporations table | Empty |

**Coverage limitations by field**:

| Field | Coverage | Notes |
|-------|----------|-------|
| sectors_array | 234K (100%) | 120 codes, but generalists carry 10+ tags |
| preferred_investment_types | 198K (84%) | Most reliable deal-stage signal |
| description | 297K (97%) | Free text; best for niche matching |
| check_size_min/max | 308K | Values in MILLIONS USD |
| preferred_geography | 77K (33%) | Sparse; inconsistent format |
| preferred_investment_amount_low/high | 23K (10%) | Very sparse; use check_size instead |
| capital_under_management | 41K (17.5%) | Free-text, not numeric |
| persons.email | ~53% | 47% unknown deliverability, 3% undeliverable |
| persons.phone | ~40% | Estimated from channel coverage data |
| persons.linkedin_profile_url | ~60% | Estimated; use `io_enrich_priorities` for LinkedIn-only contacts |
