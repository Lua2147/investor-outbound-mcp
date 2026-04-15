"""Person entity model — maps to the `persons` PostgREST table (34 columns).

All fields are Optional — the persons table is sparse (Supabase returns null
for many columns on older records). Email quality fields are pre-verified by
the upstream pipeline.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# PostgREST select strings
# ---------------------------------------------------------------------------

PERSON_SELECT_SUMMARY = (
    "id,first_name,last_name,email,phone,role,company_name,"
    "linkedin_profile_url,location,investor"
)

PERSON_SELECT_DETAIL = "*"


# ---------------------------------------------------------------------------
# Summary model — lightweight, used in list / match responses
# ---------------------------------------------------------------------------


class PersonSummary(BaseModel):
    """Lightweight person view — fields returned by PERSON_SELECT_SUMMARY."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    company_name: Optional[str] = None
    linkedin_profile_url: Optional[str] = None
    location: Optional[str] = None
    investor: Optional[int] = None  # FK → investors.id


# ---------------------------------------------------------------------------
# Detail model — all 34 columns
# ---------------------------------------------------------------------------


class PersonDetail(BaseModel):
    """Full person record — all 34 columns from PostgREST."""

    model_config = ConfigDict(populate_by_name=True)

    # Core identity
    id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_profile_url: Optional[str] = None

    # PitchBook cross-references
    pb_person_url: Optional[str] = None
    pb_person_id: Optional[str] = None
    pb_company_url: Optional[str] = None
    pb_company_id: Optional[str] = None

    # Role / employment
    role: Optional[str] = None
    description: Optional[str] = None
    company_name: Optional[str] = None
    investor: Optional[int] = None  # FK → investors.id

    # Metadata
    completeness_score: Optional[float] = None
    created_at: Optional[str] = None

    # Email quality (pre-verified)
    email_status: Optional[str] = None  # deliverable / unknown / undeliverable / risky
    email_accept_all: Optional[str] = None  # "yes" / "no" string from DB
    email_domain: Optional[str] = None
    email_disposable: Optional[bool] = None
    email_free: Optional[bool] = None
    email_provider: Optional[str] = None
    email_score: Optional[int] = None
    email_toxicity: Optional[float] = None
    good_email: Optional[bool] = None

    # Bounce tracking
    last_bounce_type: Optional[str] = None
    last_bounce_at: Optional[str] = None

    # Company metadata
    company_country: Optional[str] = None
    company_founded: Optional[int] = None
    company_linkedin: Optional[str] = None
    company_size: Optional[str] = None
    company_industry: Optional[str] = None
    domain: Optional[str] = None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_summary(data: dict[str, Any]) -> PersonSummary:
    """Parse a PostgREST persons row into PersonSummary.

    Accepts any dict shape — extra keys are silently ignored.
    """
    return PersonSummary.model_validate(data)


def format_detail(data: dict[str, Any]) -> PersonDetail:
    """Parse a PostgREST persons row (all columns) into PersonDetail."""
    return PersonDetail.model_validate(data)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def full_name(person: PersonSummary | PersonDetail) -> str:
    """Return a display name from first_name + last_name.

    Falls back to whichever part is available, or "Unknown".
    """
    parts = [p for p in (person.first_name, person.last_name) if p]
    return " ".join(parts) if parts else "Unknown"


def email_quality_label(person: PersonDetail) -> str:
    """Summarise email deliverability as a short label.

    Returns one of: "Good", "Risky", "Unknown", "Bad", "No email".
    """
    if not person.email:
        return "No email"
    if person.good_email:
        return "Good"
    status = (person.email_status or "").lower()
    if status == "deliverable":
        return "Good"
    if status == "risky":
        return "Risky"
    if status == "unknown":
        return "Unknown"
    return "Bad"
