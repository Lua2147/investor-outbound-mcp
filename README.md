# investor-outbound-mcp

MCP server wrapping the Investor Outbound database: 234K investors and 1.8M contacts with real emails, phones, and LinkedIn URLs.

---

## Data Scope

| Table | Rows | Key fields |
|-------|------|-----------|
| `investors` | 234,549 | Firm name, type (52 values), sectors (120 codes), check size (in millions USD), geography, description, contact count |
| `persons` | 1,806,686 | Email, phone, LinkedIn, role/title, company, email deliverability status |
| `investors_embeddings_3072` | 290,865 | 3072-dim vector embeddings for semantic similarity search |

Coverage highlights:
- `description`: 97% of investors (297K rows)
- `preferred_investment_types`: 84% of investors (198K rows)
- `email_status`: 100% of persons — `"deliverable"` ~47%, `"unknown"` ~47%
- `check_size_min/max`: 308K investors with check size data, **stored in MILLIONS USD**

---

## Installation

**Requirements:** Python 3.11+, pip

```bash
cd /path/to/investor-outbound-mcp
pip install -e .
```

**Dependencies** (from `pyproject.toml`):
- `mcp` — FastMCP framework
- `httpx` — async HTTP client
- `pydantic` — entity models

**Dev dependencies:**
- `pytest`, `pytest-asyncio` — test runner
- `respx` — httpx request mocking

---

## Configuration

Credentials are loaded in order:

1. Environment variables:
   ```bash
   export IO_EMAIL="your@email.com"
   export IO_PASSWORD="yourpassword"
   ```

2. `config/api_keys.json` at the monorepo root, under key `supabase_investor_outreach`:
   ```json
   {
     "supabase_investor_outreach": {
       "email": "your@email.com",
       "password": "yourpassword"
     }
   }
   ```

The server authenticates on first tool call and auto-refreshes the JWT (2-hour expiry).

---

## MCP Registration

### stdio (Claude Desktop / local)

```json
{
  "mcpServers": {
    "investor-outbound": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/investor-outbound-mcp",
      "env": {
        "IO_EMAIL": "your@email.com",
        "IO_PASSWORD": "yourpassword"
      }
    }
  }
}
```

### SSE (remote server deployment)

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

---

## Quick Start

Five tool calls covering the most common use cases:

### 1. Find investors for a deal (recommended entry point)

```
match_deal(
    role_keywords=["energy", "infrastructure", "buyout"],
    firm_keywords=["energy", "renewable", "power"],
    named_firms=["ares", "brookfield", "kkr", "blackstone"],
    sectors=["energy", "infrastructure"],
    investor_types=["pe", "infrastructure", "family office"],
    deal_size=70000000,
    deal_stage="buyout",
    max_per_firm=5,
    max_results=500
)
```

Returns scored contacts across matching firms, ranked by relevance score. Includes contacts without email (use `io_outreach_ready_contacts` to filter to email-confirmed only).

### 2. Search investors by type and sector

```
io_search_investors(
    sectors=["fintech", "financial services"],
    investor_types=["vc", "cvc"],
    geography="United States",
    check_size_min_dollars=5000000,
    check_size_max_dollars=50000000,
    limit=100
)
```

### 3. Get all contacts for a specific investor

```
io_get_contacts(investor_ids=[12345, 67890], min_score=20)
```

### 4. Filter to outreach-ready contacts (verified emails only)

```
io_outreach_ready_contacts(investor_ids=[12345, 67890, 11111])
```

Returns only contacts with `good_email=true`, no recent bounce, and non-disposable / non-free email addresses.

### 5. Check server health and auth status

```
io_health()
```

---

## Tool Inventory

### Deal Matching (4 tools)

| Tool | Description |
|------|-------------|
| `match_deal` | Hero tool. Two-phase: broad investor pull + 6-gate contact scoring. Proven on 206K contacts. Pass `role_keywords`, `firm_keywords`, `named_firms`, `sectors`, `deal_stage`. |
| `match_deal_stage` | Investors matching a deal stage via `preferred_investment_types` ilike (Seed, Buyout, Growth, Series A, M&A, etc.). |
| `match_preferences` | Investors matching stated preferences: `preferred_industry`, `preferred_geography`, `check_size_min/max`. No scoring. |
| `find_similar_investors` | Embedding cosine similarity search. Pass an `investor_id` to find the top N most similar investors from 290K embeddings. |

### Investor Discovery (4 tools)

| Tool | Description |
|------|-------------|
| `io_search_investors` | Structured filter: sectors, investor types, geography, check size (in dollars — auto-converted), status, keyword. Paginated. |
| `io_search_descriptions` | Keyword ilike on investor description field (97% coverage). Use for concepts not in sector tags. |
| `io_get_investor` | Full investor profile by ID (int) or name (ilike string). Returns all 47 fields. |
| `io_investor_freshness` | Recently updated investors ordered by `updated_at` desc. Use to find newly active firms. |

### Contact Retrieval (4 tools)

| Tool | Description |
|------|-------------|
| `io_get_contacts` | Scored + junk-filtered contacts for one or more investors. Applies `score_contact` and junk filter (not full 6-gate). Includes contacts without email. |
| `io_search_persons` | Find people by name, email, company, role, location — any combination. |
| `io_get_investor_team` | All persons at one investor, grouped by seniority tier (Tier 1: Partner/MD/GP, Tier 2: VP/Director, Tier 3: other). |
| `io_find_decision_makers` | Senior investment professionals only (Partner, MD, CIO, Founder) across a list of investor IDs. |

### Reverse Lookup (5 tools)

| Tool | Description |
|------|-------------|
| `lookup_by_email_domain` | Pass an email domain (e.g., `"kaynecapital.com"`) to find all persons at that firm. |
| `lookup_by_linkedin` | Pass a LinkedIn profile URL to retrieve the matching person record. |
| `reverse_company_lookup` | Company name to investors it belongs to (grouped by investor FK). |
| `batch_firm_lookup` | List of firm names to matched investors + top scored contacts per firm. Chunked to 50 per query. |
| `batch_person_lookup` | List of emails or `"First Last"` names to matched person records. Chunked to 100 per query. |

### Outreach Readiness (4 tools)

| Tool | Description |
|------|-------------|
| `io_outreach_ready_contacts` | Strict filter: `good_email=true`, `email_free=false`, `email_disposable=false`, no recent bounce. Use before passing contacts to an email campaign. |
| `io_assess_contact_quality` | Grade each contact A/B/C/D based on `email_score`, `email_status`, `email_toxicity`, and bounce history. Returns per-contact grades + aggregate counts. |
| `io_channel_coverage` | Email / phone / LinkedIn breakdown for a list of investor IDs. Shows percentage of contacts reachable per channel. |
| `io_enrich_priorities` | Contacts with LinkedIn URL but no email, ranked by seniority score. Use to prioritize manual enrichment effort. |

### Multi-Deal Intelligence (4 tools)

| Tool | Description |
|------|-------------|
| `io_find_cross_deal_investors` | Investors matching 2+ deals from a set of search criteria. Pass a list of deal criteria dicts; returns investors appearing in multiple result sets. |
| `io_deal_coverage_gaps` | Identifies which investor types, geographies, or sectors return 0 results for a given deal. Useful for diagnosing why a search is too narrow. |
| `io_investor_funnel` | Progressive filter: shows how result counts change as filters are added cumulatively. Use to calibrate filter tightness before running `match_deal`. |
| `io_deduplicate_across_deals` | Takes multiple per-deal person ID lists and returns persons appearing in 2+ lists. No network calls. |

### Analytics (2 tools)

| Tool | Description |
|------|-------------|
| `io_sector_landscape` | Type breakdown, top geographies, and check-size percentiles for a given sector. Aggregates up to 5,000 investors in Python. |
| `io_check_size_distribution` | Check-size histogram for a sector + investor type combination. Buckets: `<$1M`, `$1-5M`, `$5-25M`, `$25-100M`, `$100M-$1B`, `$1B+`. |

### Export and Hygiene (3 tools)

| Tool | Description |
|------|-------------|
| `io_export_contacts` | Triggers the `export2` edge function to generate a CSV. Polls `user_exports` every 3 seconds (up to 60s) and returns the signed download URL when ready. |
| `io_stale_contact_check` | Finds contacts with deliverability problems: bounced email, low `email_score` (<30), or `email_status=undeliverable`. |
| `io_search_by_company_industry` | Filter persons by `company_industry` (ilike) and optional `company_size` bucket (e.g., `"1001-5000"`). Uses LinkedIn-style industry labels. |

### Built-in (1 tool)

| Tool | Description |
|------|-------------|
| `io_health` | Returns auth status, uptime, tool module count, and Supabase project ID. Always call first to verify connectivity. |

---

## Deployment

### SSE server (port 8770)

```bash
# Start manually
python -m src.server --sse --port 8770

# Or with systemd
sudo cp ops/investor-outbound-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable investor-outbound-mcp
sudo systemctl start investor-outbound-mcp
```

The systemd service reads `IO_EMAIL` and `IO_PASSWORD` from the environment. Set them in a drop-in override file or via a secrets manager — not directly in the unit file.

### Custom port

```bash
python -m src.server --sse --port 9000
```

### Resource limits

Memory max: 1G (set in systemd unit). Peak memory is driven by the largest single tool call — a full `match_deal` scan of the Doosan deal loaded ~148K person rows and peaked at approximately 400MB.

---

## Important Notes for Tool Callers

**check_size is in MILLIONS.** Pass `deal_size=70000000` (dollars) to `match_deal` — it divides by 1M internally. The `check_size_min/check_size_max` parameters on `match_preferences` are already in millions (pass `5` for $5M).

**preferred_investment_types is TEXT, not an array.** The database stores this as a comma-delimited string. Tools use `ilike` internally. The `cs` array operator will return an error.

**Nested joins are not supported.** The persons table has no FK relationship exposed in PostgREST's schema cache. All cross-table lookups use the two-step pattern: query investors, then query persons by investor IDs.

**sectors_tsv FTS is unreliable.** The `sectors_array` overlap filter (`ov`) is reliable. FTS on `sectors_tsv` works for some keywords (`fintech`) but returns 0 for others (`energy`, `healthcare`). Tools use `ov` exclusively.

**Contacts without email are included in match_deal.** A contact with only a LinkedIn URL is still actionable. Use `io_outreach_ready_contacts` when you need email-confirmed contacts only.

**Sector-array overlap alone is not sufficient for deal matching.** Filtering by sector returns 14K+ investors including every generalist fund with one sector tag matching. The `match_deal` 6-gate scoring pipeline eliminates noise at the contact level. Do not use `io_search_investors` with a sector filter as a substitute for `match_deal`.
