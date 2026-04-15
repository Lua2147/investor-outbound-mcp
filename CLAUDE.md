# Investor Outbound MCP — Context

## Purpose
MCP server wrapping the Investor Outbound Supabase backend (CONFIRMED — 1.8M persons with real emails and phones).

## Source of Truth
All API details, schema, and access patterns are documented in `docs/` within this directory.

Do NOT duplicate endpoint docs here. Read the docs.

## Key Facts
- Supabase project: `lflcztamdsmxbdkqcumj` (EU region)
- Auth: Supabase email+password → user JWT (2hr expiry, refresh token available)
- RLS enforced — anon key returns empty arrays, need user JWT
- Credentials NOT in api_keys.json — must be provided at runtime or via env vars

## Data Model
- `investors` table — 234,549 rows, AUM/check size/sectors/types/contact info
- `persons` table — 1,806,686 rows, full contact data (email, phone, linkedin)
- `corporations` table — currently empty

## Search Flow (two-call pattern)
1. `POST /rest/v1/rpc/manual_search_investors_only2` — get investor IDs with filters
2. `POST /rest/v1/rpc/get_persons_by_investor_ids` — get contacts for those IDs

## Alt: Direct Table Read
`GET /rest/v1/persons?select=*&limit=1000&offset=N` — paginate all 1.8M persons directly. Simpler for bulk extraction.

## Export
Supabase edge function `export2` generates CSV exports. Subscribe to `user_exports` realtime channel for `export_completed` events. Then `download-export` for signed URL.

## Known Limitations
- RIA data not present (this is a separate product from FINTRX)
- corporations table is empty
- No phone/email deliverability rechecks — use `email_status` field as-is

## Proposed MCP Tools
(Not yet implemented — design phase)
- `search_investors` — filter by type, location, sector, AUM, check size
- `get_investor_contacts` — fetch persons for an investor ID
- `get_person` — lookup by email or ID
- `search_persons` — filter by role, company, location
- `semantic_search_investors` — AI search via `ai_search_with_ideal_investor` (vector embedding)
- `export_search` — kick off CSV export via edge function
