"""Tests for entity models: InvestorSummary, InvestorDetail, PersonSummary, PersonDetail.

All tests use dict fixtures derived from docs/10-live-samples.json.
No live Supabase calls — pure unit tests.
"""
from __future__ import annotations

import pytest

from src.entities.investor import (
    InvestorDetail,
    InvestorSummary,
    INVESTOR_SELECT_DETAIL,
    INVESTOR_SELECT_SUMMARY,
    check_size_display,
    format_detail as format_investor_detail,
    format_summary as format_investor_summary,
)
from src.entities.person import (
    PersonDetail,
    PersonSummary,
    PERSON_SELECT_DETAIL,
    PERSON_SELECT_SUMMARY,
    email_quality_label,
    format_detail as format_person_detail,
    format_summary as format_person_summary,
    full_name,
)


# ---------------------------------------------------------------------------
# Fixtures — lifted from docs/10-live-samples.json
# ---------------------------------------------------------------------------

INVESTOR_ROW_FULL: dict = {
    "id": 15366,
    "investors": "Veymont Participations",
    "pb_id": "56528-92",
    "investor_status": "Actively Seeking New Investments",
    "table_change_id": None,
    "timestamp": "2025-04-01T00:00:00",
    "updated_at": "2025-04-01T00:00:00",
    "completeness_updated_at": None,
    "primary_investor_type": "Family Office",
    "other_investor_types": None,
    "types_array": ["Family Office"],
    "investment_types_array": None,
    "investment_types_enhanced": None,
    "sectors_array": ["fin_invest", "real_estate"],
    "sectors_enhanced": None,
    "sectors_tsv": None,
    "primary_industry_sector": "Finance",
    "capital_under_management": "$500M",
    "check_size_min": 5.0,
    "check_size_max": 50.0,
    "investments": None,
    "preferred_geography": "Europe",
    "preferred_industry": "Real estate, Finance",
    "preferred_investment_amount_high": None,
    "preferred_investment_amount_low": None,
    "preferred_investment_types": "Growth/Expansion, Buyout/LBO",
    "hq_location": "Lyon, France",
    "hq_country_generated": "France",
    "hq_continent_generated": "Europe",
    "hq_region_generated": None,
    "locations_tsv": None,
    "extracted_locations": None,
    "extracted_additional_locations": None,
    "extracted_industries": None,
    "extracted_additional_industries": None,
    "description": "Veymont Participations is a French family office.",
    "investor_website": "https://veymont.fr",
    "primary_contact": "Frédéric Faure",
    "primary_contact_email": "ffaure@veymont.fr",
    "primary_contact_first_name": "Frédéric",
    "primary_contact_last_name": "Faure",
    "primary_contact_title": "Associate & Partner",
    "primary_contact_pbid": "119685-97P",
    "contact_count": 3,
    "has_contact_emails": True,
    "completeness_score": 0.85,
    "persons_completeness_score": 0.90,
}

PERSON_ROW_FULL: dict = {
    "id": 607385,
    "first_name": "Frédéric",
    "last_name": "Faure",
    "email": "ffaure@veymont.fr",
    "phone": "+33 (0)4 72 00 89 24",
    "location": "Lyon, France",
    "linkedin_profile_url": "http://linkedin.com/in/frederic-faure-356021167/",
    "pb_person_url": "https://my.pitchbook.com/profile/119685-97P/person/profile",
    "pb_person_id": "119685-97P",
    "pb_company_url": "https://my.pitchbook.com/profile/56528-92/investor/profile",
    "pb_company_id": "56528-92",
    "role": "Associate & Partner",
    "description": "Mr. Frédéric Faure serves as Associate & Partner at Veymont Participations.",
    "company_name": "Veymont Participations",
    "investor": 15366,
    "completeness_score": 100.0,
    "created_at": "2025-04-01",
    "email_status": "deliverable",
    "email_accept_all": "no",
    "email_domain": "veymont.fr",
    "email_disposable": False,
    "email_free": False,
    "email_provider": "outlook.com",
    "email_score": 85,
    "email_toxicity": 0.0,
    "good_email": True,
    "last_bounce_type": None,
    "last_bounce_at": None,
    "company_country": "France",
    "company_founded": 1990,
    "company_linkedin": "linkedin.com/company/veymont",
    "company_size": "1-10",
    "company_industry": "financial services",
    "domain": "veymont.fr",
}


# ---------------------------------------------------------------------------
# InvestorSummary tests
# ---------------------------------------------------------------------------


class TestInvestorSummary:
    def test_format_summary_parses_core_fields(self) -> None:
        inv = format_investor_summary(INVESTOR_ROW_FULL)
        assert inv.id == 15366
        assert inv.investors == "Veymont Participations"
        assert inv.primary_investor_type == "Family Office"

    def test_format_summary_parses_arrays(self) -> None:
        inv = format_investor_summary(INVESTOR_ROW_FULL)
        assert inv.sectors_array == ["fin_invest", "real_estate"]
        assert inv.types_array == ["Family Office"]

    def test_format_summary_parses_check_size(self) -> None:
        inv = format_investor_summary(INVESTOR_ROW_FULL)
        assert inv.check_size_min == 5.0
        assert inv.check_size_max == 50.0

    def test_format_summary_handles_sparse_row(self) -> None:
        """Minimal row — only id present."""
        inv = format_investor_summary({"id": 999})
        assert inv.id == 999
        assert inv.investors is None
        assert inv.sectors_array is None

    def test_format_summary_ignores_extra_keys(self) -> None:
        """Extra PostgREST keys should not raise."""
        row = {"id": 1, "investors": "Test", "unknown_column": "value"}
        inv = format_investor_summary(row)
        assert inv.id == 1

    def test_investor_summary_model_validate_returns_correct_type(self) -> None:
        inv = InvestorSummary.model_validate(INVESTOR_ROW_FULL)
        assert isinstance(inv, InvestorSummary)

    def test_investor_select_summary_contains_required_fields(self) -> None:
        required = [
            "id", "investors", "primary_investor_type", "check_size_min",
            "contact_count", "investor_status",
        ]
        for field in required:
            assert field in INVESTOR_SELECT_SUMMARY, f"Missing: {field}"


# ---------------------------------------------------------------------------
# InvestorDetail tests
# ---------------------------------------------------------------------------


class TestInvestorDetail:
    def test_format_detail_parses_all_identity_fields(self) -> None:
        inv = format_investor_detail(INVESTOR_ROW_FULL)
        assert inv.id == 15366
        assert inv.pb_id == "56528-92"
        assert inv.investor_status == "Actively Seeking New Investments"

    def test_format_detail_parses_primary_contact(self) -> None:
        inv = format_investor_detail(INVESTOR_ROW_FULL)
        assert inv.primary_contact == "Frédéric Faure"
        assert inv.primary_contact_email == "ffaure@veymont.fr"

    def test_format_detail_parses_completeness_scores(self) -> None:
        inv = format_investor_detail(INVESTOR_ROW_FULL)
        assert inv.completeness_score == 0.85
        assert inv.persons_completeness_score == 0.90

    def test_investor_select_detail_is_star(self) -> None:
        assert INVESTOR_SELECT_DETAIL == "*"


# ---------------------------------------------------------------------------
# check_size_display tests
# ---------------------------------------------------------------------------


class TestCheckSizeDisplay:
    def test_both_present_millions(self) -> None:
        assert check_size_display(5.0, 50.0) == "$5M – $50M"

    def test_both_present_billions(self) -> None:
        assert check_size_display(1000.0, 5000.0) == "$1B – $5B"

    def test_min_only(self) -> None:
        assert check_size_display(10.0, None) == "$10M+"

    def test_max_only(self) -> None:
        assert check_size_display(None, 100.0) == "Up to $100M"

    def test_both_none(self) -> None:
        assert check_size_display(None, None) == "Unknown"

    def test_large_value_renders_as_billion(self) -> None:
        result = check_size_display(50000.0, None)
        assert "B" in result or "50000" in result  # $50B


# ---------------------------------------------------------------------------
# PersonSummary tests
# ---------------------------------------------------------------------------


class TestPersonSummary:
    def test_format_summary_parses_core_fields(self) -> None:
        p = format_person_summary(PERSON_ROW_FULL)
        assert p.id == 607385
        assert p.first_name == "Frédéric"
        assert p.last_name == "Faure"
        assert p.email == "ffaure@veymont.fr"

    def test_format_summary_parses_investor_fk(self) -> None:
        p = format_person_summary(PERSON_ROW_FULL)
        assert p.investor == 15366

    def test_format_summary_handles_minimal_row(self) -> None:
        p = format_person_summary({"id": 42})
        assert p.id == 42
        assert p.email is None
        assert p.role is None

    def test_format_summary_ignores_extra_keys(self) -> None:
        row = {"id": 1, "first_name": "Alice", "good_email": True}
        p = format_person_summary(row)
        assert p.first_name == "Alice"

    def test_person_select_summary_contains_required_fields(self) -> None:
        required = ["id", "first_name", "last_name", "email", "role", "investor"]
        for field in required:
            assert field in PERSON_SELECT_SUMMARY, f"Missing: {field}"


# ---------------------------------------------------------------------------
# PersonDetail tests
# ---------------------------------------------------------------------------


class TestPersonDetail:
    def test_format_detail_parses_all_email_quality_fields(self) -> None:
        p = format_person_detail(PERSON_ROW_FULL)
        assert p.email_status == "deliverable"
        assert p.good_email is True
        assert p.email_score == 85
        assert p.email_toxicity == 0.0
        assert p.email_disposable is False

    def test_format_detail_parses_company_metadata(self) -> None:
        p = format_person_detail(PERSON_ROW_FULL)
        assert p.company_country == "France"
        assert p.company_industry == "financial services"
        assert p.company_size == "1-10"

    def test_format_detail_parses_pb_cross_refs(self) -> None:
        p = format_person_detail(PERSON_ROW_FULL)
        assert p.pb_person_id == "119685-97P"
        assert p.pb_company_id == "56528-92"

    def test_person_select_detail_is_star(self) -> None:
        assert PERSON_SELECT_DETAIL == "*"


# ---------------------------------------------------------------------------
# full_name helper tests
# ---------------------------------------------------------------------------


class TestFullName:
    def test_both_names_present(self) -> None:
        p = PersonSummary(first_name="Alice", last_name="Smith")
        assert full_name(p) == "Alice Smith"

    def test_first_name_only(self) -> None:
        p = PersonSummary(first_name="Alice")
        assert full_name(p) == "Alice"

    def test_last_name_only(self) -> None:
        p = PersonSummary(last_name="Smith")
        assert full_name(p) == "Smith"

    def test_no_names(self) -> None:
        p = PersonSummary()
        assert full_name(p) == "Unknown"


# ---------------------------------------------------------------------------
# email_quality_label helper tests
# ---------------------------------------------------------------------------


class TestEmailQualityLabel:
    def test_good_email_flag_takes_priority(self) -> None:
        p = PersonDetail(email="x@x.com", good_email=True, email_status="deliverable")
        assert email_quality_label(p) == "Good"

    def test_deliverable_status(self) -> None:
        p = PersonDetail(email="x@x.com", good_email=False, email_status="deliverable")
        assert email_quality_label(p) == "Good"

    def test_risky_status(self) -> None:
        p = PersonDetail(email="x@x.com", good_email=False, email_status="risky")
        assert email_quality_label(p) == "Risky"

    def test_unknown_status(self) -> None:
        p = PersonDetail(email="x@x.com", good_email=False, email_status="unknown")
        assert email_quality_label(p) == "Unknown"

    def test_undeliverable_status(self) -> None:
        p = PersonDetail(email="x@x.com", good_email=False, email_status="undeliverable")
        assert email_quality_label(p) == "Bad"

    def test_no_email(self) -> None:
        p = PersonDetail(email=None)
        assert email_quality_label(p) == "No email"
