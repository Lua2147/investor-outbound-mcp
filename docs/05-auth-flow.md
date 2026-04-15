# Authentication Flow

## Standard Supabase Auth — email + password

### Step 1: Login

```
POST https://lflcztamdsmxbdkqcumj.supabase.co/auth/v1/token?grant_type=password
Headers:
  apikey: {anon_key}
  Content-Type: application/json
Body:
  {
    "email": "user@example.com",
    "password": "..."
  }
```

**Response** (200 OK):
```json
{
  "access_token": "eyJ...",     // user JWT, ~1hr expiry
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    ...
  }
}
```

### Step 2: Authenticated requests

Every subsequent call MUST include both:
```
apikey: {anon_key}
Authorization: Bearer {access_token}
```

The `apikey` header is always the anon key (it identifies the project). The `Authorization` header carries the user JWT (it identifies the user for RLS).

## Token Refresh

```
POST https://lflcztamdsmxbdkqcumj.supabase.co/auth/v1/token?grant_type=refresh_token
Body: {"refresh_token": "..."}
```

Returns a new `access_token` + `refresh_token` pair. Use `expires_in` from login response to schedule refresh before expiry.

## Credentials

Load from environment variables or `config/api_keys.json`:
```
IO_EMAIL=<from env var or api_keys.json["supabase_investor_outreach"]["email"]>
IO_PASSWORD=<from env var or api_keys.json["supabase_investor_outreach"]["password"]>
```

Confirmed working as of 2026-03-24.

## Cookie Auth (NOT working)

Supabase stores the JWT in a cookie (`sb-lflcztamdsmxbdkqcumj-auth-token`) in the browser, but:
- The cookie is URL-encoded JSON, not a raw JWT
- Pulling it from Chrome doesn't work reliably — cookies not found during last probe
- Even when present, the JWT has <1hr TTL, so stale cookies fail

**Recommendation**: Always login fresh via the password grant. Don't rely on cookie extraction.

## RLS Scope

Once logged in with a real user JWT, these tables become readable:
- `investors` (all 234K rows)
- `persons` (all 1.8M rows)
- `corporations` (0 rows, empty)
- `user_subscriptions` (own user only, likely)

Anon access (apikey only) returns `[]` for everything.
