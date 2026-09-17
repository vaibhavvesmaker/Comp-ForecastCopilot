"""
Data models for the Commission Calculator's outputs.

Kept separate from app/models/schemas.py because these describe
calculation-engine RESULTS (Layer 2), not the raw pipeline/roster/plan
data (Layer 1) the gate validates. A CommissionPayout is the audit trail
itself, not just a number — every field on it exists so a payout can be
explained and defended without re-deriving anything from the original
deals/quotas/rules.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class CommissionPayout(BaseModel):
    deal_id: str
    rep_id: str
    rep_name: str
    team: str

    plan_id: str
    plan_name: str
    period_start: date
    period_end: date
    close_date: date

    rep_period_attainment_pct: float
    tier_floor_pct: float
    tier_ceiling_pct: Optional[float]
    tier_rate: float

    deal_amount: float
    payout_amount: float

    # Copied from the plan at calculation time so a later clawback check
    # never has to re-look-up the plan that produced this payout.
    clawback_window_days: int

    trace: str


class SkippedDeal(BaseModel):
    """A closed_won deal that could not be resolved to a payout.

    Skipped, not errored — one unresolvable deal (e.g. a rep with no
    plan yet) should never halt commission calculation for every other
    rep. The Data Quality Gate is what blocks a run outright; this is
    for gaps the gate treats as non-blocking (like a zero quota).
    """
    deal_id: str
    rep_id: str
    reason_code: str
    message: str


class ClawbackFlag(BaseModel):
    """A previously paid commission whose deal has since gone closed_lost
    within the plan's clawback window.

    This is a flag only. Nothing here reverses or adjusts the original
    CommissionPayout — that decision belongs to a human, not this engine.
    """
    deal_id: str
    rep_id: str
    plan_id: str

    original_close_date: date
    flipped_close_date: date
    days_since_close: int
    clawback_window_days: int

    payout_amount: float
    message: str


class CommissionRunResult(BaseModel):
    as_of_date: date
    payouts: list[CommissionPayout]
    skipped: list[SkippedDeal]

    @property
    def total_payout_amount(self) -> float:
        return round(sum(p.payout_amount for p in self.payouts), 2)
