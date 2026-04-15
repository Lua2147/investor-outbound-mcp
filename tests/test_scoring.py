"""Tests for src/scoring.py — contact scoring and deal-relevance gating engine.

Coverage goals
--------------
- Each of the 6 gates individually (fail on each gate in isolation)
- Each relevance path A through F2
- Edge cases: empty role, role=company name, expanded mode
- Junk pattern detection (new patterns added vs original scaffold)
- score_contact() numeric correctness
- text_matches_any() helper
- role_is_firm_name() helper
- Behavioral equivalence with segment_v2.py proven logic
"""
from __future__ import annotations

import pytest

from src.scoring import (
    JUNK_PATTERNS,
    has_investment_function,
    is_junk_role,
    is_senior,
    passes_deal_relevance,
    role_is_firm_name,
    score_contact,
    text_matches_any,
)

# ── Fixtures / helpers ───────────────────────────────────────────────────────

ENERGY_ROLE_KWS = ["energy", "infrastructure", "power", "renewable", "buyout"]
ENERGY_FIRM_KWS = ["energy", "infrastructure", "clean", "renewable", "solar"]
ENERGY_NAMED = ["ares", "brookfield", "macquarie", "kkr"]

MEDTECH_ROLE_KWS = ["healthcare", "medical", "medtech", "surgical", "biotech"]
MEDTECH_FIRM_KWS = ["health", "medical", "medtech", "surgical", "device"]
MEDTECH_NAMED = ["stryker", "medtronic", "becton dickinson"]


def _pass(
    role: str = "Managing Director, Investment",
    company: str = "Ares Management",
    investor: str = "Ares Management",
    sectors: str = "private_equity energy infrastructure",
    score: int = 45,
    role_kws: list[str] | None = None,
    firm_kws: list[str] | None = None,
    named: list[str] | None = None,
    expanded: bool = False,
    min_score: int = 20,
) -> tuple[bool, str]:
    """Call passes_deal_relevance with energy-deal defaults (easy to override)."""
    return passes_deal_relevance(
        role=role,
        company_name=company,
        investor_name=investor,
        sectors_str=sectors,
        score=score,
        role_keywords=role_kws if role_kws is not None else ENERGY_ROLE_KWS,
        firm_keywords=firm_kws if firm_kws is not None else ENERGY_FIRM_KWS,
        named_firms=named if named is not None else ENERGY_NAMED,
        expanded=expanded,
        min_score=min_score,
    )


# ── score_contact() ──────────────────────────────────────────────────────────


class TestScoreContact:
    def test_top_seniority_adds_30(self):
        score = score_contact("Managing Partner")
        assert score >= 30

    def test_ceo_adds_30(self):
        score = score_contact("CEO")
        assert score >= 30

    def test_general_partner_adds_30(self):
        # "partner" is in _SCORE_TOP_TITLES
        score = score_contact("General Partner, Venture Fund")
        assert score >= 30

    def test_vp_adds_20(self):
        score = score_contact("VP of Investments")
        # +20 seniority +15 investment function = 35
        assert score >= 20

    def test_analyst_adds_5(self):
        score = score_contact("Analyst")
        assert score == 5

    def test_investment_function_adds_15(self):
        # No seniority title, pure investment function role
        score = score_contact("Investment Associate")
        # "associate" hits junior (+5), "investment" hits fn (+15) = 20
        assert score >= 15

    def test_deal_keyword_adds_10_each(self):
        base = score_contact("Director of Investments")
        with_kw = score_contact("Director of Investments", deal_keywords=["investments"])
        assert with_kw == base + 10

    def test_multiple_deal_keywords_stack(self):
        base = score_contact("Director of Capital Markets")
        with_kws = score_contact("Director of Capital Markets", deal_keywords=["capital", "markets"])
        assert with_kws == base + 20

    def test_junk_role_subtracts_30(self):
        score = score_contact("Software Engineer")
        assert score <= -10  # 0 seniority + 0 fn - 30 junk

    def test_empty_role_returns_minus_5(self):
        assert score_contact("") == -5

    def test_short_role_returns_minus_5(self):
        assert score_contact("VP") == -5  # len("VP") == 2 < 3

    def test_three_char_role_is_valid(self):
        # "CFO" has len 3 — should not return -5
        score = score_contact("CFO")
        assert score != -5


# ── is_junk_role() ───────────────────────────────────────────────────────────


class TestIsJunkRole:
    # Original patterns (should still pass)
    def test_software_engineer_is_junk(self):
        assert is_junk_role("Software Engineer") is True

    def test_recruiter_is_junk(self):
        assert is_junk_role("Talent Recruiting Manager") is True

    def test_nurse_is_junk(self):
        assert is_junk_role("ICU Nurse") is True

    def test_intern_is_junk(self):
        assert is_junk_role("Investment Banking Intern") is True

    # New patterns added (the 12 missing from original scaffold)
    def test_janitor_is_junk(self):
        assert is_junk_role("Janitor") is True

    def test_security_guard_is_junk(self):
        assert is_junk_role("Security Guard, Night Shift") is True

    def test_driver_is_junk(self):
        assert is_junk_role("Truck Driver") is True

    def test_facilities_manager_is_junk(self):
        assert is_junk_role("Facilities Manager") is True

    def test_reservoir_engineer_is_junk(self):
        assert is_junk_role("Reservoir Engineer") is True

    def test_geologist_is_junk(self):
        assert is_junk_role("Senior Geologist") is True

    def test_drilling_is_junk(self):
        assert is_junk_role("Drilling Supervisor") is True

    def test_leasing_agent_is_junk(self):
        assert is_junk_role("Commercial Leasing Agent") is True

    def test_property_manager_is_junk(self):
        assert is_junk_role("Property Manager") is True

    # Should NOT be junk
    def test_managing_director_not_junk(self):
        assert is_junk_role("Managing Director, Private Equity") is False

    def test_partner_not_junk(self):
        assert is_junk_role("Partner, Infrastructure Fund") is False

    def test_junk_pattern_count_is_67(self):
        # 60 original patterns + 7 added in review gate (RM6)
        assert len(JUNK_PATTERNS) == 67


# ── is_senior() and has_investment_function() ────────────────────────────────


class TestHelperFunctions:
    def test_is_senior_managing_director(self):
        assert is_senior("Managing Director") is True

    def test_is_senior_vp(self):
        assert is_senior("VP of Origination") is True

    def test_is_senior_analyst_is_false(self):
        assert is_senior("Investment Analyst") is False

    def test_has_investment_function_investment(self):
        assert has_investment_function("Investment Manager") is True

    def test_has_investment_function_deal(self):
        assert has_investment_function("Deal Origination") is True

    def test_has_investment_function_m_and_a(self):
        assert has_investment_function("M&A Associate") is True

    def test_has_investment_function_false_for_admin(self):
        assert has_investment_function("Office Manager") is False

    def test_text_matches_any_case_insensitive(self):
        assert text_matches_any("Infrastructure Partner", ["INFRASTRUCTURE"]) is True

    def test_text_matches_any_no_match(self):
        assert text_matches_any("HR Director", ["energy", "clean"]) is False

    def test_text_matches_any_empty_keywords(self):
        assert text_matches_any("Partner", []) is False

    def test_text_matches_any_empty_text(self):
        assert text_matches_any("", ["energy"]) is False

    def test_role_is_firm_name_exact_match(self):
        assert role_is_firm_name("Brookfield Asset Management", "Brookfield Asset Management", "") is True

    def test_role_is_firm_name_prefix_match(self):
        assert role_is_firm_name("Macquarie Infrastructure", "Macquarie Infrastructure Partners", "") is True

    def test_role_is_firm_name_real_role(self):
        assert role_is_firm_name("Managing Director", "Ares Management", "Ares Management") is False

    def test_role_is_firm_name_short_firm_ignored(self):
        # Firm names <= 4 chars are ignored to avoid false positives
        assert role_is_firm_name("KKR", "", "KKR") is False


# ── passes_deal_relevance() — gate failures ──────────────────────────────────


class TestGates:
    def test_gate1_score_below_min(self):
        passes, reason = _pass(score=15)
        assert passes is False
        assert reason == "score<min"

    def test_gate1_exact_min_passes(self):
        # Score == min_score should pass gate 1
        passes, _ = _pass(
            role="Managing Director, Energy Infrastructure",
            score=20,
        )
        assert passes is True

    def test_gate1_custom_min_score(self):
        passes, reason = _pass(score=25, min_score=30)
        assert passes is False
        assert reason == "score<min"

    def test_gate2_empty_role(self):
        passes, reason = _pass(role="")
        assert passes is False
        assert reason == "no_role"

    def test_gate2_whitespace_only_role(self):
        passes, reason = _pass(role="  ")
        assert passes is False
        assert reason == "no_role"

    def test_gate2_two_char_role(self):
        passes, reason = _pass(role="VP")
        assert passes is False
        assert reason == "no_role"

    def test_gate3_junk_role(self):
        passes, reason = _pass(role="Software Engineer", score=25)
        assert passes is False
        assert reason == "junk_role"

    def test_gate3_new_junk_geologist(self):
        passes, reason = _pass(role="Senior Geologist", score=25)
        assert passes is False
        assert reason == "junk_role"

    def test_gate4_role_equals_firm_name(self):
        passes, reason = _pass(
            role="Ares Management",
            company="Ares Management",
            investor="Ares Management",
            score=30,
        )
        assert passes is False
        assert reason == "role=firm_name"

    def test_gate5_not_senior_not_investment(self):
        # "Events Planner" is not junk, not senior, has no investment function
        passes, reason = passes_deal_relevance(
            role="Events Planner",
            company_name="Acme Capital",
            investor_name="Acme Capital",
            sectors_str="",
            score=25,
            role_keywords=["nuclear"],
            firm_keywords=["nuclear"],
            named_firms=[],
        )
        assert passes is False
        assert reason == "not_senior_not_investment"


# ── passes_deal_relevance() — path A through F2 ──────────────────────────────


class TestRelevancePaths:
    def test_path_a_role_keyword_match(self):
        """Path A: role contains a role_keyword."""
        passes, reason = _pass(
            role="VP of Energy Infrastructure",
            score=35,
        )
        assert passes is True
        assert reason == "A"

    def test_path_a_takes_priority_over_others(self):
        """Path A is checked first — wins even when other paths would also fire."""
        passes, reason = _pass(
            role="Managing Director, Renewable Energy",
            investor="Ares",
            score=50,
            named=["ares"],
        )
        assert passes is True
        assert reason == "A"

    def test_path_b_named_firm_senior(self):
        """Path B: named firm + senior."""
        passes, reason = _pass(
            role="Managing Director",
            investor="Brookfield Asset Management",
            score=30,
            role_kws=["nuclear"],  # no role match → path A fails
        )
        assert passes is True
        assert reason == "B"

    def test_path_b_named_firm_investment_function(self):
        """Path B: named firm + investment function (non-senior)."""
        passes, reason = _pass(
            role="Investment Analyst",  # not senior, but has investment fn
            investor="KKR",
            score=20,
            role_kws=["nuclear"],  # no role match
        )
        assert passes is True
        assert reason == "B"

    def test_path_b_fails_if_not_named(self):
        """Path B does not fire if firm is not in named_firms."""
        passes, reason = _pass(
            role="Managing Director",
            investor="Random Capital Partners",
            score=30,
            role_kws=["nuclear"],
            firm_kws=["nuclear"],
            named=["brookfield"],  # random capital is not named
        )
        # Should fall through to C/D/E/fail
        assert reason != "B"

    def test_path_c_firm_keyword_plus_investment_fn(self):
        """Path C: investor name matches firm_keywords + has investment function."""
        passes, reason = _pass(
            role="Investment Manager",  # has investment function
            investor="Clean Energy Capital",  # matches firm_kws "clean","energy"
            score=25,
            role_kws=["nuclear"],  # no role match
            named=[],  # not a named firm
        )
        assert passes is True
        assert reason == "C"

    def test_path_d_firm_keyword_senior_score_35(self):
        """Path D: firm matches + senior + score >= 35."""
        passes, reason = _pass(
            role="Senior Director",  # senior, no investment function
            investor="Renewable Infrastructure Group",
            score=35,
            role_kws=["nuclear"],  # no role match
            named=[],
        )
        assert passes is True
        assert reason == "D"

    def test_path_d_fails_if_score_below_35(self):
        """Path D requires score >= 35."""
        passes, reason = _pass(
            role="Senior Director",
            investor="Renewable Infrastructure Group",
            score=30,  # below 35
            role_kws=["nuclear"],
            named=[],
        )
        # D fails → falls through to E/F/fail
        assert reason != "D"

    def test_path_e_sector_match_senior_investment_fn(self):
        """Path E: sectors_str matches firm_keywords + senior + investment fn + score >= 35."""
        passes, reason = _pass(
            role="Managing Director, Portfolio",  # senior + investment fn
            investor="Generic Diversified Fund",  # no firm keyword match
            company="Generic Diversified Fund",
            sectors="clean_tech renewable energy_storage",  # sectors match "clean","energy"
            score=40,
            role_kws=["nuclear"],  # no role match
            named=[],
        )
        assert passes is True
        assert reason == "E"

    def test_path_e_fails_without_investment_function(self):
        """Path E requires both senior AND investment function."""
        passes, reason = _pass(
            role="Managing Director",  # senior but no investment fn
            investor="Generic Fund",
            sectors="clean renewable",
            score=40,
            role_kws=["nuclear"],
            named=[],
        )
        assert reason != "E"

    def test_path_f1_expanded_senior_investment_fn(self):
        """Path F1: expanded=True + senior + investment function."""
        passes, reason = passes_deal_relevance(
            role="Director of Investments",  # senior + investment fn
            company_name="Midwest Family Office",
            investor_name="Midwest Family Office",
            sectors_str="",
            score=25,
            role_keywords=["nuclear"],  # niche — no match anywhere
            firm_keywords=["nuclear"],
            named_firms=[],
            expanded=True,
        )
        assert passes is True
        assert reason == "F1"

    def test_path_f2_expanded_investment_fn_score_30(self):
        """Path F2: expanded=True + investment function + score >= 30 (non-senior)."""
        passes, reason = passes_deal_relevance(
            role="Investment Associate",  # investment fn, NOT senior
            company_name="Small PE Shop",
            investor_name="Small PE Shop",
            sectors_str="",
            score=30,
            role_keywords=["nuclear"],
            firm_keywords=["nuclear"],
            named_firms=[],
            expanded=True,
        )
        assert passes is True
        assert reason == "F2"

    def test_path_f2_fails_if_score_below_30(self):
        """Path F2 requires score >= 30."""
        passes, reason = passes_deal_relevance(
            role="Investment Associate",
            company_name="Small PE Shop",
            investor_name="Small PE Shop",
            sectors_str="",
            score=25,  # below 30
            role_keywords=["nuclear"],
            firm_keywords=["nuclear"],
            named_firms=[],
            expanded=True,
        )
        assert passes is False
        assert reason == "no_deal_relevance"

    def test_no_path_fires_returns_no_deal_relevance(self):
        """When no path in Gate 6 fires, reason is 'no_deal_relevance'."""
        passes, reason = passes_deal_relevance(
            role="Managing Director",  # senior, no investment fn
            company_name="Acme Holdings",
            investor_name="Acme Holdings",
            sectors_str="real_estate",
            score=30,
            role_keywords=["nuclear"],
            firm_keywords=["nuclear"],
            named_firms=[],
            expanded=False,
        )
        assert passes is False
        assert reason == "no_deal_relevance"

    def test_expanded_false_does_not_fire_f1_f2(self):
        """F1/F2 must not fire when expanded=False."""
        passes, reason = passes_deal_relevance(
            role="Director of Investments",  # senior + investment fn
            company_name="Generic Fund",
            investor_name="Generic Fund",
            sectors_str="",
            score=35,
            role_keywords=["nuclear"],
            firm_keywords=["nuclear"],
            named_firms=[],
            expanded=False,
        )
        assert passes is False
        assert reason == "no_deal_relevance"


# ── Behavioral equivalence checks against segment_v2.py logic ────────────────


class TestSegmentV2Equivalence:
    """Verify that the gating logic matches segment_v2.py's proven behavior.

    These tests are derived from the actual deal configs in segment_v2.py.
    """

    def test_doosan_energy_role_passes(self):
        """Doosan deal: Energy infrastructure MD passes via path A."""
        passes, reason = passes_deal_relevance(
            role="Managing Director, Energy Infrastructure",
            company_name="Brookfield Renewable",
            investor_name="Brookfield Renewable",
            sectors_str="energy infrastructure",
            score=45,
            role_keywords=[
                "energy", "infrastructure", "power", "utility",
                "renewable", "buyout", "acquisition", "m&a",
            ],
            firm_keywords=["energy", "infrastructure", "clean", "renewable"],
            named_firms=["brookfield", "ares", "kkr"],
        )
        assert passes is True
        assert reason == "A"

    def test_doosan_expanded_mode_passes_f1(self):
        """Doosan is expanded=True: senior investment person at unrelated firm passes."""
        passes, reason = passes_deal_relevance(
            role="Partner, Portfolio Operations",  # investment fn + senior
            company_name="Unrelated Buyout Fund",
            investor_name="Unrelated Buyout Fund",
            sectors_str="private_equity",
            score=45,
            role_keywords=["nuclear"],  # no match
            firm_keywords=["nuclear"],  # no match
            named_firms=[],
            expanded=True,
        )
        assert passes is True
        assert reason == "F1"

    def test_intralogic_medtech_named_firm(self):
        """IntraLogic deal: Stryker (named firm) senior contact passes via path B."""
        passes, reason = passes_deal_relevance(
            role="VP of Corporate Development",
            company_name="Stryker Corporation",
            investor_name="Stryker Corporation",
            sectors_str="med_device health",
            score=35,
            role_keywords=MEDTECH_ROLE_KWS,
            firm_keywords=MEDTECH_FIRM_KWS,
            named_firms=["stryker", "medtronic", "becton dickinson"],
        )
        assert passes is True

    def test_hr_director_fails_gate3(self):
        """HR Director fails junk filter regardless of deal context."""
        passes, reason = passes_deal_relevance(
            role="Human Resources Director",
            company_name="Brookfield",
            investor_name="Brookfield",
            sectors_str="energy",
            score=40,
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=["brookfield"],
        )
        assert passes is False
        assert reason == "junk_role"

    def test_score_below_20_always_cut(self):
        """Score < 20 is cut regardless of firm/role keywords."""
        passes, reason = passes_deal_relevance(
            role="Managing Director, Energy",
            company_name="KKR",
            investor_name="KKR",
            sectors_str="energy",
            score=19,
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=["kkr"],
        )
        assert passes is False
        assert reason == "score<min"

    def test_sector_array_alone_insufficient_without_gate5(self):
        """Sector match alone (path E) fails if person is not senior AND lacks investment fn."""
        passes, reason = passes_deal_relevance(
            role="Director",  # senior but NO investment function
            company_name="Generalist VC",
            investor_name="Generalist VC",
            sectors_str="clean_tech renewable energy_storage",
            score=40,
            role_keywords=["nuclear"],
            firm_keywords=["clean", "renewable"],
            named_firms=[],
        )
        # Path E requires BOTH senior AND investment function
        # "Director" is senior but no investment fn → path E fails
        # Path D: firm_match=True (generalist VC doesn't match), so D also fails
        # → no_deal_relevance
        assert passes is False
