"""
Data models for the Rolling Forecast Engine's outputs.

Mirrors the shape of app/calculation/models.py: every number the engine
produces is backed by a record that explains it, not just a float.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from app.models.schemas import DealStage


class DealForecast(BaseModel):
    deal_id: str
    rep_id: str
    stage: DealStage
    deal_amount: float
    stage_weight: float
    weighted_amount: float
    trace: str


class ForecastRunResult(BaseModel):
    as_of_date: date
    deal_forecasts: list[DealForecast]

    stage_weighted_total: float
    open_pipeline_total: float

    historical_win_rate: Optional[float]
    trend_pipeline_estimate: Optional[float]

    blended_forecast: float
    trace: str
