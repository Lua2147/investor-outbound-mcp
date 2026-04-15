# Investor Outbound MCP — Architecture Reference

Technical reference for engineers integrating with or extending the Investor Outbound MCP server. Explains design decisions, internal patterns, and system architecture.

**Version:** 1.0.0 | **Tools:** 30 | **Tests:** 591 | **Modules:** 8

---

## Table of Contents

1. [Overview](#overview)
2. [Module Structure](#module-structure)
3. [Client Layer](#client-layer)
4. [Entity Layer](#entity-layer)
5. [Sector Resolution](#sector-resolution)
6. [Scoring and Gating Engine](#scoring-and-gating-engine)
7. [Tool Design Patterns](#tool-design-patterns)
8. [Concurrency Model](#concurrency-model)
9. [Discovery Pipeline](#discovery-pipeline)
10. [Testing Strategy](#testing-strategy)
11. [Deployment](#deployment)
12. [Key Decisions](#key-decisions)

---

## Overview

The Investor Outbound MCP wraps a Supabase PostgREST backend (234K investors, 1.8M contacts) as 30 MCP tools organized into 8 Python modules. It uses email+password JWT authentication through an async httpx client with automatic token refresh.

```
                         ┌──────────────────────┐
                         │    MCP Consumer       │
                         │ (Claude / agent SDK)  │
                         └──────────┬───────────┘
                                    │ MCP tool call
                         ┌──────────▼───────────┐
                         │     FastMCP server    │
                         │   src/server.py        │
                         │  stdio | SSE :8770    │
                         └──────────┬───────────┘
                                    │ auto-discover & register
                    ┌───────────────┼──────────────────────┐
                    │               │                      │
          ┌─────────▼──────┐ ┌─────▼──────┐ ┌────────────▼──────┐
          │  deal_matching  │ │  investor_  │ │  contact_retrieval │
          │  (4 tools)      │ │  discovery  │ │  (4 tools)         │
          │                 │ │  (4 tools)  │ │                    │
          └─────────┬───────┘ └─────┬──────┘ └────────────┬───────┘
                    │               │                      │
                    └───────────────┼──────────────────────┘
                                    │
                         ┌──────────▼───────────┐
                         │      IOClient         │
                         │  src/client.py        │
                         │  httpx.AsyncClient    │
                         └──────────┬───────────┘
                                    │ HTTPS
                         ┌──────────▼───────────┐
                         │  Supabase PostgREST   │
                         │  lflcztamdsmxbdkqcumj │
                         │  (EU region)          │
                         │                       │
                         │  investors     234,549 │
                         │  persons     1,806,686 │
                         │  embeddings    290,865 │
                         └──────────────────────┘
```

### Request flow

1. Consumer calls an MCP tool (e.g., `match_deal`).
2. FastMCP dispatches to the registered async function in the tool module.
3. The tool constructs one or more `QueryBuilder` instances.
4. `IOClient.query()` / `IOClient.rpc()` sends authenticated HTTPS requests to Supabase PostgREST.
5. The response is deserialized, passed through Pydantic entity models, scored and gated (for deal-matching tools), then serialized to the standard JSON envelope.
6. FastMCP returns the string result to the consumer.

### Data model at a glance

| Table | Rows | Key fields |
|-------|------|-----------|
| `investors` | 234,549 | `id`, `investors` (name), `primary_investor_type`, `sectors_array`, `check_size_min/max` (millions USD), `investor_status`, `contact_count` |
| `persons` | 1,806,686 | `id`, `email`, `phone`, `role`, `linkedin_profile_url`, `investor` (FK), `email_status`, `good_email` |
| `investors_embeddings_3072` | 290,865 | `investor_id`, `embedding` (text — stringified float array) |
| `user_exports` | varies | `id`, `status`, `download_url` — polled by `io_export_contacts` |

**Critical data facts confirmed in Phase 0:**

- `check_size_min` / `check_size_max` are stored in **MILLIONS USD**. `10` = $10M. `1000` = $1B.
- `preferred_investment_types` is a **TEXT string** (comma-delimited), not `text[]`. The `cs` array-contains operator fails. Use `ilike` only.
- `investor_status` values: `"Actively Seeking New Investments"` and `"Acquired/Merged"`. Tools exclude `"Acquired/Merged"` by default.
- `email_status` values: `"deliverable"` (~47%), `"unknown"` (~47%), `"undeliverable"` (~3%), `"risky"` (~2%).
- Nested joins from `persons` to `investors` via PostgREST `!inner` syntax return PGRST200 (no FK in schema cache). The two-step pattern (query investors → query persons by investor IDs) is mandatory.

---

## Module Structure

```
investor-outbound-mcp/
├── src/
│   ├── server.py                  # FastMCP entry point. Auto-discovers src/tools/*.py
│   ├── client.py                  # IOClient: auth, QueryBuilder, rpc(), edge(), caching
│   ├── scoring.py                 # Scoring engine + 6-gate pipeline (passes_deal_relevance)
│   ├── sectors.py                 # Sector/investor type/investment type resolution
│   ├── helpers.py                 # Response envelope helpers (tool_response, error_response)
│   ├── entities/
│   │   ├── __init__.py            # Re-exports all entity models and formatters
│   │   ├── investor.py            # InvestorSummary/Detail, select strings, formatters
│   │   ├── person.py              # PersonSummary/Detail, select strings, formatters
│   │   └── corporation.py         # CorporationSummary/Detail stub (table empty)
│   └── tools/
│       ├── deal_matching.py       # Tools 1–4:  match_deal, match_deal_stage, match_preferences, find_similar_investors
│       ├── investor_discovery.py  # Tools 5–8:  io_search_investors, io_search_descriptions, io_get_investor, io_investor_freshness
│       ├── contact_retrieval.py   # Tools 9–12: io_get_contacts, io_search_persons, io_get_investor_team, io_find_decision_makers
│       ├── reverse_lookup.py      # Tools 13–17: lookup_by_email_domain, lookup_by_linkedin, reverse_company_lookup, batch_firm_lookup, batch_person_lookup
│       ├── outreach_readiness.py  # Tools 18–21: io_outreach_ready_contacts, io_assess_contact_quality, io_channel_coverage, io_enrich_priorities
│       ├── multi_deal_intel.py    # Tools 22–25: io_find_cross_deal_investors, io_deal_coverage_gaps, io_investor_funnel, io_deduplicate_across_deals
│       ├── analytics.py           # Tools 26–27: io_sector_landscape, io_check_size_distribution
│       └── export_hygiene.py      # Tools 28–30: io_export_contacts, io_stale_contact_check, io_search_by_company_industry
├── tests/
│   ├── conftest.py
│   ├── test_client.py             # 63 tests
│   ├── test_scoring.py            # 74 tests
│   ├── test_sectors.py            # 34 tests
│   ├── test_entities.py           # 36 tests
│   ├── test_helpers.py            # 28 tests
│   ├── test_deal_matching.py      # 49 tests
│   ├── test_investor_discovery.py # 43 tests
│   ├── test_contact_retrieval.py  # 46 tests
│   ├── test_reverse_lookup.py     # 46 tests
│   ├── test_outreach_readiness.py # 59 tests
│   ├── test_multi_deal_intel.py   # 36 tests
│   ├── test_analytics.py          # 42 tests
│   └── test_export_hygiene.py     # 35 tests
├── data/
│   ├── contradiction_resolution.json  # Phase 0 live probe results (5 tests)
│   └── validation_doosan.json         # Phase 4 live validation (real deal)
├── docs/                          # Discovery docs (01–14), extractor config, live samples
├── ops/
│   └── investor-outbound-mcp.service  # systemd unit file
└── pyproject.toml
```

### Registration flow

Every tool module exports a `register(mcp, client)` function. `server.py` auto-discovers all `.py` files under `src/tools/` at startup and calls each `register()`:

```python
# server.py — simplified
for module_name in sorted(f"src.tools.{p.stem}" for p in _TOOLS_DIR.glob("*.py") if p.stem != "__init__"):
    mod = importlib.import_module(module_name)
    mod.register(mcp, client)
```

Modules missing a `register()` function emit a warning but do not crash the server. The built-in `io_health` tool is registered directly in `server.py` (not auto-discovered) because it needs access to the module count.

---

## Client Layer

### Auth model

`IOClient` uses Supabase email+password authentication to obtain a user JWT (2-hour expiry). The anon key is embedded in the Vite bundle (public, non-secret) and is used only in the `apikey` header alongside the user Bearer token.

```
env IO_EMAIL + IO_PASSWORD
    │
    ▼  POST /auth/v1/token?grant_type=password
Supabase Auth
    │
    ▼  { access_token, refresh_token, expires_in: 7200 }
IOClient._token  (stored in memory)
    │
    ▼  Authorization: Bearer <token>
    │  apikey: <anon key>
PostgREST /rest/v1/*
```

Token lifecycle:
- `_ensure_auth()` is called before every request.
- If token expires within 60 seconds, `_do_refresh()` is attempted first using the stored `refresh_token`.
- If refresh fails (400/401), falls back to full `_do_login()`.
- On a mid-call 401 (edge case for race conditions), the client invalidates the token and re-auths inline before retrying once.

Credentials load order:
1. `IO_EMAIL` / `IO_PASSWORD` environment variables.
2. `config/api_keys.json` under key `supabase_investor_outreach.email` / `.password`.

### QueryBuilder

`QueryBuilder` is a fluent interface that builds the `params` list passed to httpx. It handles URL encoding transparently — callers never deal with percent-encoding.

```python
qb = (
    QueryBuilder("investors")
    .select("id,investors,primary_investor_type,sectors_array")
    .ov("sectors_array", ["energy", "clean_tech"])    # array overlap
    .in_("primary_investor_type", ["PE/Buyout"])       # set membership
    .gte("check_size_min", 5.0)                        # min $5M (in millions)
    .lte("check_size_max", 500.0)                      # max $500M
    .neq("investor_status", "Acquired/Merged")
    .gt("contact_count", 0)
    .order("completeness_score", ascending=False)
    .limit(200)
    .offset(0)
)
rows, total = await client.query(qb, count="estimated")
```

Supported operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in_`, `is_`, `not_is`, `like`, `ilike`, `cs` (array contains), `ov` (array overlap), `fts`, `plfts`, `select`, `order`, `limit`, `offset`. The `raw()` method is an escape hatch for anything not covered.

The `count` parameter controls the `Prefer: count=X` header:
- `"estimated"` (default) — fast, safe on large tables.
- `"exact"` — may time out on the 1.8M persons table. Avoid on persons queries.
- `None` — suppresses the header (no Content-Range total returned).

### RPC wrapper

`IOClient.rpc(function, body)` POST to `/rest/v1/rpc/{function}`. Two behaviors worth noting:

1. **Null coercion:** Empty Python lists in `body` are automatically converted to `null` before sending. PostgREST array parameters must be `null` (not `[]`) when a filter is absent, otherwise the function may return zero results.
2. **Retry with backoff:** Up to 2 retries on `IOTransientError`, with `2^attempt` second sleep between attempts.

### Edge function wrapper

`IOClient.edge(function, body, timeout=120.0)` POST to `/functions/v1/{function}`. Used by `io_export_contacts` to trigger the `export2` edge function. Default timeout is 120 seconds because edge functions can take up to a minute to generate a CSV for large result sets.

### Error taxonomy

| Class | Trigger | Action |
|-------|---------|--------|
| `IOAuthError` | HTTP 401 | Re-authenticate. Do not retry the original request without re-auth. |
| `IOQueryError` | HTTP 400, 404, 422 | Bad query or wrong operator. Do NOT retry — fix the query. |
| `IOTransientError` | HTTP 429, 5xx, timeout | Retry with exponential backoff. |

All three inherit from `IOError`. Tool modules catch each class individually and map to the appropriate `error_response()` code.

### LRU caching

Two cache layers:

- **Investor ID cache** (`_INVESTOR_CACHE`): 60-second TTL dict keyed by investor `id`. Used by `get_investor_by_id()`. Prevents redundant fetches when tools enrich multiple contacts from the same firm.
- **Sector resolution cache** (`@lru_cache`): Indefinite — sector codes are static reference data that never changes at runtime.

---

## Entity Layer

Three entity modules under `src/entities/`, each providing a Summary model (lightweight, used in list responses) and a Detail model (all columns, used in single-record fetches), plus PostgREST select strings and formatters.

### Investor

| Model | Select string | Fields |
|-------|--------------|--------|
| `InvestorSummary` | `INVESTOR_SELECT_SUMMARY` | id, investors (name), primary_investor_type, types_array, sectors_array, capital_under_management, check_size_min, check_size_max, hq_location, hq_country_generated, investor_website, contact_count, has_contact_emails, investor_status, preferred_investment_types, preferred_industry, preferred_geography, completeness_score |
| `InvestorDetail` | `INVESTOR_SELECT_DETAIL` = `"*"` | All 47 columns |

Key field notes:
- `investors` — the firm name (column is named `investors`, not `name`).
- `check_size_min` / `check_size_max` — `Optional[float]`, units are **MILLIONS USD**. `10.0` = $10M.
- `preferred_investment_types` — `Optional[str]`, comma-delimited text. `"Buyout/LBO, PE Growth/Expansion"`.
- `investor_status` — `Optional[str]`. Values: `"Actively Seeking New Investments"`, `"Acquired/Merged"`, or null.
- `investment_types_array` — the actual array of investment types this investor participates in (separate from `preferred_investment_types`).

The `check_size_display(min_m, max_m)` helper formats DB values to human-readable strings: `"$5M – $50M"`, `"$1B+"`, etc.

### Person

| Model | Select string | Fields |
|-------|--------------|--------|
| `PersonSummary` | `PERSON_SELECT_SUMMARY` | id, first_name, last_name, email, phone, role, company_name, linkedin_profile_url, location, investor (FK → investors.id) |
| `PersonDetail` | `PERSON_SELECT_DETAIL` = `"*"` | All 34 columns, including PB cross-refs, email quality fields, bounce tracking, company metadata |

Key field notes:
- `investor` — integer FK to `investors.id`. PostgREST does not expose a join relationship from the persons table.
- `email_status` — pre-verified: `"deliverable"`, `"unknown"`, `"undeliverable"`, `"risky"`.
- `good_email` — boolean shorthand for `email_status == "deliverable"` and `email_score` above threshold.
- `email_score` — numeric (0–100). Below 30 is considered stale by `io_stale_contact_check`.
- `domain` — email domain extracted from email address (not full email). Used by `lookup_by_email_domain`.

The `email_quality_label(person)` helper maps email fields to: `"Good"`, `"Risky"`, `"Unknown"`, `"Bad"`, `"No email"`.

### Corporation

`CorporationSummary` and `CorporationDetail` are stubs. The `corporations` table currently has 0 rows. The models are in place for future data population.

---

## Sector Resolution

`src/sectors.py` maps human-readable names to the actual DB values used in PostgREST queries.

### Three resolution functions

| Function | Input | Maps to | DB operator |
|----------|-------|---------|-------------|
| `resolve_sectors(names)` | `["energy", "cleantech"]` | snake_case DB codes | `ov` (array overlap on `sectors_array`) |
| `resolve_investor_types(names)` | `["vc", "family office"]` | `primary_investor_type` enum values | `in_` |
| `resolve_investment_types(names)` | `["seed", "buyout"]` | `investment_types_array` / `preferred_investment_types` values | `ilike` or `ov` |

Each function accepts both human-readable aliases and raw DB values (pass-through), and falls back to fuzzy substring matching against known keys. This means `"energy storage"` will resolve because `"energy"` is a substring of the key `"energy"`.

### Sector map — 120 DB codes

The `SECTOR_MAP` covers 120 unique snake_case sector codes organized by domain:

- Energy: `energy`, `cleantech`, `clean_tech`, `renewableenergy`, `green_energy`, `newenergy`, `utilities`, `oil_gas`
- Healthcare: `healthcare`, `health_care`, `healthtech`, `biotech`, `pharma`
- Technology: `technology`, `software_saas`, `ai_ml`, `cybersecurity`, `enterprisesoftware`
- Financial: `fintech`, `fin_services`, `fin_invest`, `insurance`, `insurancetech`
- Consumer/Retail: `consumer`, `consumer_discr`, `ecommerce`, `retail`
- Real Estate: `real_estate`, `realestate`, `re_lending` (+ 10 sub-categories)
- Other 30+ codes: `industrial`, `automotive`, `blockchain`, `edtech`, `agritech`, `mining`, `cannabis`, `space_tech`, etc.

### Investor types — 52 values

`INVESTOR_TYPES` contains all 52 known `primary_investor_type` DB values. `_INVESTOR_TYPE_MAP` maps ~80 human aliases. Examples:

- `"pe"` → `["PE/Buyout", "Private Equity", "Private Equity Firm"]`
- `"family office"` → `["Family Office", "Family Office - Single", "Family Office - Multi"]`
- `"ria"` → `["Wealth Management/RIA"]`

### Investment types — 78 values + 5 presets

`INVESTMENT_TYPES` contains 78 known DB values used in `investment_types_array` and `preferred_investment_types`.

Five named presets for common deal type combinations:

| Preset | Values included |
|--------|----------------|
| `seed` | Seed Round, Seed, Angel (individual), Start-up, Accelerator/Incubator |
| `series_a` | Early Stage VC, Early Stage, Venture (General) |
| `growth` | Growth, Later Stage VC, Late Stage Venture, PE Growth/Expansion, Expansion / Late Stage, Venture (General) |
| `buyout` | Buyout/LBO, Buyout, Management Buyout, Management Buy-In, Merger/Acquisition, Add-on, Carveout, Acquisition Financing, Corporate Divestiture, Asset Acquisition, Public to Private |
| `fund_raise` | Venture (General), Early Stage VC, Later Stage VC, Growth, PE Growth/Expansion, Expansion / Late Stage, Seed Round, Seed, Start-up |

---

## Scoring and Gating Engine

This is the most critical section of the architecture. The engine was validated on 206K contacts across 5 real deals before any code was written for this MCP.

### Why sector-array matching alone is useless

Filtering `sectors_array=ov.{energy}` returns 14,000+ investors including every generalist VC/PE that tags "energy" as one of 10 sectors. A $70M energy infrastructure deal produces 206K raw contacts from a sector-only pull. Only ~19K of those are actionable.

The sector filter is a pre-filter — it reduces the search space from 234K to 14K investors. The real quality gate is contact-level.

### 6-gate pipeline (`passes_deal_relevance`)

Applied to every person fetched by `match_deal`. Returns `(bool, str)` — the string is the gate-fail reason or the match-path letter.

| Gate | Check | Fail reason |
|------|-------|-------------|
| 1 | `score >= min_score` (default 20) | `"score<min"` |
| 2 | `len(role.strip()) >= 3` | `"no_role"` |
| 3 | `not is_junk_role(role)` — 67 regex patterns | `"junk_role"` |
| 4 | `not role_is_firm_name(role, company, investor)` | `"role=firm_name"` |
| 5 | `is_senior(role)` OR `has_investment_function(role)` | `"not_senior_not_investment"` |
| 6 | Deal relevance — 7 alternative paths (A–F2) | `"no_deal_relevance"` |

### Gate 6: Deal relevance paths

| Path | Condition | Notes |
|------|-----------|-------|
| A | `role` contains a `role_keyword` | Strongest signal. E.g., "energy" in "Senior Managing Director, Energy Infrastructure". |
| B | Named firm match + (senior OR investment function) | `named_firms` always passes this path. Use for known target firms like KKR, Brookfield. |
| C | Firm name matches `firm_keywords` + has investment function | "Energy Capital Partners" matches firm_keyword "energy" + role is investment function. |
| D | Firm name matches `firm_keywords` + senior + score >= 35 | For senior generalists at relevant-named firms. |
| E | `sectors_str` matches `firm_keywords` + senior + investment function + score >= 35 | Fallback when firm name is generic but sector tags align. Sector-array used only here. |
| F1 | `expanded=True` + senior + investment function | For niche deals: any senior deal-maker passes. |
| F2 | `expanded=True` + investment function + score >= 30 | For niche deals: non-senior deal professionals with decent scores pass. |

**Critical distinction:** `role_keywords`, `firm_keywords`, and `named_firms` serve different functions. Role keywords match contact titles. Firm keywords match company/investor names. Named firms are exact-match allowlist entries. Passing the wrong keyword to the wrong list degrades precision.

### Numeric scoring (`score_contact`)

| Signal | Points |
|--------|--------|
| Top seniority (Partner, MD, GP, CEO, Founder, CIO, President) | +30 |
| Mid seniority (VP, Director, Head of, SVP, EVP, Principal) | +20 |
| Junior investment (Analyst, Associate) | +5 |
| Investment function present in role | +15 |
| Each `deal_keyword` present in role | +10 |
| Junk role detected | −30 |

Minimum useful score: 20 (default `min_score`). Scores >= 35 are required for paths D and E.

### Contact data bonus (`contact_data_bonus`)

Optional additive bonus for ranking contacts by data completeness:

| Channel | Points |
|---------|--------|
| Email present | +3 |
| LinkedIn present | +2 |
| Phone present | +1 |

Range: 0–6. Applied by tools that need to rank contacts within the same score bucket (e.g., preferring email-confirmed contacts over LinkedIn-only contacts).

### Junk role filter (67 patterns)

Regex patterns covering: HR, payroll, recruiting, administrative, receptionist, customer service, IT support, software engineer, network engineer, warehouse, shipping, maintenance, facilities, social media, graphic design, content writer, compliance officer, accountant, bookkeeper, intern, student, nurse, physician, teacher, professor, reservoir engineer, geologist, property manager, product manager, general counsel, marketing manager, project manager. Full list in `src/scoring.py`.

### Role-is-firm-name detection

`role_is_firm_name(role, company_name, investor_name)` catches low-quality CRM records where the data pipeline populated the `role` column with the organisation name instead of a job title (e.g., role = "Kayne Anderson Capital Advisors"). Returns `True` if `role` equals or starts with the first 20 chars of either name.

### Phase 4 validation results (Doosan deal — energy infrastructure)

Live test of `match_deal` with `expanded=True`:

- Investors scanned: 11,946
- Persons scored: 148,194
- Contacts passing gate: 18,335
- After per-firm cap (5) and max_results (1000): 1,000 contacts across 505 firms
- With email: 820
- Match path distribution: E=632, A=161, C=95, F1=94, B=18
- Top contacts: Senior Managing Director / Portfolio Manager at Kayne Anderson Capital, Daiwa Energy & Infrastructure (scores of 65)

---

## Tool Design Patterns

### Deal matching — two-phase architecture

`match_deal` is the highest-value tool. It runs two sequential phases:

**Phase 1: Broad investor pull** (multiple concurrent-ish PostgREST queries)

Five query strategies, each returning up to 2,000–5,000 investors:
1. Sector overlap (`sectors_array ov {codes}`) + investor type + check size
2. Description ilike for each `description_keyword`
3. Name ilike for each entry in `named_firms`
4. `preferred_investment_types` ilike for each resolved deal stage value
5. Type-only query when no sectors provided (family offices, angels)

All results are merged and deduplicated by `id` in Python.

**Phase 2: Fetch persons + score/gate**

Investor IDs are chunked to 100 per `in.(...)` query to stay within URL length limits. Persons are fetched in batches (`_PERSONS_BATCH_LIMIT = 5000`). Each person is scored, passed through the 6-gate pipeline, then capped at `max_per_firm` contacts per investor. Final global deduplication removes persons appearing under multiple investors.

### Search tools

`io_search_investors` and `io_search_descriptions` are thin tools: one PostgREST GET, Pydantic formatting, paginated response. They do not apply the scoring pipeline — they return investor summaries, not scored contacts.

### Batch tools

`batch_firm_lookup` and `batch_person_lookup` chunk their input lists to stay under URL length limits. Firm names are queried one at a time via `ilike` (PostgREST has no bulk OR). Emails are queried using `in_()` in batches of 100.

### Analytics aggregation

`io_sector_landscape` and `io_check_size_distribution` fetch up to 5,000 investors and compute statistics in Python — type breakdowns, geographic counts, and check-size histograms. No server-side aggregation (Supabase is a hosted backend with no ad-hoc SQL access from MCP). Check-size buckets are defined in millions: `<$1M`, `$1–5M`, `$5–25M`, `$25–100M`, `$100M–$1B`, `$1B+`.

### Export polling

`io_export_contacts` triggers the `export2` edge function and then polls `user_exports` via PostgREST GET every 3 seconds for up to 60 seconds. It does not use WebSockets or Supabase Realtime — polling is simpler and avoids an additional connection dependency. On timeout it returns the current status with a signed URL if one is available.

### Standard response envelope

All tools return JSON strings with this shape:

```json
{
  "data": ...,
  "summary": "480 contacts across 210 firms (from 1420 investors, 82000 persons scored).",
  "meta": { "total": 48000, "page": 1, "page_size": 50, "has_more": true },
  "next_actions": ["Call io_outreach_ready_contacts to filter to email-deliverable contacts"]
}
```

Error responses:

```json
{
  "error": {
    "code": "AUTH_FAILED",
    "message": "Authentication expired — re-login required",
    "details": "401 Unauthorized"
  }
}
```

Error codes: `AUTH_FAILED`, `QUERY_ERROR`, `RATE_LIMITED`, `SERVER_ERROR`, `VALIDATION_ERROR`, `NOT_FOUND`, `TIMEOUT`.

---

## Concurrency Model

### No rate limiting

Unlike PitchBook MCP (which serializes all requests through a thread lock and PaceTracker to prevent account bans), the Investor Outbound backend is a standard Supabase project with no ban risk. The `IOClient` uses `httpx.AsyncClient` without any artificial delay or lock.

Multiple tool calls can execute concurrently in the FastMCP event loop without risking the account.

### No PaceTracker

There is no equivalent of PitchBook's `PaceTracker` or `@pb_tool` decorator. Tool registration uses `@mcp.tool()` directly.

### httpx timeouts

```python
timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=5.0)
```

The 90-second read timeout accommodates large PostgREST queries (e.g., fetching 5,000 persons rows for a deal match). Edge function calls use a separate `timeout=120.0` parameter.

---

## Discovery Pipeline

Phase 0 ran five live contradiction tests against the production Supabase instance. Results are archived in `data/contradiction_resolution.json`.

### 5 contradictions resolved

| Test | Verdict | Implication |
|------|---------|-------------|
| Nested joins (persons → investors `!inner`) | **BROKEN** — PGRST200 | No FK in schema cache. Two-step pattern is mandatory for all cross-table lookups. |
| `sectors_tsv` FTS (`fts.energy`) | **PARTIAL** — `energy` returns 0, `fintech` returns 5 | Unreliable coverage. Do not use as primary filter. Use `sectors_array ov {code}` instead. |
| `preferred_investment_types` array `cs` operator | **BROKEN** — operator error | Field is TEXT, not `text[]`. Use `ilike.*Value*` only. |
| `check_size_min/max` units | **CONFIRMED MILLIONS** — $50B firm shows 50000.0 | All tools must divide user dollar inputs by 1,000,000. |
| `investors_embeddings_3072` readability | **WORKS** — 290,865 rows readable with user JWT | Embedding is stored as stringified float array text; must parse with `split(",")` after strip. |

### What the discovery revealed about the data

- `sectors_tsv` is populated only for name-keyword-matched investors (not semantic). The `sectors_array` with `ov` operator is reliable.
- The `corporations` table is empty (0 rows).
- `investors_scraped_data` is empty (0 rows).
- `extracted_industries` and `extracted_additional_industries` are unpopulated.
- The `investments` column on `investors` is an integer (count) not deal history.
- New embeddings cannot be generated (OpenAI quota exhausted on their end). Existing 290,865 embeddings are queryable.

---

## Testing Strategy

### 591 tests, fully offline

All tests run against a mock `IOClient` using `unittest.mock.AsyncMock`. No live Supabase connection is needed.

```bash
pytest tests/ -v
```

### Test categories

| File | Tests | What it covers |
|------|-------|---------------|
| `test_client.py` | 63 | Auth flow (login, refresh, re-auth on 401), QueryBuilder operators, RPC null coercion, error taxonomy, content-range parsing |
| `test_scoring.py` | 74 | All 6 gates, all 7 paths (A–F2), junk patterns, role-is-firm-name, score_contact points, contact_data_bonus |
| `test_sectors.py` | 34 | resolve_sectors fuzzy matching, resolve_investor_types aliases, deal-stage presets, pass-through raw DB values |
| `test_entities.py` | 36 | Pydantic model validation, check_size_display, email_quality_label, null tolerance |
| `test_helpers.py` | 28 | tool_response, paginated_response, stats_response, error_response envelope shapes |
| `test_deal_matching.py` | 49 | Happy path per deal type (buyout/Series A/SAFE/fund raise), empty results, expanded mode, no-sector search, internal helpers |
| `test_investor_discovery.py` | 43 | io_search_investors filter combinations, io_search_descriptions, io_get_investor (by ID and name), io_investor_freshness |
| `test_contact_retrieval.py` | 46 | io_get_contacts scoring, io_search_persons field combinations, io_get_investor_team tier grouping, io_find_decision_makers |
| `test_reverse_lookup.py` | 46 | lookup_by_email_domain, lookup_by_linkedin, reverse_company_lookup, batch chunking behavior |
| `test_outreach_readiness.py` | 59 | Good-email filter, quality grade computation (A/B/C/D), channel coverage counts, enrich priorities ranking |
| `test_multi_deal_intel.py` | 36 | Cross-deal intersection logic, coverage gap detection, funnel step accumulation, deduplication across deals |
| `test_analytics.py` | 42 | Sector landscape breakdowns, check-size histogram bucket assignment, percentile computation |
| `test_export_hygiene.py` | 35 | Export polling loop (ready/timeout), stale contact detection, company industry ilike search |

### Mock strategy

Tool tests receive a mock client via `pytest` fixtures. The mock's `query()` and `rpc()` methods are `AsyncMock` instances returning pre-defined row lists. This decouples tool logic from live network calls and allows testing error paths (auth failure, transient error, empty results) without live Supabase.

---

## Deployment

### Target

SSE transport on port 8770, bound to `127.0.0.1`.

### systemd service

```ini
# ops/investor-outbound-mcp.service
[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/investor-outbound-mcp
Environment=IO_EMAIL=<set-in-env>
Environment=IO_PASSWORD=<set-in-env>
ExecStart=/usr/bin/python3 -m src.server --sse --port 8770
Restart=on-failure
RestartSec=5
MemoryMax=1G
```

Credentials are injected as environment variables at runtime — not stored in the unit file itself.

### Health check

`io_health` is always registered. It returns:

```json
{
  "status": "ok",
  "auth": "authenticated",
  "uptime_seconds": 3842.1,
  "tool_modules": 8,
  "database": "lflcztamdsmxbdkqcumj"
}
```

`auth` values: `"authenticated"`, `"unauthenticated"`, `"token_expiring_soon"` (within 60s of expiry), `"error"`.

### MCP registration (SSE)

```json
{
  "mcpServers": {
    "investor-outbound": {
      "type": "sse",
      "url": "http://127.0.0.1:8770/sse"
    }
  }
}
```

---

## Key Decisions

### Why PostgREST direct queries instead of RPC for all searches

Phase 0 confirmed that the two existing RPCs (`manual_search_investors_only2` and `get_persons_by_investor_ids`) are limited. Direct PostgREST queries give full filter composability — any column, any operator, any combination. The only case where an RPC is mandatory is semantic similarity search (`ai_search_with_ideal_investor`) because it requires a server-side vector comparison.

### Why scoring beats sector-array filtering

Sector-array overlap is a coarse pre-filter. A generalist PE firm with "energy" as one of 10 tags passes the sector filter but most of their contacts (HR, IT, admin) are irrelevant. The 6-gate pipeline eliminates noise at the contact level. Skipping the gate and relying on sector filters alone produced 206K raw contacts for a single deal — too many to be useful.

### Why no rate limiting

PitchBook's backend detects parallel requests from the same account and bans it. Supabase is a cloud database with standard RLS — it has no ban mechanism for legitimate JWT-authenticated queries. The Investor Outbound account is on a premium tier with 1M credits/month. Rate limiting would add latency for no protective benefit.

### Why auto-discovery registration (not explicit imports in server.py)

Phases 2 and 3 had 8 agents working in parallel worktrees, each creating one tool module. If server.py required explicit imports, every agent would edit it and cause merge conflicts. Auto-discovery (`importlib` glob over `src/tools/*.py`) lets each agent own only their own file. Server.py has no knowledge of specific tool modules.

### Why two-phase match_deal instead of one query

PostgREST nested joins from the `persons` table to `investors` are broken (PGRST200 — no FK in schema cache). A single query filtering on both investor attributes and person attributes is not possible. The two-phase pattern (query investors → query persons by investor IDs) is the only reliable approach.

### Why contacts without email are included in match_deal results

A contact at KKR with a LinkedIn URL but no email is still actionable — the operator can manually source the email, use LinkedIn InMail, or pass the LinkedIn URL to an enrichment pipeline. Excluding no-email contacts would discard 15–20% of high-quality senior contacts. The `io_outreach_ready_contacts` tool exists specifically for callers who want email-only results.

### Why count=estimated (not exact)

`count=exact` requires a full-table scan to compute the precise count. On the 1.8M persons table this consistently times out (90s limit). `count=estimated` uses PostgreSQL's statistics for an approximate total. The total is used only for UI display and `has_more` pagination flags — approximate is sufficient.
