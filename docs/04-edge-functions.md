# Edge Functions

All called as `POST https://lflcztamdsmxbdkqcumj.supabase.co/functions/v1/{name}` with user JWT (or anon key for public functions).

## export2 — CSV Export (manual search results)

**Purpose**: Generate CSV export of current search results.

**Body**:
```json
{
  "limit_count": 1000,
  "investment_types": [...],
  "investor_types": [...],
  "search_term": "fintech",
  "locations": [...],
  "sectors": [...],
  "fund_domicile": [...],
  "offset_export": 0,
  "export_name": "my_export",
  "contacts_per_investor": 5,
  "investment_firm_min_size": null,
  "investment_firm_max_size": null
}
```

**Returns**: Export job ID. Poll `user_exports` table via realtime subscription for `export_completed` event.

## download-export — Get signed URL for completed export

**Body**: `{"id": "export_uuid"}`

**Returns**: `{"url": "https://...signed..."}` — temporary signed download URL.

## ai-export-ideal — Export after AI search

**Body**:
```json
{
  "investors": [...],
  "limit_count": 1000,
  "offset_export": 0,
  "export_name": "ai_export",
  "contacts_per_investor": 5
}
```

Same flow as `export2` but uses the pre-computed AI search results.

## generate-ideal-investor-embedding — AI search first step

**Body**:
```json
{
  "query": "Looking for VC investors interested in B2B SaaS fintech...",
  "pitchDeckUrl": "https://..."
}
```

**Returns**:
```json
{
  "embedding": [0.1, 0.2, ...],  // 3072-dim vector
  "structured_params": {
    "min_investment_amount": N,
    "max_investment_amount": N,
    "types": [...],
    "target_types": [...]
  },
  "ideal_investor_profile": "..."  // text description
}
```

This embedding then gets passed to `ai_search_with_ideal_investor` RPC.

## ai-chat — Chat assistant over search results

**Auth**: `Authorization: Bearer <anon_key>` (NOT user JWT — uses anon directly)

**Transport**: SSE streaming

**Body**: `{"messages": [...]}`

**Use case**: Conversational interface over the current search result set.

## check-subscription — Returns credit + plan info

**Body**: `{}`

**Returns**: `{"credits": N, "plan": "..."}`

## create-checkout — Stripe checkout

**Body**: `{"plan": "starter|pro|..."}`

**Returns**: `{"url": "https://checkout.stripe.com/..."}`
