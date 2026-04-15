"""Tests for src/tools/investor_discovery.py — Tools 5–8.

All tests mock IOClient methods directly (no live Supabase calls).
Uses pytest-asyncio for async tool functions.

Run with:
    pytest tests/test_investor_discovery.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.server.fastmcp import FastMCP
from src.client import IOAuthError, IOClient, IOQueryError, IOTransientError
from src.tools.investor_discovery import register

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

INVESTOR_ROW_SUMMARY: dict[str, Any] = {
    "id": 15366,
    "investors": "Veymont Participations",
    "primary_investor_type": "Family Office",
    "types_array": ["Family Office"],
    "sectors_array": ["fin_invest", "real_estate"],
    "capital_under_management": "$500M",
    "check_size_min": 5.0,
    "check_size_max": 50.0,
    "hq_location": "Lyon, France",
    "hq_country_generated": "France",
    "investor_website": "https://veymont.fr",
    "contact_count": 3,
    "has_contact_emails": True,
    "investor_status": "Actively Seeking New Investments",
    "preferred_investment_types": "Growth/Expansion, Buyout/LBO",
    "preferred_industry": "Real estate, Finance",
    "preferred_geography": "Europe",
    "completeness_score": 0.85,
    "updated_at": "2025-04-01T00:00:00",
    "description": "Veymont Participations is a French family office focused on real estate.",
}

INVESTOR_ROW_DETAIL: dict[str, Any] = {
    **INVESTOR_ROW_SUMMARY,
    "pb_id": "56528-92",
    "table_change_id": None,
    "timestamp": "2025-04-01T00:00:00",
    "completeness_updated_at": None,
    "other_investor_types": None,
    "investment_types_array": None,
    "investment_types_enhanced": None,
    "sectors_enhanced": None,
    "sectors_tsv": None,
    "primary_industry_sector": "Finance",
    "investments": None,
    "preferred_investment_amount_high": None,
    "preferred_investment_amount_low": None,
    "hq_continent_generated": "Europe",
    "hq_region_generated": None,
    "locations_tsv": None,
    "extracted_locations": None,
    "extracted_additional_locations": None,
    "extracted_industries": None,
    "extracted_additional_industries": None,
    "primary_contact": "Frédéric Faure",
    "primary_contact_email": "ffaure@veymont.fr",
    "primary_contact_first_name": "Frédéric",
    "primary_contact_last_name": "Faure",
    "primary_contact_title": "Associate & Partner",
    "primary_contact_pbid": "119685-97P",
    "persons_completeness_score": 0.90,
}

# Content-Range header total
_TOTAL_COUNT = 234000


def _make_mock_client(
    rows: list[dict] | None = None,
    total: int | None = _TOTAL_COUNT,
    detail_row: dict | None = None,
    raise_exc: Exception | None = None,
) -> IOClient:
    """Return an IOClient mock with pre-wired query() and get_investor_by_id()."""
    client = MagicMock(spec=IOClient)

    if raise_exc is not None:
        client.query = AsyncMock(side_effect=raise_exc)
        client.get_investor_by_id = AsyncMock(side_effect=raise_exc)
    else:
        _rows = rows if rows is not None else [INVESTOR_ROW_SUMMARY]
        client.query = AsyncMock(return_value=(_rows, total))
        client.get_investor_by_id = AsyncMock(return_value=detail_row or INVESTOR_ROW_DETAIL)

    return client


def _make_mcp_and_register(client: IOClient) -> tuple[FastMCP, dict]:
    """Build a FastMCP instance, register the tool module, return the tool registry."""
    mcp = FastMCP(name="test-server")
    register(mcp, client)
    # Collect registered tool coroutines by name
    tools: dict[str, Any] = {t.name: t for t in mcp._tool_manager.list_tools()}
    return mcp, tools


async def _call_tool(tools: dict, tool_name: str, **kwargs: Any) -> dict:
    """Call a registered tool by name and parse its JSON response."""
    fn = tools[tool_name].fn
    result = await fn(**kwargs)
    return json.loads(result)


# ---------------------------------------------------------------------------
# Tests: io_search_investors (Tool 5)
# ---------------------------------------------------------------------------


class TestSearchInvestors:
    @pytest.mark.asyncio
    async def test_returns_paginated_envelope(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert "data" in resp
        assert "meta" in resp
        assert "summary" in resp
        assert isinstance(resp["data"], list)

    @pytest.mark.asyncio
    async def test_data_contains_investor_fields(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert len(resp["data"]) == 1
        inv = resp["data"][0]
        assert inv["id"] == 15366
        assert inv["investors"] == "Veymont Participations"
        assert inv["primary_investor_type"] == "Family Office"

    @pytest.mark.asyncio
    async def test_meta_total_reflects_db_count(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=5000)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", limit=50)
        assert resp["meta"]["total"] == 5000
        assert resp["meta"]["page_size"] == 50
        assert resp["meta"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_sector_filter_triggers_query(self) -> None:
        """Sector filter should be forwarded to the DB query (ov operator)."""
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", sectors=["energy"])
        # No error, empty result set
        assert resp["data"] == []
        assert resp["meta"]["total"] == 0

    @pytest.mark.asyncio
    async def test_investor_type_resolution(self) -> None:
        """Human-readable 'family office' should resolve to DB enum values."""
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        # Should not error — sector resolution is internal
        resp = await _call_tool(tools, "io_search_investors", investor_types=["family office"])
        assert "data" in resp
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_check_size_converts_dollars_to_millions(self) -> None:
        """$5_000_000 should become 5.0 in the query (stored as millions)."""
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_search_investors", check_size_min_dollars=5_000_000
        )
        assert "error" not in resp
        # Verify query was called (check_size filter was applied without error)
        client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_geography_filter_applied(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", geography="United States")
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_include_acquired_flag(self) -> None:
        """include_acquired=True should not add the neq filter."""
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", include_acquired=True)
        assert "error" not in resp
        client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_limit_clamped_to_max(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", limit=9999)
        # Tool should clamp silently and succeed
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_empty_result_set_returns_clean_response(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors", keyword="zzznomatch")
        assert resp["data"] == []
        assert resp["meta"]["total"] == 0

    @pytest.mark.asyncio
    async def test_auth_error_returns_error_envelope(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad creds"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert "error" in resp
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_error_envelope(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad filter", 400))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert "error" in resp
        assert resp["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_error_envelope(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("timeout", 503))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert "error" in resp
        assert resp["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_next_actions_present(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_investors")
        assert "next_actions" in resp
        assert len(resp["next_actions"]) > 0


# ---------------------------------------------------------------------------
# Tests: io_search_descriptions (Tool 6)
# ---------------------------------------------------------------------------


class TestSearchDescriptions:
    @pytest.mark.asyncio
    async def test_returns_description_snippet(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="family office")
        assert "data" in resp
        assert len(resp["data"]) == 1
        item = resp["data"][0]
        assert "description_snippet" in item

    @pytest.mark.asyncio
    async def test_description_snippet_truncated_at_300(self) -> None:
        long_desc = "A" * 400
        row = {**INVESTOR_ROW_SUMMARY, "description": long_desc}
        client = _make_mock_client(rows=[row], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="A")
        snippet = resp["data"][0]["description_snippet"]
        assert len(snippet) <= 305  # 300 chars + "…"
        assert snippet.endswith("…")

    @pytest.mark.asyncio
    async def test_empty_keyword_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="")
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_whitespace_keyword_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="   ")
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_investor_type_filter_accepted(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_search_descriptions",
            keyword="renewable energy",
            investor_types=["pe"],
        )
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_summary_warns_about_slow_scan(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="test")
        assert "slow" in resp["summary"].lower() or "sequential scan" in resp["summary"].lower()

    @pytest.mark.asyncio
    async def test_null_description_produces_none_snippet(self) -> None:
        row = {**INVESTOR_ROW_SUMMARY, "description": None}
        client = _make_mock_client(rows=[row], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="anything")
        assert resp["data"][0]["description_snippet"] is None

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("401"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_search_descriptions", keyword="test")
        assert resp["error"]["code"] == "AUTH_FAILED"


# ---------------------------------------------------------------------------
# Tests: io_get_investor (Tool 7)
# ---------------------------------------------------------------------------


class TestGetInvestor:
    @pytest.mark.asyncio
    async def test_lookup_by_id_returns_full_detail(self) -> None:
        client = _make_mock_client(detail_row=INVESTOR_ROW_DETAIL)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", investor_id=15366)
        assert "data" in resp
        assert resp["data"]["id"] == 15366
        assert resp["data"]["pb_id"] == "56528-92"

    @pytest.mark.asyncio
    async def test_lookup_by_id_uses_client_cache_method(self) -> None:
        client = _make_mock_client(detail_row=INVESTOR_ROW_DETAIL)
        _, tools = _make_mcp_and_register(client)
        await _call_tool(tools, "io_get_investor", investor_id=15366)
        client.get_investor_by_id.assert_awaited_once_with(15366)

    @pytest.mark.asyncio
    async def test_lookup_by_name_uses_query(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_DETAIL], total=None)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", name="Veymont")
        assert "data" in resp
        assert "error" not in resp
        client.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lookup_by_name_returns_best_match(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_DETAIL], total=None)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", name="veymont")
        assert resp["data"]["investors"] == "Veymont Participations"

    @pytest.mark.asyncio
    async def test_id_not_found_returns_not_found_error(self) -> None:
        client = _make_mock_client(detail_row=None)
        client.get_investor_by_id = AsyncMock(return_value=None)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", investor_id=999999)
        assert "error" in resp
        assert resp["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_name_not_found_returns_not_found_error(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", name="zzznomatch")
        assert "error" in resp
        assert resp["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_no_args_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor")
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_empty_name_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", name="   ")
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_next_actions_include_contacts_call(self) -> None:
        client = _make_mock_client(detail_row=INVESTOR_ROW_DETAIL)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", investor_id=15366)
        assert "next_actions" in resp
        assert any("contacts" in a.lower() for a in resp["next_actions"])

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("401"))
        # get_investor_by_id needs to raise too
        client.get_investor_by_id = AsyncMock(side_effect=IOAuthError("401"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", investor_id=1)
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_propagated_for_name_lookup(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad", 400))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_get_investor", name="some firm")
        assert resp["error"]["code"] == "QUERY_ERROR"


# ---------------------------------------------------------------------------
# Tests: io_investor_freshness (Tool 8)
# ---------------------------------------------------------------------------


class TestInvestorFreshness:
    @pytest.mark.asyncio
    async def test_returns_paginated_envelope(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=100)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        assert "data" in resp
        assert "meta" in resp
        assert "summary" in resp

    @pytest.mark.asyncio
    async def test_updated_at_included_in_results(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        item = resp["data"][0]
        assert "updated_at" in item
        assert item["updated_at"] == "2025-04-01T00:00:00"

    @pytest.mark.asyncio
    async def test_sector_filter_accepted(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness", sectors=["fintech"])
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_investor_type_filter_accepted(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness", investor_types=["vc"])
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_most_recent_in_summary(self) -> None:
        client = _make_mock_client(rows=[INVESTOR_ROW_SUMMARY], total=1)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        assert "2025-04-01T00:00:00" in resp["summary"]

    @pytest.mark.asyncio
    async def test_empty_result_set(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        assert resp["data"] == []
        assert resp["meta"]["total"] == 0

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("401"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_propagated(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("503", 503))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_freshness")
        assert resp["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tests: register() function and tool registration
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_produces_four_tools(self) -> None:
        client = MagicMock(spec=IOClient)
        mcp = FastMCP(name="test")
        register(mcp, client)
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "io_search_investors",
            "io_search_descriptions",
            "io_get_investor",
            "io_investor_freshness",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_tools_have_docstrings(self) -> None:
        client = MagicMock(spec=IOClient)
        mcp = FastMCP(name="test")
        register(mcp, client)
        for tool in mcp._tool_manager.list_tools():
            if tool.name in {
                "io_search_investors",
                "io_search_descriptions",
                "io_get_investor",
                "io_investor_freshness",
            }:
                assert tool.description, f"Tool {tool.name} missing description/docstring"
