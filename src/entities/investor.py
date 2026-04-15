"""Investor entity model — maps to the `investors` PostgREST table (47 columns).

All fields are Optional because PostgREST returns null for sparse columns.
check_size values are stored as MILLIONS USD in the database (Phase 0 confirmed:
$10M = 10, $1B = 1000). Format helpers render them as human-readable strings.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# PostgREST select strings
# ---------------------------------------------------------------------------

INVESTOR_SELECT_SUMMARY = (
    "id,investors,primary_investor_type,types_array,sectors_array,"
    "capital_under_management,check_size_min,check_size_max,"
    "hq_location,hq_country_generated,investor_website,"
    "contact_count,has_contact_emails,investor_status,"
    "preferred_investment_types,preferred_industry,preferred_geography,"
    "completeness_score"
)

INVESTOR_SELECT_DETAIL = "*"


# ---------------------------------------------------------------------------
# Summary model — lightweight, used in list responses
# ---------------------------------------------------------------------------


class InvestorSummary(BaseModel):
    """Lightweight investor view — fields returned by INVESTOR_SELECT_SUMMARY."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = None
    investors: Optional[str] = None  # the firm name
    primary_investor_type: Optional[str] = None
    types_array: Optional[list[str]] = None
    sectors_array: Optional[list[str]] = None
    capital_under_management: Optional[str] = None
    check_size_min: Optional[float] = None  # millions USD
    check_size_max: Optional[float] = None  # millions USD
    hq_location: Optional[str] = None
    hq_country_generated: Optional[str] = None
    investor_website: Optional[str] = None
    contact_count: Optional[int] = None
    has_contact_emails: Optional[bool] = None
    investor_status: Optional[str] = None
    preferred_investment_types: Optional[str] = None  # comma-delimited text
    preferred_industry: Optional[str] = None
    preferred_geography: Optional[str] = None
    completeness_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Detail model — all 47 columns
# ---------------------------------------------------------------------------


class InvestorDetail(BaseModel):
    """Full investor record — all 47 columns from PostgREST."""

    model_config = ConfigDict(populate_by_name=True)

    # Identity
    id: Optional[int] = None
    investors: Optional[str] = None  # firm name
    pb_id: Optional[str] = None
    investor_status: Optional[str] = None
    table_change_id: Optional[str] = None
    timestamp: Optional[str] = None
    updated_at: Optional[str] = None
    completeness_updated_at: Optional[str] = None

    # Investor classification
    primary_investor_type: Optional[str] = None
    other_investor_types: Optional[str] = None
    types_array: Optional[list[str]] = None

    # Investment types
    investment_types_array: Optional[list[str]] = None
    investment_types_enhanced: Optional[list[str]] = None

    # Sectors
    sectors_array: Optional[list[str]] = None
    sectors_enhanced: Optional[list[str]] = None
    # sectors_tsv is a tsvector — PostgREST returns it as a string, not useful to deserialise
    sectors_tsv: Optional[str] = None
    primary_industry_sector: Optional[str] = None

    # Capital
    capital_under_management: Optional[str] = None
    check_size_min: Optional[float] = None  # millions USD
    check_size_max: Optional[float] = None  # millions USD
    investments: Optional[Any] = None  # integer in DB (not deal history)

    # Stated preferences
    preferred_geography: Optional[str] = None
    preferred_industry: Optional[str] = None
    preferred_investment_amount_high: Optional[float] = None
    preferred_investment_amount_low: Optional[float] = None
    preferred_investment_types: Optional[str] = None  # comma-delimited text in DB

    # Location
    hq_location: Optional[str] = None
    hq_country_generated: Optional[str] = None
    hq_continent_generated: Optional[str] = None
    hq_region_generated: Optional[str] = None
    locations_tsv: Optional[str] = None
    extracted_locations: Optional[list[str]] = None
    extracted_additional_locations: Optional[list[str]] = None
    extracted_industries: Optional[list[str]] = None
    extracted_additional_industries: Optional[list[str]] = None

    # Description + web
    description: Optional[str] = None
    investor_website: Optional[str] = None

    # Primary contact
    primary_contact: Optional[str] = None
    primary_contact_email: Optional[str] = None
    primary_contact_first_name: Optional[str] = None
    primary_contact_last_name: Optional[str] = None
    primary_contact_title: Optional[str] = None
    primary_contact_pbid: Optional[str] = None

    # Rollup stats
    contact_count: Optional[int] = None
    has_contact_emails: Optional[bool] = None
    completeness_score: Optional[float] = None
    persons_completeness_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_summary(data: dict[str, Any]) -> InvestorSummary:
    """Parse a PostgREST investors row into InvestorSummary.

    Accepts any dict shape — extra keys are silently ignored (model_config
    does NOT use ``extra='forbid'`` so callers can pass full rows).
    """
    return InvestorSummary.model_validate(data)


def format_detail(data: dict[str, Any]) -> InvestorDetail:
    """Parse a PostgREST investors row (all columns) into InvestorDetail."""
    return InvestorDetail.model_validate(data)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def check_size_display(min_m: float | None, max_m: float | None) -> str:
    """Render check size range as a human-readable string.

    Args:
        min_m: Minimum check size in millions USD (as stored in DB).
        max_m: Maximum check size in millions USD (as stored in DB).

    Returns:
        Formatted string like "$5M – $50M", "$10M+" or "Unknown".
    """
    if min_m is None and max_m is None:
        return "Unknown"

    def _fmt(v: float) -> str:
        if v >= 1000:
            return f"${v / 1000:.0f}B"
        return f"${v:.0f}M"

    if min_m is not None and max_m is not None:
        return f"{_fmt(min_m)} – {_fmt(max_m)}"
    if min_m is not None:
        return f"{_fmt(min_m)}+"
    return f"Up to {_fmt(max_m)}"  # type: ignore[arg-type]
