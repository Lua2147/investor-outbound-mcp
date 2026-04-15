# Database Schema

## Record Counts (live, verified 2026-03-24)

| Table | Row Count |
|-------|-----------|
| investors | 234,549 |
| persons | 1,806,686 |
| corporations | 0 (empty) |
| **Searchable universe** (via RPC) | 307,819 |

## Tables Exposed via PostgREST

### `investors` — Core investor entity (firms, funds, family offices)

**47 columns verified live** (see `docs/10-live-samples.json` for sample rows):

```
# Identity
id                                   integer PK
investors                            text           — the name
pb_id                                text           — PitchBook cross-reference
investor_status                      text
table_change_id                      text
timestamp                            timestamp
updated_at                           timestamp
completeness_updated_at              timestamp

# Investor classification
primary_investor_type                text           — e.g. "Venture Capital"
other_investor_types                 text
types_array                          text[]         — normalized investor types

# Investment types
investment_types_array               text[]         — deal types
investment_types_enhanced            text[]         — enriched type tags

# Sectors
sectors_array                        text[]         — industry sectors
sectors_enhanced                     text[]         — enriched sector tags
sectors_tsv                          tsvector       — full-text search index
primary_industry_sector              text

# Capital
capital_under_management             text           — AUM as string
check_size_min                       numeric
check_size_max                       numeric
investments                          (structure TBD — likely array or jsonb)

# Stated preferences (explicit criteria)
preferred_geography                  text
preferred_industry                   text
preferred_investment_amount_high     numeric
preferred_investment_amount_low      numeric
preferred_investment_types           text[]

# Location
hq_location                          text
hq_country_generated                 text
hq_continent_generated               text
hq_region_generated                  text
locations_tsv                        tsvector       — full-text search index
extracted_locations                  text[]
extracted_additional_locations       text[]
extracted_industries                 text[]
extracted_additional_industries      text[]

# Description + web
description                          text
investor_website                     text

# Primary contact
primary_contact                      text           — name
primary_contact_email                text           — sometimes null
primary_contact_first_name           text
primary_contact_last_name            text
primary_contact_title                text
primary_contact_pbid                 text           — PitchBook person ID

# Rollup stats
contact_count                        integer
has_contact_emails                   boolean
completeness_score                   numeric        — 0..1
persons_completeness_score           numeric        — 0..1, contact data freshness
```

### Filter-relevant columns
- `types_array`, `investment_types_array`, `sectors_array` — primary filter dims
- `hq_country_generated`, `hq_continent_generated`, `hq_region_generated` — geography
- `check_size_min/max`, `preferred_investment_amount_low/high` — deal size filters
- `primary_industry_sector`, `preferred_industry` — sector filters

### `persons` — 1.8M contacts (THE MAIN DATA SOURCE)

**34 columns verified live** (see `docs/10-live-samples.json`):
```
id                          integer PK
first_name                  text
last_name                   text
email                       text           — real email address
phone                       text           — real phone number
location                    text
linkedin_profile_url        text           — full LinkedIn URL

# PitchBook cross-references
pb_person_url               text
pb_person_id                text
pb_company_url              text
pb_company_id               text

# Role / employment
role                        text           — job title
description                 text
company_name                text
investor                    integer        — FK → investors.id

# Metadata
completeness_score          numeric
created_at                  timestamp

# Email quality (pre-verified)
email_status                email_status enum
email_accept_all            boolean
email_domain                text
email_disposable            boolean
email_free                  boolean
email_provider              text
email_score                 smallint
email_toxicity              numeric
good_email                  boolean

# Bounce tracking
last_bounce_type            text
last_bounce_at              timestamp

# Company metadata
company_country             text
company_founded             integer
company_linkedin            text
company_size                text
company_industry            text
domain                      text
```

### `corporations` — Same schema as investors + `sectors_enhanced`

Currently 0 rows. Schema mirrors `investors` for corporate investors (strategics).

### `user_subscriptions`
```
user_id, subscription_type, status, credits,
credit_renewal_amount, expires_at
```

### `user_exports`
```
user_id, file_id, count, name, status, payload
```
Real-time subscribed — `export_completed` events notify when CSV is ready.

### `user_roles`
```
user_id, role, email, disabled
```

### `api_keys`
```
user_id, key_hash, key_prefix, name, is_active
```

### `investors_scraped_data`
```
investor, scraped_data, error
```
Raw scrape payloads backing the normalized `investors` table.

### `corporations_scraped_data`
```
investor, scraped_data_json
```

### `investor_search_cache`
Cached search results — same columns as `investors`.

### `investors_embeddings_3072`
```
investor_id, embedding (vector 3072-dim)
```
pgvector-backed. Used by `ai_search_with_ideal_investor` RPC.

### `embeddings`
General embeddings table (unclear which entity).

### `investor_completeness` / `persons_completeness`
Completeness scoring tables — likely the source of the `completeness_score` columns.

### `investors_duplicate` / `persons_duplicate`
Deduplication staging tables.

### `investors_table_change`
Change tracking.
