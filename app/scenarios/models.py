"""
Data models for the Scenario Modeler's outputs.

Both models embed the full result objects from the engines they wrap
(ForecastRunResult, CommissionRunResult) rather than just headline
numbers, so every scenario or what-if figure keeps its own underlying
per-deal audit trail intact.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.calculation.models import CommissionRunResult
from app.forecasting.models import ForecastRunResult


class ScenarioSet(BaseModel):
    as_of_date: date
    best: ForecastRunResult
    commit: ForecastRunResult
    worst: ForecastRunResult
    trace: str


class WhatIfResult(BaseModel):
    as_of_date: date

    baseline_commission: CommissionRunResult
    adjusted_commission: CommissionRunResult
    baseline_forecast: ForecastRunResult
    adjusted_forecast: ForecastRunResult

    commission_delta: float
    forecast_delta: float

    trace: str
