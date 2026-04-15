"""Response envelope helpers for Investor Outbound MCP tools.

All MCP tools return JSON strings conforming to the standard envelope:
    {"data": ..., "summary": "...", "next_actions": [...]}

Error responses use:
    {"error": {"code": "...", "message": "...", "details": ...}}

Matches the PitchBook MCP / CapIQ MCP response pattern so the LLM can parse
outputs consistently across all three MCP servers.
"""
from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# Standard tool response
# ---------------------------------------------------------------------------


def tool_response(
    data: Any,
    summary: str,
    next_actions: list[str] | None = None,
) -> str:
    """Serialize a successful tool result as a JSON string.

    Args:
        data: The payload — list, dict, scalar, or None. Must be
            JSON-serialisable. Pydantic models should be passed as
            `model.model_dump()` or `[m.model_dump() for m in models]`.
        summary: One-sentence plain-text summary for the LLM (shown first in
            Claude's tool use display). Keep under 120 chars.
        next_actions: Optional list of suggested follow-up tool calls, e.g.
            ["Call io_get_contacts(investor_id=42) to fetch contacts"].

    Returns:
        JSON string with envelope ``{"data": ..., "summary": "...",
        "next_actions": [...]}``.
    """
    envelope: dict[str, Any] = {
        "data": data,
        "summary": summary,
    }
    if next_actions:
        envelope["next_actions"] = next_actions
    return json.dumps(envelope, default=str)


# ---------------------------------------------------------------------------
# Paginated tool response
# ---------------------------------------------------------------------------


def paginated_response(
    data: Any,
    total: int | None,
    page: int,
    page_size: int,
    summary: str,
    next_actions: list[str] | None = None,
) -> str:
    """Serialize a paginated tool result.

    Adds a ``meta`` block with pagination state:
        {"data": [...], "meta": {"total": N, "page": 1, "page_size": 50,
         "has_more": true}, "summary": "..."}

    Args:
        data: The current page payload.
        total: Total record count (may be None for estimated counts on large
            tables — callers should pass the value from IOClient.query()).
        page: 1-indexed current page number.
        page_size: Number of items per page.
        summary: One-sentence plain-text summary.
        next_actions: Optional suggested follow-up tool calls.

    Returns:
        JSON string with ``data``, ``meta``, ``summary``, and optionally
        ``next_actions``.
    """
    has_more: bool | None = None
    if total is not None:
        has_more = (page * page_size) < total

    envelope: dict[str, Any] = {
        "data": data,
        "meta": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
        },
        "summary": summary,
    }
    if next_actions:
        envelope["next_actions"] = next_actions
    return json.dumps(envelope, default=str)


# ---------------------------------------------------------------------------
# Stats / analytics response
# ---------------------------------------------------------------------------


def stats_response(counts: dict[str, Any], summary: str) -> str:
    """Serialize an analytics / aggregation result.

    Wraps the counts dict in the standard envelope without a ``next_actions``
    field (stats tools rarely suggest follow-ups).

    Args:
        counts: Arbitrary dict of count/stat values, e.g.
            {"total_investors": 234549, "with_emails": 189043}.
        summary: One-sentence plain-text summary.

    Returns:
        JSON string with ``{"data": {...}, "summary": "..."}``.
    """
    envelope: dict[str, Any] = {
        "data": counts,
        "summary": summary,
    }
    return json.dumps(envelope, default=str)


# ---------------------------------------------------------------------------
# Error response
# ---------------------------------------------------------------------------


def error_response(
    error_code: str,
    message: str,
    details: Any = None,
) -> str:
    """Serialize an error result as a JSON string.

    Error codes follow UPPER_SNAKE_CASE convention matching the PB/CapIQ
    pattern. Common codes:

    - ``AUTH_FAILED`` — 401 from Supabase
    - ``QUERY_ERROR`` — 400/404 bad PostgREST query
    - ``RATE_LIMITED`` — 429
    - ``SERVER_ERROR`` — 5xx transient failure
    - ``VALIDATION_ERROR`` — bad tool input params
    - ``NOT_FOUND`` — entity lookup returned no rows
    - ``TIMEOUT`` — request exceeded deadline

    Args:
        error_code: Machine-readable error identifier.
        message: Human-readable message. Must NOT expose stack traces or
            internal error details that reveal infra topology.
        details: Optional extra context (list of field-level issues, etc.).

    Returns:
        JSON string with ``{"error": {"code": ..., "message": ...,
        "details": ...}}``.
    """
    err: dict[str, Any] = {
        "code": error_code,
        "message": message,
    }
    if details is not None:
        err["details"] = details
    return json.dumps({"error": err}, default=str)
