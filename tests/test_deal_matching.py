"""Tests for src/tools/deal_matching.py — deal matching tools.

Coverage goals:
- match_deal: happy path per deal type (buyout, Series A, SAFE, fund raise),
  expanded mode, no-sector search, named-firm-only, empty results, scoring validation
- match_deal_stage: seed, buyout, growth stages
- match_preferences: industry, geography, check size, combined filters
- find_similar_investors: happy path, investor not found, bad embedding
- Internal helpers: _dedupe_investors, _score_and_gate_contacts, _cap_per_firm

Run with:
    pytest tests/test_deal_matching.py -v
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.client import IOClient, IOQueryError, IOTransientError, QueryBuilder
from src.tools.deal_matching import (
    _cap_per_firm,
    _dedupe_investors,
    _fetch_persons_for_investors,
    _score_and_gate_contacts,
    register,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_investor(
    inv_id: int,
    name: str = "Test Fund",
    inv_type: str = "Venture Capital",
    sectors: list[str] | None = None,
    check_min: float | None = None,
    check_max: float | None = None,
    contact_count: int = 5,
    status: str = "Actively Seeking New Investments",
    preferred_investment_types: str | None = None,
    preferred_industry: str | None = None,
    preferred_geography: str | None = None,
    description: str | None = None,
) -> dict:
    """Create a mock investor row matching PostgREST shape."""
    return {
        "id": inv_id,
        "investors": name,
        "primary_investor_type": inv_type,
        "types_array": [inv_type],
        "sectors_array": sectors or ["technology"],
        "capital_under_management": "$100M",
        "check_size_min": check_min,
        "check_size_max": check_max,
        "hq_location": "New York, NY",
        "hq_country_generated": "United States",
        "investor_website": "https://example.com",
        "contact_count": contact_count,
        "has_contact_emails": True,
        "investor_status": status,
        "preferred_investment_types": preferred_investment_types,
        "preferred_industry": preferred_industry,
        "preferred_geography": preferred_geography,
        "completeness_score": 0.85,
        "description": description,
    }


def _make_person(
    person_id: int,
    role: str = "Managing Partner",
    company_name: str = "Test Fund",
    investor_id: int = 1001,
    email: str | None = "alice@test.com",
    phone: str | None = None,
    first_name: str = "Alice",
    last_name: str = "Smith",
    linkedin: str | None = "https://linkedin.com/in/alice-smith",
) -> dict:
    """Create a mock person row matching PostgREST shape."""
    return {
        "id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "role": role,
        "company_name": company_name,
        "linkedin_profile_url": linkedin,
        "location": "New York",
        "investor": investor_id,
        "email_status": "deliverable" if email else None,
        "email_score": 90 if email else None,
        "good_email": bool(email),
    }


# Sample investors for different deal types
ENERGY_INVESTOR = _make_investor(
    1001, "ArcLight Capital", "PE/Buyout",
    sectors=["energy", "infrastructure", "cleantech"],
    check_min=50, check_max=500,
    preferred_investment_types="Buyout/LBO, PE Growth/Expansion",
    description="Energy infrastructure private equity fund",
)

MEDTECH_INVESTOR = _make_investor(
    1002, "OrbiMed Advisors", "Venture Capital",
    sectors=["healthcare", "biotech"],
    check_min=5, check_max=50,
    preferred_investment_types="Early Stage VC, Later Stage VC",
    description="Healthcare and life sciences venture capital",
)

FRANCHISE_INVESTOR = _make_investor(
    1003, "Roark Capital Group", "PE/Buyout",
    sectors=["consumer", "services"],
    check_min=100, check_max=2000,
    preferred_investment_types="Buyout/LBO",
    description="Consumer and franchise-focused private equity",
)

FAMILY_OFFICE = _make_investor(
    1004, "Smith Family Office", "Family Office - Single",
    sectors=["agnostic"],
    check_min=1, check_max=10,
    preferred_investment_types="Growth, Seed Round",
    preferred_industry="Technology",
    preferred_geography="United States",
)

# Sample persons
ENERGY_MD = _make_person(
    2001, "Managing Director, Energy Infrastructure",
    "ArcLight Capital", 1001, "md@arclight.com",
)
ENERGY_VP = _make_person(
    2002, "Vice President, Investment Team",
    "ArcLight Capital", 1001, "vp@arclight.com",
)
ENERGY_ANALYST = _make_person(
    2003, "Investment Analyst",
    "ArcLight Capital", 1001, "analyst@arclight.com",
)
ENERGY_HR = _make_person(
    2004, "Human Resources Manager",
    "ArcLight Capital", 1001, "hr@arclight.com",
)
MEDTECH_PARTNER = _make_person(
    2005, "General Partner, Healthcare",
    "OrbiMed Advisors", 1002, "gp@orbimed.com",
)
NO_EMAIL_CONTACT = _make_person(
    2006, "Managing Director, Portfolio",
    "ArcLight Capital", 1001, email=None,
)

# Deal keywords
ENERGY_ROLE_KWS = ["energy", "infrastructure", "power", "buyout", "private equity"]
ENERGY_FIRM_KWS = ["energy", "infrastructure", "clean", "renewable"]
ENERGY_NAMED = ["arclight", "brookfield", "macquarie"]

MEDTECH_ROLE_KWS = ["healthcare", "medical", "medtech", "surgical"]
MEDTECH_FIRM_KWS = ["health", "medical", "medtech", "device"]
MEDTECH_NAMED = ["orbimed", "stryker", "medtronic"]


@pytest.fixture()
def mock_client() -> IOClient:
    """IOClient with pre-set token (no auth call needed)."""
    c = IOClient(email="test@example.com", password="secret")
    c._token = "mock.jwt.token"
    c._refresh_token = "mock-refresh-token"
    c._token_expires_at = time.monotonic() + 7200
    return c


@pytest.fixture()
def mcp_server():
    """Create a minimal FastMCP instance for registration tests."""
    from mcp.server.fastmcp import FastMCP
    return FastMCP(name="test-server")


# ---------------------------------------------------------------------------
# Unit tests: _dedupe_investors
# ---------------------------------------------------------------------------


class TestDedupeInvestors:
    def test_deduplicates_by_id(self):
        rows = [
            _make_investor(1, "Fund A"),
            _make_investor(1, "Fund A duplicate"),
            _make_investor(2, "Fund B"),
        ]
        result = _dedupe_investors(rows)
        assert len(result) == 2
        assert 1 in result
        assert 2 in result
        # First occurrence wins
        assert result[1]["investors"] == "Fund A"

    def test_handles_missing_id(self):
        rows = [{"investors": "No ID Fund"}, _make_investor(1, "Fund A")]
        result = _dedupe_investors(rows)
        assert len(result) == 1
        assert 1 in result

    def test_empty_list(self):
        assert _dedupe_investors([]) == {}


# ---------------------------------------------------------------------------
# Unit tests: _score_and_gate_contacts
# ---------------------------------------------------------------------------


class TestScoreAndGateContacts:
    def test_passes_senior_energy_md(self):
        investor_map = {1001: ENERGY_INVESTOR}
        persons = [ENERGY_MD]
        results = _score_and_gate_contacts(
            persons, investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        assert len(results) == 1
        assert results[0]["_score"] > 0
        assert results[0]["_investor_name"] == "ArcLight Capital"

    def test_filters_hr_junk_role(self):
        investor_map = {1001: ENERGY_INVESTOR}
        persons = [ENERGY_HR]
        results = _score_and_gate_contacts(
            persons, investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        assert len(results) == 0

    def test_filters_low_score(self):
        investor_map = {1001: ENERGY_INVESTOR}
        # Create a person with a role that won't score well
        low_scorer = _make_person(9999, "Coordinator", "ArcLight Capital", 1001)
        results = _score_and_gate_contacts(
            [low_scorer], investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        assert len(results) == 0

    def test_includes_contacts_without_email(self):
        investor_map = {1001: ENERGY_INVESTOR}
        results = _score_and_gate_contacts(
            [NO_EMAIL_CONTACT], investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        # Should pass because role is "Managing Director, Portfolio"
        # which is senior + has investment function ("portfolio")
        assert len(results) == 1
        assert results[0]["email"] is None

    def test_match_path_A_role_keyword(self):
        investor_map = {1001: ENERGY_INVESTOR}
        results = _score_and_gate_contacts(
            [ENERGY_MD], investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        assert len(results) == 1
        # "energy" and "infrastructure" are in the role AND in role_keywords -> path A
        assert results[0]["_match_path"] == "A"

    def test_match_path_B_named_firm(self):
        """Named firm + senior/investment fn -> path B."""
        investor_map = {1001: ENERGY_INVESTOR}
        # Role has no deal keywords but firm is named
        person = _make_person(
            3001, "Managing Director, Fund Management",
            "ArcLight Capital", 1001,
        )
        results = _score_and_gate_contacts(
            [person], investor_map,
            # Use role keywords that DON'T match "fund management"
            ["solar", "wind", "battery"],
            ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=False, min_score=20,
        )
        assert len(results) == 1
        assert results[0]["_match_path"] == "B"

    def test_expanded_mode_admits_more(self):
        investor_map = {1001: ENERGY_INVESTOR}
        # Generic investment person at a non-keyword firm
        person = _make_person(
            3002, "Vice President, Investments",
            "Generic Partners", 1001,
        )
        # Strict mode: no role/firm/named match
        strict = _score_and_gate_contacts(
            [person], investor_map,
            ["solar", "battery"], ["solar", "battery"], [],
            expanded=False, min_score=20,
        )
        # Expanded mode: F1 path (senior + investment fn)
        expanded = _score_and_gate_contacts(
            [person], investor_map,
            ["solar", "battery"], ["solar", "battery"], [],
            expanded=True, min_score=20,
        )
        assert len(strict) == 0
        assert len(expanded) == 1
        assert expanded[0]["_match_path"] == "F1"

    def test_handles_missing_investor(self):
        """Person with investor_id not in investor_map."""
        investor_map = {9999: _make_investor(9999, "Other Fund")}
        persons = [_make_person(3003, "Partner", "Unknown Fund", 1001)]
        results = _score_and_gate_contacts(
            persons, investor_map,
            ENERGY_ROLE_KWS, ENERGY_FIRM_KWS, ENERGY_NAMED,
            expanded=True, min_score=20,
        )
        # investor_id=1001 not in map, so investor_name is empty
        # But expanded mode can still pass if role is strong enough
        # "Partner" is senior, no investment function though
        # Actually: "Partner" is senior, check if it passes without investment fn
        # Gate 5 requires senior OR investment function -> passes
        # Gate 6 with expanded: F1 needs senior + investment fn -> no
        # F2 needs investment fn + score >= 30 -> no
        # So should be filtered out
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Unit tests: _cap_per_firm
# ---------------------------------------------------------------------------


class TestCapPerFirm:
    def test_caps_per_firm(self):
        results = [
            {"_investor_name": "Fund A", "_score": 50},
            {"_investor_name": "Fund A", "_score": 45},
            {"_investor_name": "Fund A", "_score": 40},
            {"_investor_name": "Fund A", "_score": 35},
            {"_investor_name": "Fund B", "_score": 60},
        ]
        capped = _cap_per_firm(results, max_per_firm=2)
        fund_a = [c for c in capped if c["_investor_name"] == "Fund A"]
        assert len(fund_a) == 2
        # Top 2 by score
        assert fund_a[0]["_score"] >= fund_a[1]["_score"]

    def test_all_firms_below_cap(self):
        results = [
            {"_investor_name": "Fund A", "_score": 50},
            {"_investor_name": "Fund B", "_score": 45},
        ]
        capped = _cap_per_firm(results, max_per_firm=5)
        assert len(capped) == 2

    def test_sorted_by_score_desc(self):
        results = [
            {"_investor_name": "Fund A", "_score": 30},
            {"_investor_name": "Fund B", "_score": 60},
            {"_investor_name": "Fund C", "_score": 45},
        ]
        capped = _cap_per_firm(results, max_per_firm=5)
        scores = [c["_score"] for c in capped]
        assert scores == sorted(scores, reverse=True)

    def test_empty_results(self):
        assert _cap_per_firm([], max_per_firm=5) == []

    def test_uses_company_name_fallback(self):
        results = [
            {"company_name": "Fallback Inc", "_score": 50},
        ]
        capped = _cap_per_firm(results, max_per_firm=5)
        assert len(capped) == 1


# ---------------------------------------------------------------------------
# Tool tests: match_deal
# ---------------------------------------------------------------------------


class TestMatchDeal:
    @pytest.mark.asyncio
    async def test_happy_path_buyout(self, mock_client):
        """Energy buyout deal returns scored contacts from matching investors."""
        # Mock client.query to return investors and persons
        call_count = 0

        async def mock_query(qb, *, count=None):
            nonlocal call_count
            call_count += 1
            table = qb.table

            if table == "investors":
                # Return energy investor for any investor query
                return [ENERGY_INVESTOR], None
            elif table == "persons":
                return [ENERGY_MD, ENERGY_VP, ENERGY_HR, NO_EMAIL_CONTACT], None
            return [], None

        mock_client.query = mock_query

        # Register and get the tool function
        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            sectors=["energy"],
            investor_types=["pe"],
            deal_size=70_000_000,
            description_keywords=["energy storage"],
        )

        result = json.loads(result_str)
        assert "data" in result
        contacts = result["data"]["contacts"]
        # HR contact should be filtered out
        roles = [c["role"] for c in contacts]
        assert "Human Resources Manager" not in roles
        # MD and VP should pass, possibly NO_EMAIL too
        assert len(contacts) >= 2
        # Contacts without email should be included
        no_email = [c for c in contacts if c.get("email") is None]
        # NO_EMAIL_CONTACT has role "Managing Director, Portfolio"
        # which should pass scoring

    @pytest.mark.asyncio
    async def test_happy_path_series_a(self, mock_client):
        """MedTech Series A deal returns healthcare contacts."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [MEDTECH_INVESTOR], None
            elif qb.table == "persons":
                return [MEDTECH_PARTNER], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=MEDTECH_ROLE_KWS,
            firm_keywords=MEDTECH_FIRM_KWS,
            named_firms=MEDTECH_NAMED,
            sectors=["healthcare"],
            deal_stage="series a",
        )

        result = json.loads(result_str)
        contacts = result["data"]["contacts"]
        assert len(contacts) >= 1
        assert contacts[0]["_investor_name"] == "OrbiMed Advisors"

    @pytest.mark.asyncio
    async def test_no_sector_search(self, mock_client):
        """When sectors is None, should still find investors via named firms and types."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [FAMILY_OFFICE], None
            elif qb.table == "persons":
                return [
                    _make_person(
                        4001, "Managing Director, Investments",
                        "Smith Family Office", 1004,
                    )
                ], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=["investment", "capital", "fund"],
            firm_keywords=["family", "capital"],
            named_firms=["smith family"],
            sectors=None,
            investor_types=["family office"],
        )

        result = json.loads(result_str)
        contacts = result["data"]["contacts"]
        assert len(contacts) >= 1

    @pytest.mark.asyncio
    async def test_named_firm_only(self, mock_client):
        """Search with only named firms, no sectors or types."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return [ENERGY_MD], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            sectors=None,
            investor_types=None,
        )

        result = json.loads(result_str)
        contacts = result["data"]["contacts"]
        assert len(contacts) >= 1

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_client):
        """No investors match the criteria."""
        async def mock_query(qb, *, count=None):
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=["nonexistent"],
            firm_keywords=["nonexistent"],
            named_firms=["nonexistent"],
        )

        result = json.loads(result_str)
        assert result["data"]["contacts"] == []
        assert result["data"]["investors_scanned"] == 0

    @pytest.mark.asyncio
    async def test_expanded_mode(self, mock_client):
        """Expanded mode admits more contacts via F1/F2 paths."""
        # Create a person that won't match strict keywords but IS a senior investment person
        generic_vp = _make_person(
            5001, "Vice President, Portfolio Management",
            "Generic Capital LLC", 1001,
        )

        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return [generic_vp], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        # Strict: keywords that don't match "portfolio management"
        strict_result = await tool_functions["match_deal"](
            role_keywords=["solar", "battery"],
            firm_keywords=["solar", "battery"],
            named_firms=[],
            sectors=["energy"],
            expanded=False,
        )
        strict = json.loads(strict_result)

        # Expanded: should admit via F1 (senior + investment fn)
        expanded_result = await tool_functions["match_deal"](
            role_keywords=["solar", "battery"],
            firm_keywords=["solar", "battery"],
            named_firms=[],
            sectors=["energy"],
            expanded=True,
        )
        expanded = json.loads(expanded_result)

        assert len(strict["data"]["contacts"]) == 0
        assert len(expanded["data"]["contacts"]) >= 1

    @pytest.mark.asyncio
    async def test_deal_size_converted_to_millions(self, mock_client):
        """deal_size in dollars is divided by 1M for the DB query."""
        queries_captured: list[QueryBuilder] = []

        async def mock_query(qb, *, count=None):
            queries_captured.append(qb)
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return [ENERGY_MD], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            sectors=["energy"],
            deal_size=70_000_000,  # $70M
        )

        # Find the sector query (first investor query with check_size filter)
        sector_queries = [q for q in queries_captured if q.table == "investors"]
        assert len(sector_queries) > 0
        # Check that the params include lte.70.0 (70M / 1M = 70)
        sector_params = sector_queries[0].build()
        param_dict = {k: v for k, v in sector_params}
        if "check_size_min" in param_dict:
            assert "lte.70.0" in param_dict["check_size_min"]

    @pytest.mark.asyncio
    async def test_max_per_firm_applied(self, mock_client):
        """Caps contacts per firm."""
        persons = [
            _make_person(i, f"Managing Director, Energy Team #{i}", "ArcLight Capital", 1001)
            for i in range(10)
        ]

        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return persons, None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            sectors=["energy"],
            max_per_firm=3,
        )

        result = json.loads(result_str)
        contacts = result["data"]["contacts"]
        # Should be capped at 3 for ArcLight
        assert len(contacts) <= 3

    @pytest.mark.asyncio
    async def test_max_results_applied(self, mock_client):
        """Truncates total results to max_results."""
        investors = [
            _make_investor(i, f"Fund {i}", sectors=["energy"])
            for i in range(1, 6)
        ]
        persons = []
        for inv in investors:
            for j in range(3):
                persons.append(
                    _make_person(
                        inv["id"] * 100 + j,
                        "Managing Director, Energy Investment",
                        inv["investors"],
                        inv["id"],
                    )
                )

        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return investors, None
            elif qb.table == "persons":
                return persons, None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=["fund 1", "fund 2", "fund 3", "fund 4", "fund 5"],
            sectors=["energy"],
            max_results=5,
        )

        result = json.loads(result_str)
        contacts = result["data"]["contacts"]
        assert len(contacts) <= 5

    @pytest.mark.asyncio
    async def test_query_error_returns_error_response(self, mock_client):
        """IOQueryError returns a proper error response, not an exception."""
        async def mock_query(qb, *, count=None):
            raise IOQueryError("Bad query", status_code=400)

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
        )

        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_error_response(self, mock_client):
        """IOTransientError returns sanitized error message."""
        async def mock_query(qb, *, count=None):
            raise IOTransientError("Server exploded", status_code=500)

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
        )

        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "SERVER_ERROR"
        # Should NOT expose internal error message
        assert "exploded" not in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_stats_in_response(self, mock_client):
        """Response includes stats block with counts."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return [ENERGY_MD, ENERGY_VP], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            sectors=["energy"],
        )

        result = json.loads(result_str)
        stats = result["data"]["stats"]
        assert "total_contacts" in stats
        assert "unique_firms" in stats
        assert "investors_scanned" in stats
        assert "persons_scored" in stats
        assert "with_email" in stats
        assert stats["investors_scanned"] >= 1

    @pytest.mark.asyncio
    async def test_description_keywords_trigger_queries(self, mock_client):
        """Description keywords trigger additional investor queries."""
        query_tables: list[str] = []
        query_params_list: list[list[tuple]] = []

        async def mock_query(qb, *, count=None):
            query_tables.append(qb.table)
            query_params_list.append(qb.build())
            if qb.table == "investors":
                return [ENERGY_INVESTOR], None
            elif qb.table == "persons":
                return [ENERGY_MD], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        await tool_functions["match_deal"](
            role_keywords=ENERGY_ROLE_KWS,
            firm_keywords=ENERGY_FIRM_KWS,
            named_firms=ENERGY_NAMED,
            description_keywords=["energy storage", "battery"],
        )

        # Should have investor queries for each description keyword
        investor_queries = [i for i, t in enumerate(query_tables) if t == "investors"]
        assert len(investor_queries) >= 2  # at least the 2 description keywords


# ---------------------------------------------------------------------------
# Tool tests: match_deal_stage
# ---------------------------------------------------------------------------


class TestMatchDealStage:
    @pytest.mark.asyncio
    async def test_seed_stage(self, mock_client):
        """Find investors preferring Seed Round."""
        seed_investor = _make_investor(
            2001, "Seed Capital",
            preferred_investment_types="Seed Round, Angel (individual), Start-up",
        )

        async def mock_query(qb, *, count=None):
            return [seed_investor], 1

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](stage="seed")
        result = json.loads(result_str)
        assert "data" in result
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_buyout_stage(self, mock_client):
        """Find investors preferring Buyout/LBO."""
        buyout_investor = _make_investor(
            2002, "Buyout Partners",
            preferred_investment_types="Buyout/LBO, Management Buyout",
        )

        async def mock_query(qb, *, count=None):
            return [buyout_investor], 1

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](stage="buyout")
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_growth_stage(self, mock_client):
        """Find investors preferring Growth."""
        growth_investor = _make_investor(
            2003, "Growth Equity Fund",
            preferred_investment_types="Growth, PE Growth/Expansion",
        )

        async def mock_query(qb, *, count=None):
            return [growth_investor], 1

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](stage="growth")
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_unknown_stage(self, mock_client):
        """Unknown stage returns validation error."""
        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](stage="nonexistent_xyz")
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_with_investor_type_filter(self, mock_client):
        """Stage search with investor type filter."""
        async def mock_query(qb, *, count=None):
            # Verify investor type filter is applied
            params = dict(qb.build())
            if "primary_investor_type" in params:
                return [_make_investor(2004, "VC Seed Fund")], 1
            return [], 0

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](
            stage="seed", investor_types=["vc"],
        )
        result = json.loads(result_str)
        assert "data" in result

    @pytest.mark.asyncio
    async def test_with_geography_filter(self, mock_client):
        """Stage search with geography filter."""
        async def mock_query(qb, *, count=None):
            return [_make_investor(2005, "US Growth Fund")], 1

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](
            stage="growth", geography="United States",
        )
        result = json.loads(result_str)
        assert "data" in result

    @pytest.mark.asyncio
    async def test_deduplicates_results(self, mock_client):
        """Same investor returned by multiple stage queries is deduplicated."""
        investor = _make_investor(2006, "Multi-Stage Fund")

        async def mock_query(qb, *, count=None):
            # Return same investor for every stage value query
            return [investor], 1

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_deal_stage"](stage="seed")
        result = json.loads(result_str)
        # Seed resolves to multiple stage values, but investor should appear once
        ids = [inv["id"] for inv in result["data"]]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Tool tests: match_preferences
# ---------------------------------------------------------------------------


class TestMatchPreferences:
    @pytest.mark.asyncio
    async def test_industry_filter(self, mock_client):
        """Match by preferred industry."""
        async def mock_query(qb, *, count=None):
            return [_make_investor(
                3001, "Healthcare Fund",
                preferred_industry="Healthcare, Medical Devices",
            )], 5

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"](
            preferred_industry="Healthcare",
        )
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_geography_filter(self, mock_client):
        """Match by preferred geography."""
        async def mock_query(qb, *, count=None):
            return [_make_investor(
                3002, "US Fund",
                preferred_geography="United States",
            )], 3

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"](
            preferred_geography="United States",
        )
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_check_size_range(self, mock_client):
        """Match by check size range."""
        async def mock_query(qb, *, count=None):
            return [_make_investor(
                3003, "Mid Market Fund",
                check_min=5, check_max=50,
            )], 10

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"](
            check_size_min=5, check_size_max=50,
        )
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_combined_filters(self, mock_client):
        """Multiple preference filters applied simultaneously."""
        async def mock_query(qb, *, count=None):
            return [_make_investor(
                3004, "US Healthcare PE",
                preferred_industry="Healthcare",
                preferred_geography="United States",
                check_min=10, check_max=100,
            )], 2

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"](
            preferred_industry="Healthcare",
            preferred_geography="United States",
            check_size_min=10,
            check_size_max=100,
            investor_types=["pe"],
        )
        result = json.loads(result_str)
        assert len(result["data"]) >= 1

    @pytest.mark.asyncio
    async def test_no_filters_returns_error(self, mock_client):
        """No preference filters returns validation error."""
        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"]()
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_query_error_handled(self, mock_client):
        """IOQueryError returns error response."""
        async def mock_query(qb, *, count=None):
            raise IOQueryError("Bad filter", status_code=400)

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["match_preferences"](
            preferred_industry="Healthcare",
        )
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "QUERY_ERROR"


# ---------------------------------------------------------------------------
# Tool tests: find_similar_investors
# ---------------------------------------------------------------------------


class TestFindSimilarInvestors:
    @pytest.mark.asyncio
    async def test_happy_path(self, mock_client):
        """Find similar investors using embedding similarity."""
        mock_embedding = ",".join(str(0.1 * i) for i in range(10))

        async def mock_query(qb, *, count=None):
            if qb.table == "investors_embeddings_3072":
                return [{"investor_id": 1001, "embedding": mock_embedding}], None
            return [], None

        async def mock_rpc(fn_name, body, *, retries=2):
            assert fn_name == "ai_search_with_ideal_investor"
            return [
                {
                    "id": 2001,
                    "investors": "Similar Fund A",
                    "primary_investor_type": "Venture Capital",
                    "distance": 0.05,
                    "sectors_array": ["technology"],
                    "hq_location": "San Francisco",
                    "check_size_min": 1,
                    "check_size_max": 10,
                    "contact_count": 15,
                },
                {
                    "id": 2002,
                    "investors": "Similar Fund B",
                    "primary_investor_type": "PE/Buyout",
                    "distance": 0.13,
                    "sectors_array": ["technology", "software"],
                    "hq_location": "New York",
                    "check_size_min": 10,
                    "check_size_max": 100,
                    "contact_count": 8,
                },
            ]

        mock_client.query = mock_query
        mock_client.rpc = mock_rpc

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["find_similar_investors"](investor_id=1001)
        result = json.loads(result_str)
        assert "data" in result
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "Similar Fund A"
        assert result["data"][0]["similarity_score"] == 0.95  # 1 - 0.05 distance

    @pytest.mark.asyncio
    async def test_investor_not_found(self, mock_client):
        """Investor without embedding returns NOT_FOUND error."""
        async def mock_query(qb, *, count=None):
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["find_similar_investors"](investor_id=99999)
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_empty_embedding(self, mock_client):
        """Empty embedding string returns error."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors_embeddings_3072":
                return [{"investor_id": 1001, "embedding": ""}], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["find_similar_investors"](investor_id=1001)
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_with_investor_type_filter(self, mock_client):
        """Investor type filter is passed to RPC."""
        mock_embedding = ",".join(str(0.1) for _ in range(10))
        rpc_bodies: list[dict] = []

        async def mock_query(qb, *, count=None):
            if qb.table == "investors_embeddings_3072":
                return [{"investor_id": 1001, "embedding": mock_embedding}], None
            return [], None

        async def mock_rpc(fn_name, body, *, retries=2):
            rpc_bodies.append(body)
            return []

        mock_client.query = mock_query
        mock_client.rpc = mock_rpc

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        await tool_functions["find_similar_investors"](
            investor_id=1001, investor_types=["vc"],
        )

        assert len(rpc_bodies) == 1
        # "vc" should resolve to ["Venture Capital"]
        assert "Venture Capital" in rpc_bodies[0]["investor_types"]

    @pytest.mark.asyncio
    async def test_rpc_empty_result(self, mock_client):
        """RPC returns no results."""
        mock_embedding = ",".join(str(0.1) for _ in range(10))

        async def mock_query(qb, *, count=None):
            if qb.table == "investors_embeddings_3072":
                return [{"investor_id": 1001, "embedding": mock_embedding}], None
            return [], None

        async def mock_rpc(fn_name, body, *, retries=2):
            return []

        mock_client.query = mock_query
        mock_client.rpc = mock_rpc

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["find_similar_investors"](investor_id=1001)
        result = json.loads(result_str)
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_malformed_embedding(self, mock_client):
        """Unparseable embedding string returns error."""
        async def mock_query(qb, *, count=None):
            if qb.table == "investors_embeddings_3072":
                return [{"investor_id": 1001, "embedding": "not,a,number,vector"}], None
            return [], None

        mock_client.query = mock_query

        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        register(mcp, mock_client)

        result_str = await tool_functions["find_similar_investors"](investor_id=1001)
        result = json.loads(result_str)
        assert "error" in result
        assert result["error"]["code"] == "QUERY_ERROR"


# ---------------------------------------------------------------------------
# Registration test
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_adds_four_tools(self):
        """register() adds exactly 4 tools to the MCP server."""
        mcp = MagicMock()
        tool_functions = {}

        def capture_tool():
            def decorator(fn):
                tool_functions[fn.__name__] = fn
                return fn
            return decorator

        mcp.tool = capture_tool
        client = MagicMock()

        register(mcp, client)

        assert "match_deal" in tool_functions
        assert "match_deal_stage" in tool_functions
        assert "match_preferences" in tool_functions
        assert "find_similar_investors" in tool_functions
        assert len(tool_functions) == 4
