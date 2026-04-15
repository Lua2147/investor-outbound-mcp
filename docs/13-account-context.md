# Account Context

Live-verified with authenticated credentials.

## User Account

```
email:              <REDACTED>
user_id:            <REDACTED>
role:               authenticated
provider:           email
```

## Subscription

**From `user_subscriptions` table + `check-subscription` edge fn:**

```json
{
  "id": "<REDACTED>",
  "subscription_type": "premium",
  "status": "active",
  "stripe_subscription_id": null,
  "credits": 1000000,
  "credit_renewal_amount": 1000000,
  "renewal": true,
  "expires_at": null
}
```

### Key facts
- **Tier**: `premium`
- **Credits**: **1,000,000 per renewal cycle**
- **Renewal**: auto-renews (monthly, likely)
- **Stripe**: `stripe_subscription_id` is null — implies this was provisioned manually, not via Stripe checkout
- **Effectively unlimited** for standard MCP use cases (1M credits >> any realistic workload)

### What credits are spent on
Unclear from the session. Candidates (educated guess):
- CSV exports via `export2` / `ai-export-ideal` edge functions — each contact exported likely = 1 credit
- AI search via `ai_search_with_ideal_investor` — embedding generation likely costs
- Direct table reads probably do NOT deduct credits (RLS-gated, not metered)

### Recommendation
- Route bulk reads through `GET /rest/v1/persons?select=*&limit=1000&offset=N` — direct table reads do not appear to deduct credits
- Use `export2` only when the user explicitly wants a CSV
- AI search is premium feature — use sparingly

## Verified Table Counts (2026-04-14, via Range header)

```
persons:       1,806,686  (content-range: 0-0/1806686)
investors:     234,549    (from earlier probe)
corporations:  0          (empty)
```

## RLS Reachable Tables

With the `authenticated` role + this user's JWT, these tables are readable:
- `investors` (all rows)
- `persons` (all rows)
- `corporations` (empty)
- `user_subscriptions` (own row only, probably)
- `user_roles` (own row only, probably)

Not tested: `user_exports`, `api_keys`, `investor_search_cache`, `investors_embeddings_3072`.
