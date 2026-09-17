"""
Data models for the Deal Health Scorer's outputs.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from app.models.schemas import DealStage


class HealthFlag(BaseModel):
    rule_code: str
    message: str


class DealHealthScore(BaseModel):
    deal_id: str
    rep_id: str
    stage: DealStage

    days_in_stage: int
    days_since_last_activity: Optional[int]

    score: int
    is_at_risk: bool
    flags: list[HealthFlag]
    trace: str


class HealthReport(BaseModel):
    as_of_date: date
    scores: list[DealHealthScore]
    at_risk_count: int
