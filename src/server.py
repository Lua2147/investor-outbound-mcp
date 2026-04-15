"""Investor Outbound MCP server.

Entry point for the FastMCP server. Discovers all tool modules in src/tools/
at startup by calling their register(mcp, client) function — no agent should
edit this file during Phases 2/3; each tool module is self-contained.

Transport:
    stdio (default) — for Claude Desktop / local MCP use
    sse             — for remote server deployment on port 8770

Usage:
    python -m src.server                # stdio
    python -m src.server --sse          # SSE on port 8770
    python -m src.server --sse --port 9000  # SSE on custom port
"""
from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from src.client import IOClient

logger = logging.getLogger(__name__)

# Module-level start time for uptime reporting
_START_TIME = time.monotonic()

# ---------------------------------------------------------------------------
# Tool module auto-discovery
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent / "tools"


def _discover_tool_modules() -> list[str]:
    """Return sorted list of importable module names in src/tools/.

    Only includes .py files that are not __init__.py. Sorted alphabetically
    so registration order is deterministic.
    """
    if not _TOOLS_DIR.exists():
        return []
    return sorted(
        f"src.tools.{p.stem}"
        for p in _TOOLS_DIR.glob("*.py")
        if p.stem != "__init__"
    )


def _register_all_tools(mcp: FastMCP, client: IOClient) -> int:
    """Import every module in src/tools/ and call its register(mcp, client).

    Returns the number of tool modules successfully registered.
    Logs a warning (but does NOT raise) for modules missing a register()
    function so a broken tool module doesn't kill the whole server.
    """
    module_names = _discover_tool_modules()
    registered = 0

    for module_name in module_names:
        try:
            mod = importlib.import_module(module_name)
        except ImportError as exc:
            logger.error("Failed to import tool module %s: %s", module_name, exc)
            continue

        register_fn = getattr(mod, "register", None)
        if register_fn is None:
            logger.warning(
                "Tool module %s has no register() function — skipping",
                module_name,
            )
            continue

        try:
            register_fn(mcp, client)
            registered += 1
            logger.debug("Registered tool module: %s", module_name)
        except Exception as exc:
            logger.error(
                "register() in %s raised an exception: %s", module_name, exc
            )

    return registered


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server() -> tuple[FastMCP, IOClient]:
    """Build the FastMCP instance and authenticated IOClient.

    Called once at startup. The IOClient is created here but auth is
    deferred to the first request (_ensure_auth is called lazily).

    Returns:
        (mcp, client) tuple. The client is not yet authenticated.
    """
    client = IOClient.from_env()

    mcp = FastMCP(
        name="investor-outbound-mcp",
        instructions=(
            "MCP server for the Investor Outbound database: "
            "234K investors and 1.8M contacts with real emails, phones, and LinkedIn URLs. "
            "Use match_deal for deal-specific investor matching with contact-level gating. "
            "All contact searches use a 6-gate scoring pipeline to filter noise. "
            "check_size values are in MILLIONS USD."
        ),
        port=8770,
        host="127.0.0.1",
    )

    tool_count = _register_all_tools(mcp, client)
    logger.info("Registered %d tool module(s)", tool_count)

    # Register built-in health check
    _register_health_tool(mcp, client, tool_count)

    return mcp, client


# ---------------------------------------------------------------------------
# Built-in health check tool
# ---------------------------------------------------------------------------


def _register_health_tool(mcp: FastMCP, client: IOClient, module_count: int) -> None:
    """Register the io_health tool directly on the mcp instance."""

    @mcp.tool()
    async def io_health() -> str:  # type: ignore[return]
        """Check Investor Outbound MCP server health and auth status.

        Returns auth status, server uptime, and the number of registered tool
        modules. Use this to verify the server is reachable and authenticated
        before running expensive queries.

        Returns:
            JSON string with keys:
                - status: "ok" or "degraded"
                - auth: "authenticated" / "unauthenticated" / "error"
                - uptime_seconds: seconds since server start
                - tool_modules: number of registered tool modules
                - database: Supabase project ID (non-sensitive)
        """
        import json

        uptime = round(time.monotonic() - _START_TIME, 1)
        auth_status = "unauthenticated"

        if client._token is not None:
            if time.monotonic() < client._token_expires_at - 60:
                auth_status = "authenticated"
            else:
                auth_status = "token_expiring_soon"

        health = {
            "status": "ok",
            "auth": auth_status,
            "uptime_seconds": uptime,
            "tool_modules": module_count,
            "database": "lflcztamdsmxbdkqcumj",  # public project ID, not a secret
        }
        return json.dumps(health)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI args and run the server.

    Flags:
        --sse        Use SSE transport instead of stdio
        --port N     Port for SSE transport (default 8770)
    """
    parser = argparse.ArgumentParser(
        prog="investor-outbound-mcp",
        description="Investor Outbound MCP server (234K investors, 1.8M contacts)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Use SSE transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8770,
        help="Port for SSE transport (default: 8770)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    mcp, _client = create_server()

    transport = "sse" if args.sse else "stdio"

    if args.sse:
        # Reconfigure port if overridden via --port
        mcp.settings.port = args.port
        logger.info("Starting Investor Outbound MCP (SSE) on port %d", args.port)
    else:
        logger.info("Starting Investor Outbound MCP (stdio)")

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
