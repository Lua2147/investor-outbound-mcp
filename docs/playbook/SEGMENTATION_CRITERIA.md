# Segmentation Criteria — How the FINAL Contact Lists Were Built

**Date:** 2026-04-14
**Source database:** Investor Outbound (234K investors, 1.8M contacts)
**Method:** Two-phase pipeline — broad investor pull → strict contact-level gating
**Output:** 19,481 total contacts across 5 deals

---

## Pipeline Overview

### Phase 1: Broad Investor Pull (pull_investor_outbound.py)

For each deal, multiple PostgREST queries are unioned:
1. **Sector overlap** — `investors?sectors_array=ov.{sector_codes}` filtered by `primary_investor_type`
2. **Named firm search** — `investors?investors=ilike.*{firm_name}*` for each target firm
3. **Keyword search** — `investors?description=ilike.*{keyword}*` on investor descriptions
4. **Type-only search** — when sectors are empty (e.g., family offices), pull by investor type alone
5. All results deduped by investor ID, then persons fetched via `persons?investor=in.(ids)`

### Phase 2: Contact-Level Gating (segment_v2.py)

Every contact passes through a **6-gate pipeline**. Failing ANY gate = excluded.

| Gate | Rule | Purpose |
|------|------|---------|
| **1** | Score >= 20 | Minimum relevance threshold (loosened 20% from 30 for margin of safety) |
| **2** | Role has >= 3 characters | Filter blank/null roles |
| **3** | Role is NOT a junk role | Excludes HR, IT, admin, support, clinical staff (60 regex patterns) |
| **4** | Role is NOT just the firm name repeated | Filters CRM data quality issues |
| **5** | Person is senior OR has investment function | Must be a decision-maker or deal-maker |
| **6** | Deal relevance — one of 7 paths must match | The key discrimination layer (see below) |

### Gate 6: Deal Relevance — 7 Alternative Paths

A contact passes Gate 6 if ANY of these paths match:

| Path | Condition | Signal strength |
|------|-----------|-----------------|
| **A** | Role contains a deal-specific `role_keyword` | Strongest — role itself says "energy infrastructure" |
| **B** | Named target firm + person is senior or has investment function | Named firms from SOP always included |
| **C** | Firm name matches `firm_keywords` + person has investment function | Firm is sector-relevant + person makes deals |
| **D** | Firm name matches + person is senior + score >= 35 | Firm match + senior + high score |
| **E** | Sector-array matches firm_keywords + senior + investment fn + score >= 35 | Broadest sector fallback with strict gates |
| **F1** | `expanded=True` + senior + investment function | For niche deals — any senior deal-maker passes |
| **F2** | `expanded=True` + investment function + score >= 30 | For niche deals — non-senior with decent score |

### After Gating

- **Cap: 5 contacts per firm** (sorted by score descending)
- **All contacts included regardless of email** (user enriches separately)
- **Sorted by score** (highest first)

---

## Scoring Formula

| Component | Points | Example |
|-----------|--------|---------|
| **Seniority — Top tier** | +30 | Partner, Managing Director, GP, CIO, CEO, Founder |
| **Seniority — Mid tier** | +20 | VP, Director, SVP, EVP, Principal |
| **Seniority — Junior** | +5 | Analyst, Associate |
| **Investment function** | +15 | "investment", "deal", "portfolio", "acquisition", "corp dev", "M&A", "capital", "venture", "buyout", "origination", "strategy" |
| **Deal keyword match** | +10 per keyword | Each role_keyword found in the role adds +10 |
| **Junk role** | -30 | HR, IT, admin, receptionist, engineer, nurse, teacher, etc. |

### Senior Titles (Gate 5 / scoring)
```
partner, managing director, principal, director, vice president, vp,
svp, evp, president, ceo, cfo, cio, coo, founder, co-founder,
chairman, head of, chief, managing member
```

### Investment Functions (Gate 5 / scoring)
```
investment, investor, deal, portfolio, acquisition, corporate development,
corp dev, m&a, capital, fund manager, private equity, venture, buyout,
origination, sourcing, underwriting, business development, strategy, strategic
```

### Junk Roles (Gate 3 — 60 regex patterns, sample)
```
human resources, hr, payroll, recruiting, receptionist, administrative,
office manager, executive assistant, software engineer, systems analyst,
network engineer, database admin, web developer, data scientist,
it manager, quality assurance, technical support, warehouse, shipping,
maintenance, marketing coordinator, social media, graphic design,
sales representative, account manager, compliance officer, accountant,
bookkeeper, intern, student, nurse, physician, teacher, professor,
reservoir engineer, geologist, drilling, leasing agent, property manager...
```

---

## Deal-Specific Criteria

### Deal 1: Doosan Grid Tech ($70-80M BESS Buyout)

**Mode:** `expanded=True`
**Result:** 4,454 contacts across 2,026 firms

**Role Keywords** (matched against contact's job title):
```
energy, infrastructure, power, utility, utilities, grid, storage, battery,
renewable, cleantech, clean tech, climate, sustainability, bess, solar,
wind, transition, electricity, generation, buyout, private equity,
growth equity, portfolio operations, value creation, industrial, capital,
fund, add-on, bolt-on, platform, corporate development, m&a, acquisition
```

**Firm Keywords** (matched against investor/company name):
```
energy, infrastructure, power, utility, renewable, cleantech, clean,
climate, solar, wind, battery, storage, grid, sustainability, transition,
green, industrial, esg, impact
```

**Named Firms** (always included if person is senior/investment):
```
Infrastructure PE: ares, brookfield, macquarie, kkr, blackrock,
  global infrastructure, stonepeak, eqt, antin, ifm investors, cdpq,
  omers, energy capital, arclight, kayne anderson, daiwa energy,
  i squared, actis, denham capital, ara partners, carlyle, apollo,
  tpg, warburg pincus, goldman sachs, morgan stanley, jp morgan,
  blackstone, general atlantic, advent

Energy Strategics: aes, nextera, siemens energy, abb, enel, fluence,
  duke energy, southern company, dominion, edf, engie, orsted,
  ge vernova, schneider, hitachi, mitsubishi, toshiba

Clean Energy: ls power, invenergy, clearway, pattern energy, pine gate,
  sol systems, generate capital, spring lane, terra-gen, arevon,
  intersect power, 8minute, longroad energy, savion, origis

Battery OEMs: hithium, catl, byd, lg energy, samsung sdi, sungrow,
  canadian solar, tesla energy, panasonic energy, northvolt, envision

Other: quantum capital, fonds de solidarite, foresight group,
  greencoat, octopus energy, blueleaf energy, equinor ventures
```

**Investor Types Used in Pull:**
```
PE/Buyout, Venture Capital, Infrastructure, Corporate Venture Capital,
Impact Investing, Family Office - Single, Asset Manager
```

**Sector Codes Used:**
```
energy, cleantech, renewableenergy, green_energy, utilities,
sustainableinfrastructure, newenergy, industrial, industrials,
industrialtechnology
```

---

### Deal 2: IntraLogic Health Solutions ($7M Series A MedTech SaaS)

**Mode:** Standard (not expanded)
**Result:** 3,828 contacts across 1,800 firms

**Role Keywords:**
```
healthcare, health, medical, medtech, med tech, surgical, hospital,
life science, biotech, bio tech, pharma, clinical, sterile, diagnostic,
device, patient, digital health, health tech, healthtech
```

**Firm Keywords:**
```
health, medical, medtech, surgical, hospital, life science, biotech,
pharma, clinical, sterile, diagnostic, censis, steris, orbimed, device
```

**Named Firms:**
```
Strategic Acquirers: stryker, medtronic, becton dickinson, bd, steris,
  fortive, cardinal health, smith nephew, getinge, johnson johnson,
  abbott, zimmer biomet, hologic, intuitive surgical, baxter,
  boston scientific, edwards lifesciences, teleflex,
  integra lifesciences, danaher, ge healthcare, philips

Sterile Processing: censis, mobile aspects, intelligent insites

Healthcare PE/VC: orbimed, polaris partners, general catalyst, 8vc,
  lux capital, eclipse ventures, foresite, ra capital, section 32,
  venrock, nea, warburg pincus, welsh carson, thoma bravo,
  vista equity, tpg capital, bain capital
```

**Investor Types:**
```
Venture Capital, PE/Buyout, Corporate Venture Capital,
Family Office - Single, Impact Investing, Angel (individual),
Growth/Expansion, Asset Manager
```

**Sector Codes:**
```
healthcare, health_care, health, healthtech, healthcaretechnology,
biotech, pharma, software_saas, software
```

---

### Deal 3: Brakes To Go ($2M SAFE Mobile Brake Repair)

**Mode:** `expanded=True` (CRITICAL — niche keywords yield too few matches otherwise)
**Result:** 3,412 contacts across 1,693 firms

**Role Keywords:**
```
franchise, consumer service, home service, auto, automotive,
mobile service, repair, aftermarket, family office, family capital,
direct invest, principal invest, private invest, lower middle market,
small cap, small business, consumer, services, retail, operations,
growth equity, buyout, private equity, portfolio operations,
value creation, add-on, bolt-on, platform, multi-unit,
multi-location, unit economics
```

**Firm Keywords:**
```
franchise, auto, automotive, brake, repair, aftermarket, mobile,
family office, family capital, family invest, family holding,
family fund, family equity, family partner, family venture,
family group, consumer, service, home service, roark,
driven brands, valvoline, safelite, lower middle, small cap, growth
```

**Named Firms:**
```
Auto Aftermarket: roark capital, driven brands, valvoline, safelite,
  belron, meineke, midas, carstar, maaco, servicemaster, autonation,
  pep boys, advance auto, o'reilly auto, autozone, bridgestone,
  goodyear, nubrakes

Franchise PE: trp capital, concentric equity, trive capital,
  neighborly, authority brands, home franchise, firstservice,
  sun holdings, franworth, flynn restaurant, dine brands,
  focus brands, rego restaurant, captain d, jersey mike, wingstop, take 5

Texas PE / Family Offices: blue sage, suntx, sallyport, hicks equity,
  lone star funds, highland capital, platinum equity

Lower-Mid PE: allied capital, main street capital, newspring,
  linsalata capital, shore capital, prairie capital, nelson mullins,
  comvest, mill point capital, centre partners, palladium equity,
  sentinel capital, american securities, genstar
```

**Investor Types:**
```
PE/Buyout, Family Office - Single, Venture Capital, Growth/Expansion,
Holding Company, Angel (individual)
```

**Sector Codes:** NONE (skipped — family offices have sparse sector tags; relied on type + keywords)

---

### Deal 4: Grapeviine / GV Auto Leads ($1.8M SAFE Auto Dealer SaaS)

**Mode:** Standard (not expanded)
**Result:** 871 contacts across 439 firms

**Role Keywords:**
```
automotive, auto tech, autotech, dealer, mobility, vehicle, seed,
angel, early stage, pre-seed, accelerator
```

**Firm Keywords:**
```
automotive, auto, dealer, mobility, vehicle, seed, angel, accelerator,
incubator, early stage, pre-seed
```

**Named Firms:**
```
Auto Dealer Software: cdk global, reynolds and reynolds, cox automotive,
  cars.com, dealer.com, dealersocket, autotrader, truecar, cargurus,
  tekion, fullpath, solera, dealeron, dealer inspire, impel, podium

Auto-Tech VCs: automotive ventures, autotech ventures, toyota ventures,
  bmw ventures, gm ventures, motus ventures, fm capital, canvas ventures,
  first round capital, y combinator, bling capital
```

**Investor Types:**
```
Venture Capital, Angel (individual), Corporate Venture Capital,
Accelerator/Incubator
```

**Sector Codes:**
```
automotive, mobility, software_saas, software, technology, ai_ml
```

---

### Deal 5: Future Fund One ($250M Multi-Strategy Fund Raise)

**Mode:** Standard (not expanded)
**Result:** 6,916 contacts across 3,118 firms

**Role Keywords:**
```
crypto, bitcoin, blockchain, digital asset, web3, defi, token,
real estate, reit, net lease, nnn, triple net, property,
commercial real estate, franchise, qsr, restaurant, wealth,
advisory, capital markets, alternatives, allocation
```

**Firm Keywords:**
```
crypto, bitcoin, blockchain, digital asset, web3, real estate, reit,
realty, property, net lease, wealth, advisory, capital advisory
```

**Named Firms:**
```
Crypto/Digital Asset: galaxy digital, grayscale, microstrategy,
  coinbase, bitwise, pantera capital, polychain, paradigm,
  electric capital, digital currency group, circle

NNN Real Estate: realty income, spirit realty, store capital,
  national retail properties, agree realty, essential properties,
  broadstone net lease

Known Connections: clear street, cowan, caz investments

QSR/Franchise: swig, black mountain, inspire brands

Advisory: evercore, jll, starwood, tpg
```

**Investor Types:**
```
Venture Capital, Hedge Fund, Real Estate, Family Office - Single,
PE/Buyout, Angel (individual), Wealth Management/RIA, Asset Manager
```

**Sector Codes:**
```
blockchain, crypto, realestate, real_estate, realestatedirectinvestments,
realestateretail, realestateoffice, re, multifamilyrealestate,
realestateindustrial/logistics
```

---

## Source Scripts

All scripts stored at `~/Desktop/Deal-Investor-Research/`:

| File | Purpose |
|------|---------|
| `pull_investor_outbound.py` | Phase 1: broad investor pull from Supabase PostgREST |
| `segment_v2.py` | Phase 2: 6-gate contact-level gating with deal-specific configs |
| `INVESTOR_TARGETING_SOP.md` | Deal targeting criteria (from CapIQ extraction session) |
| `SEGMENTATION_CRITERIA.md` | This document |

## Data Source

- **Database:** Investor Outbound (investoroutbound.com)
- **Backend:** Supabase project `lflcztamdsmxbdkqcumj` (EU region)
- **Total investors:** 234,549
- **Total persons:** 1,806,686
- **Extraction date:** 2026-04-14
- **Auth method:** Supabase email+password → JWT Bearer token
- **Contact data includes:** email, phone, LinkedIn URL, role, company, email verification status
