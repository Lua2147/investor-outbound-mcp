"""Contact relevance scoring and deal-relevance gating engine.

Proven in the Deal-Investor-Research extraction pipeline (segment_v2.py):
- Validated against 206K contacts → ~19K actionable contacts across 5 real deals.
- Seniority scoring: Partner/MD/GP +30, VP/Director +20, Analyst +5
- Investment function detection: +15 for deal-making roles
- Deal keyword matching: +10 per keyword hit in role
- Junk role filtering: 67 regex patterns → excluded
- 6-gate boolean pipeline (passes_deal_relevance) with 7 relevance paths (A–F2)
"""
from __future__ import annotations

import re

# ── Junk role patterns (67 total — synced to segment_v2.py JUNK_ROLE_PATTERNS) ──

JUNK_PATTERNS: list[str] = [
    r"\bhuman resources\b",
    r"\b(?:^|\s)hr\b",
    r"\bpayroll\b",
    r"\brecruiting\b",
    r"\breceptionist\b",
    r"\badministrative\b",
    r"\boffice manager\b",
    r"\bexecutive assistant\b",
    r"\bassistant to\b",
    r"\bhelp desk\b",
    r"\bcustomer service rep\b",
    r"\bcustomer support\b",
    r"\bsoftware engineer\b",
    r"\bsystems analyst\b",
    r"\bnetwork engineer\b",
    r"\bdatabase admin\b",
    r"\bweb developer\b",
    r"\bdata scientist\b",
    r"\bit manager\b",
    r"\bit support\b",
    r"\bit director\b",
    r"\bquality assurance\b",
    r"\btechnical support\b",
    r"\bwarehouse\b",
    r"\bshipping\b",
    r"\bmaintenance\b",
    r"\bjanitor\b",
    r"\bsecurity guard\b",
    r"\bdriver\b",
    r"\bfacilities manager\b",
    r"\bmarketing coordinator\b",
    r"\bsocial media\b",
    r"\bgraphic design\b",
    r"\bcontent writer\b",
    r"\bsales representative\b",
    r"\baccount manager\b",
    r"\baccount executive\b",
    r"\bcompliance officer\b",
    r"\bcompliance analyst\b",
    r"\blegal assistant\b",
    r"\bparalegal\b",
    r"\baccountant\b",
    r"\bbookkeeper\b",
    r"\bpayable\b",
    r"\breceivable\b",
    r"\bintern\b",
    r"\bstudent\b",
    r"\bvolunteer\b",
    r"\bnurse\b",
    r"\bphysician\b",
    r"\bclinician\b",
    r"\btherapist\b",
    r"\bteacher\b",
    r"\bprofessor\b",
    r"\blecturer\b",
    r"\breservoir engineer\b",
    r"\bgeologist\b",
    r"\bdrilling\b",
    r"\bleasing agent\b",
    r"\bproperty manager\b",
    r"\bproduct manager\b",
    r"\bgeneral counsel\b",
    r"\blegal counsel\b",
    r"\bmarketing manager\b",
    r"\bproject manager\b",
    r"\bgraphic designer\b",
    r"\bdatabase administrator\b",
]

# ── Senior title substrings ──

SENIOR_TITLES: list[str] = [
    "partner",
    "managing director",
    "principal",
    "director",
    "vice president",
    "vp ",
    "svp",
    "evp",
    "president",
    "ceo",
    "cfo",
    "cio",
    "coo",
    "founder",
    "co-founder",
    "chairman",
    "head of",
    "chief",
    "managing member",
]

# ── Investment function substrings ──

INVESTMENT_FUNCTIONS: list[str] = [
    "investment",
    "investor",
    "deal",
    "portfolio",
    "acquisition",
    "corporate development",
    "corp dev",
    "m&a",
    "capital",
    "fund manager",
    "private equity",
    "venture",
    "buyout",
    "origination",
    "sourcing",
    "underwriting",
    "business development",
    "strategy",
    "strategic",
]

# ── Top-tier senior titles used in score_contact() ──

_SCORE_TOP_TITLES: list[str] = [
    "partner",
    "managing director",
    "managing partner",
    "general partner",
    "chief investment officer",
    "cio",
    "president",
    "ceo",
    "founder",
    "co-founder",
    "chief executive",
]

_SCORE_MID_TITLES: list[str] = [
    "vice president",
    "vp ",
    "svp",
    "evp",
    "director",
    "head of",
    "senior vice president",
    "executive vice president",
    "principal",
]


# ── Public helper functions ──────────────────────────────────────────────────


def is_junk_role(role: str) -> bool:
    """Return True if the role matches any of the 67 junk-role patterns."""
    role_l = role.lower()
    return any(re.search(p, role_l) for p in JUNK_PATTERNS)


def is_senior(role: str) -> bool:
    """Return True if the role contains a senior-title substring."""
    role_l = role.lower()
    return any(s in role_l for s in SENIOR_TITLES)


def has_investment_function(role: str) -> bool:
    """Return True if the role indicates a deal/investment function."""
    role_l = role.lower()
    return any(f in role_l for f in INVESTMENT_FUNCTIONS)


def text_matches_any(text: str, keywords: list[str]) -> bool:
    """Return True if *text* contains any keyword (case-insensitive)."""
    if not text or not keywords:
        return False
    text_l = text.lower()
    return any(kw.lower() in text_l for kw in keywords)


def role_is_firm_name(role: str, company: str, investor: str) -> bool:
    """Return True when the role field is just the firm name repeated.

    Detects low-quality data entries where the CRM populated 'role' with
    the organisation name instead of an actual job title.
    """
    role_l = role.lower().strip()
    for name in [company, investor]:
        name_l = (name or "").lower().strip()
        if name_l and len(name_l) > 4:
            if role_l == name_l or role_l.startswith(name_l[:20]):
                return True
    return False


def score_contact(role: str, deal_keywords: list[str] | None = None) -> int:
    """Compute a numeric relevance score for a contact's role.

    Higher score = more likely to be a decision-maker for the deal.

    Scoring rules
    -------------
    Top seniority (Partner/MD/GP/CEO/…): +30
    Mid seniority (VP/Director/Principal/…): +20
    Junior investment (Analyst/Associate): +5
    Investment function present: +15
    Each matching deal keyword in role: +10
    Junk role: -30

    Returns
    -------
    int
        Raw score; may be negative for junk/empty roles.
    """
    role_l = role.lower() if role else ""
    if not role_l or len(role_l) < 3:
        return -5

    score = 0

    if any(s in role_l for s in _SCORE_TOP_TITLES):
        score += 30
    elif any(s in role_l for s in _SCORE_MID_TITLES):
        score += 20
    elif any(s in role_l for s in ["analyst", "associate"]):
        score += 5

    if has_investment_function(role):
        score += 15

    if deal_keywords:
        for kw in deal_keywords:
            if kw.lower() in role_l:
                score += 10

    if is_junk_role(role):
        score -= 30

    return score


def contact_data_bonus(
    email: str | None,
    phone: str | None,
    linkedin: str | None,
) -> int:
    """Return a contact data availability bonus (0–6 points).

    Callers should add this to the score from score_contact() when
    contact completeness should factor into ranking decisions.

    Scoring
    -------
    Email present:    +3
    LinkedIn present: +2
    Phone present:    +1
    """
    bonus = 0
    if email:
        bonus += 3
    if linkedin:
        bonus += 2
    if phone:
        bonus += 1
    return bonus


def passes_deal_relevance(
    role: str,
    company_name: str,
    investor_name: str,
    sectors_str: str,
    score: int,
    role_keywords: list[str],
    firm_keywords: list[str],
    named_firms: list[str],
    expanded: bool = False,
    min_score: int = 20,
) -> tuple[bool, str]:
    """6-gate boolean pipeline that decides whether a contact reaches outreach.

    Mirrors the proven segment_v2.py logic validated on 206K contacts.

    Gates
    -----
    1. score >= min_score (default 20)
    2. role must be >= 3 chars
    3. role must not match any of the 43 junk patterns
    4. role must not be just the firm name repeated
    5. person must be senior OR have investment function
    6. deal relevance via 7 alternative paths (A–F2):

       A  — role contains a role_keyword (strongest signal)
       B  — named target firm + senior or has investment function
       C  — firm name matches firm_keywords + has investment function
       D  — firm name matches firm_keywords + is senior + score >= 35
       E  — sectors_str matches firm_keywords + is senior + investment fn + score >= 35
       F1 — expanded=True + is senior + has investment function
       F2 — expanded=True + has investment function + score >= 30

    Parameters
    ----------
    role:
        Job title / role string from the persons record.
    company_name:
        Person's company name (from persons.company_name).
    investor_name:
        Investor firm name (from investors.name or persons._investor_name).
    sectors_str:
        Concatenated sector codes / tags for the investor (used in path E).
    score:
        Pre-computed numeric score from score_contact().
    role_keywords:
        Keywords matched against role text.
    firm_keywords:
        Keywords matched against investor/company name text.
    named_firms:
        Exact (partial) firm names that always pass Gate 6 (path B).
        Matched case-insensitively as substrings of investor_name/company_name.
    expanded:
        When True, loosens Gate 6 to admit any senior investment professional
        (paths F1/F2). Use for niche deals where keyword coverage is sparse.
    min_score:
        Gate 1 threshold. Default 20 (proven safe level from segment_v2.py).

    Returns
    -------
    tuple[bool, str]
        (passes, reason) — reason is the path letter (A–F2) on pass, or a
        descriptive cut reason on fail (e.g. "score<min", "junk_role").
    """
    # ── Gate 1: minimum score ────────────────────────────────────────────────
    if score < min_score:
        return False, "score<min"

    # ── Gate 2: must have a real role ────────────────────────────────────────
    if not role or len(role.strip()) < 3:
        return False, "no_role"

    # ── Gate 3: not a junk role ──────────────────────────────────────────────
    if is_junk_role(role):
        return False, "junk_role"

    # ── Gate 4: role is not just the firm name ───────────────────────────────
    if role_is_firm_name(role, company_name, investor_name):
        return False, "role=firm_name"

    # ── Gate 5: senior OR investment function ────────────────────────────────
    person_is_senior = is_senior(role)
    person_has_fn = has_investment_function(role)
    if not person_is_senior and not person_has_fn:
        return False, "not_senior_not_investment"

    # ── Gate 6: deal relevance — 7 alternative paths ────────────────────────
    named_lower = [n.lower() for n in (named_firms or [])]
    investor_l = (investor_name or "").lower()
    company_l = (company_name or "").lower()

    # Path A: role contains a role_keyword (strongest signal)
    if text_matches_any(role, role_keywords):
        return True, "A"

    # Path B: named target firm + senior or investment function
    is_named = any(
        n in investor_l or n in company_l
        for n in named_lower
    )
    if is_named and (person_is_senior or person_has_fn):
        return True, "B"

    # Path C: firm name matches firm_keywords + has investment function
    firm_match = (
        text_matches_any(investor_name, firm_keywords)
        or text_matches_any(company_name, firm_keywords)
    )
    if firm_match and person_has_fn:
        return True, "C"

    # Path D: firm name matches + senior + score >= 35
    if firm_match and person_is_senior and score >= 35:
        return True, "D"

    # Path E: sector-array matches firm_keywords + senior + investment fn + score >= 35
    sector_match = text_matches_any(sectors_str, firm_keywords)
    if sector_match and person_is_senior and person_has_fn and score >= 35:
        return True, "E"

    # Path F1: expanded + senior + investment function
    if expanded and person_is_senior and person_has_fn:
        return True, "F1"

    # Path F2: expanded + investment function + score >= 30
    if expanded and person_has_fn and score >= 30:
        return True, "F2"

    return False, "no_deal_relevance"
