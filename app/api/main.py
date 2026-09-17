"""
Layer 4 — the lightweight dashboard's FastAPI backend, per README section 4.

Every endpoint here re-runs the deterministic engines (Layers 0-2) fresh
against the shared clean demo sample (sample_data.demo_dataset.CLEAN_DEALS
/ CLEAN_QUOTAS) on every request — there is no cached "current" result
anywhere in this module, matching the Rolling Forecast Engine's own
"recalculated on demand, not a static snapshot" design. The intentionally
dirty DEALS list from the demo dataset is what the Data Quality Gate is
FOR; this dashboard demos the calculation engines that only run once the
gate has already passed, so it deliberately uses the clean sample instead.

The /api/narrative endpoint is the only one that touches the AI Narrative
Layer (app/narrative/), and it is a thin pass-through: it hands the
narrative layer already-computed CommissionRunResult / ForecastRunResult
/ HealthReport / ScenarioSet objects and returns whatever prose comes
back, unmodified. It never lets the model see a raw Deal.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.calculation.commission import CommissionCalculator
from app.calculation.models import CommissionRunResult
from app.forecasting.engine import RollingForecastEngine
from app.forecasting.models import ForecastRunResult
from app.health.models import HealthReport
from app.health.scorer import DealHealthScorer
from app.narrative.client import NarrativeConfigError
from app.narrative.narrative_layer import NarrativeLayer
from app.scenarios.modeler import ScenarioModeler
from app.scenarios.models import ScenarioSet
from sample_data.demo_dataset import CLEAN_DEALS, CLEAN_QUOTAS, COMMISSION_RULES, HISTORICAL_DEALS, TODAY

app = FastAPI(title="Comp & Forecast Copilot API")

DEFAULT_NARRATIVE_QUESTION = (
    "Summarize this period's commission payouts, revenue forecast, and deal health "
    "for a sales leader, calling out anything that needs attention."
)


def _run_commission() -> CommissionRunResult:
    return CommissionCalculator().calculate(CLEAN_DEALS, CLEAN_QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def _run_forecast() -> ForecastRunResult:
    return RollingForecastEngine().forecast(
        CLEAN_DEALS, CLEAN_QUOTAS, COMMISSION_RULES,
        historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
    )


def _run_health() -> HealthReport:
    return DealHealthScorer().score(CLEAN_DEALS, CLEAN_QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def _run_scenarios() -> ScenarioSet:
    return ScenarioModeler().generate_scenarios(
        CLEAN_DEALS, CLEAN_QUOTAS, COMMISSION_RULES,
        historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
    )


@app.get("/api/commission", response_model=CommissionRunResult)
def get_commission() -> CommissionRunResult:
    return _run_commission()


@app.get("/api/forecast", response_model=ForecastRunResult)
def get_forecast() -> ForecastRunResult:
    return _run_forecast()


@app.get("/api/health", response_model=HealthReport)
def get_health() -> HealthReport:
    return _run_health()


@app.get("/api/scenarios", response_model=ScenarioSet)
def get_scenarios() -> ScenarioSet:
    return _run_scenarios()


class NarrativeRequest(BaseModel):
    question: str | None = None


class NarrativeResponse(BaseModel):
    narrative: str


@app.post("/api/narrative", response_model=NarrativeResponse)
def post_narrative(body: NarrativeRequest | None = None) -> NarrativeResponse:
    question = (body.question if body and body.question else None) or DEFAULT_NARRATIVE_QUESTION

    try:
        narrative_layer = NarrativeLayer()
    except NarrativeConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    answer = narrative_layer.ask(
        question,
        commission=_run_commission(),
        forecast=_run_forecast(),
        health=_run_health(),
        scenarios=_run_scenarios(),
    )
    return NarrativeResponse(narrative=answer)


# Mounted last so it never shadows the /api/* routes above — Starlette
# matches routes in registration order, and a root Mount is a catch-all.
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
