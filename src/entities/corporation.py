"""Corporation entity stub. Table is currently empty — ready for future data."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CorporationSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[int] = None
    investors: Optional[str] = None  # corporation name
    primary_investor_type: Optional[str] = None
    hq_location: Optional[str] = None


class CorporationDetail(CorporationSummary):
    """Full corporation record — same schema as investors + sectors_enhanced."""

    pass


CORPORATION_SELECT_SUMMARY = "id,investors,primary_investor_type,hq_location"
