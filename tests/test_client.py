"""Tests for src/client.py — IOClient, QueryBuilder, error taxonomy.

Uses respx to mock httpx calls. No live network calls.

Run with:
    pytest tests/test_client.py -v
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import httpx
import pytest
import respx

from src.client import (
    BASE_URL,
    ANON_KEY,
    IOAuthError,
    IOClient,
    IOQueryError,
    IOTransientError,
    QueryBuilder,
    _parse_content_range,
    rpc_params,
    _get_cached_investor,
    _set_cached_investor,
    _INVESTOR_CACHE,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LOGIN_URL = f"{BASE_URL}/auth/v1/token?grant_type=password"
REFRESH_URL = f"{BASE_URL}/auth/v1/token?grant_type=refresh_token"

MOCK_TOKEN_RESPONSE = {
    "access_token": "mock.jwt.token",
    "token_type": "bearer",
    "expires_in": 3600,
    "refresh_token": "mock-refresh-token",
    "user": {"id": "test-user-uuid", "email": "test@example.com"},
}

MOCK_INVESTORS = [
    {
        "id": 1001,
        "investors": "Acme Ventures",
        "primary_investor_type": "Venture Capital",
        "check_size_min": 1.0,
        "check_size_max": 10.0,
    },
    {
        "id": 1002,
        "investors": "Beta Capital",
        "primary_investor_type": "PE/Buyout",
        "check_size_min": 50.0,
        "check_size_max": 500.0,
    },
]

MOCK_PERSONS = [
    {
        "id": 2001,
        "first_name": "Alice",
        "last_name": "Smith",
        "role": "Managing Partner",
        "email": "alice@acme.com",
        "email_status": "deliverable",
        "good_email": True,
    }
]


@pytest.fixture()
def client() -> IOClient:
    """IOClient with pre-set token (no auth call needed for most tests)."""
    c = IOClient(email="test@example.com", password="secret")
    c._token = "mock.jwt.token"
    c._refresh_token = "mock-refresh-token"
    # Set expiry far in the future so _ensure_auth is a no-op
    c._token_expires_at = time.monotonic() + 7200
    return c


@pytest.fixture(autouse=True)
def _clear_investor_cache():
    """Clear module-level investor cache between tests."""
    _INVESTOR_CACHE.clear()
    yield
    _INVESTOR_CACHE.clear()


# ---------------------------------------------------------------------------
# 1. Credential loading
# ---------------------------------------------------------------------------


class TestCredentialLoading:
    def test_loads_from_env_vars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IO_EMAIL", "env@example.com")
        monkeypatch.setenv("IO_PASSWORD", "env-secret")
        from src.client import _load_credentials

        email, password = _load_credentials()
        assert email == "env@example.com"
        assert password == "env-secret"

    def test_env_vars_take_precedence_over_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("IO_EMAIL", "env@example.com")
        monkeypatch.setenv("IO_PASSWORD", "env-secret")
        # Even if a config file exists, env vars win
        from src.client import _load_credentials

        email, _ = _load_credentials()
        assert email == "env@example.com"

    def test_raises_when_no_credentials(self, monkeypatch):
        monkeypatch.delenv("IO_EMAIL", raising=False)
        monkeypatch.delenv("IO_PASSWORD", raising=False)
        fake_config = Path("/tmp/nonexistent_api_keys_xyz.json")
        with patch("src.client._CONFIG_PATH", fake_config):
            from src.client import _load_credentials

            with pytest.raises(IOAuthError, match="IO_EMAIL/IO_PASSWORD not set"):
                _load_credentials()

    def test_loads_from_api_keys_json(self, monkeypatch, tmp_path):
        monkeypatch.delenv("IO_EMAIL", raising=False)
        monkeypatch.delenv("IO_PASSWORD", raising=False)
        config_file = tmp_path / "api_keys.json"
        config_file.write_text(
            json.dumps(
                {"supabase_investor_outreach": {"email": "cfg@example.com", "password": "cfg-pass"}}
            )
        )
        with patch("src.client._CONFIG_PATH", config_file):
            from src.client import _load_credentials

            email, password = _load_credentials()
        assert email == "cfg@example.com"
        assert password == "cfg-pass"


# ---------------------------------------------------------------------------
# 2. Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    @respx.mock
    @pytest.mark.asyncio
    async def test_login_on_first_call(self):
        """Client authenticates when no token is set."""
        c = IOClient(email="u@example.com", password="pass")
        respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json=MOCK_TOKEN_RESPONSE))
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={"Content-Range": "*/0"})
        )
        try:
            await c.query(QueryBuilder("investors").select("id").limit(1), count=None)
        finally:
            await c.close()

        assert c._token == "mock.jwt.token"
        assert c._refresh_token == "mock-refresh-token"

    @respx.mock
    @pytest.mark.asyncio
    async def test_refresh_token_used_before_relogin(self, client):
        """When token is expired but refresh_token exists, refresh is attempted."""
        client._token_expires_at = time.monotonic() - 1  # expired

        respx.post(REFRESH_URL).mock(
            return_value=httpx.Response(
                200,
                json={**MOCK_TOKEN_RESPONSE, "access_token": "refreshed.jwt"},
            )
        )
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=MOCK_INVESTORS, headers={"Content-Range": "0-1/2"})
        )

        await client.query(QueryBuilder("investors").select("id").limit(2))
        assert client._token == "refreshed.jwt"

    @respx.mock
    @pytest.mark.asyncio
    async def test_fallback_to_login_when_refresh_fails(self, client):
        """If refresh returns 401, client falls back to full password login."""
        client._token_expires_at = time.monotonic() - 1  # force re-auth

        respx.post(REFRESH_URL).mock(return_value=httpx.Response(401, json={"error": "expired"}))
        respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json=MOCK_TOKEN_RESPONSE))
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={"Content-Range": "*/0"})
        )

        await client.query(QueryBuilder("investors").select("id").limit(1))
        # After fallback login the token is re-set from the password grant
        assert client._token == "mock.jwt.token"

    @respx.mock
    @pytest.mark.asyncio
    async def test_login_bad_credentials_raises_auth_error(self):
        """400 from login endpoint raises IOAuthError."""
        c = IOClient(email="bad@example.com", password="wrong")
        respx.post(LOGIN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(IOAuthError, match="Login failed"):
            await c._do_login()
        await c.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_token_not_refreshed_when_still_valid(self, client):
        """No auth call is made when the token is fresh."""
        login_called = []
        respx.post(LOGIN_URL).mock(side_effect=lambda r: login_called.append(r))
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={"Content-Range": "*/0"})
        )

        await client.query(QueryBuilder("investors").select("id").limit(1))
        assert len(login_called) == 0


# ---------------------------------------------------------------------------
# 3. QueryBuilder
# ---------------------------------------------------------------------------


class TestQueryBuilder:
    def test_eq(self):
        params = QueryBuilder("investors").eq("investor_status", "Actively Seeking New Investments").build()
        assert ("investor_status", "eq.Actively Seeking New Investments") in params

    def test_neq(self):
        params = QueryBuilder("persons").neq("company_country", "United States").build()
        assert ("company_country", "neq.United States") in params

    def test_gt_gte_lt_lte(self):
        qb = QueryBuilder("investors").gt("check_size_min", 1).gte("check_size_max", 5).lt("check_size_min", 100).lte("check_size_max", 200)
        params = dict(qb.build())
        assert params["check_size_min"] == "lt.100"  # last write wins for same key in list
        assert params["check_size_max"] == "lte.200"

    def test_in(self):
        params = QueryBuilder("investors").in_("primary_investor_type", ["Venture Capital", "PE/Buyout"]).build()
        assert ("primary_investor_type", "in.(Venture Capital,PE/Buyout)") in params

    def test_is_null(self):
        params = QueryBuilder("persons").is_("email", "null").build()
        assert ("email", "is.null") in params

    def test_not_is_null(self):
        params = QueryBuilder("persons").not_is("email", "null").build()
        assert ("email", "not.is.null") in params

    def test_like(self):
        params = QueryBuilder("persons").like("first_name", "Ja%").build()
        assert ("first_name", "like.Ja%") in params

    def test_ilike(self):
        params = QueryBuilder("investors").ilike("preferred_investment_types", "*Buyout/LBO*").build()
        assert ("preferred_investment_types", "ilike.*Buyout/LBO*") in params

    def test_cs_single(self):
        params = QueryBuilder("investors").cs("sectors_array", ["energy"]).build()
        assert ("sectors_array", 'cs.{"energy"}') in params

    def test_cs_multi(self):
        params = QueryBuilder("investors").cs("sectors_array", ["energy", "clean_tech"]).build()
        assert ("sectors_array", 'cs.{"energy","clean_tech"}') in params

    def test_ov(self):
        params = QueryBuilder("investors").ov("sectors_array", ["fintech", "ai_ml"]).build()
        assert ("sectors_array", 'ov.{"fintech","ai_ml"}') in params

    def test_fts(self):
        params = QueryBuilder("investors").fts("sectors_tsv", "fintech").build()
        assert ("sectors_tsv", "fts.fintech") in params

    def test_plfts(self):
        params = QueryBuilder("investors").plfts("sectors_tsv", "energy+storage").build()
        assert ("sectors_tsv", "plfts.energy+storage") in params

    def test_order_asc_nullslast(self):
        params = QueryBuilder("investors").order("check_size_min", ascending=True, nulls_last=True).build()
        assert ("order", "check_size_min.asc.nullslast") in params

    def test_order_desc_nullsfirst(self):
        params = QueryBuilder("persons").order("email_score", ascending=False, nulls_last=False).build()
        assert ("order", "email_score.desc.nullsfirst") in params

    def test_limit_offset_select(self):
        params = dict(
            QueryBuilder("persons").select("id,email").limit(50).offset(100).build()
        )
        assert params["select"] == "id,email"
        assert params["limit"] == "50"
        assert params["offset"] == "100"

    def test_table_property(self):
        qb = QueryBuilder("persons")
        assert qb.table == "persons"

    def test_raw(self):
        params = QueryBuilder("investors").raw("custom_col", "cs.{x}").build()
        assert ("custom_col", "cs.{x}") in params

    def test_chaining_produces_multiple_params(self):
        qb = (
            QueryBuilder("investors")
            .select("id,investors")
            .ov("sectors_array", ["energy"])
            .eq("investor_status", "Actively Seeking New Investments")
            .limit(10)
        )
        params = qb.build()
        assert len(params) == 4


# ---------------------------------------------------------------------------
# 4. Query execution
# ---------------------------------------------------------------------------


class TestQueryExecution:
    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_rows_and_count(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(
                200,
                json=MOCK_INVESTORS,
                headers={"Content-Range": "0-1/2"},
            )
        )
        rows, total = await client.query(
            QueryBuilder("investors").select("id,investors").limit(2)
        )
        assert len(rows) == 2
        assert total == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_default_count_mode_is_estimated(self, client):
        """Prefer: count=estimated header is sent by default."""
        captured_headers: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=[], headers={"Content-Range": "*/0"})

        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(side_effect=capture)
        await client.query(QueryBuilder("investors").limit(1))
        assert captured_headers.get("prefer") == "count=estimated"

    @respx.mock
    @pytest.mark.asyncio
    async def test_count_exact_opt_in(self, client):
        """Prefer: count=exact is sent only when explicitly requested."""
        captured_headers: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=[], headers={"Content-Range": "*/0"})

        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(side_effect=capture)
        await client.query(QueryBuilder("investors").limit(1), count="exact")
        assert captured_headers.get("prefer") == "count=exact"

    @respx.mock
    @pytest.mark.asyncio
    async def test_count_none_omits_prefer_header(self, client):
        """count=None sends no Prefer header."""
        captured_headers: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json=[], headers={})

        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(side_effect=capture)
        await client.query(QueryBuilder("investors").limit(1), count=None)
        assert "prefer" not in captured_headers

    @respx.mock
    @pytest.mark.asyncio
    async def test_params_sent_via_httpx(self, client):
        """Params are passed via httpx params= (not manual URL building)."""
        sent_url: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            sent_url.append(str(request.url))
            return httpx.Response(200, json=[], headers={"Content-Range": "*/0"})

        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(side_effect=capture)
        await client.query(
            QueryBuilder("investors")
            .eq("investor_status", "Actively Seeking New Investments")
            .limit(10)
        )
        assert len(sent_url) == 1
        # httpx handles URL encoding — spaces become %20
        assert "investor_status=eq.Actively+Seeking+New+Investments" in sent_url[0] or \
               "investor_status=eq.Actively%20Seeking%20New%20Investments" in sent_url[0]

    @respx.mock
    @pytest.mark.asyncio
    async def test_content_range_missing_returns_none_count(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={})
        )
        _, total = await client.query(QueryBuilder("investors").limit(1), count=None)
        assert total is None


# ---------------------------------------------------------------------------
# 5. RPC calls
# ---------------------------------------------------------------------------


class TestRPC:
    @respx.mock
    @pytest.mark.asyncio
    async def test_rpc_basic(self, client):
        respx.post(f"{BASE_URL}/rest/v1/rpc/manual_search_investors_only2").mock(
            return_value=httpx.Response(200, json=MOCK_INVESTORS)
        )
        result = await client.rpc(
            "manual_search_investors_only2",
            {"search_term": "fintech", "limit_count": 10, "page": 1},
        )
        assert result == MOCK_INVESTORS

    @respx.mock
    @pytest.mark.asyncio
    async def test_rpc_empty_list_becomes_null(self, client):
        """Empty list params MUST be sent as null to PostgREST."""
        captured_body: dict = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json=[])

        respx.post(f"{BASE_URL}/rest/v1/rpc/manual_search_investors_only2").mock(side_effect=capture)

        await client.rpc(
            "manual_search_investors_only2",
            {
                "search_term": "energy",
                "investment_types": [],   # must become null
                "investor_types": [],    # must become null
                "sectors": ["energy"],   # non-empty list, stays as-is
                "limit_count": 10,
                "page": 1,
            },
        )
        assert captured_body["investment_types"] is None
        assert captured_body["investor_types"] is None
        assert captured_body["sectors"] == ["energy"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_rpc_retries_on_transient_error(self, client):
        """RPC retries up to 2x on 500 errors."""
        call_count = 0

        def flaky(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(500, json={"message": "internal error"})
            return httpx.Response(200, json=[])

        respx.post(f"{BASE_URL}/rest/v1/rpc/manual_search_investors_only2").mock(side_effect=flaky)

        with patch("asyncio.sleep"):
            result = await client.rpc("manual_search_investors_only2", {"search_term": ""})
        assert call_count == 3
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_rpc_raises_query_error_on_400(self, client):
        respx.post(f"{BASE_URL}/rest/v1/rpc/bad_function").mock(
            return_value=httpx.Response(400, json={"message": "function does not exist"})
        )
        with pytest.raises(IOQueryError):
            await client.rpc("bad_function", {})

    @respx.mock
    @pytest.mark.asyncio
    async def test_rpc_reraises_after_max_retries(self, client):
        """After max retries are exhausted, IOTransientError propagates."""
        respx.post(f"{BASE_URL}/rest/v1/rpc/manual_search_investors_only2").mock(
            return_value=httpx.Response(500, json={"message": "persistent failure"})
        )
        with patch("asyncio.sleep"), pytest.raises(IOTransientError):
            await client.rpc("manual_search_investors_only2", {}, retries=1)


# ---------------------------------------------------------------------------
# 6. Edge functions
# ---------------------------------------------------------------------------


class TestEdgeFunctions:
    @respx.mock
    @pytest.mark.asyncio
    async def test_edge_basic(self, client):
        respx.post(f"{BASE_URL}/functions/v1/export2").mock(
            return_value=httpx.Response(200, json={"export_id": "abc123"})
        )
        result = await client.edge("export2", {"filters": {}})
        assert result == {"export_id": "abc123"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_edge_timeout_raises_transient(self, client):
        respx.post(f"{BASE_URL}/functions/v1/export2").mock(side_effect=httpx.TimeoutException(""))
        with pytest.raises(IOTransientError, match="timed out"):
            await client.edge("export2", {})


# ---------------------------------------------------------------------------
# 7. Error taxonomy
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(401, json={"message": "JWT expired"})
        )
        with pytest.raises(IOAuthError):
            await client.query(QueryBuilder("investors").limit(1))

    @respx.mock
    @pytest.mark.asyncio
    async def test_400_raises_query_error(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(400, json={"message": "bad operator"})
        )
        with pytest.raises(IOQueryError):
            await client.query(QueryBuilder("investors").limit(1))

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises_query_error(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/nonexistent_table").mock(
            return_value=httpx.Response(404, json={"message": "relation not found"})
        )
        with pytest.raises(IOQueryError) as exc_info:
            await client.query(QueryBuilder("nonexistent_table").limit(1))
        assert exc_info.value.status_code == 404

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_raises_transient_error(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(500, json={"message": "internal server error"})
        )
        with pytest.raises(IOTransientError):
            await client.query(QueryBuilder("investors").limit(1))

    @respx.mock
    @pytest.mark.asyncio
    async def test_timeout_raises_transient_error(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            side_effect=httpx.TimeoutException("")
        )
        with pytest.raises(IOTransientError, match="timed out"):
            await client.query(QueryBuilder("investors").limit(1))

    def test_query_error_preserves_status_code(self):
        exc = IOQueryError("bad query", status_code=422)
        assert exc.status_code == 422

    def test_transient_error_preserves_status_code(self):
        exc = IOTransientError("server down", status_code=503)
        assert exc.status_code == 503


# ---------------------------------------------------------------------------
# 8. Investor cache
# ---------------------------------------------------------------------------


class TestInvestorCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_cache_hit_skips_network(self, client):
        investor = {"id": 999, "investors": "Cache Test Fund"}
        _set_cached_investor(999, investor)

        call_count = 0

        def should_not_call(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=[investor])

        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(side_effect=should_not_call)

        result = await client.get_investor_by_id(999)
        assert result == investor
        assert call_count == 0  # no network call

    @respx.mock
    @pytest.mark.asyncio
    async def test_cache_miss_fetches_and_stores(self, client):
        investor = {"id": 888, "investors": "Fresh Fund"}
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[investor], headers={"Content-Range": "0-0/1"})
        )
        result = await client.get_investor_by_id(888)
        assert result == investor
        # Should now be in cache
        assert _get_cached_investor(888) == investor

    @respx.mock
    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self, client):
        investor = {"id": 777, "investors": "Expired Fund"}
        # Manually set with an already-expired entry
        from src.client import _INVESTOR_CACHE
        import time as _time
        _INVESTOR_CACHE[777] = (_time.monotonic() - 1, investor)

        fresh_investor = {"id": 777, "investors": "Fresh Fund After Expiry"}
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[fresh_investor], headers={"Content-Range": "0-0/1"})
        )
        result = await client.get_investor_by_id(777)
        assert result["investors"] == "Fresh Fund After Expiry"

    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_investor_returns_none(self, client):
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={"Content-Range": "*/0"})
        )
        result = await client.get_investor_by_id(99999)
        assert result is None


# ---------------------------------------------------------------------------
# 9. Content-Range parsing
# ---------------------------------------------------------------------------


class TestParseContentRange:
    def test_standard_range(self):
        assert _parse_content_range("0-99/1806686") == 1806686

    def test_unknown_total(self):
        assert _parse_content_range("0-99/*") is None

    def test_empty_string(self):
        assert _parse_content_range("") is None

    def test_no_slash(self):
        assert _parse_content_range("0-99") is None

    def test_zero_total(self):
        assert _parse_content_range("*/0") == 0


# ---------------------------------------------------------------------------
# 10. rpc_params helper
# ---------------------------------------------------------------------------


class TestRpcParams:
    def test_empty_lists_become_none(self):
        result = rpc_params(investment_types=[], investor_types=[], search_term="energy")
        assert result["investment_types"] is None
        assert result["investor_types"] is None
        assert result["search_term"] == "energy"

    def test_non_empty_lists_are_preserved(self):
        result = rpc_params(sectors=["energy", "clean_tech"])
        assert result["sectors"] == ["energy", "clean_tech"]

    def test_none_values_are_preserved(self):
        result = rpc_params(locations=None)
        assert result["locations"] is None

    def test_scalars_are_preserved(self):
        result = rpc_params(limit_count=50, page=1, search_term="")
        assert result["limit_count"] == 50
        assert result["page"] == 1

    def test_empty_body_returns_empty_dict(self):
        assert rpc_params() == {}


# ---------------------------------------------------------------------------
# 11. IOClient context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    @respx.mock
    @pytest.mark.asyncio
    async def test_async_context_manager_closes_on_exit(self):
        respx.post(LOGIN_URL).mock(return_value=httpx.Response(200, json=MOCK_TOKEN_RESPONSE))
        respx.get(url__startswith=f"{BASE_URL}/rest/v1/investors").mock(
            return_value=httpx.Response(200, json=[], headers={"Content-Range": "*/0"})
        )

        closed = False
        original_aclose = httpx.AsyncClient.aclose

        async def track_close(self):
            nonlocal closed
            closed = True
            await original_aclose(self)

        with patch.object(httpx.AsyncClient, "aclose", track_close):
            async with IOClient(email="u@example.com", password="p") as c:
                await c.query(QueryBuilder("investors").limit(1))

        assert closed
