# Investor Outbound — Architecture

## Platform Identity

- **App URL**: `https://investoroutbound.com`
- **Backend**: Supabase project `lflcztamdsmxbdkqcumj` (EU region)
- **Tech stack**: Vite + React SPA, single JS bundle (`/assets/index-DvE4bRaN.js`, 744 KB)
- **State management**: Jotai seen in bundle
- **Auth**: Supabase Auth (email + password)

## Supabase Project Details

```
project_ref: lflcztamdsmxbdkqcumj
supabase_url: https://lflcztamdsmxbdkqcumj.supabase.co
region: EU
```

### Anon Key (public, embedded in bundle)
```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxmbGN6dGFtZHNteGJka3FjdW1qIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM4NzM4MDcsImV4cCI6MjA1OTQ0OTgwN30.nGk0eSzJwmLkHi9IIbWQ1RtqnSWlhgh2cIfhlJZgAPU
```

- **Decoded payload**:
  - iss: `supabase`
  - ref: `lflcztamdsmxbdkqcumj`
  - role: `anon`
  - iat: 1743873807 (2025-04-05)
  - exp: 2059449807 (2035-03-05)
- **Long-lived**: 10 years

### RLS Status
- RLS is enforced on all tables
- Anon key alone returns `[]` (empty arrays) — no data readable
- Must authenticate as a real user to get JWT → then data is accessible

## Key Insight

**NOT the same product as Inven (app.inven.ai).** Previous session confused them because `investoroutbound.com/login` may redirect to Inven's auth, but the backend is entirely separate. Investor Outbound has its own Supabase backend, its own database, its own account.

## Separation from FINTRX

Also NOT the same as FINTRX (platform.fintrx.com). FINTRX is Rails 7 + Devise with its own 4,508 family offices and 29K contacts. Investor Outbound has 234K investors and 1.8M persons.
