# RPC Functions

All called as `POST /rest/v1/rpc/{name}` with user JWT.

## manual_search_investors_only2 — Primary paginated investor search

**Purpose**: Search investors with full filter set, returns paginated results.

**Call**: `POST /rest/v1/rpc/manual_search_investors_only2`

**Body params**:
```json
{
  "search_term": "string or empty",
  "investment_types": ["Venture Capital", ...] | null,
  "investor_types": ["VC-Backed Company", ...] | null,
  "search_term": "fintech",
  "locations": ["United States"] | null,
  "sectors": ["Technology"] | null,
  "fund_domicile": ["USA"] | null,
  "min_investment_amount": 500000 | null,
  "max_investment_amount": 10000000 | null,
  "investment_firm_min_size": null,
  "investment_firm_max_size": null,
  "limit_count": 50,
  "page": 1
}
```

**Returns**: Array of investor rows (same schema as `investors` table).

**Status**: CONFIRMED WORKING with user JWT.

## count_manual_search_with_paging_4 — Total count for pagination

Same params as `manual_search_investors_only2` but without `limit_count`/`page`.

Returns: integer count. Confirmed: **307,819** for empty filter set.

## count_total_contacts_4 — Total contact count for search

Same params. Returns total person count for given investor filters.

**Note**: Heavy query, times out on anon access. Use sparingly.

## get_persons_by_investor_ids — Fetch contacts for investor IDs

**Call**: `POST /rest/v1/rpc/get_persons_by_investor_ids`

**Body**: `{"investor_ids": [123, 456, 789]}`

**Returns**: Array of person rows with `email`, `phone`, `linkedin_profile_url`, `role`, `first_name`, `last_name`, `company_name`, etc. Returns up to ~10 persons per investor.

**Use case**: Two-call pattern — search investors → get their contacts.

**Status**: CONFIRMED WORKING.

## manual_search_with_paging / manual_search_with_paging_fast

Older variants of `manual_search_investors_only2`. Include different params:
- `investor_types`
- `investment_types`
- `locations`
- `sectors`
- `min_investment_amount` / `max_investment_amount`
- `limit_count`
- `page`

Use `manual_search_investors_only2` as the current version.

## manual_search_investors_only

Older variant without `investment_firm_min_size` / `investment_firm_max_size` params.

## ai_search / ai_search_2 / ai_search_with_ideal_investor — Vector search

**Call**: `POST /rest/v1/rpc/ai_search_with_ideal_investor`

**Body**:
```json
{
  "query_embedding": "[0.1, 0.2, ...]",
  "search_limit": 5000,
  "investor_types": ["Venture Capital"] | null,
  "target_investor_types": null,
  "min_investment_amount": null,
  "max_investment_amount": null
}
```

**Flow**:
1. User provides pitch deck URL + query string
2. Frontend calls edge function `generate-ideal-investor-embedding` to convert that into a 3072-dim embedding vector
3. Embedding is passed to this RPC which does cosine similarity against `investors_embeddings_3072` table

**Returns**: Ranked list of investors by similarity.

## export_contacts through export_contacts7

Bulk export RPCs. Variants 1-7 are iterative improvements.

**Params**: `limit_count`, `offset_export`, filter fields

**Use**: Generates CSV/JSON export data. The edge function `export2` orchestrates these.

## get_users — Admin only

**Returns**: All users with subscription info. Admin role required.

## toggle_user_disabled — Admin only

**Body**: `{"_user_id": "uuid", "_disabled": true}`

## has_role — Role check

**Body**: `{"_role": "ADMIN"}`
