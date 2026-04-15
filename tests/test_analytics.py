"""Tests for src/tools/analytics.py — Tools 26–27.

All tests mock IOClient methods directly (no live Supabase calls).
Uses pytest-asyncio for async tool functions.

Run with:
    pytest tests/test_analytics.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp.server.fastmcp import FastMCP
from src.client import IOAuthError, IOClient, IOQueryError, IOTransientError
from src.tools.analytics import _bucket_label, _compute_percentiles, register

# ---------------------------------------------------------------------------
# Sample investor rows
# ---------------------------------------------------------------------------

_BASE_ROW: dict[str, Any] = {
    "id": 1,
    "investors": "Apex Capital",
    "primary_investor_type": "Venture Capital",
    "hq_country_generated": "United States",
    "check_size_min": 5.0,    # $5M
    "check_size_max": 25.0,   # $25M
    "contact_count": 8,
    "investor_status": "Actively Seeking New Investments",
}

# A diverse set of rows spanning multiple types, countries, and check sizes
_ROWS: list[dict[str, Any]] = [
    {**_BASE_ROW, "id": 1, "investors": "Apex Capital", "primary_investor_type": "Venture Capital",
     "hq_country_generated": "United States", "check_size_min": 5.0, "contact_count": 8},
    {**_BASE_ROW, "id": 2, "investors": "London PE", "primary_investor_type": "PE/Buyout",
     "hq_country_generated": "United Kingdom", "check_size_min": 50.0, "contact_count": 12},
    {**_BASE_ROW, "id": 3, "investors": "Paris FO", "primary_investor_type": "Family Office",
     "hq_country_generated": "France", "check_size_min": 0.5, "contact_count": 3},
    {**_BASE_ROW, "id": 4, "investors": "Boston VC", "primary_investor_type": "Venture Capital",
     "hq_country_generated": "United States", "check_size_min": 2.0, "contact_count": 5},
    {**_BASE_ROW, "id": 5, "investors": "NY Hedge", "primary_investor_type": "Hedge Fund",
     "hq_country_generated": "United States", "check_size_min": 500.0, "contact_count": 20},
    {**_BASE_ROW, "id": 6, "investors": "GigaFund", "primary_investor_type": "Venture Capital",
     "hq_country_generated": "United States", "check_size_min": 1500.0, "contact_count": 30},
    {**_BASE_ROW, "id": 7, "investors": "SeedAngel", "primary_investor_type": "Angel (individual)",
     "hq_country_generated": "Canada", "check_size_min": None, "contact_count": 1},
    {**_BASE_ROW, "id": 8, "investors": "EuroGrowth", "primary_investor_type": "Growth/Expansion",
     "hq_country_generated": "Germany", "check_size_min": 15.0, "contact_count": 7},
]


def _make_mock_client(
    rows: list[dict] | None = None,
    total: int | None = 8,
    raise_exc: Exception | None = None,
) -> IOClient:
    """Return an IOClient mock with a pre-wired query()."""
    client = MagicMock(spec=IOClient)
    if raise_exc is not None:
        client.query = AsyncMock(side_effect=raise_exc)
    else:
        _rows = rows if rows is not None else list(_ROWS)
        client.query = AsyncMock(return_value=(_rows, total))
    return client


def _make_mcp_and_register(client: IOClient) -> tuple[FastMCP, dict]:
    """Build FastMCP, register analytics tools, return tool registry."""
    mcp = FastMCP(name="test-analytics")
    register(mcp, client)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    return mcp, tools


async def _call_tool(tools: dict, tool_name: str, **kwargs: Any) -> dict:
    """Call a registered tool by name and parse its JSON response."""
    fn = tools[tool_name].fn
    result = await fn(**kwargs)
    return json.loads(result)


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


class TestBucketLabel:
    def test_below_one_million(self) -> None:
        assert _bucket_label(0.0) == "<$1M"
        assert _bucket_label(0.5) == "<$1M"
        assert _bucket_label(0.99) == "<$1M"

    def test_one_to_five(self) -> None:
        assert _bucket_label(1.0) == "$1–5M"
        assert _bucket_label(3.0) == "$1–5M"
        assert _bucket_label(4.99) == "$1–5M"

    def test_five_to_twenty_five(self) -> None:
        assert _bucket_label(5.0) == "$5–25M"
        assert _bucket_label(10.0) == "$5–25M"

    def test_twenty_five_to_one_hundred(self) -> None:
        assert _bucket_label(25.0) == "$25–100M"
        assert _bucket_label(99.9) == "$25–100M"

    def test_one_hundred_to_one_billion(self) -> None:
        assert _bucket_label(100.0) == "$100M–$1B"
        assert _bucket_label(500.0) == "$100M–$1B"
        assert _bucket_label(999.9) == "$100M–$1B"

    def test_one_billion_plus(self) -> None:
        assert _bucket_label(1_000.0) == "$1B+"
        assert _bucket_label(5_000.0) == "$1B+"


class TestComputePercentiles:
    def test_empty_list_returns_nones(self) -> None:
        result = _compute_percentiles([])
        assert result == {"p25": None, "p50": None, "p75": None}

    def test_single_element(self) -> None:
        result = _compute_percentiles([10.0])
        assert result["p50"] == 10.0

    def test_known_values(self) -> None:
        # [1, 2, 3, 4, 5] → p25=1.5 (index 1 of 5-1=4 * 0.25 = 1.0), p50=3.0, p75=4.5
        result = _compute_percentiles([5.0, 1.0, 3.0, 2.0, 4.0])  # unsorted input
        assert result["p25"] is not None
        assert result["p50"] == 3.0
        assert result["p75"] is not None

    def test_two_elements(self) -> None:
        result = _compute_percentiles([10.0, 20.0])
        # p50 should be the midpoint
        assert result["p50"] == 15.0


# ---------------------------------------------------------------------------
# Tests: io_sector_landscape (Tool 26)
# ---------------------------------------------------------------------------


class TestSectorLandscape:
    @pytest.mark.asyncio
    async def test_returns_stats_envelope(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert "data" in resp
        assert "summary" in resp
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_data_has_required_keys(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        data = resp["data"]
        assert "sector" in data
        assert "total_investors" in data
        assert "by_investor_type" in data
        assert "by_country_top10" in data
        assert "check_size_histogram" in data
        assert "top_firms_by_contacts" in data

    @pytest.mark.asyncio
    async def test_sector_echo_in_response(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="fintech")
        assert resp["data"]["sector"] == "fintech"

    @pytest.mark.asyncio
    async def test_by_investor_type_counts(self) -> None:
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        by_type = resp["data"]["by_investor_type"]
        # 3 VCs in _ROWS
        assert by_type.get("Venture Capital") == 3
        assert by_type.get("PE/Buyout") == 1
        assert by_type.get("Family Office") == 1

    @pytest.mark.asyncio
    async def test_by_country_top10_capped(self) -> None:
        # Build 12 rows with 12 different countries to verify top-10 cap
        rows = [
            {**_BASE_ROW, "id": i, "hq_country_generated": f"Country{i}", "check_size_min": 5.0}
            for i in range(12)
        ]
        client = _make_mock_client(rows=rows, total=12)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert len(resp["data"]["by_country_top10"]) == 10

    @pytest.mark.asyncio
    async def test_check_size_histogram_keys_are_all_buckets(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        histogram = resp["data"]["check_size_histogram"]
        expected_keys = {"<$1M", "$1–5M", "$5–25M", "$25–100M", "$100M–$1B", "$1B+"}
        assert set(histogram.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_check_size_histogram_counts_correct(self) -> None:
        # _ROWS: 0.5(<$1M), 2.0($1-5M), 5.0($5-25M), 15.0($5-25M), 50.0($25-100M),
        #        500.0($100M-$1B), 1500.0($1B+), None(skipped)
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        h = resp["data"]["check_size_histogram"]
        assert h["<$1M"] == 1
        assert h["$1–5M"] == 1
        assert h["$5–25M"] == 2
        assert h["$25–100M"] == 1
        assert h["$100M–$1B"] == 1
        assert h["$1B+"] == 1

    @pytest.mark.asyncio
    async def test_top_firms_by_contacts_capped_at_10(self) -> None:
        rows = [
            {**_BASE_ROW, "id": i, "investors": f"Firm{i}", "contact_count": i + 1}
            for i in range(15)
        ]
        client = _make_mock_client(rows=rows, total=15)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert len(resp["data"]["top_firms_by_contacts"]) == 10

    @pytest.mark.asyncio
    async def test_top_firms_excludes_zero_contact_firms(self) -> None:
        rows = [
            {**_BASE_ROW, "id": 1, "investors": "HasContacts", "contact_count": 5},
            {**_BASE_ROW, "id": 2, "investors": "NoContacts", "contact_count": 0},
            {**_BASE_ROW, "id": 3, "investors": "NullContacts", "contact_count": None},
        ]
        client = _make_mock_client(rows=rows, total=3)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        firm_names = [f["investors"] for f in resp["data"]["top_firms_by_contacts"]]
        assert "HasContacts" in firm_names
        assert "NoContacts" not in firm_names
        assert "NullContacts" not in firm_names

    @pytest.mark.asyncio
    async def test_empty_result_set(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert "error" not in resp
        assert resp["data"]["total_investors"] == 0
        assert resp["data"]["by_investor_type"] == {}
        assert resp["data"]["by_country_top10"] == {}
        assert resp["data"]["top_firms_by_contacts"] == []

    @pytest.mark.asyncio
    async def test_invalid_sector_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        # Pass something that cannot possibly resolve
        resp = await _call_tool(tools, "io_sector_landscape", sector="zzznomatchsector12345xyz")
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad token"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad filter"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert resp["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("timeout"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="energy")
        assert resp["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_summary_contains_sector_name(self) -> None:
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_sector_landscape", sector="fintech")
        assert "fintech" in resp["summary"]


# ---------------------------------------------------------------------------
# Tests: io_check_size_distribution (Tool 27)
# ---------------------------------------------------------------------------


class TestCheckSizeDistribution:
    @pytest.mark.asyncio
    async def test_returns_stats_envelope(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert "data" in resp
        assert "summary" in resp
        assert "error" not in resp

    @pytest.mark.asyncio
    async def test_data_has_required_keys(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        data = resp["data"]
        assert "sector" in data
        assert "total_investors" in data
        assert "with_check_size_data" in data
        assert "without_check_size_data" in data
        assert "histogram" in data
        assert "percentiles_millions" in data

    @pytest.mark.asyncio
    async def test_histogram_has_all_bucket_keys(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        keys = set(resp["data"]["histogram"].keys())
        assert keys == {"<$1M", "$1–5M", "$5–25M", "$25–100M", "$100M–$1B", "$1B+"}

    @pytest.mark.asyncio
    async def test_with_and_without_data_counts(self) -> None:
        # _ROWS has 7 rows with check_size_min and 1 row with None
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        data = resp["data"]
        assert data["with_check_size_data"] == 7
        assert data["without_check_size_data"] == 1
        assert data["with_check_size_data"] + data["without_check_size_data"] == len(_ROWS)

    @pytest.mark.asyncio
    async def test_histogram_counts_correct(self) -> None:
        # 0.5(<$1M), 2.0($1-5M), 5.0($5-25M), 15.0($5-25M), 50.0($25-100M),
        # 500.0($100M-$1B), 1500.0($1B+), None(skipped)
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        h = resp["data"]["histogram"]
        assert h["<$1M"] == 1
        assert h["$1–5M"] == 1
        assert h["$5–25M"] == 2
        assert h["$25–100M"] == 1
        assert h["$100M–$1B"] == 1
        assert h["$1B+"] == 1

    @pytest.mark.asyncio
    async def test_percentiles_present(self) -> None:
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        p = resp["data"]["percentiles_millions"]
        assert "p25" in p
        assert "p50" in p
        assert "p75" in p
        # All should be floats (not None) since we have 7 data points
        assert p["p50"] is not None

    @pytest.mark.asyncio
    async def test_percentiles_none_when_no_data(self) -> None:
        rows = [
            {**_BASE_ROW, "id": i, "check_size_min": None}
            for i in range(3)
        ]
        client = _make_mock_client(rows=rows, total=3)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        p = resp["data"]["percentiles_millions"]
        assert p["p25"] is None
        assert p["p50"] is None
        assert p["p75"] is None

    @pytest.mark.asyncio
    async def test_investor_type_filter_accepted(self) -> None:
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_check_size_distribution",
            sector="energy", investor_type="venture capital"
        )
        assert "error" not in resp
        # resolved_investor_types should be populated
        assert resp["data"]["resolved_investor_types"] is not None

    @pytest.mark.asyncio
    async def test_investor_type_echoed_in_response(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_check_size_distribution",
            sector="energy", investor_type="family office"
        )
        assert resp["data"]["investor_type"] == "family office"

    @pytest.mark.asyncio
    async def test_no_investor_type_leaves_resolved_types_none(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert resp["data"]["resolved_investor_types"] is None

    @pytest.mark.asyncio
    async def test_invalid_sector_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_check_size_distribution", sector="zzznomatchsector12345xyz"
        )
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_invalid_investor_type_returns_validation_error(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools, "io_check_size_distribution",
            sector="energy", investor_type="zzznomatchtype12345xyz"
        )
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad token"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad query"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert resp["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("timeout"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert resp["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_empty_result_set(self) -> None:
        client = _make_mock_client(rows=[], total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="energy")
        assert "error" not in resp
        assert resp["data"]["total_investors"] == 0
        assert resp["data"]["with_check_size_data"] == 0
        p = resp["data"]["percentiles_millions"]
        assert p["p50"] is None

    @pytest.mark.asyncio
    async def test_summary_contains_sector_name(self) -> None:
        client = _make_mock_client(rows=_ROWS, total=len(_ROWS))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_check_size_distribution", sector="cleantech")
        assert "cleantech" in resp["summary"]
