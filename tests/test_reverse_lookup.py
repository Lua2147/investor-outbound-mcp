"""Tests for src/tools/reverse_lookup.py — Tools 13–17.

All tests mock IOClient. No live Supabase calls.

Run with:
    pytest tests/test_reverse_lookup.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.reverse_lookup import _chunk, _top_contacts_for_investor, register


# ---------------------------------------------------------------------------
# Fixtures — sample data
# ---------------------------------------------------------------------------

PERSON_ROW_SUMMARY = {
    "id": 101,
    "first_name": "Alice",
    "last_name": "Walker",
    "email": "alice@kaynecapital.com",
    "phone": "+1-310-555-0100",
    "role": "Managing Director",
    "company_name": "Kayne Anderson Capital Advisors",
    "linkedin_profile_url": "https://www.linkedin.com/in/alice-walker-xyz/",
    "location": "Los Angeles, CA",
    "investor": 2001,
}

PERSON_ROW_DETAIL = {
    **PERSON_ROW_SUMMARY,
    "pb_person_url": None,
    "pb_person_id": None,
    "pb_company_url": None,
    "pb_company_id": None,
    "description": None,
    "completeness_score": 0.85,
    "created_at": "2025-01-01T00:00:00",
    "email_status": "deliverable",
    "email_accept_all": "no",
    "email_domain": "kaynecapital.com",
    "email_disposable": False,
    "email_free": False,
    "email_provider": None,
    "email_score": 90,
    "email_toxicity": 0.01,
    "good_email": True,
    "last_bounce_type": None,
    "last_bounce_at": None,
    "company_country": "United States",
    "company_founded": 1984,
    "company_linkedin": "https://www.linkedin.com/company/kayne-anderson/",
    "company_size": "201-500",
    "company_industry": "financial services",
    "domain": "kaynecapital.com",
}

PERSON_ROW_2 = {
    "id": 102,
    "first_name": "Bob",
    "last_name": "Chen",
    "email": "bob@kaynecapital.com",
    "phone": None,
    "role": "Vice President, Investments",
    "company_name": "Kayne Anderson Capital Advisors",
    "linkedin_profile_url": "https://www.linkedin.com/in/bob-chen-abc/",
    "location": "Los Angeles, CA",
    "investor": 2001,
}

INVESTOR_ROW = {
    "id": 2001,
    "investors": "Kayne Anderson Capital Advisors",
    "primary_investor_type": "Asset Manager",
    "types_array": ["Asset Manager"],
    "sectors_array": ["energy", "real_estate"],
    "capital_under_management": "$10B",
    "check_size_min": 10.0,
    "check_size_max": 200.0,
    "hq_location": "Los Angeles, CA",
    "hq_country_generated": "United States",
    "investor_website": "https://www.kaynecapital.com",
    "contact_count": 15,
    "has_contact_emails": True,
    "investor_status": "Actively Seeking New Investments",
    "preferred_investment_types": "Growth/Expansion, Buyout/LBO",
    "preferred_industry": "Energy, Real Estate",
    "preferred_geography": "United States",
    "completeness_score": 0.92,
}

INVESTOR_ROW_2 = {
    "id": 2002,
    "investors": "KKR & Co.",
    "primary_investor_type": "PE/Buyout",
    "types_array": ["PE/Buyout"],
    "sectors_array": ["industrials", "technology"],
    "capital_under_management": "$500B",
    "check_size_min": 100.0,
    "check_size_max": 5000.0,
    "hq_location": "New York, NY",
    "hq_country_generated": "United States",
    "investor_website": "https://www.kkr.com",
    "contact_count": 80,
    "has_contact_emails": True,
    "investor_status": "Actively Seeking New Investments",
    "preferred_investment_types": "Buyout/LBO",
    "preferred_industry": "Industrials, Technology",
    "preferred_geography": "Global",
    "completeness_score": 0.97,
}


def _make_mock_client(
    query_side_effect: Any = None,
    query_return: Any = None,
    investor_by_id_return: dict | None = None,
) -> MagicMock:
    """Build a mock IOClient.

    Args:
        query_side_effect: If given, client.query raises this exception.
        query_return: Default (rows, total) tuple returned by client.query.
        investor_by_id_return: Dict returned by client.get_investor_by_id.
    """
    client = MagicMock()
    if query_side_effect:
        client.query = AsyncMock(side_effect=query_side_effect)
    else:
        default = query_return if query_return is not None else ([], None)
        client.query = AsyncMock(return_value=default)
    client.get_investor_by_id = AsyncMock(return_value=investor_by_id_return)
    return client


def _make_mcp() -> MagicMock:
    """Build a mock FastMCP that captures tool registrations."""
    mcp = MagicMock()
    # tool() is a decorator — return identity so the function stays callable
    mcp.tool = MagicMock(return_value=lambda fn: fn)
    return mcp


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestChunk:
    def test_even_split(self) -> None:
        assert _chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]

    def test_odd_split(self) -> None:
        assert _chunk([1, 2, 3], 2) == [[1, 2], [3]]

    def test_single_chunk(self) -> None:
        assert _chunk([1, 2, 3], 10) == [[1, 2, 3]]

    def test_empty_list(self) -> None:
        assert _chunk([], 5) == []

    def test_chunk_size_one(self) -> None:
        assert _chunk([1, 2, 3], 1) == [[1], [2], [3]]


class TestTopContactsForInvestor:
    def test_returns_sorted_by_score_desc(self) -> None:
        rows = [
            {"role": "Analyst", "id": 1},
            {"role": "Managing Partner", "id": 2},
            {"role": "Vice President", "id": 3},
        ]
        result = _top_contacts_for_investor(rows, cap=3)
        # Managing Partner should score highest
        assert result[0]["id"] == 2

    def test_cap_respected(self) -> None:
        rows = [{"role": "Partner", "id": i} for i in range(10)]
        result = _top_contacts_for_investor(rows, cap=3)
        assert len(result) == 3

    def test_empty_input(self) -> None:
        assert _top_contacts_for_investor([], cap=5) == []

    def test_handles_missing_role(self) -> None:
        rows = [{"id": 1}, {"role": None, "id": 2}, {"role": "CEO", "id": 3}]
        result = _top_contacts_for_investor(rows, cap=10)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Integration-style tests using mock client + register()
# ---------------------------------------------------------------------------
#
# Pattern: register tools onto a mock MCP to capture the decorated functions,
# then call them directly to test end-to-end behaviour.


def _extract_tool(mcp: MagicMock, name: str):
    """Return the tool function captured by mcp.tool() decorator."""
    for call in mcp.tool.call_args_list:
        # mcp.tool() is called with no args — we need the wrapped function.
        # The returned lambda is called with the function as argument.
        pass
    # Since mcp.tool returns identity lambda, the registered function is the
    # original async def. We need to collect them during registration.
    # Use a capturing decorator instead — patch via a wrapper approach below.
    return None


class _CapturingMCP:
    """Minimal MCP stub that stores registered tools by name."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):
        """Decorator factory that stores the function under its __name__."""
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


# ---------------------------------------------------------------------------
# Tool 13: lookup_by_email_domain
# ---------------------------------------------------------------------------


class TestLookupByEmailDomain:
    def _setup(self, query_return=None, investor_return=None):
        mcp = _CapturingMCP()
        client = _make_mock_client(
            query_return=query_return or ([], None),
            investor_by_id_return=investor_return,
        )
        register(mcp, client)
        return mcp.tools["lookup_by_email_domain"], client

    @pytest.mark.asyncio
    async def test_returns_persons_for_domain(self) -> None:
        fn, client = self._setup(
            query_return=([PERSON_ROW_SUMMARY, PERSON_ROW_2], 2),
            investor_return=INVESTOR_ROW,
        )
        result = json.loads(await fn("kaynecapital.com"))
        assert "data" in result
        assert len(result["data"]) == 2
        assert result["data"][0]["investor_name"] == "Kayne Anderson Capital Advisors"

    @pytest.mark.asyncio
    async def test_strips_leading_at_sign(self) -> None:
        fn, client = self._setup(query_return=([], None))
        await fn("@kaynecapital.com")
        # Should have queried with domain "kaynecapital.com" (no @)
        call_args = client.query.call_args
        builder = call_args[0][0]
        params = dict(builder.build())
        assert params.get("domain") == "eq.kaynecapital.com"

    @pytest.mark.asyncio
    async def test_empty_domain_returns_validation_error(self) -> None:
        fn, _ = self._setup()
        result = json.loads(await fn(""))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_no_persons_returns_empty_data_with_suggestion(self) -> None:
        fn, _ = self._setup(query_return=([], None))
        result = json.loads(await fn("unknowndomain.io"))
        assert result["data"] == []
        assert "next_actions" in result

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self) -> None:
        from src.client import IOAuthError
        mcp = _CapturingMCP()
        client = _make_mock_client(query_side_effect=IOAuthError())
        register(mcp, client)
        result = json.loads(await mcp.tools["lookup_by_email_domain"]("kaynecapital.com"))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self) -> None:
        from src.client import IOTransientError
        mcp = _CapturingMCP()
        client = _make_mock_client(query_side_effect=IOTransientError("timeout"))
        register(mcp, client)
        result = json.loads(await mcp.tools["lookup_by_email_domain"]("kaynecapital.com"))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_multiple_investors_enriched(self) -> None:
        """Persons from two different investors both get their investor name."""
        person_other_investor = {**PERSON_ROW_2, "investor": 2002}
        investor_2 = INVESTOR_ROW_2

        async def mock_get_investor_by_id(inv_id: int):
            if inv_id == 2001:
                return INVESTOR_ROW
            if inv_id == 2002:
                return investor_2
            return None

        mcp = _CapturingMCP()
        client = _make_mock_client(query_return=([PERSON_ROW_SUMMARY, person_other_investor], 2))
        client.get_investor_by_id = AsyncMock(side_effect=mock_get_investor_by_id)
        register(mcp, client)

        result = json.loads(await mcp.tools["lookup_by_email_domain"]("example.com"))
        names = {r["investor_name"] for r in result["data"]}
        assert "Kayne Anderson Capital Advisors" in names
        assert "KKR & Co." in names


# ---------------------------------------------------------------------------
# Tool 14: lookup_by_linkedin
# ---------------------------------------------------------------------------


class TestLookupByLinkedin:
    def _setup(self, query_return=None, query_side_effect=None):
        mcp = _CapturingMCP()
        client = _make_mock_client(
            query_return=query_return,
            query_side_effect=query_side_effect,
        )
        register(mcp, client)
        return mcp.tools["lookup_by_linkedin"], client

    @pytest.mark.asyncio
    async def test_found_person_returns_detail(self) -> None:
        fn, _ = self._setup(query_return=([PERSON_ROW_DETAIL], None))
        result = json.loads(await fn("https://www.linkedin.com/in/alice-walker-xyz/"))
        assert result["data"]["email"] == "alice@kaynecapital.com"
        assert result["data"]["good_email"] is True

    @pytest.mark.asyncio
    async def test_not_found_returns_not_found_error(self) -> None:
        fn, _ = self._setup(query_return=([], None))
        result = json.loads(await fn("https://www.linkedin.com/in/ghost/"))
        assert result["error"]["code"] == "NOT_FOUND"
        assert "details" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_url_returns_validation_error(self) -> None:
        fn, _ = self._setup()
        result = json.loads(await fn(""))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_whitespace_stripped_from_url(self) -> None:
        fn, client = self._setup(query_return=([PERSON_ROW_DETAIL], None))
        await fn("  https://www.linkedin.com/in/alice-walker-xyz/  ")
        call_args = client.query.call_args[0][0]
        params = dict(call_args.build())
        assert params["linkedin_profile_url"] == "eq.https://www.linkedin.com/in/alice-walker-xyz/"

    @pytest.mark.asyncio
    async def test_summary_contains_name_and_role(self) -> None:
        fn, _ = self._setup(query_return=([PERSON_ROW_DETAIL], None))
        result = json.loads(await fn("https://www.linkedin.com/in/alice-walker-xyz/"))
        assert "Alice" in result["summary"]
        assert "Managing Director" in result["summary"]

    @pytest.mark.asyncio
    async def test_auth_error_propagates(self) -> None:
        from src.client import IOAuthError
        fn, _ = self._setup(query_side_effect=IOAuthError())
        result = json.loads(await fn("https://www.linkedin.com/in/alice/"))
        assert result["error"]["code"] == "AUTH_FAILED"


# ---------------------------------------------------------------------------
# Tool 15: reverse_company_lookup
# ---------------------------------------------------------------------------


class TestReverseCompanyLookup:
    def _setup(self, query_return=None, investor_return=None, query_side_effect=None):
        mcp = _CapturingMCP()
        client = _make_mock_client(
            query_return=query_return or ([], None),
            investor_by_id_return=investor_return,
            query_side_effect=query_side_effect,
        )
        register(mcp, client)
        return mcp.tools["reverse_company_lookup"], client

    @pytest.mark.asyncio
    async def test_groups_by_investor(self) -> None:
        person_1 = {**PERSON_ROW_SUMMARY, "investor": 2001}
        person_2 = {**PERSON_ROW_2, "investor": 2001}
        fn, _ = self._setup(
            query_return=([person_1, person_2], 2),
            investor_return=INVESTOR_ROW,
        )
        result = json.loads(await fn("Kayne"))
        groups = result["data"]
        assert len(groups) == 1
        assert groups[0]["investor_id"] == 2001
        assert groups[0]["contact_count"] == 2
        assert len(groups[0]["persons"]) == 2

    @pytest.mark.asyncio
    async def test_groups_sorted_largest_first(self) -> None:
        persons = [
            {**PERSON_ROW_SUMMARY, "investor": 2001},
            {**PERSON_ROW_2, "investor": 2001},
            {**PERSON_ROW_SUMMARY, "id": 200, "investor": 2002},
        ]

        async def mock_get_investor(inv_id: int):
            if inv_id == 2001:
                return INVESTOR_ROW
            return INVESTOR_ROW_2

        mcp = _CapturingMCP()
        client = _make_mock_client(query_return=(persons, 3))
        client.get_investor_by_id = AsyncMock(side_effect=mock_get_investor)
        register(mcp, client)

        result = json.loads(await mcp.tools["reverse_company_lookup"]("Capital"))
        groups = result["data"]
        assert groups[0]["contact_count"] >= groups[1]["contact_count"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self) -> None:
        fn, _ = self._setup(query_return=([], None))
        result = json.loads(await fn("NonExistentFirmXYZ"))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_empty_name_returns_validation_error(self) -> None:
        fn, _ = self._setup()
        result = json.loads(await fn(""))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_summary_contains_counts(self) -> None:
        fn, _ = self._setup(
            query_return=([PERSON_ROW_SUMMARY, PERSON_ROW_2], 2),
            investor_return=INVESTOR_ROW,
        )
        result = json.loads(await fn("Kayne"))
        assert "2" in result["summary"]

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self) -> None:
        from src.client import IOTransientError
        fn, _ = self._setup(query_side_effect=IOTransientError("timeout"))
        result = json.loads(await fn("Kayne"))
        assert result["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tool 16: batch_firm_lookup
# ---------------------------------------------------------------------------


class TestBatchFirmLookup:
    def _setup_multi(self, investor_rows_by_name: dict, contacts_by_id: dict):
        """Build a mock client whose query returns differ by params."""

        async def mock_query(builder, *, count=None):
            params = dict(builder.build())
            if builder.table == "investors":
                # Extract the ilike pattern to find which firm name was queried
                for key, val in params.items():
                    if val.startswith("ilike."):
                        pattern = val[len("ilike.*"):]
                        pattern = pattern.rstrip("*")
                        for name, rows in investor_rows_by_name.items():
                            if name.lower() in pattern.lower():
                                return rows, len(rows)
                return [], 0
            if builder.table == "persons":
                # Get the investor ID from eq.{id}
                inv_id_str = params.get("investor", "eq.0").replace("eq.", "")
                try:
                    inv_id = int(inv_id_str)
                except ValueError:
                    return [], 0
                return contacts_by_id.get(inv_id, []), None
            return [], None

        mcp = _CapturingMCP()
        client = MagicMock()
        client.query = AsyncMock(side_effect=mock_query)
        client.get_investor_by_id = AsyncMock(return_value=INVESTOR_ROW)
        register(mcp, client)
        return mcp.tools["batch_firm_lookup"], client

    @pytest.mark.asyncio
    async def test_single_firm_matched(self) -> None:
        fn, _ = self._setup_multi(
            {"Kayne": [INVESTOR_ROW]},
            {2001: [PERSON_ROW_SUMMARY, PERSON_ROW_2]},
        )
        result = json.loads(await fn(["Kayne Anderson"]))
        assert len(result["data"]) == 1
        entry = result["data"][0]
        assert entry["query"] == "Kayne Anderson"
        assert len(entry["matched_investors"]) >= 1
        assert len(entry["top_contacts"]) >= 1

    @pytest.mark.asyncio
    async def test_unmatched_firm_has_empty_lists(self) -> None:
        fn, _ = self._setup_multi({}, {})
        result = json.loads(await fn(["UnknownFirmXYZ"]))
        entry = result["data"][0]
        assert entry["matched_investors"] == []
        assert entry["top_contacts"] == []

    @pytest.mark.asyncio
    async def test_multiple_firms_returned(self) -> None:
        fn, _ = self._setup_multi(
            {"Kayne": [INVESTOR_ROW], "KKR": [INVESTOR_ROW_2]},
            {2001: [PERSON_ROW_SUMMARY], 2002: [PERSON_ROW_2]},
        )
        result = json.loads(await fn(["Kayne Anderson", "KKR"]))
        assert len(result["data"]) == 2

    @pytest.mark.asyncio
    async def test_empty_list_returns_validation_error(self) -> None:
        mcp = _CapturingMCP()
        client = _make_mock_client()
        register(mcp, client)
        result = json.loads(await mcp.tools["batch_firm_lookup"]([]))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_whitespace_only_names_filtered(self) -> None:
        mcp = _CapturingMCP()
        client = _make_mock_client(query_return=([], None))
        register(mcp, client)
        result = json.loads(await mcp.tools["batch_firm_lookup"](["  ", "", "KKR"]))
        # Only "KKR" should be processed
        assert len(result["data"]) == 1
        assert result["data"][0]["query"] == "KKR"

    @pytest.mark.asyncio
    async def test_contacts_capped_at_5(self) -> None:
        many_persons = [
            {**PERSON_ROW_SUMMARY, "id": i, "role": "Managing Partner"}
            for i in range(20)
        ]
        fn, _ = self._setup_multi(
            {"BigFirm": [INVESTOR_ROW]},
            {2001: many_persons},
        )
        result = json.loads(await fn(["BigFirm"]))
        top = result["data"][0]["top_contacts"]
        assert len(top) <= 5

    @pytest.mark.asyncio
    async def test_summary_contains_match_count(self) -> None:
        fn, _ = self._setup_multi(
            {"Kayne": [INVESTOR_ROW]},
            {2001: [PERSON_ROW_SUMMARY]},
        )
        result = json.loads(await fn(["Kayne Anderson", "UnknownXYZ"]))
        # 1 of 2 matched
        assert "1" in result["summary"]


# ---------------------------------------------------------------------------
# Tool 17: batch_person_lookup
# ---------------------------------------------------------------------------


class TestBatchPersonLookup:
    def _setup(self, email_rows=None, name_rows=None):
        """Build a client that returns different rows for email vs name queries."""

        async def mock_query(builder, *, count=None):
            params = dict(builder.build())
            # Email batch: uses in.() on email column
            if "email" in params and params["email"].startswith("in.("):
                return email_rows or [], None
            # Name query: uses ilike on first_name
            if "first_name" in params:
                return name_rows or [], None
            return [], None

        mcp = _CapturingMCP()
        client = MagicMock()
        client.query = AsyncMock(side_effect=mock_query)
        register(mcp, client)
        return mcp.tools["batch_person_lookup"], client

    @pytest.mark.asyncio
    async def test_email_lookup_returns_matched(self) -> None:
        fn, _ = self._setup(email_rows=[PERSON_ROW_SUMMARY])
        result = json.loads(await fn(["alice@kaynecapital.com"]))
        assert len(result["data"]["matched"]) == 1
        assert result["data"]["matched"][0]["email"] == "alice@kaynecapital.com"

    @pytest.mark.asyncio
    async def test_name_lookup_returns_matched(self) -> None:
        fn, _ = self._setup(name_rows=[PERSON_ROW_SUMMARY])
        result = json.loads(await fn(["Alice Walker"]))
        assert len(result["data"]["matched"]) == 1

    @pytest.mark.asyncio
    async def test_mixed_email_and_name(self) -> None:
        fn, _ = self._setup(
            email_rows=[PERSON_ROW_SUMMARY],
            name_rows=[PERSON_ROW_2],
        )
        result = json.loads(await fn(["alice@kaynecapital.com", "Bob Chen"]))
        assert len(result["data"]["matched"]) == 2

    @pytest.mark.asyncio
    async def test_unmatched_email_in_unmatched_list(self) -> None:
        fn, _ = self._setup(email_rows=[])
        result = json.loads(await fn(["ghost@nowhere.com"]))
        assert "ghost@nowhere.com" in result["data"]["unmatched_identifiers"]

    @pytest.mark.asyncio
    async def test_unmatched_name_in_unmatched_list(self) -> None:
        fn, _ = self._setup(name_rows=[])
        result = json.loads(await fn(["Ghost Person"]))
        assert "Ghost Person" in result["data"]["unmatched_identifiers"]

    @pytest.mark.asyncio
    async def test_empty_identifiers_returns_validation_error(self) -> None:
        fn, _ = self._setup()
        result = json.loads(await fn([]))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_whitespace_only_identifiers_filtered(self) -> None:
        fn, _ = self._setup(email_rows=[PERSON_ROW_SUMMARY])
        result = json.loads(await fn(["  ", "", "alice@kaynecapital.com"]))
        # Only the email should be processed
        assert result["data"]["matched"][0]["email"] == "alice@kaynecapital.com"

    @pytest.mark.asyncio
    async def test_chunking_emails_at_100(self) -> None:
        """101 emails should result in exactly 2 query calls."""
        emails = [f"user{i}@example.com" for i in range(101)]

        call_count = 0

        async def counting_query(builder, *, count=None):
            nonlocal call_count
            params = dict(builder.build())
            if "email" in params and params["email"].startswith("in.("):
                call_count += 1
            return [], None

        mcp = _CapturingMCP()
        client = MagicMock()
        client.query = AsyncMock(side_effect=counting_query)
        register(mcp, client)

        await mcp.tools["batch_person_lookup"](emails)
        assert call_count == 2  # ceil(101 / 100) = 2

    @pytest.mark.asyncio
    async def test_single_token_name_searches_first_name_only(self) -> None:
        """A single-word identifier (no space) searches first_name only."""
        called_params = []

        async def capturing_query(builder, *, count=None):
            called_params.append(dict(builder.build()))
            return [], None

        mcp = _CapturingMCP()
        client = MagicMock()
        client.query = AsyncMock(side_effect=capturing_query)
        register(mcp, client)

        await mcp.tools["batch_person_lookup"](["Alice"])
        # Should have called with first_name ilike but NOT last_name ilike
        name_queries = [p for p in called_params if "first_name" in p]
        assert len(name_queries) >= 1
        last_name_queries = [p for p in called_params if "last_name" in p]
        assert len(last_name_queries) == 0

    @pytest.mark.asyncio
    async def test_summary_contains_counts(self) -> None:
        fn, _ = self._setup(email_rows=[PERSON_ROW_SUMMARY], name_rows=[])
        result = json.loads(await fn(["alice@kaynecapital.com", "Ghost Person"]))
        # 1 matched, 1 unmatched
        assert "1" in result["summary"]

    @pytest.mark.asyncio
    async def test_query_error_handled_gracefully(self) -> None:
        """A query error on one chunk should not crash the whole lookup."""
        from src.client import IOQueryError

        async def erroring_query(builder, *, count=None):
            raise IOQueryError("bad query", 400)

        mcp = _CapturingMCP()
        client = MagicMock()
        client.query = AsyncMock(side_effect=erroring_query)
        register(mcp, client)

        # Should not raise — should return with 0 matched
        result = json.loads(await mcp.tools["batch_person_lookup"](["alice@example.com"]))
        assert result["data"]["matched"] == []
        assert "alice@example.com" in result["data"]["unmatched_identifiers"]
