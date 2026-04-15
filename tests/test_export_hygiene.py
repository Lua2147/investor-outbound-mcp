"""Tests for src/tools/export_hygiene.py — Tools 28–30.

Coverage targets
----------------
Tool 28 io_export_contacts:
  - Happy path: edge fn returns id, polling reaches "Ready", download URL returned
  - Edge fn returns export_id key (not id)
  - Edge fn returns raw string (not dict)
  - Timeout path: polling never reaches Ready
  - Export job fails with status "failed"
  - Auth error on export2 trigger
  - Transient error on export2 trigger
  - Query error on export2 trigger
  - download-export edge fn raises — returns ready_no_url partial success
  - Partial body params (no optional fields)

Tool 29 io_stale_contact_check:
  - Happy path with investor_ids
  - Happy path with sectors filter (resolves investor IDs first)
  - No stale contacts found (empty result)
  - No investor_ids and no sectors/investor_types — validation error
  - Empty investor set from sector filter
  - Auth error on investor fetch
  - Individual stale batch query fails silently, others succeed
  - Contacts appear in multiple categories — total_flagged deduplication

Tool 30 io_search_by_company_industry:
  - Happy path: industry only
  - With company_size filter
  - With company_country filter
  - With has_email=True filter
  - With has_email=False filter
  - Blank company_industry — validation error
  - Pagination: page=2
  - Auth error
  - Query error
  - Transient error

Mock strategy
-------------
All tests mock IOClient directly — no live Supabase calls. We patch:
- client.edge (AsyncMock) for edge function calls
- client.query (AsyncMock) for PostgREST polls and person queries
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXPORT_ID = "abc123-export-uuid"

EXPORT_ROW_PENDING: dict[str, Any] = {
    "id": EXPORT_ID,
    "status": "Pending",
    "file_url": None,
    "created_at": "2026-04-14T10:00:00Z",
    "updated_at": "2026-04-14T10:00:01Z",
}

EXPORT_ROW_READY: dict[str, Any] = {
    "id": EXPORT_ID,
    "status": "Ready",
    "file_url": "https://storage.example.com/exports/abc123.csv",
    "created_at": "2026-04-14T10:00:00Z",
    "updated_at": "2026-04-14T10:00:10Z",
}

EXPORT_ROW_FAILED: dict[str, Any] = {
    "id": EXPORT_ID,
    "status": "failed",
    "file_url": None,
    "created_at": "2026-04-14T10:00:00Z",
    "updated_at": "2026-04-14T10:00:05Z",
}

DOWNLOAD_RESP: dict[str, Any] = {"url": "https://signed.example.com/exports/abc123.csv?token=xyz"}

# Sample stale person rows
BOUNCED_PERSON: dict[str, Any] = {
    "id": 2001,
    "first_name": "Alice",
    "last_name": "Chen",
    "email": "achen@example.com",
    "role": "Managing Director",
    "company_name": "Ares Capital",
    "investor": 42,
    "email_status": "deliverable",
    "email_score": 85,
    "last_bounce_type": "hard",
    "last_bounce_at": "2026-03-01T00:00:00Z",
    "linkedin_profile_url": "https://linkedin.com/in/achen",
}

LOW_SCORE_PERSON: dict[str, Any] = {
    "id": 2002,
    "first_name": "Bob",
    "last_name": "Smith",
    "email": "bsmith@example.com",
    "role": "Partner",
    "company_name": "KKR",
    "investor": 55,
    "email_status": "unknown",
    "email_score": 15,
    "last_bounce_type": None,
    "last_bounce_at": None,
    "linkedin_profile_url": None,
}

UNDELIVERABLE_PERSON: dict[str, Any] = {
    "id": 2003,
    "first_name": "Carol",
    "last_name": "Doe",
    "email": "cdoe@example.com",
    "role": "VP Investments",
    "company_name": "Brookfield",
    "investor": 99,
    "email_status": "undeliverable",
    "email_score": 5,
    "last_bounce_type": None,
    "last_bounce_at": None,
    "linkedin_profile_url": None,
}

# Sample person for search_by_company_industry
INDUSTRY_PERSON: dict[str, Any] = {
    "id": 3001,
    "first_name": "David",
    "last_name": "Park",
    "email": "dpark@firm.com",
    "phone": "+1-212-555-0100",
    "role": "CIO",
    "company_name": "Blackstone",
    "company_industry": "financial services",
    "company_size": "1001-5000",
    "company_country": "United States",
    "domain": "blackstone.com",
    "linkedin_profile_url": "https://linkedin.com/in/dpark",
    "email_status": "deliverable",
    "email_score": 92,
    "good_email": True,
    "investor": 77,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client() -> MagicMock:
    """Build a mock IOClient with controllable async edge + query methods."""
    client = MagicMock()
    client.edge = AsyncMock()
    client.query = AsyncMock()
    return client


def _make_mcp() -> MagicMock:
    """Build a minimal FastMCP mock that captures @mcp.tool decorated functions."""
    mcp = MagicMock()
    _tools: dict[str, Any] = {}

    def _tool_decorator(**kwargs):
        name = kwargs.get("name", "")

        def _register(fn):
            # If no explicit name, use the function name
            _tools[fn.__name__] = fn
            if name:
                _tools[name] = fn
            return fn

        return _register

    mcp.tool.side_effect = _tool_decorator
    mcp._tools = _tools
    return mcp


def _register_tools() -> tuple[dict[str, Any], MagicMock]:
    """Register the module and return (tools_dict, mock_client)."""
    from src.tools.export_hygiene import register

    mcp = _make_mcp()
    client = _make_mock_client()
    register(mcp, client)
    return mcp._tools, client


# ---------------------------------------------------------------------------
# Tests: io_export_contacts (Tool 28)
# ---------------------------------------------------------------------------


class TestExportContacts:
    def setup_method(self):
        self.tools, self.client = _register_tools()
        self.export_fn = self.tools["io_export_contacts"]

    @pytest.mark.asyncio
    async def test_happy_path_ready_on_first_poll(self):
        """Polling finds Ready on the first check — returns download URL."""
        self.client.edge.side_effect = [
            {"id": EXPORT_ID},          # export2 response
            DOWNLOAD_RESP,               # download-export response
        ]
        # First poll returns Ready immediately
        self.client.query.return_value = ([EXPORT_ROW_READY], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert "error" not in data
        assert data["data"]["status"] == "ready"
        assert data["data"]["download_url"] == DOWNLOAD_RESP["url"]
        assert data["data"]["export_id"] == EXPORT_ID

    @pytest.mark.asyncio
    async def test_happy_path_pending_then_ready(self):
        """Two polls: first returns Pending, second returns Ready."""
        self.client.edge.side_effect = [
            {"id": EXPORT_ID},
            DOWNLOAD_RESP,
        ]
        self.client.query.side_effect = [
            ([EXPORT_ROW_PENDING], 1),
            ([EXPORT_ROW_READY], 1),
        ]

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert data["data"]["status"] == "ready"
        assert data["data"]["export_id"] == EXPORT_ID

    @pytest.mark.asyncio
    async def test_export_id_from_export_id_key(self):
        """Edge fn returns export_id key instead of id key."""
        self.client.edge.side_effect = [
            {"export_id": EXPORT_ID},
            DOWNLOAD_RESP,
        ]
        self.client.query.return_value = ([EXPORT_ROW_READY], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert data["data"]["status"] == "ready"
        assert data["data"]["export_id"] == EXPORT_ID

    @pytest.mark.asyncio
    async def test_export_id_from_raw_string_response(self):
        """Edge fn returns raw string (the UUID itself)."""
        self.client.edge.side_effect = [
            EXPORT_ID,       # raw string
            DOWNLOAD_RESP,
        ]
        self.client.query.return_value = ([EXPORT_ROW_READY], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert data["data"]["status"] == "ready"
        assert data["data"]["export_id"] == EXPORT_ID

    @pytest.mark.asyncio
    async def test_timeout_never_becomes_ready(self):
        """All polls return Pending — timeout path returns partial status."""
        from src.tools.export_hygiene import _EXPORT_POLL_INTERVAL, _EXPORT_POLL_MAX

        self.client.edge.return_value = {"id": EXPORT_ID}
        self.client.query.return_value = ([EXPORT_ROW_PENDING], 1)

        poll_count = int(_EXPORT_POLL_MAX / _EXPORT_POLL_INTERVAL)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert data["data"]["status"] == "timeout"
        assert data["data"]["export_id"] == EXPORT_ID
        assert "summary" in data

    @pytest.mark.asyncio
    async def test_export_fails_with_failed_status(self):
        """Export job returns status='failed' — returns error response."""
        self.client.edge.return_value = {"id": EXPORT_ID}
        self.client.query.return_value = ([EXPORT_ROW_FAILED], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert "error" in data
        assert data["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_auth_error_on_trigger(self):
        """IOAuthError on export2 call returns AUTH_FAILED."""
        from src.client import IOAuthError

        self.client.edge.side_effect = IOAuthError("Token expired")

        result = await self.export_fn(export_name="test_export")
        data = json.loads(result)
        assert data["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_on_trigger(self):
        """IOTransientError on export2 returns SERVER_ERROR."""
        from src.client import IOTransientError

        self.client.edge.side_effect = IOTransientError("500 internal server error")

        result = await self.export_fn(export_name="test_export")
        data = json.loads(result)
        assert data["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_query_error_on_trigger(self):
        """IOQueryError on export2 returns QUERY_ERROR."""
        from src.client import IOQueryError

        self.client.edge.side_effect = IOQueryError("400 bad request")

        result = await self.export_fn(export_name="test_export")
        data = json.loads(result)
        assert data["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_no_export_id_in_response(self):
        """Edge fn returns dict with no recognisable ID key — SERVER_ERROR."""
        self.client.edge.return_value = {"something_else": "value"}

        result = await self.export_fn(export_name="test_export")
        data = json.loads(result)
        assert data["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_download_url_error_returns_partial_success(self):
        """download-export edge fn raises — returns ready_no_url partial success."""
        from src.client import IOTransientError

        self.client.edge.side_effect = [
            {"id": EXPORT_ID},
            IOTransientError("download-export timed out"),
        ]
        self.client.query.return_value = ([EXPORT_ROW_READY], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            result = await self.export_fn(export_name="test_export")

        data = json.loads(result)
        assert data["data"]["status"] == "ready_no_url"
        assert data["data"]["export_id"] == EXPORT_ID

    @pytest.mark.asyncio
    async def test_optional_filters_included_in_body(self):
        """Provided optional params are forwarded to the export2 body."""
        self.client.edge.side_effect = [
            {"id": EXPORT_ID},
            DOWNLOAD_RESP,
        ]
        self.client.query.return_value = ([EXPORT_ROW_READY], 1)

        with patch("src.tools.export_hygiene.asyncio.sleep", new=AsyncMock()):
            await self.export_fn(
                export_name="filtered_export",
                search_term="fintech",
                investor_types=["Venture Capital"],
                sectors=["fintech"],
            )

        # Verify the export2 call received the optional params
        edge_calls = self.client.edge.call_args_list
        export2_body = edge_calls[0][0][1]  # positional arg: body dict
        assert export2_body["search_term"] == "fintech"
        assert export2_body["investor_types"] == ["Venture Capital"]
        assert export2_body["sectors"] == ["fintech"]


# ---------------------------------------------------------------------------
# Tests: io_stale_contact_check (Tool 29)
# ---------------------------------------------------------------------------


class TestStaleContactCheck:
    def setup_method(self):
        self.tools, self.client = _register_tools()
        self.stale_fn = self.tools["io_stale_contact_check"]

    @pytest.mark.asyncio
    async def test_happy_path_investor_ids(self):
        """Finds bounced, low-score, and undeliverable contacts for given investor IDs."""
        # Three asyncio.gather calls → one per stale category
        self.client.query.side_effect = [
            ([BOUNCED_PERSON], None),        # bounced query
            ([LOW_SCORE_PERSON], None),      # low_score query
            ([UNDELIVERABLE_PERSON], None),  # undeliverable query
        ]

        result = await self.stale_fn(investor_ids=[42, 55, 99])
        data = json.loads(result)

        assert "error" not in data
        categories = data["data"]["categories"]
        assert categories["bounced"]["count"] == 1
        assert categories["low_score"]["count"] == 1
        assert categories["undeliverable"]["count"] == 1
        # All three are unique IDs — total_flagged = 3
        assert data["data"]["total_flagged"] == 3
        assert data["data"]["investor_scope"] == 3

    @pytest.mark.asyncio
    async def test_deduplication_contact_in_multiple_categories(self):
        """Contact appearing in multiple categories counted once in total_flagged."""
        # Same person ID in all three categories
        shared_person = dict(BOUNCED_PERSON, email_status="undeliverable", email_score=10)

        self.client.query.side_effect = [
            ([shared_person], None),
            ([shared_person], None),
            ([shared_person], None),
        ]

        result = await self.stale_fn(investor_ids=[42])
        data = json.loads(result)

        # All three categories have 1 record each, but total_flagged = 1 (same ID)
        assert data["data"]["total_flagged"] == 1

    @pytest.mark.asyncio
    async def test_happy_path_with_sectors_filter(self):
        """Resolves investor IDs from sectors then queries stale contacts."""
        # First query: investor ID resolution
        # Three subsequent queries: one per stale category
        self.client.query.side_effect = [
            ([{"id": 42}, {"id": 55}], None),    # investor lookup
            ([BOUNCED_PERSON], None),             # bounced
            ([], None),                           # low_score (empty)
            ([], None),                           # undeliverable (empty)
        ]

        result = await self.stale_fn(sectors=["fin_invest"])
        data = json.loads(result)

        assert "error" not in data
        assert data["data"]["investor_scope"] == 2
        assert data["data"]["categories"]["bounced"]["count"] == 1

    @pytest.mark.asyncio
    async def test_no_stale_contacts(self):
        """All three queries return empty — no flagged contacts."""
        self.client.query.side_effect = [
            ([], None),
            ([], None),
            ([], None),
        ]

        result = await self.stale_fn(investor_ids=[42])
        data = json.loads(result)

        assert "error" not in data
        assert data["data"]["total_flagged"] == 0
        assert "No stale contacts" in data["summary"]

    @pytest.mark.asyncio
    async def test_validation_error_no_scope(self):
        """Neither investor_ids nor sectors/investor_types provided — VALIDATION_ERROR."""
        result = await self.stale_fn()
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_empty_investor_set_from_filter(self):
        """Sector filter returns no investors — short-circuit with zero results."""
        self.client.query.return_value = ([], None)

        result = await self.stale_fn(sectors=["nonexistent_sector"])
        data = json.loads(result)

        assert "error" not in data
        assert data["data"]["total_flagged"] == 0
        assert data["data"]["investor_scope"] == 0

    @pytest.mark.asyncio
    async def test_auth_error_on_investor_fetch(self):
        """IOAuthError when fetching investor IDs returns SERVER_ERROR."""
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("Unauthorized")

        result = await self.stale_fn(sectors=["fintech"])
        data = json.loads(result)
        assert data["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_partial_batch_failure_is_silent(self):
        """If one stale batch query fails, others still succeed (no exception raised)."""
        from src.client import IOQueryError

        # Gather runs three coroutines; we simulate the low_score query failing
        # by making query raise on the 2nd and 5th call (since investor_ids is given,
        # no pre-filter needed; each category runs one query for one chunk)
        self.client.query.side_effect = [
            ([BOUNCED_PERSON], None),       # bounced succeeds
            IOQueryError("bad filter"),     # low_score fails — should be caught
            ([UNDELIVERABLE_PERSON], None), # undeliverable succeeds
        ]

        result = await self.stale_fn(investor_ids=[42])
        data = json.loads(result)

        # Should NOT be an error — partial results returned
        # bounced and undeliverable got results; low_score silently empty
        assert "error" not in data
        assert data["data"]["categories"]["bounced"]["count"] == 1
        assert data["data"]["categories"]["undeliverable"]["count"] == 1


# ---------------------------------------------------------------------------
# Tests: io_search_by_company_industry (Tool 30)
# ---------------------------------------------------------------------------


class TestSearchByCompanyIndustry:
    def setup_method(self):
        self.tools, self.client = _register_tools()
        self.search_fn = self.tools["io_search_by_company_industry"]

    @pytest.mark.asyncio
    async def test_happy_path_industry_only(self):
        """Basic industry search with estimated total returned."""
        self.client.query.return_value = ([INDUSTRY_PERSON], 12500)

        result = await self.search_fn(company_industry="financial services")
        data = json.loads(result)

        assert "error" not in data
        assert len(data["data"]) == 1
        assert data["data"][0]["company_industry"] == "financial services"
        assert data["meta"]["total"] == 12500
        assert data["meta"]["page"] == 1
        assert "financial services" in data["summary"]

    @pytest.mark.asyncio
    async def test_company_size_filter_passed_as_eq(self):
        """company_size is forwarded as an eq filter in the query."""
        self.client.query.return_value = ([INDUSTRY_PERSON], 450)

        await self.search_fn(
            company_industry="financial services",
            company_size="1001-5000",
        )

        # Inspect the QueryBuilder that was passed to client.query
        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("company_size") == "eq.1001-5000"

    @pytest.mark.asyncio
    async def test_company_country_filter(self):
        """company_country is forwarded as an ilike filter."""
        self.client.query.return_value = ([INDUSTRY_PERSON], 8000)

        await self.search_fn(
            company_industry="financial services",
            company_country="United States",
        )

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("company_country") == "ilike.*United States*"

    @pytest.mark.asyncio
    async def test_has_email_true_filter(self):
        """has_email=True adds not.is.null filter on email column."""
        self.client.query.return_value = ([INDUSTRY_PERSON], 6000)

        await self.search_fn(
            company_industry="financial services",
            has_email=True,
        )

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("email") == "not.is.null"

    @pytest.mark.asyncio
    async def test_has_email_false_filter(self):
        """has_email=False adds is.null filter on email column."""
        self.client.query.return_value = ([], 0)

        await self.search_fn(
            company_industry="financial services",
            has_email=False,
        )

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("email") == "is.null"

    @pytest.mark.asyncio
    async def test_blank_company_industry_validation_error(self):
        """Empty or whitespace-only company_industry returns VALIDATION_ERROR."""
        result = await self.search_fn(company_industry="   ")
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_empty_string_company_industry_validation_error(self):
        """Empty string company_industry returns VALIDATION_ERROR."""
        result = await self.search_fn(company_industry="")
        data = json.loads(result)
        assert data["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_pagination_offset_applied(self):
        """page=2 with limit=10 applies offset=10 to the query."""
        self.client.query.return_value = ([INDUSTRY_PERSON], 50)

        await self.search_fn(
            company_industry="financial services",
            limit=10,
            page=2,
        )

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("offset") == "10"
        assert param_map.get("limit") == "10"

    @pytest.mark.asyncio
    async def test_limit_capped_at_max(self):
        """Limit > MAX is silently capped to _MAX_LIMIT."""
        from src.tools.export_hygiene import _MAX_LIMIT

        self.client.query.return_value = ([], 0)

        await self.search_fn(
            company_industry="financial services",
            limit=9999,
        )

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert int(param_map.get("limit", 0)) <= _MAX_LIMIT

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        """IOAuthError in query returns AUTH_FAILED error."""
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("Token expired")

        result = await self.search_fn(company_industry="financial services")
        data = json.loads(result)
        assert data["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self):
        """IOQueryError in query returns QUERY_ERROR error."""
        from src.client import IOQueryError

        self.client.query.side_effect = IOQueryError("Invalid filter")

        result = await self.search_fn(company_industry="financial services")
        data = json.loads(result)
        assert data["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        """IOTransientError in query returns SERVER_ERROR error."""
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("Gateway timeout")

        result = await self.search_fn(company_industry="financial services")
        data = json.loads(result)
        assert data["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_industry_ilike_wraps_with_wildcards(self):
        """company_industry is wrapped in * wildcards for ilike matching."""
        self.client.query.return_value = ([], 0)

        await self.search_fn(company_industry="hospital & health care")

        qb = self.client.query.call_args[0][0]
        params = qb.build()
        param_map = {k: v for k, v in params}
        assert param_map.get("company_industry") == "ilike.*hospital & health care*"

    @pytest.mark.asyncio
    async def test_none_values_stripped_from_response(self):
        """None-valued fields are stripped from returned person dicts."""
        person_with_nulls = dict(INDUSTRY_PERSON, phone=None, linkedin_profile_url=None)
        self.client.query.return_value = ([person_with_nulls], 1)

        result = await self.search_fn(company_industry="financial services")
        data = json.loads(result)

        assert len(data["data"]) == 1
        # None-valued keys should be absent from the stripped dict
        assert "phone" not in data["data"][0]
        assert "linkedin_profile_url" not in data["data"][0]

    @pytest.mark.asyncio
    async def test_no_results_returns_empty_list(self):
        """Query with no matches returns empty data list, not an error."""
        self.client.query.return_value = ([], 0)

        result = await self.search_fn(company_industry="nonexistent industry xyz")
        data = json.loads(result)

        assert "error" not in data
        assert data["data"] == []
        assert data["meta"]["total"] == 0
