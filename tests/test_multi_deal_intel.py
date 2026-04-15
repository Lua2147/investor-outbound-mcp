"""Tests for src/tools/multi_deal_intel.py — Tools 22–25.

Coverage:
    io_find_cross_deal_investors  — overlap detection, min_deal_matches gate, labelling,
                                    validation errors, error propagation, empty results
    io_deal_coverage_gaps         — gap lists populated, covered lists populated, error paths
    io_investor_funnel            — single step, multi-step accumulation, narrowing delta,
                                    list-field extension, validation error, error propagation
    io_deduplicate_across_deals   — shared persons found, unique-only case, label requirement,
                                    person_ids type coercion, dedup_rate calculation,
                                    validation errors, no network calls made

Mock strategy:
    All tests mock IOClient.query directly (no live Supabase calls).
    io_deduplicate_across_deals has NO network calls — those tests use a real
    (non-mocked) client spec just to satisfy the function signature.

Run with:
    pytest tests/test_multi_deal_intel.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from src.client import IOAuthError, IOClient, IOQueryError, IOTransientError
from src.tools.multi_deal_intel import register

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

INVESTOR_PE: dict[str, Any] = {
    "id": 101,
    "investors": "Blackstone",
    "primary_investor_type": "PE/Buyout",
    "types_array": ["PE/Buyout"],
    "sectors_array": ["energy", "industrials"],
    "capital_under_management": "$100B",
    "check_size_min": 50.0,
    "check_size_max": 2000.0,
    "hq_location": "New York, NY",
    "hq_country_generated": "United States",
    "investor_website": "https://blackstone.com",
    "contact_count": 25,
    "has_contact_emails": True,
    "investor_status": "Actively Seeking New Investments",
    "preferred_investment_types": "Buyout/LBO",
    "preferred_industry": "Energy, Industrials",
    "preferred_geography": "North America",
    "completeness_score": 0.95,
}

INVESTOR_VC: dict[str, Any] = {
    "id": 202,
    "investors": "Andreessen Horowitz",
    "primary_investor_type": "Venture Capital",
    "types_array": ["Venture Capital"],
    "sectors_array": ["technology", "software_saas", "ai_ml"],
    "capital_under_management": "$35B",
    "check_size_min": 5.0,
    "check_size_max": 500.0,
    "hq_location": "Menlo Park, CA",
    "hq_country_generated": "United States",
    "investor_website": "https://a16z.com",
    "contact_count": 15,
    "has_contact_emails": True,
    "investor_status": "Actively Seeking New Investments",
    "preferred_investment_types": "Seed Round, Early Stage VC",
    "preferred_industry": "Technology, Software",
    "preferred_geography": "United States",
    "completeness_score": 0.92,
}

INVESTOR_FAMILY: dict[str, Any] = {
    "id": 303,
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
}


def _make_mock_client(
    query_rows: list[dict] | None = None,
    query_total: int | None = 100,
    raise_exc: Exception | None = None,
) -> IOClient:
    """Return an IOClient mock with a pre-wired query() method."""
    client = MagicMock(spec=IOClient)
    if raise_exc is not None:
        client.query = AsyncMock(side_effect=raise_exc)
    else:
        rows = query_rows if query_rows is not None else [INVESTOR_PE, INVESTOR_VC]
        client.query = AsyncMock(return_value=(rows, query_total))
    return client


def _make_mcp_and_register(client: IOClient) -> tuple[FastMCP, dict]:
    mcp = FastMCP(name="test-server")
    register(mcp, client)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    return mcp, tools


async def _call_tool(tools: dict, tool_name: str, **kwargs: Any) -> dict:
    fn = tools[tool_name].fn
    result = await fn(**kwargs)
    return json.loads(result)


# ---------------------------------------------------------------------------
# Tests: io_find_cross_deal_investors (Tool 22)
# ---------------------------------------------------------------------------


class TestFindCrossDealInvestors:
    @pytest.mark.asyncio
    async def test_returns_investors_appearing_in_both_deals(self) -> None:
        """Investor 101 appears in both deal result sets — should be returned."""

        # Deal 1 returns [PE, VC], Deal 2 returns [PE, Family]
        # PE (id=101) is the overlap.
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [INVESTOR_PE, INVESTOR_VC], 2
            return [INVESTOR_PE, INVESTOR_FAMILY], 2

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[
                {"sectors": ["energy"], "label": "deal_energy"},
                {"sectors": ["real estate"], "label": "deal_realestate"},
            ],
        )

        assert "data" in resp
        assert len(resp["data"]) == 1
        inv = resp["data"][0]
        assert inv["id"] == 101
        assert inv["deal_match_count"] == 2
        assert "deal_energy" in inv["matched_deals"]
        assert "deal_realestate" in inv["matched_deals"]

    @pytest.mark.asyncio
    async def test_min_deal_matches_gate(self) -> None:
        """Investor appearing in only 1 deal is excluded when min_deal_matches=2."""
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [INVESTOR_PE], 1
            return [INVESTOR_VC], 1  # different investor — no overlap

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[
                {"sectors": ["energy"]},
                {"sectors": ["technology"]},
            ],
            min_deal_matches=2,
        )

        assert resp["data"] == []

    @pytest.mark.asyncio
    async def test_labels_default_to_deal_n(self) -> None:
        """When label is omitted, deals are labelled deal_1, deal_2, etc."""
        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            return [INVESTOR_PE], 1

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[
                {"sectors": ["energy"]},
                {"sectors": ["energy"]},
            ],
        )
        assert "data" in resp
        inv = resp["data"][0]
        assert "deal_1" in inv["matched_deals"]
        assert "deal_2" in inv["matched_deals"]

    @pytest.mark.asyncio
    async def test_validation_error_fewer_than_2_deals(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[{"sectors": ["energy"]}],
        )
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_error_empty_deals_list(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[],
        )
        assert "error" in resp
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad token"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[{"sectors": ["energy"]}, {"sectors": ["technology"]}],
        )
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad filter"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[{"sectors": ["energy"]}, {"sectors": ["technology"]}],
        )
        assert resp["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("timeout"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[{"sectors": ["energy"]}, {"sectors": ["technology"]}],
        )
        assert resp["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_results_sorted_by_deal_count_desc(self) -> None:
        """Investor appearing in 3 deals must rank above one in 2 deals."""
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [INVESTOR_PE, INVESTOR_VC], 2
            elif call_count == 2:
                return [INVESTOR_PE, INVESTOR_VC], 2
            else:
                return [INVESTOR_PE], 1  # PE in all 3

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[
                {"sectors": ["energy"], "label": "d1"},
                {"sectors": ["energy"], "label": "d2"},
                {"sectors": ["energy"], "label": "d3"},
            ],
            min_deal_matches=2,
        )

        assert resp["data"][0]["id"] == INVESTOR_PE["id"]
        assert resp["data"][0]["deal_match_count"] == 3

    @pytest.mark.asyncio
    async def test_summary_mentions_deal_labels(self) -> None:
        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            return [INVESTOR_PE], 1

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_find_cross_deal_investors",
            deals=[
                {"label": "alpha_deal", "sectors": ["energy"]},
                {"label": "beta_deal", "sectors": ["energy"]},
            ],
        )
        assert "alpha_deal" in resp["summary"]
        assert "beta_deal" in resp["summary"]


# ---------------------------------------------------------------------------
# Tests: io_deal_coverage_gaps (Tool 23)
# ---------------------------------------------------------------------------


class TestDealCoverageGaps:
    @pytest.mark.asyncio
    async def test_returns_gap_and_covered_lists(self) -> None:
        """Some probes return 0, others return counts — both appear in output."""
        probe_call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            nonlocal probe_call_count
            probe_call_count += 1
            # Alternate: even probes return 0, odd probes return 50
            count = 0 if probe_call_count % 2 == 0 else 50
            return [], count

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deal_coverage_gaps",
            sectors=["energy"],
        )

        assert "data" in resp
        data = resp["data"]
        assert "zero_investor_types" in data
        assert "covered_investor_types" in data
        assert "zero_geographies" in data
        assert "covered_geographies" in data
        assert "zero_sectors" in data
        assert "covered_sectors" in data

    @pytest.mark.asyncio
    async def test_all_zero_when_db_returns_empty(self) -> None:
        """When every probe returns 0, all lists end up in zero_* keys."""
        client = _make_mock_client(query_rows=[], query_total=0)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deal_coverage_gaps",
            sectors=["energy"],
        )

        data = resp["data"]
        assert len(data["covered_investor_types"]) == 0
        assert len(data["covered_geographies"]) == 0
        assert len(data["covered_sectors"]) == 0
        assert len(data["zero_investor_types"]) > 0
        assert len(data["zero_geographies"]) > 0
        assert len(data["zero_sectors"]) > 0

    @pytest.mark.asyncio
    async def test_covered_entries_include_count(self) -> None:
        """Covered entries must include the investor count."""
        client = _make_mock_client(query_rows=[], query_total=42)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deal_coverage_gaps",
        )

        data = resp["data"]
        for entry in data["covered_investor_types"]:
            assert "investor_type" in entry
            assert "count" in entry
            assert entry["count"] == 42

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad token"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_deal_coverage_gaps")
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOTransientError("timeout"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_deal_coverage_gaps")
        assert resp["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_summary_contains_counts(self) -> None:
        client = _make_mock_client(query_rows=[], query_total=0)
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_deal_coverage_gaps")
        assert "gaps" in resp["summary"].lower()


# ---------------------------------------------------------------------------
# Tests: io_investor_funnel (Tool 24)
# ---------------------------------------------------------------------------


class TestInvestorFunnel:
    @pytest.mark.asyncio
    async def test_single_step_returns_one_entry(self) -> None:
        client = _make_mock_client(query_rows=[], query_total=500)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[{"sectors": ["energy"], "label": "broad energy"}],
        )

        assert "data" in resp
        steps = resp["data"]["steps"]
        assert len(steps) == 1
        assert steps[0]["step"] == 1
        assert steps[0]["label"] == "broad energy"
        assert steps[0]["count"] == 500

    @pytest.mark.asyncio
    async def test_multi_step_accumulates_correctly(self) -> None:
        """Each step should show narrowing counts."""
        counts = [1000, 400, 100]
        call_index = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            nonlocal call_index
            count = counts[call_index] if call_index < len(counts) else 50
            call_index += 1
            return [], count

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[
                {"sectors": ["energy"], "label": "step1"},
                {"investor_types": ["PE/Buyout"], "label": "step2"},
                {"check_size_min_dollars": 50_000_000, "label": "step3"},
            ],
        )

        data = resp["data"]
        steps = data["steps"]
        assert len(steps) == 3
        assert steps[0]["count"] == 1000
        assert steps[1]["count"] == 400
        assert steps[2]["count"] == 100
        assert data["total_narrowing"] == 900

    @pytest.mark.asyncio
    async def test_list_filters_accumulate_across_steps(self) -> None:
        """Adding sectors in step 2 should extend the step 1 sectors list."""
        captured_params: list[Any] = []

        async def _side_effect(*args: Any, **kwargs: Any) -> tuple[list[dict], int]:
            # Capture what was passed to query — we inspect cumulative_filters in output
            return [], 100

        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=_side_effect)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[
                {"sectors": ["energy"], "label": "s1"},
                {"sectors": ["technology"], "label": "s2"},
            ],
        )

        steps = resp["data"]["steps"]
        # Step 2's cumulative_filters should have both sectors
        step2_filters = steps[1]["cumulative_filters"]
        assert "technology" in step2_filters.get("sectors", [])
        assert "energy" in step2_filters.get("sectors", [])

    @pytest.mark.asyncio
    async def test_step_labels_default_to_step_n(self) -> None:
        client = _make_mock_client(query_rows=[], query_total=100)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[{"sectors": ["energy"]}, {"investor_types": ["vc"]}],
        )

        steps = resp["data"]["steps"]
        assert steps[0]["label"] == "step_1"
        assert steps[1]["label"] == "step_2"

    @pytest.mark.asyncio
    async def test_validation_error_empty_filters(self) -> None:
        client = _make_mock_client()
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(tools, "io_investor_funnel", filters=[])
        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOAuthError("bad token"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[{"sectors": ["energy"]}],
        )
        assert resp["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_propagates(self) -> None:
        client = _make_mock_client(raise_exc=IOQueryError("bad col"))
        _, tools = _make_mcp_and_register(client)
        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[{"sectors": ["energy"]}],
        )
        assert resp["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_total_narrowing_none_when_count_unavailable(self) -> None:
        """When PostgREST returns no Content-Range, total_narrowing must be None."""
        client = _make_mock_client(query_rows=[], query_total=None)
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_investor_funnel",
            filters=[{"sectors": ["energy"]}, {"investor_types": ["vc"]}],
        )

        assert resp["data"]["total_narrowing"] is None


# ---------------------------------------------------------------------------
# Tests: io_deduplicate_across_deals (Tool 25)
# ---------------------------------------------------------------------------


class TestDeduplicateAcrossDeals:
    """io_deduplicate_across_deals makes NO network calls."""

    def _make_noop_client(self) -> IOClient:
        """Client that raises if any method is called (should never happen)."""
        client = MagicMock(spec=IOClient)
        client.query = AsyncMock(side_effect=AssertionError("query should not be called"))
        return client

    @pytest.mark.asyncio
    async def test_finds_shared_person(self) -> None:
        """Person 999 appears in both deal lists — should be in duplicates."""
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "deal_alpha", "person_ids": [999, 1, 2]},
                {"label": "deal_beta", "person_ids": [999, 3, 4]},
            ],
        )

        assert "data" in resp
        data = resp["data"]
        assert len(data["duplicates"]) == 1
        dup = data["duplicates"][0]
        assert dup["person_id"] == 999
        assert dup["deal_count"] == 2
        assert "deal_alpha" in dup["deal_labels"]
        assert "deal_beta" in dup["deal_labels"]

    @pytest.mark.asyncio
    async def test_no_duplicates_when_lists_disjoint(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "deal_a", "person_ids": [1, 2, 3]},
                {"label": "deal_b", "person_ids": [4, 5, 6]},
            ],
        )

        data = resp["data"]
        assert data["duplicates"] == []
        assert data["total_unique_persons"] == 6
        assert data["total_input_persons"] == 6
        assert data["deduplication_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_person_in_3_deals_flagged_correctly(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": [42]},
                {"label": "d2", "person_ids": [42]},
                {"label": "d3", "person_ids": [42]},
            ],
        )

        data = resp["data"]
        assert len(data["duplicates"]) == 1
        assert data["duplicates"][0]["deal_count"] == 3
        assert data["duplicates"][0]["deal_labels"] == ["d1", "d2", "d3"]

    @pytest.mark.asyncio
    async def test_total_unique_vs_input_counts(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": [1, 2, 3]},
                {"label": "d2", "person_ids": [2, 3, 4]},
            ],
        )

        data = resp["data"]
        assert data["total_input_persons"] == 6  # 3 + 3
        assert data["total_unique_persons"] == 4  # 1, 2, 3, 4
        assert len(data["duplicates"]) == 2  # person 2 and 3

    @pytest.mark.asyncio
    async def test_dedup_rate_calculation(self) -> None:
        """deduplication_rate = duplicates / total_input."""
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": [1, 2]},
                {"label": "d2", "person_ids": [1, 3]},
            ],
        )

        data = resp["data"]
        # 1 duplicate out of 4 total entries
        assert data["deduplication_rate"] == 0.25

    @pytest.mark.asyncio
    async def test_validation_error_fewer_than_2_lists(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[{"label": "only_one", "person_ids": [1, 2]}],
        )

        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_error_missing_label(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "valid_deal", "person_ids": [1]},
                {"person_ids": [2]},  # missing label
            ],
        )

        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_error_person_ids_not_a_list(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": "not_a_list"},
                {"label": "d2", "person_ids": [1, 2]},
            ],
        )

        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_error_empty_list(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[],
        )

        assert resp["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_duplicates_sorted_by_deal_count_desc(self) -> None:
        """Person appearing in 3 deals must rank above person in 2 deals."""
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": [7, 8]},
                {"label": "d2", "person_ids": [7, 8]},
                {"label": "d3", "person_ids": [7]},  # 7 appears in 3, 8 in 2
            ],
        )

        data = resp["data"]
        dupes = data["duplicates"]
        assert dupes[0]["person_id"] == 7
        assert dupes[0]["deal_count"] == 3

    @pytest.mark.asyncio
    async def test_no_network_calls_made(self) -> None:
        """Verify the tool makes zero network calls (no query invocations)."""
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        # This would raise AssertionError if any network call happened
        await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "d1", "person_ids": [1, 2, 3]},
                {"label": "d2", "person_ids": [2, 3, 4]},
            ],
        )

        client.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_mentions_deal_labels(self) -> None:
        client = self._make_noop_client()
        _, tools = _make_mcp_and_register(client)

        resp = await _call_tool(
            tools,
            "io_deduplicate_across_deals",
            deal_person_lists=[
                {"label": "ProjectAlpha", "person_ids": [1]},
                {"label": "ProjectBeta", "person_ids": [1]},
            ],
        )

        assert "ProjectAlpha" in resp["summary"]
        assert "ProjectBeta" in resp["summary"]
