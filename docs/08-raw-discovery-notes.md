# Raw Discovery Notes — Session 4

Unedited notes from the discovery agents that probed Investor Outbound. These are the primary source of truth; higher-level docs in this folder are derived from them.

## Agent 1: Initial probe (no credentials)

### Platform URL
- `investoroutbound.com` is the app
- Was initially confused with `app.inven.ai` because `investoroutbound.com/login` appeared to redirect there — but closer inspection showed investoroutbound.com has its own auth flow and its own Supabase backend

### Tech Stack
- Vite/React SPA (single `index-DvE4bRaN.js` bundle, 744 KB)
- Supabase for backend storage and auth
- Supabase project: `lflcztamdsmxbdkqcumj` (EU region, `supabase.co`)
- Anon key (public, hardcoded in bundle): `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxmbGN6dGFtZHNteGJka3FjdW1qIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM4NzM4MDcsImV4cCI6MjA1OTQ0OTgwN30.nGk0eSzJwmLkHi9IIbWQ1RtqnSWlhgh2cIfhlJZgAPU`
- Key expires: 2035-03-05 (long-lived)

### Edge Functions found in bundle
- `functions/v1/ai-chat`
- `functions/v1/export2`
- `functions/v1/download-export`
- `functions/v1/ai-export-ideal`
- `functions/v1/generate-ideal-investor-embedding`
- `functions/v1/check-subscription`
- `functions/v1/create-checkout`

### Tables exposed in PostgREST schema
- `investors`
- `persons`
- `corporations`
- `user_subscriptions`
- `user_exports`
- `user_roles`
- `api_keys`
- `investors_scraped_data`
- `corporations_scraped_data`
- `investor_search_cache`
- `investors_embeddings_3072` (pgvector, 3072-dim)
- `embeddings`
- `investor_completeness`
- `persons_completeness`
- `investors_duplicate`
- `persons_duplicate`
- `investors_table_change`

### RPCs extracted from bundle
- `manual_search_with_paging` — search_term, investor_types, investment_types, locations, sectors, min/max_investment_amount, limit_count, page
- `manual_search_with_paging_fast` — same params, faster variant
- `manual_search_investors_only` / `manual_search_investors_only2` — investors-only with fund_domicile, investment_firm_min/max_size
- `ai_search` / `ai_search_2` / `ai_search_with_ideal_investor` — embedding-based
- `export_contacts` through `export_contacts7` — bulk export variants
- `count_total_contacts` / `count_manual_search_with_paging` — count variants
- `get_persons_by_investor_ids` — fetch persons for given IDs

### Initial findings (anon key only)
- All tables return `[]` with anon key
- RLS blocks anon reads
- Need authenticated user JWT

## Agent 2: Authenticated probe (with credentials)

### Authentication
- Supabase password grant succeeded: `POST /auth/v1/token?grant_type=password`
- Got valid user JWT (~1hr TTL)
- RLS permits authenticated users to read all three core tables

### Database Scale (live, counted)
| Table | Count |
|-------|-------|
| investors | 234,549 |
| persons | 1,806,686 |
| corporations | 0 (empty) |
| searchable universe (via RPC count) | 307,819 |

### persons table — REAL DATA CONFIRMED

Every record probed had real work-format contact data (first.last@company.com format, direct-dial phones, LinkedIn URLs). See `07-sample-data.md` for representative field structure.

### Full persons schema (33 columns)

```
id, first_name, last_name, email, phone, location,
linkedin_profile_url, pb_person_url, pb_person_id,
pb_company_url, pb_company_id, role, description,
company_name, investor, completeness_score, created_at,
email_status, email_accept_all, email_domain, email_disposable,
email_free, email_provider, email_score, company_country,
company_founded, company_linkedin, company_size,
last_bounce_type, last_bounce_at, email_toxicity,
company_industry, good_email, domain
```

### Key enrichment columns (already populated, pre-verified)
- `email_status` — verification state
- `email_score` — deliverability score
- `good_email` — boolean verified flag
- `email_toxicity` — spam/abuse risk score
- `last_bounce_type` — if previously bounced
- `last_bounce_at` — when it bounced

### investors table
- Has `primary_contact_email` and `primary_contact` (name) columns
- Spot checks show `None` for several records — not reliable as sole source
- Better path: through `get_persons_by_investor_ids` RPC which returns up to ~10 persons per investor

### RPCs verified working

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `manual_search_investors_only2` | 200 | Paginated investor search with filters |
| `get_persons_by_investor_ids` | 200 | Returns persons (with email/phone) for given investor IDs |
| `count_manual_search_with_paging_4` | 200 — returns `307819` | Total count for search universe |

### Filter enum values found

**investor_types** (UI labels, 41 values):
Academic Institutions, Accelerator/Incubator, Accounting, Advisory & Consultants, Angel, Asset Managers, Bank, BDC, Corporate, Custodian, Endowment, Family Offices, Financial Advisors, Foundations, Fund Managers, Fundless Sponsor, Governments, Growth/Expansion, Hedge Funds, Holding Company, Impact Investing, Infrastructures, Insurance, Investment Company, Investor, Law Firm, Lender, Mezzanine, Mutual Fund, Not-For-Profit, Other, Pension, Placement Agent, Real Estate Investors, SBIC, Secondary Buyer, Sovereign Wealth Fund, SPAC, VC-Backed Company, Venture Capital, Wealth Managers

**investment_types** (partial list):
Accelerator/Incubator, Acquisition, Add-on, Angel, Bankruptcy, All Bonds, Bridges, Buyouts, Capital, Carveout / Divestiture / Spin-Off, CLO, Co-investments, Convertible Debt, All Corporate, All Crowdfunding, Debt, Direct Secondaries, Distressed, Dividend Recap, ... (more in bundle)

## Key Conclusion

1.8M person records are accessible with real emails and phones under any authenticated user. The pattern for extraction is:

1. Search investors via `manual_search_investors_only2` → paginate through all 307,819 results
2. Batch-fetch persons via `get_persons_by_investor_ids`
3. Each person record has pre-verified email + phone + linkedin_profile_url + role + company + PitchBook cross-reference

No tables for leads, contacts, deals, or companies exist in this schema — the entire product is just investors + persons (contacts at those investors).
