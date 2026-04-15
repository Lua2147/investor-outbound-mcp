"""Tests for src/server.py — tool discovery, registration, health tool, and server factory.

No live network calls. IOClient.from_env() is patched throughout so tests
run without IO_EMAIL / IO_PASSWORD env vars set.

Run with:
    pytest tests/test_server.py -v
"""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.server as server_module
from src.server import (
    _discover_tool_modules,
    _register_all_tools,
    create_server,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mock_client(token: str | None = None, expires_delta: float = 3600.0) -> MagicMock:
    """Return a MagicMock that quacks like IOClient."""
    client = MagicMock()
    client._token = token
    client._token_expires_at = time.monotonic() + expires_delta
    return client


def _make_mock_mcp() -> MagicMock:
    """Return a MagicMock that quacks like FastMCP."""
    mcp = MagicMock()
    # tool() is used as a decorator — it must return a callable that accepts a fn
    mcp.tool.return_value = lambda fn: fn
    return mcp


# ---------------------------------------------------------------------------
# _discover_tool_modules
# ---------------------------------------------------------------------------


class TestDiscoverToolModules:
    def test_returns_list_of_strings(self) -> None:
        modules = _discover_tool_modules()
        assert isinstance(modules, list)
        assert all(isinstance(m, str) for m in modules)

    def test_excludes_init_module(self) -> None:
        modules = _discover_tool_modules()
        assert not any(m.endswith("__init__") for m in modules)

    def test_all_names_have_src_tools_prefix(self) -> None:
        modules = _discover_tool_modules()
        for m in modules:
            assert m.startswith("src.tools."), f"Unexpected module name: {m}"

    def test_sorted_alphabetically(self) -> None:
        modules = _discover_tool_modules()
        assert modules == sorted(modules), "Discovery order must be deterministic (sorted)"

    def test_covers_all_known_tool_modules(self) -> None:
        """All tool files present in src/tools/ must appear in the discovery list."""
        tools_dir = Path(server_module.__file__).parent / "tools"
        expected = sorted(
            f"src.tools.{p.stem}"
            for p in tools_dir.glob("*.py")
            if p.stem != "__init__"
        )
        assert _discover_tool_modules() == expected

    def test_returns_empty_list_when_tools_dir_missing(self, tmp_path: Path) -> None:
        """If the tools directory doesn't exist, discovery returns []."""
        with patch.object(server_module, "_TOOLS_DIR", tmp_path / "nonexistent"):
            result = _discover_tool_modules()
        assert result == []


# ---------------------------------------------------------------------------
# _register_all_tools
# ---------------------------------------------------------------------------


class TestRegisterAllTools:
    def test_returns_count_of_registered_modules(self) -> None:
        mcp = _make_mock_mcp()
        client = _make_mock_client()
        count = _register_all_tools(mcp, client)
        # There are 8 tool modules in src/tools/ (one per file, excluding __init__)
        tools_dir = Path(server_module.__file__).parent / "tools"
        expected_count = len([p for p in tools_dir.glob("*.py") if p.stem != "__init__"])
        assert count == expected_count

    def test_each_module_register_called_with_mcp_and_client(self) -> None:
        """Every real tool module must receive (mcp, client) in its register() call."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        called_with: list[tuple[Any, Any]] = []

        def tracking_register(m: Any, c: Any) -> None:
            called_with.append((m, c))

        # Patch every discovered module's register function
        module_names = _discover_tool_modules()
        patches: list[Any] = []
        for mod_name in module_names:
            mod = importlib.import_module(mod_name)
            p = patch.object(mod, "register", side_effect=tracking_register)
            patches.append(p)
            p.start()

        try:
            _register_all_tools(mcp, client)
        finally:
            for p in patches:
                p.stop()

        assert len(called_with) == len(module_names)
        for m, c in called_with:
            assert m is mcp
            assert c is client

    def test_import_error_is_logged_and_does_not_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        """A broken import should log an error and skip that module, not crash."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        fake_modules = ["src.tools.broken_module"]
        with patch.object(server_module, "_discover_tool_modules", return_value=fake_modules):
            with patch("importlib.import_module", side_effect=ImportError("no module named broken_module")):
                import logging
                with caplog.at_level(logging.ERROR, logger="src.server"):
                    count = _register_all_tools(mcp, client)

        assert count == 0
        assert any("broken_module" in r.message for r in caplog.records)

    def test_module_without_register_is_skipped_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A module that has no register() function gets a warning, not an exception."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        fake_mod = ModuleType("src.tools.no_register")
        # Deliberately do NOT add a register attribute

        fake_modules = ["src.tools.no_register"]
        with patch.object(server_module, "_discover_tool_modules", return_value=fake_modules):
            with patch("importlib.import_module", return_value=fake_mod):
                import logging
                with caplog.at_level(logging.WARNING, logger="src.server"):
                    count = _register_all_tools(mcp, client)

        assert count == 0
        assert any("no_register" in r.message for r in caplog.records)

    def test_register_raising_exception_is_logged_and_does_not_raise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A register() that raises must log the error and continue to next module."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        fake_mod = ModuleType("src.tools.exploding")
        fake_mod.register = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[attr-defined]

        fake_modules = ["src.tools.exploding"]
        with patch.object(server_module, "_discover_tool_modules", return_value=fake_modules):
            with patch("importlib.import_module", return_value=fake_mod):
                import logging
                with caplog.at_level(logging.ERROR, logger="src.server"):
                    count = _register_all_tools(mcp, client)

        assert count == 0
        assert any("exploding" in r.message for r in caplog.records)

    def test_partial_failure_still_counts_successes(self) -> None:
        """If one module fails and another succeeds, the count reflects successes only."""
        mcp = _make_mock_mcp()
        client = _make_mock_client()

        good_mod = ModuleType("src.tools.good")
        good_mod.register = MagicMock()  # type: ignore[attr-defined]

        bad_mod = ModuleType("src.tools.bad")
        bad_mod.register = MagicMock(side_effect=RuntimeError("bang"))  # type: ignore[attr-defined]

        modules_iter = iter(["src.tools.good", "src.tools.bad"])
        mods_map = {"src.tools.good": good_mod, "src.tools.bad": bad_mod}

        with patch.object(server_module, "_discover_tool_modules", return_value=["src.tools.good", "src.tools.bad"]):
            with patch("importlib.import_module", side_effect=lambda name: mods_map[name]):
                count = _register_all_tools(mcp, client)

        assert count == 1


# ---------------------------------------------------------------------------
# create_server
# ---------------------------------------------------------------------------


class TestCreateServer:
    def test_returns_mcp_and_client_tuple(self) -> None:
        from mcp.server.fastmcp import FastMCP
        from src.client import IOClient

        with patch.object(IOClient, "from_env", return_value=_make_mock_client()):
            mcp, client = create_server()

        assert isinstance(mcp, FastMCP)
        # client is the mock returned by from_env
        assert client is not None

    def test_server_name_is_correct(self) -> None:
        from mcp.server.fastmcp import FastMCP
        from src.client import IOClient

        with patch.object(IOClient, "from_env", return_value=_make_mock_client()):
            mcp, _ = create_server()

        assert mcp.name == "investor-outbound-mcp"

    def test_tool_modules_are_registered(self) -> None:
        """create_server() must register all tool modules — count > 0."""
        from src.client import IOClient

        mock_client = _make_mock_client()
        with patch.object(IOClient, "from_env", return_value=mock_client):
            with patch.object(server_module, "_register_all_tools", return_value=7) as mock_reg:
                create_server()

        mock_reg.assert_called_once()

    def test_health_tool_registered(self) -> None:
        """The io_health built-in tool must appear in the tool list."""
        from src.client import IOClient

        with patch.object(IOClient, "from_env", return_value=_make_mock_client()):
            mcp, _ = create_server()

        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "io_health" in tool_names


# ---------------------------------------------------------------------------
# io_health tool (tested via create_server)
# ---------------------------------------------------------------------------


class TestIoHealthTool:
    @pytest.fixture
    def server_and_client(self) -> tuple[Any, MagicMock]:
        from src.client import IOClient

        mock_client = _make_mock_client(token=None)
        with patch.object(IOClient, "from_env", return_value=mock_client):
            mcp, _ = create_server()
        return mcp, mock_client

    @pytest.mark.asyncio
    async def test_health_returns_json_string(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_health_status_is_ok(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_auth_unauthenticated_when_no_token(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["auth"] == "unauthenticated"

    @pytest.mark.asyncio
    async def test_health_auth_authenticated_when_valid_token(self) -> None:
        from src.client import IOClient

        mock_client = _make_mock_client(token="valid.jwt.token", expires_delta=3600.0)
        with patch.object(IOClient, "from_env", return_value=mock_client):
            mcp, _ = create_server()

        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["auth"] == "authenticated"

    @pytest.mark.asyncio
    async def test_health_auth_expiring_soon_when_within_60s(self) -> None:
        from src.client import IOClient

        # Token expires in 30 seconds — within the 60s warning window
        mock_client = _make_mock_client(token="expiring.jwt", expires_delta=30.0)
        with patch.object(IOClient, "from_env", return_value=mock_client):
            mcp, _ = create_server()

        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["auth"] == "token_expiring_soon"

    @pytest.mark.asyncio
    async def test_health_uptime_seconds_is_non_negative(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["uptime_seconds"] >= 0.0

    @pytest.mark.asyncio
    async def test_health_tool_modules_count_is_non_negative(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert parsed["tool_modules"] >= 0

    @pytest.mark.asyncio
    async def test_health_database_field_present(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        assert "database" in parsed
        assert parsed["database"] == "lflcztamdsmxbdkqcumj"

    @pytest.mark.asyncio
    async def test_health_has_all_required_keys(self, server_and_client: tuple) -> None:
        mcp, _ = server_and_client
        tools = mcp._tool_manager.list_tools()
        health_tool = next(t for t in tools if t.name == "io_health")
        result = await health_tool.fn()
        parsed = json.loads(result)
        required_keys = {"status", "auth", "uptime_seconds", "tool_modules", "database"}
        assert required_keys.issubset(parsed.keys())
