"""Investor Outbound Supabase client.

Handles auth (email+password → JWT with auto-refresh), PostgREST queries,
RPC calls, edge function calls, and connection pooling.

234K investors, 1.8M persons with real emails/phones/LinkedIn.

Design decisions (Phase 0 confirmed):
- count=estimated by default — count=exact times out on 1.8M persons table.
- preferred_investment_types is a TEXT string (comma-delimited), use ilike only.
- check_size units are MILLIONS USD ($10M = 10 in DB).
- Nested joins (persons→investors) are broken — use two-step RPC pattern.
- RPC calls with empty array params must send null, not [].
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://lflcztamdsmxbdkqcumj.supabase.co"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxmbGN6dGFtZHNteGJka3FjdW1qIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NDM4NzM4MDcsImV4cCI6MjA1OTQ0OTgwN30."
    "nGk0eSzJwmLkHi9IIbWQ1RtqnSWlhgh2cIfhlJZgAPU"
)  # pragma: allowlist secret — public anon key (embedded in Vite bundle)

_CONFIG_PATH = Path(__file__).parents[3] / "config" / "api_keys.json"  # monorepo root config directory

CountMode = Literal["estimated", "planned", "exact"]

# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class IOClientError(Exception):
    """Base class for all Investor Outbound client errors."""


class IOAuthError(IOClientError):
    """401 from Supabase — token invalid or expired. Client will re-auth."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class IOQueryError(IOClientError):
    """400/404 from PostgREST — bad query, wrong operator, missing table.

    Do NOT retry. Fix the query.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class IOTransientError(IOClientError):
    """500/503/timeout — transient failure. Retry with backoff."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Credentials loader
# ---------------------------------------------------------------------------


def _load_credentials() -> tuple[str, str]:
    """Load IO_EMAIL / IO_PASSWORD from env vars with fallback to api_keys.json."""
    email = os.environ.get("IO_EMAIL")
    password = os.environ.get("IO_PASSWORD")
    if email and password:
        return email, password

    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open() as fh:
            keys = json.load(fh)
        creds = keys.get("supabase_investor_outreach", {})
        email = creds.get("email") or email
        password = creds.get("password") or password

    if not email or not password:
        raise IOAuthError(
            "IO_EMAIL/IO_PASSWORD not set and not found in config/api_keys.json "
            "under 'supabase_investor_outreach'"
        )
    return email, password


# ---------------------------------------------------------------------------
# LRU cache helpers (module-level to survive client recreations)
# ---------------------------------------------------------------------------

_INVESTOR_CACHE: dict[int, tuple[float, dict]] = {}  # id → (expires_at, data)
_INVESTOR_CACHE_TTL = 60.0  # seconds

def _get_cached_investor(investor_id: int) -> dict | None:
    entry = _INVESTOR_CACHE.get(investor_id)
    if entry is None:
        return None
    expires_at, data = entry
    if time.monotonic() > expires_at:
        del _INVESTOR_CACHE[investor_id]
        return None
    return data


def _set_cached_investor(investor_id: int, data: dict) -> None:
    _INVESTOR_CACHE[investor_id] = (time.monotonic() + _INVESTOR_CACHE_TTL, data)


# ---------------------------------------------------------------------------
# QueryBuilder
# ---------------------------------------------------------------------------


class QueryBuilder:
    """Fluent PostgREST query builder.

    Builds the params dict passed to httpx (which handles URL encoding).

    Supported operators:
        eq, neq, gt, gte, lt, lte, in, is, like, ilike, cs, ov,
        not.is, not.like, fts, plfts, order, limit, offset, select
    """

    def __init__(self, table: str) -> None:
        self._table = table
        self._params: list[tuple[str, str]] = []

    # --- column filters ---

    def eq(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"eq.{value}"))
        return self

    def neq(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"neq.{value}"))
        return self

    def gt(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"gt.{value}"))
        return self

    def gte(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"gte.{value}"))
        return self

    def lt(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"lt.{value}"))
        return self

    def lte(self, column: str, value: Any) -> "QueryBuilder":
        self._params.append((column, f"lte.{value}"))
        return self

    def in_(self, column: str, values: list[Any]) -> "QueryBuilder":
        """?column=in.(a,b,c)"""
        joined = ",".join(str(v) for v in values)
        self._params.append((column, f"in.({joined})"))
        return self

    def is_(self, column: str, value: Literal["null", "true", "false"]) -> "QueryBuilder":
        self._params.append((column, f"is.{value}"))
        return self

    def not_is(self, column: str, value: Literal["null", "true", "false"]) -> "QueryBuilder":
        self._params.append((column, f"not.is.{value}"))
        return self

    def like(self, column: str, pattern: str) -> "QueryBuilder":
        self._params.append((column, f"like.{pattern}"))
        return self

    def ilike(self, column: str, pattern: str) -> "QueryBuilder":
        self._params.append((column, f"ilike.{pattern}"))
        return self

    def cs(self, column: str, values: list[str]) -> "QueryBuilder":
        """Array contains — ?column=cs.{"v1","v2"}"""
        inner = ",".join(f'"{v}"' for v in values)
        self._params.append((column, f"cs.{{{inner}}}"))
        return self

    def ov(self, column: str, values: list[str]) -> "QueryBuilder":
        """Array overlap — ?column=ov.{"v1","v2"}"""
        inner = ",".join(f'"{v}"' for v in values)
        self._params.append((column, f"ov.{{{inner}}}"))
        return self

    def fts(self, column: str, query: str) -> "QueryBuilder":
        """Full-text search."""
        self._params.append((column, f"fts.{query}"))
        return self

    def plfts(self, column: str, query: str) -> "QueryBuilder":
        """Phrase-level FTS."""
        self._params.append((column, f"plfts.{query}"))
        return self

    # --- modifiers ---

    def select(self, columns: str) -> "QueryBuilder":
        self._params.append(("select", columns))
        return self

    def order(self, column: str, ascending: bool = True, nulls_last: bool = True) -> "QueryBuilder":
        direction = "asc" if ascending else "desc"
        nulls = ".nullslast" if nulls_last else ".nullsfirst"
        self._params.append(("order", f"{column}.{direction}{nulls}"))
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._params.append(("limit", str(n)))
        return self

    def offset(self, n: int) -> "QueryBuilder":
        self._params.append(("offset", str(n)))
        return self

    def raw(self, key: str, value: str) -> "QueryBuilder":
        """Escape hatch for operators not covered above."""
        self._params.append((key, value))
        return self

    def build(self) -> list[tuple[str, str]]:
        """Return params as a list of (key, value) tuples for httpx."""
        return list(self._params)

    @property
    def table(self) -> str:
        return self._table


# ---------------------------------------------------------------------------
# IOClient
# ---------------------------------------------------------------------------


class IOClient:
    """Authenticated Supabase PostgREST client for Investor Outbound.

    Usage::

        async with IOClient.from_env() as client:
            rows, total = await client.query(
                QueryBuilder("investors")
                .select("id,investors,primary_investor_type")
                .eq("investor_status", "Actively Seeking New Investments")
                .ov("sectors_array", ["energy", "clean_tech"])
                .limit(100)
            )
    """

    def __init__(self, email: str, password: str) -> None:
        self._email = email
        self._password = password
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._refresh_token: str | None = None
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=5.0),
        )

    @classmethod
    def from_env(cls) -> "IOClient":
        """Construct client from env vars / api_keys.json."""
        email, password = _load_credentials()
        return cls(email, password)

    # --- async context manager ---

    async def __aenter__(self) -> "IOClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # --- auth ---

    async def _ensure_auth(self) -> None:
        """Authenticate or refresh token if within 60s of expiry."""
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return

        if self._refresh_token:
            try:
                await self._do_refresh()
                return
            except IOAuthError:
                logger.warning("Token refresh failed, re-authenticating from password")

        await self._do_login()

    async def _do_login(self) -> None:
        try:
            resp = await self._http.post(
                f"{BASE_URL}/auth/v1/token?grant_type=password",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={"email": self._email, "password": self._password},
            )
        except httpx.TimeoutException as exc:
            raise IOTransientError("Login request timed out") from exc

        if resp.status_code == 400:
            raise IOAuthError(f"Login failed (bad credentials?): {resp.text}")
        if resp.status_code != 200:
            raise IOAuthError(f"Login returned HTTP {resp.status_code}: {resp.text}")

        self._apply_token_response(resp.json())
        logger.info("Authenticated to Investor Outbound (expires in %ds)", resp.json().get("expires_in", 0))

    async def _do_refresh(self) -> None:
        try:
            resp = await self._http.post(
                f"{BASE_URL}/auth/v1/token?grant_type=refresh_token",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={"refresh_token": self._refresh_token},
            )
        except httpx.TimeoutException as exc:
            raise IOTransientError("Refresh request timed out") from exc

        if resp.status_code in (400, 401):
            # Refresh token expired — need full re-login
            self._refresh_token = None
            raise IOAuthError("Refresh token rejected")
        if resp.status_code != 200:
            raise IOTransientError(f"Refresh returned HTTP {resp.status_code}")

        self._apply_token_response(resp.json())

    def _apply_token_response(self, data: dict) -> None:
        self._token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        expires_in = data.get("expires_in", 3600)
        self._token_expires_at = time.monotonic() + float(expires_in)

    def _auth_headers(self, count_mode: CountMode | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if count_mode is not None:
            h["Prefer"] = f"count={count_mode}"
        return h

    # --- query ---

    async def query(
        self,
        builder: QueryBuilder,
        *,
        count: CountMode | None = "estimated",
    ) -> tuple[list[dict], int | None]:
        """Execute a PostgREST GET query.

        Args:
            builder: QueryBuilder instance describing table + filters.
            count: Count mode. ``"estimated"`` (default) is fast and safe on
                large tables. ``"exact"`` may time out on persons (1.8M rows).
                Pass ``None`` to suppress the Prefer header entirely.

        Returns:
            ``(rows, total_count)`` where total_count may be None if the
            Content-Range header is absent or unparseable.
        """
        await self._ensure_auth()

        url = f"{BASE_URL}/rest/v1/{builder.table}"
        params = builder.build()

        try:
            resp = await self._http.get(
                url,
                params=params,
                headers=self._auth_headers(count),
            )
        except httpx.TimeoutException as exc:
            raise IOTransientError(f"Query on {builder.table} timed out") from exc

        self._raise_for_status(resp)

        total = _parse_content_range(resp.headers.get("Content-Range", ""))
        return resp.json(), total

    # --- rpc ---

    async def rpc(
        self,
        function: str,
        body: dict[str, Any],
        *,
        retries: int = 2,
    ) -> Any:
        """Execute a Supabase RPC call.

        Empty lists in body are automatically converted to null — PostgREST
        array params must be null (not []) when the filter is absent.

        Args:
            function: RPC function name.
            body: Request body. Lists that are empty will be sent as null.
            retries: Max retry attempts on IOTransientError (default 2).

        Returns:
            Parsed JSON response (array or scalar).
        """
        await self._ensure_auth()

        # Convert empty arrays to null so PostgREST ignores them
        sanitized = {k: (None if isinstance(v, list) and len(v) == 0 else v) for k, v in body.items()}

        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            if attempt > 0:
                await asyncio.sleep(2 ** attempt)
            try:
                resp = await self._http.post(
                    f"{BASE_URL}/rest/v1/rpc/{function}",
                    headers=self._auth_headers(),
                    json=sanitized,
                )
            except httpx.TimeoutException as exc:
                last_exc = IOTransientError(f"RPC {function} timed out", None)
                continue

            if resp.status_code == 401:
                # Token expired mid-call — force re-auth and retry once
                self._token = None
                await self._ensure_auth()
                try:
                    resp = await self._http.post(
                        f"{BASE_URL}/rest/v1/rpc/{function}",
                        headers=self._auth_headers(),
                        json=sanitized,
                    )
                except httpx.TimeoutException as exc:
                    raise IOTransientError(f"RPC {function} timed out after re-auth") from exc

            try:
                self._raise_for_status(resp)
            except IOTransientError as exc:
                last_exc = exc
                continue
            except (IOAuthError, IOQueryError):
                raise

            return resp.json()

        raise last_exc or IOTransientError(f"RPC {function} failed after {retries + 1} attempts")

    # --- edge functions ---

    async def edge(
        self,
        function: str,
        body: dict[str, Any],
        *,
        timeout: float = 120.0,
    ) -> Any:
        """Call a Supabase Edge Function.

        Args:
            function: Edge function name (e.g. ``"export2"``).
            body: JSON payload.
            timeout: Per-request timeout in seconds (edge fns can be slow).

        Returns:
            Parsed JSON response.
        """
        await self._ensure_auth()

        try:
            resp = await self._http.post(
                f"{BASE_URL}/functions/v1/{function}",
                headers=self._auth_headers(),
                json=body,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise IOTransientError(f"Edge function '{function}' timed out") from exc

        self._raise_for_status(resp)
        return resp.json()

    # --- investor cache ---

    async def get_investor_by_id(self, investor_id: int) -> dict | None:
        """Fetch one investor by primary key, with 60s LRU cache."""
        cached = _get_cached_investor(investor_id)
        if cached is not None:
            return cached

        rows, _ = await self.query(
            QueryBuilder("investors")
            .select("*")
            .eq("id", investor_id)
            .limit(1),
            count=None,
        )
        if not rows:
            return None
        _set_cached_investor(investor_id, rows[0])
        return rows[0]

    # --- helpers ---

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Map HTTP status codes to typed IO exceptions."""
        if resp.status_code < 400:
            return
        if resp.status_code == 401:
            raise IOAuthError(f"Unauthorized (401): {resp.text[:200]}")
        if resp.status_code in (400, 404, 422):
            raise IOQueryError(
                f"Bad query (HTTP {resp.status_code}): {resp.text[:400]}",
                status_code=resp.status_code,
            )
        if resp.status_code == 429:
            raise IOTransientError(f"Rate limited (429): {resp.text[:200]}", status_code=429)
        if resp.status_code >= 500:
            raise IOTransientError(
                f"Server error (HTTP {resp.status_code}): {resp.text[:200]}",
                status_code=resp.status_code,
            )
        # fallback for unexpected 4xx
        raise IOQueryError(f"HTTP {resp.status_code}: {resp.text[:400]}", status_code=resp.status_code)

    async def close(self) -> None:
        """Close the underlying httpx connection pool."""
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_content_range(header: str) -> int | None:
    """Extract total count from a PostgREST Content-Range header.

    Format: ``0-99/1806686`` or ``0-99/*`` (unknown total).
    Returns None when total is absent or unparseable.
    """
    if "/" not in header:
        return None
    _, total_part = header.rsplit("/", 1)
    if total_part == "*":
        return None
    try:
        return int(total_part)
    except ValueError:
        return None


def rpc_params(**kwargs: Any) -> dict[str, Any]:
    """Build an RPC body dict, converting empty lists to null.

    Convenience wrapper so callers don't have to remember the rule.

    Example::

        body = rpc_params(
            search_term="fintech",
            investment_types=["Venture Capital"],
            investor_types=[],   # becomes null
        )
    """
    return {k: (None if isinstance(v, list) and len(v) == 0 else v) for k, v in kwargs.items()}
