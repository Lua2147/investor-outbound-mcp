# Filter Enum Values — Real DB Values

**IMPORTANT**: Earlier notes used UI labels from the JS bundle. Those labels DON'T match the actual DB values. This doc uses real values extracted from a live 1000-row sample.

See `docs/12-real-enums-from-1k-sample.json` for complete counts.

## types_array — 68 unique values (top 15 from 1K sample)

Real DB values use **singular** form, **proper case**, sometimes parenthetical qualifiers:

```
Financial Advisor          (85 occurrences in 1K sample)
Wealth Manager             (64)
Asset Manager              (39)
Venture Capital            (28)
Family Office              (27)
Angel (individual)
Angel Group
Corporate Development
Corporate Venture Capital
Private Equity
Hedge Fund
Government
Growth/Expansion
Accelerator/Incubator
Impact Investing
```

**Filter syntax** (PostgREST array contains):
```
GET /rest/v1/investors?types_array=cs.{"Venture Capital"}
```

## investment_types_array — 77 unique values

```
Seed Round                 (194)
Early Stage VC             (191)
Venture (General)          (190)
Growth                     (172)
Later Stage VC             (163)
Series A
Series B
Series C
Buyout/LBO
Angel (individual)
Debt
PIPE
Secondary Transaction
...
```

These are actual deal-stage/type strings.

## primary_investor_type — 48 unique values

Different from `types_array`! Uses category labels:

```
Wealth Management/RIA      (168)
Venture Capital            (130)
Angel (individual)         (116)
Wealth Manager             (58)
Family Office              (51)
Financial Advisor
Asset Manager
Private Equity
Corporate
Hedge Fund
Growth/Expansion
...
```

## primary_industry_sector — Only 5 top-level categories

This is a coarse top-level rollup:

```
Financial Services                       (23)
Consumer Products and Services (B2C)     (10)
Business Products and Services (B2B)     (8)
Healthcare                               (2)
Energy                                   (1)
```

Use for rough categorization only.

## sectors_array — 88 unique values (snake_case)

Uses **snake_case machine-readable codes**, not human labels:

```
fin_services        (453)
fin_invest          (345)
technology          (239)
business_services   (180)
agnostic            (154)
healthcare
energy
consumer_products
real_estate
education
...
```

These map to human labels somewhere (likely in the JS bundle's `Wl`/`Hl` constants).

## ⚠️ CORRECTION: investment_types_enhanced & sectors_enhanced

These are **NOT additional category lists**. They are enrichment pipeline status flags:

```
investment_types_enhanced:
  "No Attempt"    (621)
  "Success"       (285)
  "Failed"        (94)

sectors_enhanced:
  "Success"       (778)
  "No Attempt"    (181)
  "Failed"        (3)
```

Values indicate whether the enrichment worker successfully processed that row. The raw enrichment data lives in `investors_scraped_data.scraped_data`.

**Filter use**: `investment_types_enhanced=eq.Success` to get only successfully-enriched rows.

## Geography enums

### hq_country_generated — 79 unique countries
```
United States       (376)
China               (82)
United Kingdom      (73)
India               (36)
Singapore           (35)
Canada
Germany
France
Switzerland
Australia
...
```

### hq_continent_generated — 6 values
```
North America       (415)
Europe              (264)
Asia                (240)
South America       (26)
Oceania             (24)
Africa
```

### hq_region_generated — 7 values
```
APAC        (235)
DACH        (47)
Nordics     (40)
MENA        (31)
LATAM       (29)
(North America not shown — likely uses country directly)
```

## Page Routes (for reference, not DB filters)

| Route | Auth | Purpose |
|-------|------|---------|
| `/` | No | Marketing home |
| `/login` | No | Email+password login |
| `/reset-password-request` | No | Password reset |
| `/investors` | Yes | Main search/browse table |
| `/exports` | Yes | CSV export download list |
| `/billing` | Yes | Stripe subscription mgmt |
| `/dashboard` | Yes | Post-login home |
| `/admin/users` | Admin | User management |
