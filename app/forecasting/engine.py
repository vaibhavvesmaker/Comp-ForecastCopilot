"""
The Rolling Forecast Engine — Layer 2 of the architecture described in
the README.

Like the Commission Calculator, this is deterministic code that calls
the Data Quality Gate (`DataQualityGate.run_or_raise()`) before doing
anything else, and every number it produces traces back to the specific
deals and weights that produced it.

Key design decision — stateless, recalculated on demand:
    `forecast()` holds no memory of previous calls. There is no cached
    "this month's forecast" anywhere in this class — every call
    recomputes the whole thing fresh from whatever deals and historical
    data it's handed. This is the literal meaning of "rolling forecast,
    not a static snapshot" from the README: call it again five minutes
    later with one deal's stage changed, and the number moves, with a
    fresh trace explaining why.

Key design decision — two independent estimates, blended, not multiplied:
    The engine computes two separate numbers and averages them, rather
    than layering one probability on top of another:
      1. Stage-weighted pipeline total — each OPEN deal's amount times a
         default probability weight for its current stage (see
         STAGE_WEIGHTS below).
      2. Historical trend estimate — the empirical win rate from closed
         historical deals (won $ / total closed $) applied to the total
         (unweighted) open pipeline dollar amount.
    Multiplying the historical win rate into the already stage-weighted
    total would double-count probability into the same dollar. Blending
    two independently-derived estimates (default 70% stage-weighted /
    30% trend, see BLEND_WEIGHT_STAGE / BLEND_WEIGHT_TREND) keeps each
    number's provenance legible in the audit trail instead of compounding
    assumptions no one can untangle later.

`forecast()` accepts an optional `stage_weight_overrides` dict to swap
in a different set of stage probabilities for a single call, without
touching the module-level STAGE_WEIGHTS defaults everyone else relies
on. This exists for the Scenario Modeler's best/worst-case scenarios
(app/scenarios/modeler.py) — it re-runs this same engine with scaled
weights rather than re-implementing the stage-weighting math elsewhere.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.data_quality.gate import DataQualityGate
from app.forecasting.models import DealForecast, ForecastRunResult
from app.models.schemas import CommissionPlanRule, Deal, DealStage, OPEN_STAGES, RepQuota, TERMINAL_STAGES

# Default stage-weighted probabilities. Named constants so they're easy
# to defend or tune in an interview conversation, same spirit as the
# thresholds in app/data_quality/rules.py.
STAGE_WEIGHTS: dict[DealStage, float] = {
    DealStage.PROSPECTING: 0.10,
    DealStage.QUALIFICATION: 0.25,
    DealStage.PROPOSAL: 0.50,
    DealStage.NEGOTIATION: 0.75,
}

BLEND_WEIGHT_STAGE = 0.70
BLEND_WEIGHT_TREND = 0.30


class RollingForecastEngine:
    def __init__(self, gate: DataQualityGate | None = None):
        self.gate = gate or DataQualityGate()

    def forecast(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        historical_deals: list[Deal] | None = None,
        as_of_date: date | None = None,
        stage_weight_overrides: dict[DealStage, float] | None = None,
    ) -> ForecastRunResult:
        as_of_date = as_of_date or date.today()
        historical_deals = historical_deals or []
        stage_weights = stage_weight_overrides or STAGE_WEIGHTS

        # Nothing below this line runs on unvalidated data.
        self.gate.run_or_raise(deals, quotas, rules, as_of_date)

        open_deals = [d for d in deals if d.stage in OPEN_STAGES]

        deal_forecasts: list[DealForecast] = []
        stage_weighted_total = 0.0
        open_pipeline_total = 0.0

        for d in open_deals:
            weight = stage_weights[d.stage]
            weighted_amount = round(d.amount * weight, 2)
            stage_weighted_total += weighted_amount
            open_pipeline_total += d.amount

            deal_forecasts.append(DealForecast(
                deal_id=d.deal_id,
                rep_id=d.rep_id,
                stage=d.stage,
                deal_amount=d.amount,
                stage_weight=weight,
                weighted_amount=weighted_amount,
                trace=(
                    f"Deal {d.deal_id} (${d.amount:,.2f}) in stage '{d.stage.value}' "
                    f"weighted at {weight:.0%} -> ${weighted_amount:,.2f} pipeline contribution."
                ),
            ))

        stage_weighted_total = round(stage_weighted_total, 2)
        open_pipeline_total = round(open_pipeline_total, 2)

        historical_win_rate, trend_pipeline_estimate = self._historical_trend(
            historical_deals, open_pipeline_total
        )

        if trend_pipeline_estimate is None:
            blended_forecast = stage_weighted_total
            trace = (
                f"Stage-weighted pipeline across {len(deal_forecasts)} open deal(s) = "
                f"${stage_weighted_total:,.2f}. No historical closed deals were provided, "
                "so the blended forecast equals the stage-weighted total."
            )
        else:
            blended_forecast = round(
                BLEND_WEIGHT_STAGE * stage_weighted_total
                + BLEND_WEIGHT_TREND * trend_pipeline_estimate,
                2,
            )
            trace = (
                f"Stage-weighted pipeline across {len(deal_forecasts)} open deal(s) = "
                f"${stage_weighted_total:,.2f}. Historical win rate = "
                f"{historical_win_rate:.1%} applied to ${open_pipeline_total:,.2f} total open "
                f"pipeline -> trend estimate ${trend_pipeline_estimate:,.2f}. Blended forecast = "
                f"{BLEND_WEIGHT_STAGE:.0%} x stage-weighted + {BLEND_WEIGHT_TREND:.0%} x trend "
                f"= ${blended_forecast:,.2f}."
            )

        return ForecastRunResult(
            as_of_date=as_of_date,
            deal_forecasts=deal_forecasts,
            stage_weighted_total=stage_weighted_total,
            open_pipeline_total=open_pipeline_total,
            historical_win_rate=historical_win_rate,
            trend_pipeline_estimate=trend_pipeline_estimate,
            blended_forecast=blended_forecast,
            trace=trace,
        )

    @staticmethod
    def _historical_trend(
        historical_deals: list[Deal], open_pipeline_total: float
    ) -> tuple[Optional[float], Optional[float]]:
        closed = [d for d in historical_deals if d.stage in TERMINAL_STAGES]
        total_closed_amount = sum(d.amount for d in closed)
        if not closed or total_closed_amount == 0:
            return None, None

        won_amount = sum(d.amount for d in closed if d.stage == DealStage.CLOSED_WON)
        win_rate = won_amount / total_closed_amount
        trend_estimate = round(open_pipeline_total * win_rate, 2)
        return win_rate, trend_estimate
