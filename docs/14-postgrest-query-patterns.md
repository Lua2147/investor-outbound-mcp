# PostgREST Query Patterns

Supabase exposes PostgreSQL tables via PostgREST. All standard operators work with user JWT auth.

## Base URL

```
https://lflcztamdsmxbdkqcumj.supabase.co/rest/v1/{table}
```

## Required headers

```
apikey: {anon_key}
Authorization: Bearer {user_jwt}
Accept: application/json
```

## Select columns

```
GET /rest/v1/persons?select=*
GET /rest/v1/persons?select=id,email,phone,first_name,last_name
GET /rest/v1/persons?select=id,email,investors(id,investors,primary_investor_type)   # nested join via FK
```

## Filtering operators

### Equality / inequality
```
?email=eq.jane@example.com
?email_score=gte.50
?company_country=neq.United States
```

### IN list
```
?company_country=in.(United States,Canada,Mexico)
?primary_investor_type=in.(Venture Capital,Private Equity)
```

### NULL checks
```
?email=not.is.null              # has email
?email=is.null                  # missing email
?linkedin_profile_url=not.is.null
```

### Pattern match
```
?email=ilike.*@farther.com      # case-insensitive LIKE
?first_name=like.Ja%
```

### Array operators (tested, working)
```
?types_array=cs.{"Venture Capital"}                      # contains value
?types_array=cs.{"Venture Capital","Private Equity"}     # contains all
?sectors_array=ov.{"fintech","healthcare"}               # overlap (any match)
```

### Full-text search (tested, working)
```
?sectors_tsv=fts.fintech                                 # tsvector FTS
?sectors_tsv=plfts.fintech+healthcare                    # phrase FTS
?locations_tsv=fts.New+York
```

## Pagination

### Option A: offset + limit
```
?limit=100&offset=0
?limit=100&offset=100
```

### Option B: Range header (more efficient)
```
Range-Unit: items
Range: 0-99
```

### Counting
```
Prefer: count=exact       # accurate, slow on 1.8M rows (may time out)
Prefer: count=estimated   # fast, uses pg_stats
Prefer: count=planned     # from query plan
```

Response includes `Content-Range: 0-99/1806686`.

**Warning**: `count=exact` on persons table timed out during testing. Use `count=estimated`.

## Ordering

```
?order=completeness_score.desc
?order=created_at.desc,id.asc
?order=email_score.desc.nullslast
```

## RPC calls

```
POST /rest/v1/rpc/{function_name}
Body: { ...params }
```

RPC calls return array or scalar depending on the function's return type.

## Edge function calls

```
POST /functions/v1/{function_name}
Headers: same auth (apikey + Authorization)
Body: { ...params }
```

## Common query recipes

### All persons with verified email, US-based, at VCs
```
GET /rest/v1/persons
  ?select=id,first_name,last_name,email,phone,company_name,role,investors(investors,primary_investor_type)
  &good_email=eq.true
  &company_country=eq.United States
  &limit=100
```

### Persons whose investor is tagged "Venture Capital" and "Technology"
```
GET /rest/v1/persons
  ?select=*,investors!inner(*)
  &investors.types_array=cs.{"Venture Capital"}
  &investors.sectors_array=cs.{"technology"}
  &limit=50
```
Note the `!inner` join — required for filtering on the joined table.

### Investors with stated check size $1M-$10M, premium enriched
```
GET /rest/v1/investors
  ?check_size_min=gte.1000000
  &check_size_max=lte.10000000
  &investment_types_enhanced=eq.Success
  &sectors_enhanced=eq.Success
  &limit=100
```

### Full-text search: "fintech ai"
```
GET /rest/v1/investors
  ?select=id,investors,sectors_array
  &sectors_tsv=fts.fintech+ai
  &limit=50
```

### Deduplicated emails
```
GET /rest/v1/persons
  ?select=email,first_name,last_name
  &email=not.is.null
  &email_free=eq.false
  &order=email_score.desc
  &limit=1000
```

## Supabase Auth

### Password login
```
POST /auth/v1/token?grant_type=password
Headers: apikey: {anon_key}
Body: {"email": "...", "password": "..."}
→ {access_token, refresh_token, expires_in, user}
```

### Refresh
```
POST /auth/v1/token?grant_type=refresh_token
Body: {"refresh_token": "..."}
```

### User info
```
GET /auth/v1/user
Headers: apikey + Authorization
```
