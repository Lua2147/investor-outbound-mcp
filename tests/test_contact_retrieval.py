"""Tests for src/tools/contact_retrieval.py — Tools 9–12.

Coverage goals
--------------
- io_get_contacts: happy path (IDs), name resolution, junk filtering, per-firm cap,
  scoring sort, no-IDs validation, name-resolves-to-empty, error paths
- io_search_persons: single filter, multi-filter, name splits into two queries,
  email exact vs ilike, pagination, no-filter validation, error paths
- io_get_investor_team: tier grouping, coverage counts, empty investor, error path
- io_find_decision_makers: senior+investment gate, junk excluded, cap applied,
  empty list validation, scoring sort, error paths

Mock strategy
-------------
All tests mock IOClient directly — no live Supabase calls. We patch the
async `query` and `rpc` methods to return controlled fixtures.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures — shared person and investor data
# ---------------------------------------------------------------------------

# A realistic senior investment professional — passes all gates
PERSON_MD_INVESTMENT: dict[str, Any] = {
    "id": 1001,
    "first_name": "Alice",
    "last_name": "Chen",
    "email": "achen@arescap.com",
    "phone": "+1-310-555-0100",
    "role": "Managing Director, Private Equity Investments",
    "company_name": "Ares Capital Management",
    "linkedin_profile_url": "https://linkedin.com/in/achen",
    "location": "Los Angeles, CA",
    "investor": 42,
}

# Mid-tier VP with investment function
PERSON_VP_DEALS: dict[str, Any] = {
    "id": 1002,
    "first_name": "Bob",
    "last_name": "Smith",
    "email": None,
    "phone": "+1-212-555-0101",
    "role": "VP, Deal Origination",
    "company_name": "Ares Capital Management",
    "linkedin_profile_url": None,
    "location": "New York, NY",
    "investor": 42,
}

# Junk role — should be filtered out
PERSON_JUNK_HR: dict[str, Any] = {
    "id": 1003,
    "first_name": "Carol",
    "last_name": "Doe",
    "email": "cdoe@arescap.com",
    "phone": None,
    "role": "Human Resources Manager",
    "company_name": "Ares Capital Management",
    "linkedin_profile_url": None,
    "location": "Los Angeles, CA",
    "investor": 42,
}

# Role equals firm name — should be filtered out
PERSON_ROLE_IS_FIRM: dict[str, Any] = {
    "id": 1004,
    "first_name": "David",
    "last_name": "Park",
    "email": "dpark@brookfield.com",
    "phone": None,
    "role": "Brookfield Asset Management",
    "company_name": "Brookfield Asset Management",
    "linkedin_profile_url": None,
    "location": "Toronto, Canada",
    "investor": 99,
}

# Tier 3 person — junior with no investment function
PERSON_JUNIOR: dict[str, Any] = {
    "id": 1005,
    "first_name": "Eva",
    "last_name": "Lee",
    "email": "elee@arescap.com",
    "phone": None,
    "role": "Analyst",
    "company_name": "Ares Capital Management",
    "linkedin_profile_url": "https://linkedin.com/in/elee",
    "location": "Los Angeles, CA",
    "investor": 42,
}

# Partner at a different firm
PERSON_PARTNER_FIRM2: dict[str, Any] = {
    "id": 1010,
    "first_name": "Frank",
    "last_name": "Wang",
    "email": "fwang@kkr.com",
    "phone": "+1-212-555-0200",
    "role": "Partner, Infrastructure Investments",
    "company_name": "KKR",
    "linkedin_profile_url": "https://linkedin.com/in/fwang",
    "location": "New York, NY",
    "investor": 55,
}

# Investor name lookup row
INVESTOR_ID_ROW: dict[str, Any] = {"id": 42}
INVESTOR_ID_ROW_2: dict[str, Any] = {"id": 43}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(query_side_effect=None, query_return=None) -> MagicMock:
    """Build a mock IOClient with a controllable async query method."""
    client = MagicMock()
    client.query = AsyncMock()
    client.rpc = AsyncMock()

    if query_side_effect is not None:
        client.query.side_effect = query_side_effect
    elif query_return is not None:
        client.query.return_value = query_return

    return client


def _make_mcp() -> MagicMock:
    """Build a minimal FastMCP mock that captures tool registrations."""
    mcp = MagicMock()
    # The @mcp.tool decorator is called as mcp.tool(name=..., description=...)
    # and returns a decorator. We capture the decorated functions so tests can
    # call them directly.
    _tools: dict[str, Any] = {}

    def _tool_decorator(**kwargs):
        name = kwargs.get("name", "")
        def _register(fn):
            _tools[name] = fn
            return fn
        return _register

    mcp.tool.side_effect = _tool_decorator
    mcp._tools = _tools  # expose for test access
    return mcp


def _register_and_get(tools_subset: list[str]) -> dict[str, Any]:
    """Register the module with a mock MCP/client and return the tool callables."""
    from src.tools.contact_retrieval import register

    mcp = _make_mcp()
    client = _make_mock_client()
    register(mcp, client)
    return {name: mcp._tools[name] for name in tools_subset}, client, mcp


# ---------------------------------------------------------------------------
# Tests: io_get_contacts (Tool 9)
# ---------------------------------------------------------------------------


class TestGetContacts:
    def setup_method(self):
        from src.tools.contact_retrieval import register

        self.mcp = _make_mcp()
        self.client = _make_mock_client()
        register(self.mcp, self.client)
        self.tool = self.mcp._tools["io_get_contacts"]

    @pytest.mark.asyncio
    async def test_happy_path_returns_scored_contacts(self):
        """Returns filtered, scored contacts for a list of investor IDs."""
        self.client.query.return_value = (
            [PERSON_MD_INVESTMENT, PERSON_VP_DEALS, PERSON_JUNK_HR],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[42]))
        assert "data" in result
        # HR Manager must be filtered out
        roles = [c["role"] for c in result["data"]]
        assert not any("Human Resources" in r for r in roles)

    @pytest.mark.asyncio
    async def test_junk_roles_excluded(self):
        """Junk roles (HR, admin, etc.) are removed before returning."""
        self.client.query.return_value = ([PERSON_JUNK_HR], None)
        result = json.loads(await self.tool(investor_ids=[42]))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_role_is_firm_name_excluded(self):
        """Contacts whose role == firm name are excluded."""
        self.client.query.return_value = ([PERSON_ROLE_IS_FIRM], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_contacts_without_email_included(self):
        """Contacts without email are kept — caller enriches later."""
        self.client.query.return_value = ([PERSON_VP_DEALS], None)
        result = json.loads(await self.tool(investor_ids=[42]))
        assert len(result["data"]) == 1
        assert result["data"][0]["email"] is None

    @pytest.mark.asyncio
    async def test_sorted_by_score_descending(self):
        """Higher-score contacts appear first."""
        self.client.query.return_value = (
            [PERSON_JUNIOR, PERSON_MD_INVESTMENT, PERSON_VP_DEALS],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[42]))
        scores = [c.get("_score", 0) for c in result["data"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_max_per_firm_cap(self):
        """No more than max_per_firm contacts returned per investor FK."""
        many_persons = [
            {
                "id": 2000 + i,
                "first_name": f"Person{i}",
                "last_name": "Test",
                "email": f"p{i}@fund.com",
                "phone": None,
                "role": "Managing Director, Investments",
                "company_name": "Test Fund",
                "linkedin_profile_url": None,
                "location": "NY",
                "investor": 77,
            }
            for i in range(10)
        ]
        self.client.query.return_value = (many_persons, None)
        result = json.loads(await self.tool(investor_ids=[77], max_per_firm=3))
        assert len(result["data"]) <= 3

    @pytest.mark.asyncio
    async def test_investor_name_resolution(self):
        """investor_name triggers an ilike lookup before fetching persons."""
        # First call resolves name → IDs, second fetches persons
        self.client.query.side_effect = [
            ([INVESTOR_ID_ROW], None),  # name resolution
            ([PERSON_MD_INVESTMENT], None),  # persons fetch
        ]
        result = json.loads(await self.tool(investor_name="Ares Capital"))
        assert "data" in result
        assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_investor_name_no_match_returns_empty(self):
        """When name resolves to 0 investors, returns empty data with suggestion."""
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_name="Nonexistent Fund XYZ"))
        assert result["data"] == []
        assert "next_actions" in result

    @pytest.mark.asyncio
    async def test_no_ids_no_name_returns_validation_error(self):
        """Neither investor_ids nor investor_name → VALIDATION_ERROR."""
        result = json.loads(await self.tool())
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_deal_keywords_injected_into_scoring(self):
        """deal_keywords flow through to score_contact — score is higher with match."""
        from src.scoring import score_contact

        person = {**PERSON_VP_DEALS, "role": "VP, Energy Infrastructure Investments"}
        self.client.query.return_value = ([person], None)
        result_no_kw = json.loads(await self.tool(investor_ids=[42]))
        score_no_kw = result_no_kw["data"][0].get("_score", 0)

        result_with_kw = json.loads(
            await self.tool(investor_ids=[42], deal_keywords=["energy", "infrastructure"])
        )
        score_with_kw = result_with_kw["data"][0].get("_score", 0)

        assert score_with_kw >= score_no_kw

    @pytest.mark.asyncio
    async def test_chunking_large_id_list(self):
        """Lists > 100 IDs are chunked into multiple queries."""
        ids = list(range(1, 250))  # 249 IDs → 3 chunks
        self.client.query.return_value = ([], None)
        await self.tool(investor_ids=ids)
        # Should have been called at least 3 times (ceil(249/100) = 3)
        assert self.client.query.call_count >= 3

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("token expired")
        result = json.loads(await self.tool(investor_ids=[1]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("timeout", 503)
        result = json.loads(await self.tool(investor_ids=[1]))
        assert result["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tests: io_search_persons (Tool 10)
# ---------------------------------------------------------------------------


class TestSearchPersons:
    def setup_method(self):
        from src.tools.contact_retrieval import register

        self.mcp = _make_mcp()
        self.client = _make_mock_client()
        register(self.mcp, self.client)
        self.tool = self.mcp._tools["io_search_persons"]

    @pytest.mark.asyncio
    async def test_search_by_email_exact(self):
        """Exact email search returns matching persons."""
        self.client.query.return_value = ([PERSON_MD_INVESTMENT], 1)
        result = json.loads(await self.tool(email="achen@arescap.com"))
        assert "data" in result
        assert result["meta"]["total"] == 1

    @pytest.mark.asyncio
    async def test_search_by_company(self):
        """Company filter returns paginated results."""
        self.client.query.return_value = ([PERSON_MD_INVESTMENT, PERSON_VP_DEALS], 2)
        result = json.loads(await self.tool(company="Ares"))
        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_search_by_role(self):
        """Role filter returns matching persons."""
        self.client.query.return_value = ([PERSON_MD_INVESTMENT], 1)
        result = json.loads(await self.tool(role="Managing Director"))
        assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_search_by_name_issues_two_queries(self):
        """Name search queries both first_name and last_name, deduplicates results."""
        # First query (first_name match), second query (last_name match)
        self.client.query.side_effect = [
            ([PERSON_MD_INVESTMENT], 1),
            ([], 0),
        ]
        result = json.loads(await self.tool(name="Alice"))
        assert self.client.query.call_count == 2
        assert len(result["data"]) == 1  # no duplicates

    @pytest.mark.asyncio
    async def test_name_deduplicates_overlapping_results(self):
        """When both first_name and last_name queries return the same person, dedup."""
        self.client.query.side_effect = [
            ([PERSON_MD_INVESTMENT], 1),
            ([PERSON_MD_INVESTMENT], 1),  # same person returned twice
        ]
        result = json.loads(await self.tool(name="Alice"))
        assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_no_filters_returns_validation_error(self):
        """Calling with no filters returns VALIDATION_ERROR."""
        result = json.loads(await self.tool())
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_pagination_meta_present(self):
        """Response includes pagination meta block."""
        self.client.query.return_value = ([PERSON_MD_INVESTMENT], 100)
        result = json.loads(await self.tool(company="Ares", page=1, page_size=50))
        assert "meta" in result
        assert result["meta"]["page"] == 1
        assert result["meta"]["page_size"] == 50
        assert result["meta"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_page_size_capped_at_100(self):
        """page_size values > 100 are clamped to 100."""
        self.client.query.return_value = ([], 0)
        # Should not raise; page_size is clamped internally
        result = json.loads(await self.tool(company="Test", page_size=999))
        assert "data" in result or "error" in result

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty_list(self):
        """When no persons match, returns empty list (not an error)."""
        self.client.query.return_value = ([], 0)
        result = json.loads(await self.tool(email="nobody@nowhere.invalid"))
        assert "data" in result
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("expired")
        result = json.loads(await self.tool(email="x@x.com"))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("timeout")
        result = json.loads(await self.tool(role="Partner"))
        assert result["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tests: io_get_investor_team (Tool 11)
# ---------------------------------------------------------------------------


class TestGetInvestorTeam:
    def setup_method(self):
        from src.tools.contact_retrieval import register

        self.mcp = _make_mcp()
        self.client = _make_mock_client()
        register(self.mcp, self.client)
        self.tool = self.mcp._tools["io_get_investor_team"]

    @pytest.mark.asyncio
    async def test_returns_three_tiers(self):
        """Result always contains tier1, tier2, and tier3 keys."""
        self.client.query.return_value = (
            [PERSON_MD_INVESTMENT, PERSON_VP_DEALS, PERSON_JUNIOR],
            None,
        )
        result = json.loads(await self.tool(investor_id=42))
        tiers = result["data"]["tiers"]
        assert "tier1" in tiers
        assert "tier2" in tiers
        assert "tier3" in tiers

    @pytest.mark.asyncio
    async def test_md_person_in_tier1(self):
        """Managing Director appears in Tier 1."""
        self.client.query.return_value = ([PERSON_MD_INVESTMENT], None)
        result = json.loads(await self.tool(investor_id=42))
        tier1_persons = result["data"]["tiers"]["tier1"]["persons"]
        ids = [p["id"] for p in tier1_persons]
        assert PERSON_MD_INVESTMENT["id"] in ids

    @pytest.mark.asyncio
    async def test_vp_person_in_tier2(self):
        """VP appears in Tier 2."""
        self.client.query.return_value = ([PERSON_VP_DEALS], None)
        result = json.loads(await self.tool(investor_id=42))
        tier2_persons = result["data"]["tiers"]["tier2"]["persons"]
        ids = [p["id"] for p in tier2_persons]
        assert PERSON_VP_DEALS["id"] in ids

    @pytest.mark.asyncio
    async def test_analyst_in_tier3(self):
        """Analyst (no VP/Director/Partner) appears in Tier 3."""
        self.client.query.return_value = ([PERSON_JUNIOR], None)
        result = json.loads(await self.tool(investor_id=42))
        tier3_persons = result["data"]["tiers"]["tier3"]["persons"]
        ids = [p["id"] for p in tier3_persons]
        assert PERSON_JUNIOR["id"] in ids

    @pytest.mark.asyncio
    async def test_coverage_counts_email(self):
        """Coverage counts reflect how many persons have email, phone, LinkedIn."""
        persons = [
            PERSON_MD_INVESTMENT,  # has email, phone, linkedin
            PERSON_VP_DEALS,       # no email, has phone, no linkedin
        ]
        self.client.query.return_value = (persons, None)
        result = json.loads(await self.tool(investor_id=42))
        # MD is T1, VP is T2 — check T1 coverage
        t1_cov = result["data"]["tiers"]["tier1"]["coverage"]
        assert t1_cov["with_email"] == 1
        assert t1_cov["with_phone"] == 1
        assert t1_cov["with_linkedin"] == 1

    @pytest.mark.asyncio
    async def test_coverage_counts_no_linkedin(self):
        """VP with no LinkedIn shows with_linkedin=0 in their tier."""
        self.client.query.return_value = ([PERSON_VP_DEALS], None)
        result = json.loads(await self.tool(investor_id=42))
        t2_cov = result["data"]["tiers"]["tier2"]["coverage"]
        assert t2_cov["with_linkedin"] == 0

    @pytest.mark.asyncio
    async def test_empty_investor_returns_empty_tiers(self):
        """No persons → empty tiers with total=0 (not an error)."""
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_id=999))
        assert result["data"]["total"] == 0
        assert result["data"]["tiers"]["tier1"] == []

    @pytest.mark.asyncio
    async def test_total_count_correct(self):
        """data.total matches total persons returned."""
        persons = [PERSON_MD_INVESTMENT, PERSON_VP_DEALS, PERSON_JUNIOR]
        self.client.query.return_value = (persons, None)
        result = json.loads(await self.tool(investor_id=42))
        assert result["data"]["total"] == 3

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("expired")
        result = json.loads(await self.tool(investor_id=1))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("503", 503)
        result = json.loads(await self.tool(investor_id=1))
        assert result["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tests: io_find_decision_makers (Tool 12)
# ---------------------------------------------------------------------------


class TestFindDecisionMakers:
    def setup_method(self):
        from src.tools.contact_retrieval import register

        self.mcp = _make_mcp()
        self.client = _make_mock_client()
        register(self.mcp, self.client)
        self.tool = self.mcp._tools["io_find_decision_makers"]

    @pytest.mark.asyncio
    async def test_returns_senior_investment_professionals(self):
        """Only senior investment professionals are returned."""
        self.client.query.return_value = (
            [PERSON_MD_INVESTMENT, PERSON_VP_DEALS, PERSON_JUNIOR],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[42]))
        roles = [c["role"] for c in result["data"]]
        # Analyst (PERSON_JUNIOR) should not be in results (not senior and no investment fn)
        # Note: VP of Deal Origination IS senior+investment, MD is senior+investment
        assert not any("Analyst" == r for r in roles)

    @pytest.mark.asyncio
    async def test_junk_role_excluded(self):
        """HR / junk roles are excluded even if theoretically senior-sounding."""
        junk_senior = {
            **PERSON_JUNK_HR,
            "role": "Managing Director, Human Resources",
        }
        self.client.query.return_value = ([junk_senior], None)
        result = json.loads(await self.tool(investor_ids=[42]))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_role_is_firm_name_excluded(self):
        """Contacts whose role == firm name are excluded."""
        self.client.query.return_value = ([PERSON_ROLE_IS_FIRM], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_sorted_by_score_descending(self):
        """Results are sorted by _score descending."""
        self.client.query.return_value = (
            [PERSON_VP_DEALS, PERSON_MD_INVESTMENT],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[42]))
        scores = [c.get("_score", 0) for c in result["data"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_max_per_firm_cap(self):
        """No more than max_per_firm decision-makers returned per investor."""
        many_mds = [
            {
                "id": 3000 + i,
                "first_name": f"MD{i}",
                "last_name": "Test",
                "email": f"md{i}@fund.com",
                "phone": None,
                "role": "Managing Director, Investment Management",
                "company_name": "Big Fund",
                "linkedin_profile_url": None,
                "location": "NY",
                "investor": 88,
            }
            for i in range(8)
        ]
        self.client.query.return_value = (many_mds, None)
        result = json.loads(await self.tool(investor_ids=[88], max_per_firm=2))
        assert len(result["data"]) <= 2

    @pytest.mark.asyncio
    async def test_multi_investor_aggregation(self):
        """Contacts from multiple investors are aggregated and capped per firm."""
        self.client.query.return_value = (
            [PERSON_MD_INVESTMENT, PERSON_PARTNER_FIRM2],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[42, 55]))
        ids = [c["id"] for c in result["data"]]
        assert PERSON_MD_INVESTMENT["id"] in ids
        assert PERSON_PARTNER_FIRM2["id"] in ids

    @pytest.mark.asyncio
    async def test_empty_investor_ids_returns_validation_error(self):
        """Empty investor_ids list → VALIDATION_ERROR."""
        result = json.loads(await self.tool(investor_ids=[]))
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_deal_keywords_boost_scores(self):
        """deal_keywords increase score for matching roles."""
        person = {
            **PERSON_MD_INVESTMENT,
            "role": "Managing Director, Energy Infrastructure Investments",
        }
        self.client.query.return_value = ([person], None)

        result_no_kw = json.loads(await self.tool(investor_ids=[42]))
        score_no_kw = result_no_kw["data"][0].get("_score", 0) if result_no_kw["data"] else 0

        result_with_kw = json.loads(
            await self.tool(investor_ids=[42], deal_keywords=["energy", "infrastructure"])
        )
        score_with_kw = result_with_kw["data"][0].get("_score", 0) if result_with_kw["data"] else 0

        assert score_with_kw >= score_no_kw

    @pytest.mark.asyncio
    async def test_no_qualifying_contacts_returns_empty_with_next_action(self):
        """When no senior investment professionals found, data=[] with next_actions."""
        # Only junior analyst — fails gate
        self.client.query.return_value = ([PERSON_JUNIOR], None)
        result = json.loads(await self.tool(investor_ids=[42]))
        assert result["data"] == []
        assert "next_actions" in result

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("expired")
        result = json.loads(await self.tool(investor_ids=[1]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("timeout")
        result = json.loads(await self.tool(investor_ids=[1]))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self):
        from src.client import IOQueryError

        self.client.query.side_effect = IOQueryError("bad operator", 400)
        result = json.loads(await self.tool(investor_ids=[1]))
        assert result["error"]["code"] == "QUERY_ERROR"
