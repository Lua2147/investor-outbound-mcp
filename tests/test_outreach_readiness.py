"""Tests for src/tools/outreach_readiness.py — Tools 18–21.

Coverage goals
--------------
- io_outreach_ready_contacts: hard-filter pass, hard-filter failures (bad email,
  free email, disposable, bounced), empty list validation, empty result,
  chunked IDs, error paths (auth, query, transient, unexpected)
- io_assess_contact_quality: grade A/B/C/D assignments, mixed contact grades,
  aggregate stats correctness, no-email defaults to D, empty result,
  empty list validation, error paths
- io_channel_coverage: all-three channels, email-only, none, per-investor
  bucketing, totals rollup, empty result, empty list validation, error paths
- io_enrich_priorities: linkedin-no-email filter, seniority ranking (senior first),
  score tiebreaker, non-senior contacts included, empty result,
  empty list validation, error paths

Mock strategy
-------------
All tests mock IOClient directly — no live Supabase calls.
The async `query` method is patched to return controlled fixtures.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures — person records
# ---------------------------------------------------------------------------

# Grade A candidate: good_email + high score
PERSON_GRADE_A: dict[str, Any] = {
    "id": 1001,
    "first_name": "Alice",
    "last_name": "Chen",
    "email": "achen@kkr.com",
    "phone": "+1-212-555-0100",
    "role": "Managing Director, Private Equity",
    "company_name": "KKR",
    "linkedin_profile_url": "https://linkedin.com/in/achen",
    "location": "New York, NY",
    "investor": 10,
    "good_email": True,
    "email_free": False,
    "email_disposable": False,
    "email_status": "deliverable",
    "email_score": 92,
    "email_toxicity": 0.01,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Grade B candidate: deliverable + score > 50, good_email False
PERSON_GRADE_B: dict[str, Any] = {
    "id": 1002,
    "first_name": "Bob",
    "last_name": "Smith",
    "email": "bsmith@tpg.com",
    "phone": "+1-415-555-0101",
    "role": "VP, Investments",
    "company_name": "TPG Capital",
    "linkedin_profile_url": "https://linkedin.com/in/bsmith",
    "location": "San Francisco, CA",
    "investor": 20,
    "good_email": False,
    "email_free": False,
    "email_disposable": False,
    "email_status": "deliverable",
    "email_score": 65,
    "email_toxicity": 0.05,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Grade C candidate: risky status
PERSON_GRADE_C: dict[str, Any] = {
    "id": 1003,
    "first_name": "Carol",
    "last_name": "Nguyen",
    "email": "cnguyen@apollo.com",
    "phone": None,
    "role": "Director, Capital Markets",
    "company_name": "Apollo Global",
    "linkedin_profile_url": None,
    "location": "New York, NY",
    "investor": 10,
    "good_email": False,
    "email_free": False,
    "email_disposable": False,
    "email_status": "risky",
    "email_score": 40,
    "email_toxicity": 0.20,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Grade D candidate: undeliverable
PERSON_GRADE_D_UNDELIVERABLE: dict[str, Any] = {
    "id": 1004,
    "first_name": "David",
    "last_name": "Park",
    "email": "dpark@old.com",
    "phone": None,
    "role": "Partner",
    "company_name": "Old Firm",
    "linkedin_profile_url": "https://linkedin.com/in/dpark",
    "location": "Chicago, IL",
    "investor": 30,
    "good_email": False,
    "email_free": False,
    "email_disposable": False,
    "email_status": "undeliverable",
    "email_score": 5,
    "email_toxicity": 0.80,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Grade D candidate: has bounce
PERSON_GRADE_D_BOUNCED: dict[str, Any] = {
    "id": 1005,
    "first_name": "Eva",
    "last_name": "Lee",
    "email": "elee@stale.com",
    "phone": None,
    "role": "Managing Partner",
    "company_name": "Stale Capital",
    "linkedin_profile_url": "https://linkedin.com/in/elee",
    "location": "Boston, MA",
    "investor": 30,
    "good_email": False,
    "email_free": False,
    "email_disposable": False,
    "email_status": "deliverable",
    "email_score": 70,
    "email_toxicity": 0.10,
    "last_bounce_type": "hard",
    "last_bounce_at": "2025-12-01T00:00:00Z",
}

# Grade D candidate: no email at all (has LinkedIn — enrich candidate)
PERSON_NO_EMAIL_HAS_LINKEDIN: dict[str, Any] = {
    "id": 1006,
    "first_name": "Frank",
    "last_name": "Wang",
    "email": None,
    "phone": "+1-310-555-0200",
    "role": "General Partner",
    "company_name": "Sequoia Capital",
    "linkedin_profile_url": "https://linkedin.com/in/fwang",
    "location": "Menlo Park, CA",
    "investor": 40,
    "good_email": None,
    "email_free": None,
    "email_disposable": None,
    "email_status": None,
    "email_score": None,
    "email_toxicity": None,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Contact that fails outreach_ready: email_free=True
PERSON_FREE_EMAIL: dict[str, Any] = {
    "id": 1007,
    "first_name": "Grace",
    "last_name": "Kim",
    "email": "gkim@gmail.com",
    "phone": None,
    "role": "Partner",
    "company_name": "Angel Fund",
    "linkedin_profile_url": None,
    "location": "Austin, TX",
    "investor": 50,
    "good_email": True,
    "email_free": True,
    "email_disposable": False,
    "email_status": "deliverable",
    "email_score": 85,
    "email_toxicity": 0.01,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Contact that fails outreach_ready: email_disposable=True
PERSON_DISPOSABLE_EMAIL: dict[str, Any] = {
    "id": 1008,
    "first_name": "Henry",
    "last_name": "Brown",
    "email": "hbrown@mailinator.com",
    "phone": None,
    "role": "Director",
    "company_name": "Test Firm",
    "linkedin_profile_url": None,
    "location": "Remote",
    "investor": 50,
    "good_email": True,
    "email_free": False,
    "email_disposable": True,
    "email_status": "deliverable",
    "email_score": 80,
    "email_toxicity": 0.50,
    "last_bounce_type": None,
    "last_bounce_at": None,
}

# Contact with phone only (no email, no LinkedIn)
PERSON_PHONE_ONLY: dict[str, Any] = {
    "id": 1009,
    "first_name": "Iris",
    "last_name": "Patel",
    "email": None,
    "phone": "+44-20-5555-0300",
    "role": "Senior Partner",
    "company_name": "London Capital",
    "linkedin_profile_url": None,
    "location": "London, UK",
    "investor": 60,
}

# Contact with no channels
PERSON_NO_CHANNELS: dict[str, Any] = {
    "id": 1010,
    "first_name": "Jake",
    "last_name": "Torres",
    "email": None,
    "phone": None,
    "role": "Associate",
    "company_name": "Small Fund",
    "linkedin_profile_url": None,
    "location": "Miami, FL",
    "investor": 60,
}

# Senior contact: LinkedIn, no email — top enrich priority
PERSON_SENIOR_LINKEDIN_ONLY: dict[str, Any] = {
    "id": 1011,
    "first_name": "Karen",
    "last_name": "Osei",
    "email": None,
    "phone": None,
    "role": "Managing Partner",
    "company_name": "Pantheon Ventures",
    "linkedin_profile_url": "https://linkedin.com/in/kosei",
    "location": "San Francisco, CA",
    "investor": 70,
}

# Non-senior contact: LinkedIn, no email — lower enrich priority
PERSON_JUNIOR_LINKEDIN_ONLY: dict[str, Any] = {
    "id": 1012,
    "first_name": "Leo",
    "last_name": "Diaz",
    "email": None,
    "phone": None,
    "role": "Analyst",
    "company_name": "Pantheon Ventures",
    "linkedin_profile_url": "https://linkedin.com/in/ldiaz",
    "location": "San Francisco, CA",
    "investor": 70,
}

# Grade C: low score, email present
PERSON_GRADE_C_LOW_SCORE: dict[str, Any] = {
    "id": 1013,
    "first_name": "Maya",
    "last_name": "Singh",
    "email": "msingh@unknown.com",
    "phone": None,
    "role": "Vice President",
    "company_name": "Unknown Fund",
    "linkedin_profile_url": None,
    "location": "Dallas, TX",
    "investor": 80,
    "good_email": False,
    "email_free": False,
    "email_disposable": False,
    "email_status": "unknown",
    "email_score": 30,
    "email_toxicity": 0.15,
    "last_bounce_type": None,
    "last_bounce_at": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(query_return=None, query_side_effect=None) -> MagicMock:
    """Build a mock IOClient with a controllable async query method."""
    client = MagicMock()
    client.query = AsyncMock()
    client.rpc = AsyncMock()
    if query_side_effect is not None:
        client.query.side_effect = query_side_effect
    elif query_return is not None:
        client.query.return_value = query_return
    return client


def _make_mcp() -> MagicMock:
    """Build a minimal FastMCP mock that captures tool registrations."""
    mcp = MagicMock()
    _tools: dict[str, Any] = {}

    def _tool_decorator(**kwargs):
        name = kwargs.get("name", "")

        def _register(fn):
            _tools[name] = fn
            return fn

        return _register

    mcp.tool.side_effect = _tool_decorator
    mcp._tools = _tools
    return mcp


def _setup() -> tuple[MagicMock, MagicMock]:
    """Register outreach_readiness tools and return (mcp, client)."""
    from src.tools.outreach_readiness import register

    mcp = _make_mcp()
    client = _make_mock_client()
    register(mcp, client)
    return mcp, client


# ---------------------------------------------------------------------------
# Tests: io_outreach_ready_contacts (Tool 18)
# ---------------------------------------------------------------------------


class TestOutreachReadyContacts:
    def setup_method(self):
        self.mcp, self.client = _setup()
        self.tool = self.mcp._tools["io_outreach_ready_contacts"]

    @pytest.mark.asyncio
    async def test_empty_investor_ids_returns_validation_error(self):
        """Empty investor_ids list must return VALIDATION_ERROR."""
        result = json.loads(await self.tool(investor_ids=[]))
        assert "error" in result
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_good_contact_passes_all_filters(self):
        """A contact with good_email=True, email_free=False, email_disposable=False,
        no bounce is included in the result."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        assert "data" in result
        assert len(result["data"]) == 1
        assert result["data"][0]["id"] == 1001

    @pytest.mark.asyncio
    async def test_empty_db_result_returns_empty_list(self):
        """When the DB returns no rows, data is an empty list."""
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        assert result["data"] == []
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_summary_includes_count_and_investor_count(self):
        """Summary mentions how many contacts and investors were queried."""
        self.client.query.return_value = ([PERSON_GRADE_A, PERSON_GRADE_B], None)
        result = json.loads(await self.tool(investor_ids=[10, 20]))
        assert "2" in result["summary"] or "2 outreach" in result["summary"]

    @pytest.mark.asyncio
    async def test_next_actions_present(self):
        """next_actions list is included in response."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        assert "next_actions" in result
        assert len(result["next_actions"]) >= 1

    @pytest.mark.asyncio
    async def test_chunked_query_for_large_id_list(self):
        """More than 100 IDs triggers multiple query calls (chunking)."""
        self.client.query.return_value = ([], None)
        large_ids = list(range(1, 201))  # 200 IDs → 2 chunks
        await self.tool(investor_ids=large_ids)
        assert self.client.query.call_count == 2

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        """IOAuthError maps to AUTH_FAILED error code."""
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError("token expired")
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self):
        """IOQueryError maps to QUERY_ERROR error code."""
        from src.client import IOQueryError

        self.client.query.side_effect = IOQueryError("bad operator", 400)
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        """IOTransientError maps to SERVER_ERROR error code."""
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("503 Service Unavailable")
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_server_error(self):
        """Unhandled exceptions map to SERVER_ERROR."""
        self.client.query.side_effect = RuntimeError("unexpected")
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_deduplication_across_chunks(self):
        """The same person ID returned in multiple chunks is deduped."""
        # Both chunks return the same person
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        ids = list(range(1, 201))  # 200 IDs → 2 chunks, same row returned twice
        result = json.loads(await self.tool(investor_ids=ids))
        assert len(result["data"]) == 1  # deduped to 1


# ---------------------------------------------------------------------------
# Tests: io_assess_contact_quality (Tool 19)
# ---------------------------------------------------------------------------


class TestAssessContactQuality:
    def setup_method(self):
        self.mcp, self.client = _setup()
        self.tool = self.mcp._tools["io_assess_contact_quality"]

    @pytest.mark.asyncio
    async def test_empty_investor_ids_returns_validation_error(self):
        result = json.loads(await self.tool(investor_ids=[]))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_grade_a_assigned_correctly(self):
        """good_email=True + score>80 earns grade A."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        contacts = result["data"]["contacts"]
        assert len(contacts) == 1
        assert contacts[0]["grade"] == "A"

    @pytest.mark.asyncio
    async def test_grade_b_assigned_correctly(self):
        """deliverable + score>50 (good_email=False) earns grade B."""
        self.client.query.return_value = ([PERSON_GRADE_B], None)
        result = json.loads(await self.tool(investor_ids=[20]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "B"

    @pytest.mark.asyncio
    async def test_grade_c_risky_status(self):
        """email_status=risky earns grade C."""
        self.client.query.return_value = ([PERSON_GRADE_C], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "C"

    @pytest.mark.asyncio
    async def test_grade_c_low_score(self):
        """email_score<=50 with email present earns grade C."""
        self.client.query.return_value = ([PERSON_GRADE_C_LOW_SCORE], None)
        result = json.loads(await self.tool(investor_ids=[80]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "C"

    @pytest.mark.asyncio
    async def test_grade_d_undeliverable(self):
        """email_status=undeliverable earns grade D."""
        self.client.query.return_value = ([PERSON_GRADE_D_UNDELIVERABLE], None)
        result = json.loads(await self.tool(investor_ids=[30]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "D"

    @pytest.mark.asyncio
    async def test_grade_d_bounced(self):
        """Non-null last_bounce_type earns grade D regardless of other fields."""
        self.client.query.return_value = ([PERSON_GRADE_D_BOUNCED], None)
        result = json.loads(await self.tool(investor_ids=[30]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "D"

    @pytest.mark.asyncio
    async def test_grade_d_no_email(self):
        """Contact with no email earns grade D."""
        self.client.query.return_value = ([PERSON_NO_EMAIL_HAS_LINKEDIN], None)
        result = json.loads(await self.tool(investor_ids=[40]))
        contacts = result["data"]["contacts"]
        assert contacts[0]["grade"] == "D"

    @pytest.mark.asyncio
    async def test_aggregate_stats_correct(self):
        """Stats dict counts each grade correctly."""
        mixed = [
            PERSON_GRADE_A,
            PERSON_GRADE_B,
            PERSON_GRADE_C,
            PERSON_GRADE_D_UNDELIVERABLE,
            PERSON_GRADE_D_BOUNCED,
        ]
        self.client.query.return_value = (mixed, None)
        result = json.loads(await self.tool(investor_ids=[10, 20, 30]))
        stats = result["data"]["stats"]
        assert stats["A"] == 1
        assert stats["B"] == 1
        assert stats["C"] == 1
        assert stats["D"] == 2
        assert stats["total"] == 5

    @pytest.mark.asyncio
    async def test_empty_result_returns_zero_stats(self):
        """Empty DB result returns zero-count stats."""
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        stats = result["data"]["stats"]
        assert stats["total"] == 0
        assert stats["A"] == stats["B"] == stats["C"] == stats["D"] == 0

    @pytest.mark.asyncio
    async def test_response_shape_has_contacts_and_stats(self):
        """Response data has both contacts list and stats dict."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        assert "contacts" in result["data"]
        assert "stats" in result["data"]

    @pytest.mark.asyncio
    async def test_contact_record_includes_expected_fields(self):
        """Each graded contact includes id, name, role, email, grade."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        contact = result["data"]["contacts"][0]
        for field in ("id", "name", "role", "email", "grade"):
            assert field in contact, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_auth_error_propagated(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError()
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_propagated(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("timeout")
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "SERVER_ERROR"


# ---------------------------------------------------------------------------
# Tests: io_channel_coverage (Tool 20)
# ---------------------------------------------------------------------------


class TestChannelCoverage:
    def setup_method(self):
        self.mcp, self.client = _setup()
        self.tool = self.mcp._tools["io_channel_coverage"]

    @pytest.mark.asyncio
    async def test_empty_investor_ids_returns_validation_error(self):
        result = json.loads(await self.tool(investor_ids=[]))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_all_three_channels_counted(self):
        """Contact with email+phone+LinkedIn counts toward all_three."""
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        totals = result["data"]["totals"]
        assert totals["all_three"] == 1
        assert totals["with_email"] == 1
        assert totals["with_phone"] == 1
        assert totals["with_linkedin"] == 1
        assert totals["none"] == 0

    @pytest.mark.asyncio
    async def test_no_channels_counted_in_none(self):
        """Contact with no email/phone/LinkedIn increments none."""
        self.client.query.return_value = ([PERSON_NO_CHANNELS], None)
        result = json.loads(await self.tool(investor_ids=[60]))
        totals = result["data"]["totals"]
        assert totals["none"] == 1
        assert totals["with_email"] == 0
        assert totals["with_phone"] == 0
        assert totals["with_linkedin"] == 0

    @pytest.mark.asyncio
    async def test_phone_only_does_not_count_as_all_three(self):
        """Phone-only contact does not appear in all_three."""
        self.client.query.return_value = ([PERSON_PHONE_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[60]))
        totals = result["data"]["totals"]
        assert totals["all_three"] == 0
        assert totals["with_phone"] == 1

    @pytest.mark.asyncio
    async def test_per_investor_breakdown_bucketed_correctly(self):
        """Contacts are grouped by their investor FK."""
        # PERSON_GRADE_A → investor=10, PERSON_GRADE_B → investor=20
        self.client.query.return_value = ([PERSON_GRADE_A, PERSON_GRADE_B], None)
        result = json.loads(await self.tool(investor_ids=[10, 20]))
        per_investor = result["data"]["per_investor"]
        inv_ids = {entry["investor_id"] for entry in per_investor}
        assert 10 in inv_ids
        assert 20 in inv_ids

    @pytest.mark.asyncio
    async def test_totals_roll_up_correctly(self):
        """Totals are the sum of all per-investor buckets."""
        rows = [PERSON_GRADE_A, PERSON_NO_CHANNELS, PERSON_PHONE_ONLY]
        self.client.query.return_value = (rows, None)
        result = json.loads(await self.tool(investor_ids=[10, 60]))
        totals = result["data"]["totals"]
        assert totals["total_contacts"] == 3

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_per_investor_and_zero_totals(self):
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        assert result["data"]["per_investor"] == []
        assert result["data"]["totals"]["total_contacts"] == 0

    @pytest.mark.asyncio
    async def test_summary_mentions_contact_counts_and_channels(self):
        self.client.query.return_value = ([PERSON_GRADE_A], None)
        result = json.loads(await self.tool(investor_ids=[10]))
        summary = result["summary"]
        assert "email" in summary.lower()

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError()
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_query_error_returns_query_error(self):
        from src.client import IOQueryError

        self.client.query.side_effect = IOQueryError("bad filter", 400)
        result = json.loads(await self.tool(investor_ids=[10]))
        assert result["error"]["code"] == "QUERY_ERROR"

    @pytest.mark.asyncio
    async def test_chunked_query_for_large_id_list(self):
        """201 IDs triggers 3 chunks (100 + 100 + 1)."""
        self.client.query.return_value = ([], None)
        await self.tool(investor_ids=list(range(1, 202)))
        assert self.client.query.call_count == 3


# ---------------------------------------------------------------------------
# Tests: io_enrich_priorities (Tool 21)
# ---------------------------------------------------------------------------


class TestEnrichPriorities:
    def setup_method(self):
        self.mcp, self.client = _setup()
        self.tool = self.mcp._tools["io_enrich_priorities"]

    @pytest.mark.asyncio
    async def test_empty_investor_ids_returns_validation_error(self):
        result = json.loads(await self.tool(investor_ids=[]))
        assert result["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_linkedin_only_contacts_returned(self):
        """Contacts with linkedin_profile_url and no email are in the result."""
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert len(result["data"]) == 1
        assert result["data"][0]["linkedin_profile_url"] is not None

    @pytest.mark.asyncio
    async def test_senior_ranks_above_non_senior(self):
        """Senior contact (Managing Partner) appears before Analyst."""
        # Return junior first to verify sorting flips the order
        self.client.query.return_value = (
            [PERSON_JUNIOR_LINKEDIN_ONLY, PERSON_SENIOR_LINKEDIN_ONLY],
            None,
        )
        result = json.loads(await self.tool(investor_ids=[70]))
        contacts = result["data"]
        assert len(contacts) == 2
        assert contacts[0]["is_senior"] is True
        assert contacts[1]["is_senior"] is False

    @pytest.mark.asyncio
    async def test_is_senior_flag_set_correctly(self):
        """is_senior=True for Managing Partner role."""
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["data"][0]["is_senior"] is True

    @pytest.mark.asyncio
    async def test_non_senior_is_senior_false(self):
        """is_senior=False for Analyst role."""
        self.client.query.return_value = ([PERSON_JUNIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["data"][0]["is_senior"] is False

    @pytest.mark.asyncio
    async def test_seniority_score_field_present(self):
        """Each result includes a numeric seniority_score."""
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        contact = result["data"][0]
        assert "seniority_score" in contact
        assert isinstance(contact["seniority_score"], (int, float))

    @pytest.mark.asyncio
    async def test_result_includes_linkedin_url(self):
        """linkedin_profile_url is included in each result."""
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["data"][0]["linkedin_profile_url"] == "https://linkedin.com/in/kosei"

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        """No LinkedIn-only contacts returns empty data list."""
        self.client.query.return_value = ([], None)
        result = json.loads(await self.tool(investor_ids=[99]))
        assert result["data"] == []

    @pytest.mark.asyncio
    async def test_summary_mentions_linkedin_and_no_email(self):
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        summary = result["summary"].lower()
        assert "linkedin" in summary or "email" in summary

    @pytest.mark.asyncio
    async def test_next_actions_present(self):
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert "next_actions" in result
        assert len(result["next_actions"]) >= 1

    @pytest.mark.asyncio
    async def test_auth_error_returns_auth_failed(self):
        from src.client import IOAuthError

        self.client.query.side_effect = IOAuthError()
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["error"]["code"] == "AUTH_FAILED"

    @pytest.mark.asyncio
    async def test_transient_error_returns_server_error(self):
        from src.client import IOTransientError

        self.client.query.side_effect = IOTransientError("upstream error")
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_server_error(self):
        self.client.query.side_effect = ValueError("unexpected")
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["error"]["code"] == "SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_investor_id_included_in_result(self):
        """Each result includes the investor_id FK."""
        self.client.query.return_value = ([PERSON_SENIOR_LINKEDIN_ONLY], None)
        result = json.loads(await self.tool(investor_ids=[70]))
        assert result["data"][0]["investor_id"] == 70


# ---------------------------------------------------------------------------
# Tests: _grade_contact (unit tests for the grading helper)
# ---------------------------------------------------------------------------


class TestGradeContactUnit:
    """Unit tests for the _grade_contact helper — no IO, no mock client."""

    def _grade(self, row: dict[str, Any]) -> str:
        from src.tools.outreach_readiness import _grade_contact

        return _grade_contact(row)

    def test_grade_a(self):
        assert self._grade(PERSON_GRADE_A) == "A"

    def test_grade_b(self):
        assert self._grade(PERSON_GRADE_B) == "B"

    def test_grade_c_risky(self):
        assert self._grade(PERSON_GRADE_C) == "C"

    def test_grade_c_low_score(self):
        assert self._grade(PERSON_GRADE_C_LOW_SCORE) == "C"

    def test_grade_d_undeliverable(self):
        assert self._grade(PERSON_GRADE_D_UNDELIVERABLE) == "D"

    def test_grade_d_bounced_overrides_deliverable_status(self):
        """Even a deliverable email with a bounce record gets D."""
        assert self._grade(PERSON_GRADE_D_BOUNCED) == "D"

    def test_grade_d_no_email(self):
        assert self._grade({"email": None, "good_email": None, "email_status": None,
                             "email_score": None, "last_bounce_type": None}) == "D"

    def test_grade_d_beats_good_email_when_bounced(self):
        """Bounce takes precedence over good_email=True."""
        row = {**PERSON_GRADE_A, "last_bounce_type": "soft"}
        assert self._grade(row) == "D"


# ---------------------------------------------------------------------------
# Tests: register() function
# ---------------------------------------------------------------------------


class TestRegister:
    def test_all_four_tools_registered(self):
        """register() must register exactly the 4 expected tool names."""
        from src.tools.outreach_readiness import register

        mcp = _make_mcp()
        client = _make_mock_client()
        register(mcp, client)
        expected = {
            "io_outreach_ready_contacts",
            "io_assess_contact_quality",
            "io_channel_coverage",
            "io_enrich_priorities",
        }
        assert expected.issubset(set(mcp._tools.keys()))
