"""Tests for response envelope helpers in src/helpers.py.

Pure unit tests — no I/O, no network, no Supabase.
"""
from __future__ import annotations

import json

import pytest

from src.helpers import (
    error_response,
    paginated_response,
    stats_response,
    tool_response,
)


# ---------------------------------------------------------------------------
# tool_response tests
# ---------------------------------------------------------------------------


class TestToolResponse:
    def test_returns_json_string(self) -> None:
        result = tool_response([], "No results found")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_envelope_structure(self) -> None:
        result = tool_response({"id": 1}, "Found one investor")
        parsed = json.loads(result)
        assert "data" in parsed
        assert "summary" in parsed

    def test_data_field_preserved(self) -> None:
        data = [{"id": 1, "name": "Acme Capital"}, {"id": 2, "name": "Beta Fund"}]
        parsed = json.loads(tool_response(data, "Found 2 investors"))
        assert parsed["data"] == data

    def test_summary_field_preserved(self) -> None:
        parsed = json.loads(tool_response({}, "Summary text here"))
        assert parsed["summary"] == "Summary text here"

    def test_next_actions_included_when_provided(self) -> None:
        actions = ["Call io_get_contacts(investor_id=1) to fetch contacts"]
        parsed = json.loads(tool_response([], "No results", next_actions=actions))
        assert "next_actions" in parsed
        assert parsed["next_actions"] == actions

    def test_next_actions_absent_when_none(self) -> None:
        parsed = json.loads(tool_response([], "Summary"))
        assert "next_actions" not in parsed

    def test_next_actions_absent_when_empty_list(self) -> None:
        parsed = json.loads(tool_response([], "Summary", next_actions=[]))
        assert "next_actions" not in parsed

    def test_data_can_be_none(self) -> None:
        parsed = json.loads(tool_response(None, "Nothing found"))
        assert parsed["data"] is None

    def test_data_can_be_scalar(self) -> None:
        parsed = json.loads(tool_response(42, "Count is 42"))
        assert parsed["data"] == 42

    def test_datetime_serialization_via_default_str(self) -> None:
        """Non-serialisable objects fall back to str() via default=str."""
        from datetime import datetime
        dt = datetime(2026, 1, 15)
        result = tool_response({"ts": dt}, "Has datetime")
        parsed = json.loads(result)
        assert "2026" in parsed["data"]["ts"]


# ---------------------------------------------------------------------------
# paginated_response tests
# ---------------------------------------------------------------------------


class TestPaginatedResponse:
    def test_envelope_has_meta_block(self) -> None:
        parsed = json.loads(paginated_response([], total=500, page=1, page_size=50, summary="Page 1"))
        assert "meta" in parsed
        meta = parsed["meta"]
        assert meta["total"] == 500
        assert meta["page"] == 1
        assert meta["page_size"] == 50

    def test_has_more_true_when_not_on_last_page(self) -> None:
        parsed = json.loads(paginated_response([], total=500, page=1, page_size=50, summary=""))
        assert parsed["meta"]["has_more"] is True

    def test_has_more_false_on_last_page(self) -> None:
        # page 10 of 10 (10*50 = 500 = total)
        parsed = json.loads(paginated_response([], total=500, page=10, page_size=50, summary=""))
        assert parsed["meta"]["has_more"] is False

    def test_has_more_none_when_total_unknown(self) -> None:
        parsed = json.loads(paginated_response([], total=None, page=1, page_size=50, summary=""))
        assert parsed["meta"]["has_more"] is None

    def test_data_and_summary_present(self) -> None:
        data = [{"id": 1}]
        parsed = json.loads(paginated_response(data, total=1, page=1, page_size=50, summary="One result"))
        assert parsed["data"] == data
        assert parsed["summary"] == "One result"

    def test_next_actions_included(self) -> None:
        parsed = json.loads(
            paginated_response(
                [], total=100, page=1, page_size=50, summary="",
                next_actions=["Call again with page=2"]
            )
        )
        assert "next_actions" in parsed

    def test_next_actions_absent_when_none(self) -> None:
        parsed = json.loads(paginated_response([], total=0, page=1, page_size=50, summary=""))
        assert "next_actions" not in parsed


# ---------------------------------------------------------------------------
# stats_response tests
# ---------------------------------------------------------------------------


class TestStatsResponse:
    def test_returns_json_string(self) -> None:
        result = stats_response({"total": 234549}, "Stats retrieved")
        assert isinstance(result, str)

    def test_envelope_structure(self) -> None:
        counts = {"total_investors": 234549, "with_emails": 189043}
        parsed = json.loads(stats_response(counts, "Investor counts"))
        assert parsed["data"] == counts
        assert parsed["summary"] == "Investor counts"

    def test_no_next_actions_field(self) -> None:
        """Stats responses should not include next_actions."""
        parsed = json.loads(stats_response({}, ""))
        assert "next_actions" not in parsed

    def test_empty_counts_dict(self) -> None:
        parsed = json.loads(stats_response({}, "No data"))
        assert parsed["data"] == {}


# ---------------------------------------------------------------------------
# error_response tests
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_returns_json_string(self) -> None:
        result = error_response("NOT_FOUND", "Investor 999 not found")
        assert isinstance(result, str)

    def test_error_envelope_structure(self) -> None:
        parsed = json.loads(error_response("NOT_FOUND", "Investor 999 not found"))
        assert "error" in parsed
        err = parsed["error"]
        assert err["code"] == "NOT_FOUND"
        assert err["message"] == "Investor 999 not found"

    def test_details_included_when_provided(self) -> None:
        details = [{"field": "investor_id", "issue": "must be a positive integer"}]
        parsed = json.loads(error_response("VALIDATION_ERROR", "Bad input", details=details))
        assert parsed["error"]["details"] == details

    def test_details_absent_when_none(self) -> None:
        parsed = json.loads(error_response("AUTH_FAILED", "Unauthorized"))
        assert "details" not in parsed["error"]

    def test_error_code_values(self) -> None:
        """Standard error codes must round-trip correctly."""
        codes = [
            "AUTH_FAILED",
            "QUERY_ERROR",
            "RATE_LIMITED",
            "SERVER_ERROR",
            "VALIDATION_ERROR",
            "NOT_FOUND",
            "TIMEOUT",
        ]
        for code in codes:
            parsed = json.loads(error_response(code, "msg"))
            assert parsed["error"]["code"] == code

    def test_no_stack_trace_in_message(self) -> None:
        """Callers must sanitize messages — we just verify no traceback leaks."""
        msg = "Failed to connect"
        parsed = json.loads(error_response("SERVER_ERROR", msg))
        assert "Traceback" not in parsed["error"]["message"]
        assert "File " not in parsed["error"]["message"]

    def test_details_can_be_dict(self) -> None:
        details = {"raw_error": "PGRST200", "hint": "no FK relationship"}
        parsed = json.loads(error_response("QUERY_ERROR", "Query failed", details=details))
        assert parsed["error"]["details"]["raw_error"] == "PGRST200"
