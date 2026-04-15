# Investor Outbound MCP — Master Build Plan

**Version:** 2.0 | **Date:** 2026-04-15
**Target:** Full MCP server wrapping Investor Outbound Supabase backend (234K investors, 1.8M contacts)
**Reference builds:** PitchBook MCP (v2.2.2, 211 tools), CapIQ MCP (v1.2.0, 76 tools)
**Deploy target:** the SSE server server, port 8770, SSE transport

---

## Executive Summary

Build a production MCP server for the Investor Outbound database. 30 tools across 8 categories: deal matching, investor discovery, contact retrieval, reverse lookup, outreach readiness, multi-deal intelligence, analytics, and export/hygiene. Entity layer for Investor, Person, and Corporation. Maximizes parallelism via worktrees with one agent per task. Tests against 5 live deals. Ships to GitHub standalone repo + deploys to the SSE server:8770.

---

## Critical Design Principle — LEARNED FROM SESSION

**Sector-array matching alone is USELESS.** Filtering investors by `sectors_array=ov.{energy}` returns 14K+ results including every generalist VC/PE with "energy" as one of 10 tags. This produced 206K noise contacts in V1.

**The proven approach is a two-phase pipeline:**
1. **Broad investor pull** — PostgREST queries by sector overlap + investor type + named firm ilike search
2. **Tight contact-level gating** — 6-gate pipeline on persons: score threshold, role validation, junk filtering, firm-name-as-role detection, seniority/investment-function gate, and 6-path deal relevance check (role keywords > firm keywords > named firms > sector-array fallback)

**The highest-value filter dimensions are:**
- `preferred_investment_types` (84% coverage) — deal stage fit
- `description` (97% coverage) — investor bios catch what sector tags miss
- Role/firm keyword matching — not sector-array overlap

Every tool that returns contacts MUST implement this two-phase approach. See `~/Desktop/Deal-Investor-Research/segment_v2.py` for the proven implementation.

---

## Pre-Execution Protocol

**MANDATORY FIRST ACTION:** Invoke `superpowers:execute-plan` skill before ANY code is written.

Before ANY phase begins:
1. Invoke `superpowers:execute-plan` skill
2. Read ALL of these documents (in order):
   - This plan: `PLAN.md`
   - Project context: `CLAUDE.md`
   - Discovery docs (markdown): `docs/01-architecture.md`, `docs/02-database-schema.md`, `docs/03-rpc-functions.md`, `docs/04-edge-functions.md`, `docs/05-auth-flow.md`, `docs/06-filter-enums.md`, `docs/07-sample-data.md`, `docs/08-raw-discovery-notes.md`, `docs/13-account-context.md`, `docs/14-postgrest-query-patterns.md`
   - Data reference files (reference as needed): `docs/09-extractor-config.py`, `docs/10-live-samples.json`, `docs/11-account-context.json`, `docs/12-real-enums-from-1k-sample.json`
   - Proven extraction logic: `~/Desktop/Deal-Investor-Research/segment_v2.py`
   - Deal targeting criteria: `~/Desktop/Deal-Investor-Research/INVESTOR_TARGETING_SOP.md`
   - Existing code scaffolds: `src/client.py`, `src/scoring.py`, `src/sectors.py`
3. Invoke `/toolkit-scout` to find all applicable tools for the phase
4. Use the Toolkit Map (Appendix A below) to identify which tools/skills/agents apply to the current phase

After EVERY phase completes — **HARD GATE, no phase advances without passing:**

1. **Invoke `/toolkit-scout`** with phase-specific args to find all applicable review tools
2. **Launch ALL of these in parallel** (one agent per command, ensemble at end):
   - `/review:code` on all changed files → Agent: `code-reviewer`
   - `/review:architecture` on module structure → Agent: `staff-reviewer`
   - `/review:security` on auth/credential handling → Agent: `security-engineer`
   - `/simplify` → 3 parallel sub-agents (code quality, reuse, efficiency)
   - `/grill` → adversarial edge case review
   - `/double-check:verify` → verification pass
   - `/testing:test-coverage` → coverage analysis
   - Skill: `tob-coverage-analysis` → Trail of Bits coverage check
   - Skill: `tob-modern-python` → Python best practices audit
3. **Ensemble:** Collect ALL findings from all parallel agents. Categorize: Critical (blocks) / Major (fix before next phase) / Minor (fix later). 
4. **Fix ALL Critical and Major issues.** Re-run failing reviews until they pass.
5. **Only then:** Git commit with phase tag + advance to next phase.

**This is non-negotiable. No shortcuts. No "we'll fix it later." Every phase is gated.**

---

## Data Inventory

### Field Coverage (from live probing)

| Table | Field | Coverage | Notes |
|-------|-------|----------|-------|
| investors | sectors_array | 234K | 120 unique snake_case codes (energy, cleantech, etc.) |
| investors | preferred_investment_types | 198K (84%) | Comma-delimited string, use ilike. Values: "Seed Round", "Buyout/LBO", "Early Stage VC", etc. |
| investors | preferred_industry | 135K (58%) | Comma-delimited string. 599 unique combinations. |
| investors | preferred_geography | 77K (33%) | Free text: "United States", "US", "San Francisco, California", "Europe" |
| investors | preferred_investment_amount_low/high | 23K (10%) | Sparse. Exact dollar figures when present. |
| investors | description | 297K (97%) | Free text. ilike search: "energy storage"=302, "battery"=284, "franchise"=455, "medtech"=588 |
| investors | check_size_min/max | 308K | **Units: MILLIONS USD** (Phase 0 confirmed: $10M = 10, $1B = 1000, $50B = 50000). Tools accepting dollar amounts must divide by 1,000,000 before querying. |
| investors | capital_under_management | 41K (17.5%) | Text field ("$500M"), not numeric |
| investors | investor_status | 234K | Values: "Actively Seeking New Investments", "Acquired/Merged", null |
| investors | contact_count | 234K | Integer. Filter by gt.0 for investors with contacts. |
| investors | embeddings | 291K | investors_embeddings_3072 table, 3072-dim vectors |
| persons | company_industry | 1.66M (92%) | LinkedIn-style: "financial services", "hospital & health care", "computer software" |
| persons | company_size | 1.69M (94%) | Buckets: "10001+", "1001-5000", "501-1000", "201-500", "51-200", "1-10" |
| persons | company_country | 1.68M (93%) | Standard country names |
| persons | domain | 1.54M (85%) | Email domains |
| persons | email_status | 1.8M (100%) | Values: "deliverable" (~47%), "unknown" (~47%), "undeliverable" (~3%), "risky" (~2%) |
| persons | good_email | 1.8M | Boolean |
| persons | email_score | varies | Numeric deliverability score |

### Investor Type Enum (primary_investor_type — 48+ values, top 15)

```
Wealth Management/RIA, Venture Capital, Angel (individual), Wealth Manager,
Family Office, Other, Accelerator/Incubator, Government, Family Office - Single,
PE/Buyout, Hedge Fund, Real Estate, Asset Manager, Corporate Venture Capital,
Growth/Expansion, Investment Bank, Holding Company, Infrastructure, Impact Investing
```

### Sector Codes (sectors_array — 120 values, top 20)

```
fin_services(453), fin_invest(345), technology(239), business_services(180),
agnostic(154), software_saas(138), real_estate(114), private_equity(101),
health_care(87), ai_ml(83), industrials(77), fintech(65), green_energy(64),
clean_tech(47), energy(39), edtech(39), healthtech(37), agritech(31),
blockchain(30), biotech(29)
```

### What DOESN'T Work

| Feature | Status | Detail |
|---------|--------|--------|
| Nested joins (persons→investors `!inner`) | **BROKEN (Phase 0 confirmed)** | No FK relationship in PostgREST schema (PGRST200). Two-step pattern required. |
| sectors_tsv FTS | **UNRELIABLE (Phase 0 confirmed)** | Works for some keywords (fintech), returns 0 for others (energy, healthcare). Do NOT use as primary filter. |
| preferred_investment_types type | **TEXT string (Phase 0 confirmed)** | `cs` operator fails ("operator does not exist: text @> unknown"). Use ilike only. |
| Embedding generation edge fn | 500 | OpenAI quota exceeded on their end. Existing 290K embeddings still queryable. |
| investors_scraped_data | Empty | 0 rows |
| extracted_industries | Empty | Unpopulated |
| extracted_additional_industries | Empty | 0 rows |
| investments field | Useless | Just an integer, not deal history |

---

## Credential Management

**Runtime:** Credentials load from environment variables `IO_EMAIL` and `IO_PASSWORD`, or from `config/api_keys.json` under key `supabase_investor_outreach`.

**SECURITY:** `docs/05-auth-flow.md` lines 56-58 contain plaintext credentials. These MUST be redacted **immediately in Phase 0, BEFORE the first git commit** — every phase commit would permanently bake them into git history. Create `.gitignore` in Phase 0 to exclude `data/`, `.env`, and sensitive files from the start.

**Account:** Premium tier, 1M credits/month, auto-renewing. Direct PostgREST reads likely don't consume credits. Edge function calls may.

---

## Phase 0: Discovery & Contradiction Resolution (PARTIALLY PARALLEL)

**Goal:** Resolve 3 contradictions, verify untested tables, find new endpoints. Focused verification pass — NOT an exhaustive deep probe.

### 0.0: Security (FIRST — before any commit)
- Redact credentials from `docs/05-auth-flow.md` lines 56-58 (replace with `<REDACTED — load from IO_EMAIL/IO_PASSWORD env vars>`)
- Create `.gitignore` (exclude `data/`, `.env`, `*.pyc`, `__pycache__/`)

### 0.1: Resolve Contradictions (BLOCKING — must complete before tool design)
All 5 tests are independent — **run in parallel:**
1. **Nested joins:** Test `persons?select=*,investors!inner(*)&investors.primary_investor_type=eq.PE/Buyout` live
2. **sectors_tsv FTS:** Test `investors?select=id&sectors_tsv=fts.energy` live
3. **preferred_investment_types type:** Test array contains `cs.{"Seed Round"}` AND string ilike `*Seed Round*`
4. **check_size units:** Probe `investors?select=check_size_min,check_size_max&check_size_min=gt.0&limit=10`
5. **Embedding table readability:** Test `investors_embeddings_3072?select=investor_id&limit=1`

### 0.2: Targeted Endpoint Discovery (PARALLEL with 0.1 — independent tasks)
Run these in parallel with 0.1:
- Download Vite bundle, grep for NEW RPCs/edge functions
- Test untested tables from `docs/13`: `user_exports`, `api_keys`, `investor_search_cache`
- Probe `investors_embeddings_3072` for direct vector reads

### 0.3: Update Tool Designs (after 0.1 + 0.2 complete)
- If nested joins work → simplify tools that currently require two-step
- If FTS works → add to search tools as option
- If preferred_investment_types is text[] → use cs operator instead of ilike

### Phase 0 Deliverables
- `data/contradiction_resolution.json` — results of all 5 contradiction tests
- `data/new_endpoints.json` — any newly discovered RPCs/edge functions
- `.gitignore` created
- Updated PLAN.md if tool designs change
- Commit: `feat(io-mcp): phase 0 — discovery + contradiction resolution`

---

## Phase 1: Foundation (PARTIALLY PARALLEL — 3 independent tracks then merge)

**Goal:** Build the core infrastructure that all tools depend on.

**Parallelism via worktrees:** Tasks 1.1 (client), 1.2 (sectors), 1.3 (scoring) have NO cross-dependencies — 3 worktrees, 3 agents, merge. Then 1.4 (entities) depends on 1.1. Then 1.5 (server) depends on all. Then 1.6-1.8 can run in 3 parallel worktrees.

```
Worktree io-mcp/p1-client:  Task 1.1 (client)  ── Agent ─┐
Worktree io-mcp/p1-sectors: Task 1.2 (sectors) ── Agent ─┤─→ [MERGE] → Task 1.4 (entities) → Task 1.5 (server)
Worktree io-mcp/p1-scoring: Task 1.3 (scoring) ── Agent ─┘                                          │
                                                                                                      ▼
                                                              Worktree io-mcp/p1-tests:    Task 1.6 ── Agent ─┐
                                                              Worktree io-mcp/p1-pyproject: Task 1.7 ── Agent ─┤→ [MERGE] → REVIEW
                                                              Worktree io-mcp/p1-systemd:  Task 1.8 ── Agent ─┘
```

**6 worktrees in Phase 1** (3 + merge + 3 + merge).

### Task 1.1: Client Layer (`src/client.py`)
- Supabase auth (email+password → JWT, auto-refresh)
- **Credentials from:** `IO_EMAIL`/`IO_PASSWORD` env vars, fallback to `config/api_keys.json` under `supabase_investor_outreach`
- PostgREST query builder with all operators from Phase 0
- RPC call wrapper (**must pass null, not [] for empty array params**)
- Edge function call wrapper
- Connection pooling via httpx.AsyncClient
- No rate limiting needed (per user instruction — IO has no ban risk)
- **Error taxonomy:** Define `IOAuthError` (401 → re-auth), `IOQueryError` (400 → bad query, don't retry), `IOTransientError` (500/timeout → retry with backoff)
- **Count mode:** Default to `count=estimated` (NOT `count=exact` which times out on persons table). Make exact count opt-in.
- **LRU caching:** Cache investor lookups by ID (60s TTL), sector resolution (indefinite — static data)
- **URL encoding:** Use httpx's built-in params support, NOT manual `.replace(" ", "%20")`
- **Already partially written** — extend from existing `src/client.py` scaffold. Fix the hardcoded `count=exact`.

### Task 1.2: Sector Resolution (`src/sectors.py`)
- **Already complete.** 120 sector codes mapped with fuzzy matching.
- Review and extend `INVESTOR_TYPES` list (currently 16 values, real data has 48+). Full enum source: `docs/12-real-enums-from-1k-sample.json` under `primary_investor_type` key. Add missing types: Financial Advisor, Wealth Manager, Investment Consultant, Limited Partner, etc.

### Task 1.3: Contact Scoring & Gating Engine (`src/scoring.py`)
- **Numeric scoring** (existing `score_contact()`):
  - Seniority: Partner/MD/GP +30, VP/Director +20, Analyst +5
  - Investment function detection: +15
  - Deal keyword matching: +10 per keyword
  - Junk role filtering: -25 to -30 (sync to segment_v2.py's full 43 patterns — add: janitor, security guard, driver, facilities manager, reservoir engineer, geologist, drilling, leasing agent, property manager)
- **Boolean gating** (NEW — `passes_deal_relevance()`):
  - Gate 1: Minimum score threshold (default 20)
  - Gate 2: Must have real role (>= 3 chars)
  - Gate 3: Not a junk role (43 regex patterns)
  - Gate 4: Role is not just the firm name repeated (`role_is_firm_name()` — already in scoring.py, must be used as gate)
  - Gate 5: Must be senior OR have investment function
  - Gate 6: Deal relevance via 6 alternative paths:

| Path | Condition |
|------|-----------|
| A | Role contains a `role_keyword` (strongest signal) |
| B | Named target firm + person is senior or has investment function |
| C | Firm name matches `firm_keywords` + person has investment function |
| D | Firm name matches + person is senior + score >= 35 |
| E | Sector-array matches firm_keywords + person is senior + has investment function + score >= 35 |
| F1 | `expanded=True` + senior + investment function: any senior investment person passes |
| F2 | `expanded=True` + investment function + score >= 30: non-senior investment people with decent score also pass |

- **Inputs must distinguish:** `role_keywords` (matched against role), `firm_keywords` (matched against investor/company name), `named_firms` (exact firm match — always include)

### Task 1.4: Entity Layer (`src/entities/`)
**Reference:** PitchBook `src/tools/entity_handlers/` pattern

- `src/entities/__init__.py`
- `src/entities/investor.py` — Pydantic model for Investor (47 columns), summary formatter (name, type, AUM, sectors, HQ, check size, status, contact_count), detail formatter (all fields), PostgREST select strings
- `src/entities/person.py` — Pydantic model for Person (34 columns), summary formatter (name, role, email, phone, LinkedIn, company), scoring integration, email quality assessment
- `src/entities/corporation.py` — Stub entity (table empty), ready for future data

### Task 1.5: Response Envelope (`src/helpers.py`)
- Standard `_tool_response(data, summary, next_actions)` format matching PB/CapIQ pattern
- Error response format `_error_json(exc)` with error codes
- Pagination metadata wrapper
- Count/stats helper
- RPC parameter helper: converts empty lists to null automatically

### Task 1.6: Server Skeleton (`src/server.py`)
- FastMCP server with stdio + SSE transport (port 8770)
- **Auto-discovery registration:** server.py discovers all modules in `src/tools/` at startup, calls `register(mcp, client)` on each. **NO AGENT EDITS server.py** during Phases 2/3. Each tool module exports its own `register()` function.
- Auth on startup from env vars / api_keys.json
- Health check tool (`io_health`) returning auth status, table counts, server uptime
- Graceful shutdown

### Task 1.7: Test Infrastructure
- `conftest.py` with mock client, mock data fixtures
- Test helpers for asserting tool response shapes
- Sample investor/person data fixtures from `docs/10-live-samples.json`
- **Mock strategy:** All tool tests use mock client (unit tests). Live Supabase is Phase 4's job.
- Dev dependencies in `pyproject.toml`: pytest, pytest-asyncio, respx (httpx mocking)
- `.gitignore` (if not created in Phase 0)

### Task 1.8: Systemd Service Template
- `ops/investor-outbound-mcp.service` — systemd unit file
- Environment: `IO_EMAIL`, `IO_PASSWORD`
- WorkingDirectory, Restart=on-failure, MemoryMax=1G

### Phase 1 Review Gate
- All unit tests pass
- Client can auth and query (manual smoke test)
- Entity models validate against live data shapes
- `/review:code`, `/simplify`, `/grill` on all files
- Commit: `feat(io-mcp): phase 1 — foundation complete`

---

## Phase 2: Core Tools (PARALLEL — 4 Worktrees)

**Goal:** Build 17 core tools across 4 categories. One worktree per category, one agent per worktree.

**CRITICAL FOR ALL AGENTS:** Do NOT edit `src/server.py`. Create your tool module at `src/tools/{category}.py` with a `register(mcp, client)` function. The server auto-discovers all modules.

### Worktree A: Deal Matching Tools (Agent 1)
**Branch:** `io-mcp/deal-matching`
**Model:** **Opus** — deal matching + AI similarity are the highest-value tools.

| Tool | Description |
|------|-------------|
| `match_deal` | **Hero tool.** Two-phase internal architecture: (1) Broad investor pull via PostgREST — sector overlap + investor type + named firm ilike search + description ilike, (2) Tight contact-level gating via the 6-gate pipeline from scoring.py. Returns top `max_per_firm` contacts per firm. **CRITICAL:** Sector-array overlap is a pre-filter only. The real filtering is contact-level role/firm keyword matching. Include contacts regardless of email availability. |
| `match_deal_stage` | Match by preferred_investment_types ilike (Seed/Buyout/Growth). 198K investors (84% coverage). |
| `match_preferences` | Match against stated preferred_* fields only (industry, geography, check size). Separate from match_deal — this uses ONLY stated preferences, no scoring. |
| `find_similar_investors` | Given one investor, find N similar via embedding cosine similarity. **CONTINGENT on Phase 0 embedding table probe.** If embeddings unreadable, defer to Phase 3 or redesign. |

**`match_deal` input signature:**
```
match_deal(
    role_keywords: list[str],       # matched against contact role
    firm_keywords: list[str],       # matched against investor/company name
    named_firms: list[str],         # exact firm names — always include
    sectors: list[str] | None,      # sector codes for broad investor pre-filter (can be null)
    investor_types: list[str] | None,
    deal_size: float | None,        # for check_size matching
    geography: str | None,
    description_keywords: list[str] | None,  # ilike search on investor descriptions
    deal_stage: str | None,         # for preferred_investment_types matching
    expanded: bool = False,         # loosened matching for niche deals
    max_per_firm: int = 5,
    max_results: int = 1000,
    min_score: int = 20,
)
```

**When `sectors` is null/empty:** Skip sector-array filter entirely, rely on investor_type + named_firms + keyword matching. Necessary for Family Office and Angel searches where sector data is sparse.

**When `expanded=True`:** Any senior investment professional passes Gate 6 regardless of keyword match. Necessary for niche deals (franchise, auto) where keywords yield too few matches.

**Tests:** Happy path per deal type (buyout, Series A, SAFE, fund raise), empty results, scoring validation, expanded vs strict mode, no-sector search, named-firm-only search.

### Worktree B: Investor Discovery Tools (Agent 2)
**Branch:** `io-mcp/investor-discovery`

| Tool | Description |
|------|-------------|
| `search_investors` | Filter by type, sector, geography, AUM, check size, keyword, status. Uses PostgREST (not RPC). Default: exclude "Acquired/Merged" status. |
| `search_descriptions` | Keyword search on investor description field. 297K descriptions (97% coverage). Returns investors whose description ilike matches. |
| `get_investor` | Full profile by ID or name. Returns all fields via entity formatter. |
| `investor_freshness` | Recently updated investors. Order by `updated_at` desc (note: `created_at` may not exist — check). |

### Worktree C: Contact Retrieval Tools (Agent 3)
**Branch:** `io-mcp/contact-retrieval`

| Tool | Description |
|------|-------------|
| `get_contacts` | Scored contacts for investor(s). Applies scoring + junk filtering + role-is-firm-name detection. **Includes contacts without email.** Uses `max_per_firm` cap. |
| `search_persons` | Find people by name, email, company, role. Any combo. |
| `get_investor_team` | All persons at an investor, grouped by seniority tier. |
| `find_decision_makers` | Filter to investment committee / partner / MD / CIO only. |

### Worktree D: Reverse Lookup Tools (Agent 4)
**Branch:** `io-mcp/reverse-lookup`

| Tool | Description |
|------|-------------|
| `lookup_by_email_domain` | Pass domain → all persons at that domain. |
| `lookup_by_linkedin` | Pass LinkedIn URL → person record. |
| `reverse_company_lookup` | Company name → which investors. |
| `batch_firm_lookup` | List of firm names → matches + top contacts. **Chunk to 50 names per PostgREST query (URL length limits).** |
| `batch_person_lookup` | List of emails/names → matches. **Chunk to 100 per query.** |

### Phase 2 Parallelism Protocol
1. Create 4 worktrees from main branch (after Phase 1 commit)
2. Dispatch one agent per worktree with full context: this plan, all src/ files, all docs/
3. Each agent creates ONLY: `src/tools/{category}.py` (with `register(mcp, client)`) and `tests/test_{category}.py`
4. **NO agent edits `src/server.py`** — auto-discovery handles registration
5. Each agent runs its own tests before marking complete
6. Orchestrator waits for all 4, then merges

### Phase 2 Review Gate
- All 4 worktrees merged to main
- All tests pass on merged branch (target: 120+ tool tests + 50 foundation tests = 170+)
- Full review suite
- Commit: `feat(io-mcp): phase 2 — 17 core tools complete`

---

## Phase 3: Advanced Tools (PARALLEL — 4 Worktrees)

**Goal:** Build the remaining 13 tools across 4 categories.

### Worktree E: Outreach Readiness Tools (Agent 5)
**Branch:** `io-mcp/outreach-readiness`

| Tool | Description |
|------|-------------|
| `outreach_ready_contacts` | Only contacts with good_email=true, email_free=false, email_disposable=false, no recent bounce. |
| `assess_contact_quality` | Quality grade A/B/C/D per contact based on email_score, email_toxicity, email_status, bounce history. |
| `channel_coverage` | Email/phone/LinkedIn breakdown for investor set. |
| `enrich_priorities` | Contacts with LinkedIn but no email, ranked by seniority. |

### Worktree F: Multi-Deal Intelligence (Agent 6)
**Branch:** `io-mcp/multi-deal-intel`

| Tool | Description |
|------|-------------|
| `find_cross_deal_investors` | Investors matching 2+ deals. |
| `deal_coverage_gaps` | Missing types/geos/sectors for a deal. |
| `investor_funnel` | Progressive filter counts. |
| `deduplicate_across_deals` | Persons appearing in multiple deal contact lists. |

### Worktree G: Analytics Tools (Agent 7)
**Branch:** `io-mcp/analytics`

| Tool | Description |
|------|-------------|
| `sector_landscape` | Type/geo/check-size breakdown for a sector. Note: AUM is text field — parse "$500M" to numeric or skip. |
| `check_size_distribution` | Check size histogram for sector + type. |

### Worktree H: Export & Hygiene Tools (Agent 8)
**Branch:** `io-mcp/export-hygiene`

| Tool | Description |
|------|-------------|
| `export_contacts` | CSV export via edge function. Poll user_exports via PostgREST GET (NOT realtime WebSocket). |
| `stale_contact_check` | Contacts with bounces, low scores, undeliverable status. |
| `search_by_company_industry` | Filter persons by company_industry + company_size. |

### Phase 3 Review Gate
- All 4 worktrees merged
- All tests pass (target: 250+ total)
- Full review suite
- Commit: `feat(io-mcp): phase 3 — 30 tools complete`

---

## Phase 4: Live Validation Against 5 Deals (PARALLEL — 5 Agents + 2 Sequential)

**Goal:** Test every tool against the 5 real deals. Iterate until results match the proven segment_v2.py baseline.

**Parallelism via worktrees:** All 5 deal validations (4.1-4.5) are independent read-only queries — 5 worktrees, 5 agents, merge. Each agent writes its validation results to `data/validation_{deal}.json`. Then cross-deal tests (4.6) and reverse lookup tests (4.7) run on the merged branch.

**Worktrees:**
- `io-mcp/p4-doosan` — Agent D1
- `io-mcp/p4-intralogic` — Agent D2
- `io-mcp/p4-brakestogo` — Agent D3
- `io-mcp/p4-grapeviine` — Agent D4
- `io-mcp/p4-futurefund` — Agent D5

**Test data:**
- `~/Desktop/Deal-Investor-Research/INVESTOR_TARGETING_SOP.md` — deal criteria
- `~/Desktop/Deal-Investor-Research/*/contacts/*_FINAL.csv` — baseline contacts
- `~/Desktop/Deal-Investor-Research/segment_v2.py` — proven segmentation logic

### 4.1-4.5: Per-Deal Validation (Doosan, IntraLogic, BrakesToGo, Grapeviine, FutureFundOne)

For each deal:
- `match_deal` with deal-specific `role_keywords`, `firm_keywords`, `named_firms` from SOP
- `match_deal_stage` with appropriate deal stage
- `search_descriptions` with deal-specific keywords
- `get_contacts` for top matched investors
- Compare against FINAL CSV

**Baseline contact counts:** Doosan=4,454 | IntraLogic=3,828 | BrakesToGo=3,412 | Grapeviine=871 | FutureFundOne=6,916

### 4.6-4.7: Cross-Deal + Reverse Lookup Tests

### Phase 4 Iteration Loop
1. Run tool with deal criteria
2. Compare results against baseline — measure investor overlap rate and contact relevance
3. If results diverge significantly: diagnose whether it's filter logic, scoring thresholds, or missing keywords
4. Fix and re-test until results are comparable to baseline
5. Log all results in `data/validation_results.json`

### Phase 4 Review Gate
- All 5 deals validated
- Commit: `feat(io-mcp): phase 4 — live validation complete`

---

## Phase 5: Documentation (PARALLEL — 3 Agents)

**Goal:** Write "the quad" — the 4 production docs. Based on real Phase 4 test data.

### Agent 9: ARCHITECTURE.md + README.md
**Reference:** PB `ARCHITECTURE.md` (23.4K)

ARCHITECTURE.md sections: Overview, Module Structure, Client Layer, Entity Layer, Sector Resolution, Scoring & Gating Engine (including the 6-gate pipeline and 6-path deal relevance logic), Tool Design Patterns, Concurrency Model, Discovery Pipeline, Testing Strategy, Deployment, Key Decisions (why PostgREST over RPC, why scoring > sector-array, why no rate limiting, why auto-discovery registration).

README.md sections: Project overview, Installation, MCP registration config, Quick start (5 tool calls), Tool inventory table, Deployment, Cross-system usage.

### Agent 10: INTEGRATION.md
**Reference:** CapIQ `INTEGRATION.md` (36.3K)

Sections: MCP Registration, Quick Start, Tool Groups, Response Format, Deal Matching Workflows (step-by-step for buyout, Series A, SAFE, fund raise), Filter Reference (all sector codes, investor types, investment types, status values, enum tables), Scoring Reference, Limitations.

### Agent 11: RECIPES.md
**Reference:** PB `RECIPES.md` (20.4K)

10 recipes based on Phase 4 test data with exact tool calls, params, and expected output.

### Phase 5 Review Gate
- All 4 docs complete (the quad: ARCHITECTURE + INTEGRATION + RECIPES + README)
- Commit: `feat(io-mcp): phase 5 — documentation complete`

---

## Phase 6: Merge, Final Review & Ship (Sequential)

### 6.1: Worktree Reconciliation
- Merge all remaining worktrees
- Resolve any conflicts
- Full test suite on merged branch

### 6.2: Pre-Ship Security
- **Redact credentials from `docs/05-auth-flow.md`** lines 56-58
- Verify `.gitignore` excludes: `data/`, `.env`, credentials, `*.pyc`
- Run `/review:security` on auth/credential handling

### 6.3: Final Review Run
- `/toolkit-scout` → find ALL review tools → run in parallel
- `/review:code`, `/review:architecture`, `/review:security`, `/review:performance`, `/simplify`, `/grill`, `/double-check:verify`
- Ensemble findings, fix blockers
- Re-run tests

### 6.4: GitHub Push (Standalone Repo)
- Push to `investor-outbound-mcp` repo on GitHub
- Create release tag `v0.1.0`

### 6.5: Server Deployment
- Deploy to the SSE server server, port 8770
- Systemd service from `ops/investor-outbound-mcp.service`
- SSE transport
- Verify MCP registration in `~/.claude.json`
- Smoke test: 3 tools against live data
- Post-deploy: run 5-deal test battery

---

## Parallelism Map

```
Phase 0: Discovery          [PARTIAL PARALLEL — 0.0 first, then 0.1+0.2 parallel, then 0.3]
    │                                              [REVIEW GATE]
Phase 1: Foundation         [PARTIAL PARALLEL — 3 tracks parallel, then merge, then 3 more]
    ├── Track A: Client (1.1)                      ── Agent A
    ├── Track B: Sectors (1.2)                     ── Agent B
    └── Track C: Scoring (1.3)                     ── Agent C
    │   then: Entities (1.4) → Server (1.5) → Tests (1.6) + Pyproject (1.7) + Systemd (1.8)
    │                                              [REVIEW GATE]
Phase 2: Core Tools         [PARALLEL — 4 worktrees, 4 agents]
    ├── Worktree A: Deal Matching (4 tools)        ── Agent 1 (Opus)
    ├── Worktree B: Investor Discovery (4 tools)   ── Agent 2
    ├── Worktree C: Contact Retrieval (4 tools)    ── Agent 3
    └── Worktree D: Reverse Lookup (5 tools)       ── Agent 4
    │                                              [MERGE + REVIEW GATE]
Phase 3: Advanced Tools     [PARALLEL — 4 worktrees, 4 agents]
    ├── Worktree E: Outreach Readiness (4 tools)   ── Agent 5
    ├── Worktree F: Multi-Deal Intel (4 tools)     ── Agent 6
    ├── Worktree G: Analytics (2 tools)            ── Agent 7
    └── Worktree H: Export & Hygiene (3 tools)     ── Agent 8
    │                                              [MERGE + REVIEW GATE]
Phase 4: Live Validation    [PARALLEL — 5 deal agents, then 2 sequential]
    ├── Deal 1: Doosan                             ── Agent D1
    ├── Deal 2: IntraLogic                         ── Agent D2
    ├── Deal 3: BrakesToGo                         ── Agent D3
    ├── Deal 4: Grapeviine                         ── Agent D4
    └── Deal 5: FutureFundOne                      ── Agent D5
    │   then: Cross-deal tests (4.6) → Reverse lookup tests (4.7)
    │                                              [REVIEW GATE]
Phase 5: Documentation      [PARALLEL — 3 agents]
    ├── ARCHITECTURE.md + README.md                ── Agent 9
    ├── INTEGRATION.md                             ── Agent 10
    └── RECIPES.md                                 ── Agent 11
    │                                              [MERGE + REVIEW GATE]
Phase 6: Ship               [Sequential — security, review, deploy]
    │                                              [FINAL REVIEW GATE]
```

**Max concurrent agents:** 5 (Phase 4 deal validation)
**Total unique agent slots:** 25 (6 foundation + 4 Phase 2 + 4 Phase 3 + 5 Phase 4 + 3 Phase 5 + 3 Phase 6 review)
**Total worktrees:** 22 (6 Phase 1 + 4 Phase 2 + 4 Phase 3 + 5 Phase 4 + 3 Phase 5)
**Tool counts:** Phase 2 = 17, Phase 3 = 13, Total = 30

---

## Tool Inventory (30 Tools)

### Deal Matching (4)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 1 | `match_deal` | role_keywords, firm_keywords, named_firms, sectors, investor_types, deal_size, geography, description_keywords, deal_stage, expanded, max_per_firm, max_results, min_score | Ranked investors + top N contacts each (includes contacts without email) |
| 2 | `match_deal_stage` | deal_stage | Investors with matching preferred_investment_types |
| 3 | `match_preferences` | industry, geography, check_size_min/max | Investors with matching stated preferences |
| 4 | `find_similar_investors` | investor_id or name, limit | N most similar via embedding cosine (contingent on Phase 0) |

### Investor Discovery (4)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 5 | `search_investors` | sectors, investor_types, geography, check_size_min/max, keyword, status, limit, offset | Paginated investor list (excludes Acquired/Merged by default) |
| 6 | `search_descriptions` | query, investor_types (optional), limit | Investors whose description matches |
| 7 | `get_investor` | id or name | Full investor profile via entity formatter |
| 8 | `investor_freshness` | sectors, investor_types, days_back, limit | Recently updated investors |

### Contact Retrieval (4)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 9 | `get_contacts` | investor_ids or name, deal_keywords, max_per_firm, min_score | Scored contacts (includes no-email contacts) |
| 10 | `search_persons` | name, email, company, role (any combo), limit | Person records |
| 11 | `get_investor_team` | investor_id or name | All persons grouped by seniority |
| 12 | `find_decision_makers` | investor_ids, role_patterns | Senior investment professionals only |

### Reverse Lookup (5)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 13 | `lookup_by_email_domain` | domain | All persons at domain |
| 14 | `lookup_by_linkedin` | linkedin_url | Person record |
| 15 | `reverse_company_lookup` | company_name | Investors linked to company |
| 16 | `batch_firm_lookup` | firm_names (list, chunked 50/query) | Matches + top contacts |
| 17 | `batch_person_lookup` | emails or names (list, chunked 100/query) | Matched persons |

### Outreach Readiness (4)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 18 | `outreach_ready_contacts` | investor_ids | Verified deliverable emails only |
| 19 | `assess_contact_quality` | investor_ids or person_ids | A/B/C/D quality grade |
| 20 | `channel_coverage` | investor_ids | Email/phone/LinkedIn breakdown |
| 21 | `enrich_priorities` | investor_ids, min_seniority | LinkedIn-but-no-email contacts |

### Multi-Deal Intelligence (4)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 22 | `find_cross_deal_investors` | deal_criteria_list | Cross-deal overlap |
| 23 | `deal_coverage_gaps` | deal_criteria, current_investor_ids | Missing segments |
| 24 | `investor_funnel` | filters (progressive) | Count at each step |
| 25 | `deduplicate_across_deals` | deal_contact_lists | Persons in multiple lists |

### Analytics (2)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 26 | `sector_landscape` | sector, investor_types | Type/geo/check-size breakdown |
| 27 | `check_size_distribution` | sector, investor_type | Check size histogram |

### Export & Hygiene (3)
| # | Tool | Inputs | Output |
|---|------|--------|--------|
| 28 | `export_contacts` | filters, contacts_per_investor, export_name | Download URL |
| 29 | `stale_contact_check` | investor_ids or filters | Bounced/low-score contacts |
| 30 | `search_by_company_industry` | company_industry, company_size, limit | Persons at matching companies |

---

## Key Decisions

1. **PostgREST over RPC:** The RPC `manual_search_investors_only2` was abandoned because (a) it returns 0 when passed empty arrays instead of null, (b) PostgREST gives more granular operator control (ov, cs, ilike, gte/lte). RPC may be re-evaluated if Phase 0 finds new capabilities.

2. **Scoring > sector-array:** Sector-array overlap produces too many false positives (14K+ for "energy"). The real filtering happens at contact level via role/firm keyword matching. Sector-array is a pre-filter only.

3. **No rate limiting:** IO has no ban risk (Supabase managed, premium account). No PaceTracker, no circuit breaker, no burst limits.

4. **Auto-discovery registration:** To prevent merge conflicts during parallel builds, server.py auto-discovers tool modules. No agent edits server.py.

5. **Two-phase match_deal:** Broad investor pull → tight contact gating. Never return investor-filtered-only results — always apply the 6-gate contact pipeline.

6. **Include contacts without email:** User can enrich them. Only `outreach_ready_contacts` filters by email availability.

---

## Files Created So Far (Pre-Plan)

| File | Status | Notes |
|------|--------|-------|
| `src/__init__.py` | Created | Empty |
| `src/client.py` | Created | Auth + PostgREST queries. Needs: error taxonomy, caching, count=estimated fix, credential loading from env/config. |
| `src/scoring.py` | Created | Core scoring. Needs: 6-gate pipeline, firm_keywords matching, 12 missing junk patterns, passes_deal_relevance(). |
| `src/sectors.py` | Created | 120 sector codes mapped. Needs: extend INVESTOR_TYPES from 16 to 48+ values. |
| `pyproject.toml` | Created | Needs: pydantic>=2.0 runtime dep + pytest, pytest-asyncio, respx dev deps. |
| `CLAUDE.md` | Exists | Needs update post-build. |
| `README.md` | Exists | Scaffold. Replace in Phase 5. |
| `docs/01-14` | Exists | Full discovery docs. Reference material. |

---

## Success Criteria

1. **30 tools** registered and responding via MCP protocol
2. **All 5 deals** tested with `match_deal` producing comparable results to baseline FINAL CSVs
3. **All tests pass** (target: 250+ tests — 150 tool + 50 foundation + 30 entity + 20 scoring)
4. **4 docs complete** (the quad: ARCHITECTURE.md, INTEGRATION.md, RECIPES.md, README.md)
5. **GitHub repo** pushed with v0.1.0 tag
6. **Server deployment** live on the SSE server:8770 and health-checked
7. **No review blockers** remaining after Phase 6 ensemble review
8. **Credentials redacted** from all committed files before GitHub push

---

## Appendix A: Toolkit Map (Per Phase)

Tools, skills, agents, and commands mapped to each phase. Execution agents MUST use these.

### Phase 0: Discovery
| Type | Resource | Use |
|------|----------|-----|
| MCP | `playwright` | JS bundle download if needed |
| MCP | `context7` | FastMCP docs, httpx docs |
| Skill | `supabase` | PostgREST query patterns |
| Lib | `httpx` (already a dep) | Direct HTTP probing for PostgREST |

### Phase 1: Foundation
| Type | Resource | Use |
|------|----------|-----|
| Skill | `mcp-development` | FastMCP server patterns |
| Skill | `python-best-practices` | Python async, typing |
| Skill | `supabase` | Supabase client patterns |
| Skill | `api-design-patterns` | Tool interface design |
| Command | `/model-context-protocol:create-server` | MCP server scaffold |
| Command | `/testing:tdd` | TDD workflow |
| Agent | `backend-engineer` | Implementation |
| MCP | `context7` | Latest FastMCP + httpx + pytest docs |

### Phase 2-3: Tool Building (parallel worktrees)
| Type | Resource | Use |
|------|----------|-----|
| Command | `/create-worktrees:worktree-create` | Batch create worktrees |
| Command | `/model-context-protocol:add-tool` | Add tools |
| Command | `/testing:tdd` | TDD per tool |
| Skill | `tdd-mastery` | TDD workflow |
| Skill | `llm-integration` | Embedding/AI tools (Worktree A only) |
| Agent | `backend-engineer` | Tool implementation |
| Model | **Opus** | Worktree A only |

### Phase 4: Live Validation
| Type | Resource | Use |
|------|----------|-----|
| Skill | `verification-loop` | Iteration discipline |
| Skill | `eval-harness` | Formal evaluation |
| Data | `~/Desktop/Deal-Investor-Research/` | Deal criteria + baseline CSVs |

### Phase 5: Documentation
| Type | Resource | Use |
|------|----------|-----|
| Reference | PB `ARCHITECTURE.md` | Structure template |
| Reference | CapIQ `INTEGRATION.md` | Structure template |
| Reference | PB `RECIPES.md` | Recipe format |
| Agent | `documentation-writer` | All doc agents |

### Phase 6: Ship
| Type | Resource | Use |
|------|----------|-----|
| Command | `/review:code`, `/review:architecture`, `/review:security` | Final reviews |
| Command | `/simplify`, `/grill`, `/double-check:verify` | Quality gates |
| Command | `/git:pr-create`, `/git:release` | GitHub |
| Command | `/deploy` | Server deployment |
| Skill | `ship-software` | Shipping SOP |
| Agent | `staff-reviewer` | Deep final review |
| Agent | `devops-engineer` | Deployment |

### Review Gates (Every Phase)
| Type | Resource | Use |
|------|----------|-----|
| Command | `/toolkit-scout` | Find review tools |
| Command | `/review:code`, `/review:architecture`, `/simplify`, `/grill`, `/double-check:verify` | Quality |
| Agent | `code-reviewer` | Review |
