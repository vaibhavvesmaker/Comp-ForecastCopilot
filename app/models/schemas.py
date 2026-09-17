"""
Core data models for the Comp & Forecast Copilot.

These are intentionally strict (Pydantic) because the Data Quality Gate
depends on well-typed inputs to catch problems early, rather than
discovering a bad value three layers downstream in a forecast number.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DealStage(str, Enum):
    PROSPECTING = "prospecting"
    QUALIFICATION = "qualification"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


# Stage-weighted probability defaults, used by the forecast engine later.
# Kept here because the Data Quality Gate needs to know which stages are
# "open" (forecastable) vs. terminal.
OPEN_STAGES = {
    DealStage.PROSPECTING,
    DealStage.QUALIFICATION,
    DealStage.PROPOSAL,
    DealStage.NEGOTIATION,
}
TERMINAL_STAGES = {DealStage.CLOSED_WON, DealStage.CLOSED_LOST}


class Deal(BaseModel):
    deal_id: str
    rep_id: str
    account_name: str
    amount: float = Field(..., description="Deal value in USD")
    stage: DealStage
    stage_entered_date: date = Field(
        ..., description="Date the deal entered its CURRENT stage"
    )
    expected_close_date: Optional[date] = None
    actual_close_date: Optional[date] = None
    last_activity_date: Optional[date] = Field(
        None, description="Last logged CRM activity (email, call, meeting)"
    )

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("deal amount must be positive")
        return v


class RepQuota(BaseModel):
    rep_id: str
    rep_name: str
    team: str
    quota_amount: float
    period_start: date
    period_end: date
    start_date: date = Field(..., description="Rep's start date, for ramp calculations")

    @field_validator("quota_amount")
    @classmethod
    def quota_must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("quota_amount cannot be negative")
        return v


class AcceleratorTier(BaseModel):
    """A single tier in a tiered commission structure.

    Example: attainment 0-100% -> 0.05 rate, 100-150% -> 0.08 rate.
    """
    attainment_floor_pct: float
    attainment_ceiling_pct: Optional[float] = None  # None = no ceiling (top tier)
    rate: float


class CommissionPlanRule(BaseModel):
    plan_id: str
    plan_name: str
    applies_to_team: str
    tiers: list[AcceleratorTier]
    clawback_window_days: int = Field(
        90, description="Deals that go to closed_lost within this window after "
        "close trigger a clawback on any commission already paid."
    )

    @field_validator("tiers")
    @classmethod
    def tiers_must_not_be_empty(cls, v: list[AcceleratorTier]) -> list[AcceleratorTier]:
        if not v:
            raise ValueError("a commission plan must have at least one tier")
        return v
