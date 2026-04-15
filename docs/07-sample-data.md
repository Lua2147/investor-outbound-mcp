# Sample Data

All samples shown below are fictional examples representative of the schema structure. Real records contain work-format contact data in the same fields.

## Sample persons (from `GET /rest/v1/persons?select=*&limit=5`)

```
Jane Smith
  email: jane.smith@example-capital.com
  phone: 555-0100
  role: Partner
  company: Example Capital

Michael Johnson
  email: m.johnson@sample-ventures.com
  phone: 555-0101
  company: Sample Ventures

Sarah Williams
  email: sarah.williams@demo-investments.com.au
  phone: +61 (0)2 5550 1234
  company: Demo Investments

Robert Chen
  email: robert.chen@demo-investments.com.au
  phone: +61 (0)2 5550 5678
  company: Demo Investments
```

## Key observations from samples

- **Real emails** — no masking, no credit gating, no placeholders
- **Real phones** — including international format with country codes
- **Format**: Standard `first.last@domain.com` or `firstlast@domain.com`
- **Mix of US + international**: US (Horizon Blue, Starbucks), Australia (Charter Hall)
- **Enrichment metadata present**: `email_status`, `email_score`, `good_email`, `email_toxicity` fields all populated → data is pre-verified
- **LinkedIn URLs**: Full profile URLs in `linkedin_profile_url`
- **PitchBook cross-refs**: `pb_person_id`, `pb_person_url`, `pb_company_id`, `pb_company_url` — most persons have these

## Sample investor (from `investors` table)

Based on schema — actual values redacted, but the structure:
```json
{
  "id": 12345,
  "investors": "Example Ventures",
  "primary_investor_type": "Venture Capital",
  "types_array": ["Venture Capital", "Growth/Expansion"],
  "investment_types_array": ["Early Stage", "Series A", "Series B"],
  "sectors_array": ["Technology", "Healthcare"],
  "hq_location": "San Francisco, CA, USA",
  "hq_country_generated": "United States",
  "capital_under_management": "$500M",
  "check_size_min": 1000000,
  "check_size_max": 10000000,
  "investor_website": "https://example.com",
  "primary_contact": "Jane Smith",
  "primary_contact_email": "jane@example.com",  // sometimes null
  "contact_count": 12,
  "has_contact_emails": true,
  "completeness_score": 0.87,
  "extracted_industries": ["SaaS", "Fintech"],
  "extracted_locations": ["United States", "California"]
}
```

## Recommended extraction strategy

### Option A: Direct table pagination (simplest)
```
GET /rest/v1/persons?select=*&limit=1000&offset=N
```
Paginate through all 1.8M persons in 1,807 pages. Each page = 1 request. Fast.

### Option B: Filtered by investor type (targeted)
```
POST /rest/v1/rpc/manual_search_investors_only2
  {"investor_types": ["Family Offices"], "limit_count": 100, "page": 1}
→ investor IDs
POST /rest/v1/rpc/get_persons_by_investor_ids
  {"investor_ids": [1, 2, 3, ...]}
→ contacts with emails
```
Good for building segmented lists.

### Option C: AI semantic search (for matching a pitch to investors)
```
POST /functions/v1/generate-ideal-investor-embedding
  {"query": "...", "pitchDeckUrl": "..."}
→ embedding
POST /rest/v1/rpc/ai_search_with_ideal_investor
  {"query_embedding": [...], "search_limit": 5000}
→ ranked investors
POST /rest/v1/rpc/get_persons_by_investor_ids
→ their contacts
```

### Option D: Bulk CSV export (for offline use)
```
POST /functions/v1/export2
  {filters + "export_name": "..."}
→ export job ID
(wait for realtime 'export_completed' event on user_exports)
POST /functions/v1/download-export
  {"id": "..."}
→ signed URL → download CSV
```
