"""
The Scenario Modeler — Layer 2 of the architecture described in the
README.

Two capabilities, both built on top of engines that already exist rather
than duplicating their math:

1. `generate_scenarios()` — best/commit/worst revenue scenarios, built by
   re-running the Rolling Forecast Engine with its stage weights scaled
   up or down (see BEST_CASE_MULTIPLIER / WORST_CASE_MULTIPLIER).
   "Commit" is just the forecast engine's own default weights — the case
   the business would actually commit to a board.

2. `what_if()` — re-runs the Commission Calculator and Rolling Forecast
   Engine with a hypothetical quota or commission-rate change, and diffs
   the result against the unmodified baseline.

Key design decision — a quota or rate change moves commission, not
revenue:
    The Rolling Forecast Engine's math depends only on deal stage,
    amount, and historical win rate — never on quota or commission rate.
    So `what_if()` will, correctly, show a $0 forecast_delta for a pure
    quota or rate change: those levers change what the business pays a
    rep for a dollar of revenue, not how much revenue the pipeline is
    projected to produce. That's not a gap in this code; it's a claim
    about how the business actually works, and the what-if trace states
    it explicitly rather than leaving the reader to wonder why the
    forecast number didn't move.
"""

from __future__ import annotations

from datetime import date

from app.calculation.commission import CommissionCalculator
from app.data_quality.gate import DataQualityGate
from app.forecasting.engine import STAGE_WEIGHTS, RollingForecastEngine
from app.models.schemas import CommissionPlanRule, Deal, DealStage, RepQuota
from app.scenarios.models import ScenarioSet, WhatIfResult

# Scaling applied to the forecast engine's default stage weights.
# Named constants, same spirit as the thresholds elsewhere in this repo.
BEST_CASE_MULTIPLIER = 1.3
WORST_CASE_MULTIPLIER = 0.7


class ScenarioModeler:
    def __init__(self, gate: DataQualityGate | None = None):
        self.gate = gate or DataQualityGate()
        self.forecast_engine = RollingForecastEngine(gate=self.gate)
        self.commission_calculator = CommissionCalculator(gate=self.gate)

    def generate_scenarios(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        historical_deals: list[Deal] | None = None,
        as_of_date: date | None = None,
    ) -> ScenarioSet:
        as_of_date = as_of_date or date.today()

        commit = self.forecast_engine.forecast(
            deals, quotas, rules, historical_deals=historical_deals, as_of_date=as_of_date,
        )
        best = self.forecast_engine.forecast(
            deals, quotas, rules, historical_deals=historical_deals, as_of_date=as_of_date,
            stage_weight_overrides=self._scaled_weights(BEST_CASE_MULTIPLIER),
        )
        worst = self.forecast_engine.forecast(
            deals, quotas, rules, historical_deals=historical_deals, as_of_date=as_of_date,
            stage_weight_overrides=self._scaled_weights(WORST_CASE_MULTIPLIER),
        )

        trace = (
            f"Scenario spread as of {as_of_date}: worst ${worst.blended_forecast:,.2f} "
            f"(stage weights x{WORST_CASE_MULTIPLIER}) -> commit ${commit.blended_forecast:,.2f} "
            f"(default stage weights) -> best ${best.blended_forecast:,.2f} "
            f"(stage weights x{BEST_CASE_MULTIPLIER})."
        )

        return ScenarioSet(as_of_date=as_of_date, best=best, commit=commit, worst=worst, trace=trace)

    def what_if(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        historical_deals: list[Deal] | None = None,
        as_of_date: date | None = None,
        quota_overrides: dict[str, float] | None = None,
        commission_rate_multiplier: float | None = None,
    ) -> WhatIfResult:
        as_of_date = as_of_date or date.today()

        baseline_commission = self.commission_calculator.calculate(deals, quotas, rules, as_of_date)
        baseline_forecast = self.forecast_engine.forecast(
            deals, quotas, rules, historical_deals=historical_deals, as_of_date=as_of_date,
        )

        adjusted_quotas = self._apply_quota_overrides(quotas, quota_overrides)
        adjusted_rules = self._apply_rate_multiplier(rules, commission_rate_multiplier)

        adjusted_commission = self.commission_calculator.calculate(
            deals, adjusted_quotas, adjusted_rules, as_of_date,
        )
        adjusted_forecast = self.forecast_engine.forecast(
            deals, adjusted_quotas, adjusted_rules,
            historical_deals=historical_deals, as_of_date=as_of_date,
        )

        commission_delta = round(
            adjusted_commission.total_payout_amount - baseline_commission.total_payout_amount, 2
        )
        forecast_delta = round(
            adjusted_forecast.blended_forecast - baseline_forecast.blended_forecast, 2
        )

        changes = []
        if quota_overrides:
            changes.append(f"quota changes for {sorted(quota_overrides.keys())}")
        if commission_rate_multiplier is not None:
            changes.append(f"commission rates x{commission_rate_multiplier}")
        change_desc = " and ".join(changes) if changes else "no changes"

        trace = (
            f"What-if ({change_desc}) as of {as_of_date}: total commission payout moved from "
            f"${baseline_commission.total_payout_amount:,.2f} to "
            f"${adjusted_commission.total_payout_amount:,.2f} ({commission_delta:+,.2f}). "
            f"Revenue forecast moved from ${baseline_forecast.blended_forecast:,.2f} to "
            f"${adjusted_forecast.blended_forecast:,.2f} ({forecast_delta:+,.2f}) — quota and "
            "commission-rate changes affect what's paid, not the revenue forecast itself."
        )

        return WhatIfResult(
            as_of_date=as_of_date,
            baseline_commission=baseline_commission,
            adjusted_commission=adjusted_commission,
            baseline_forecast=baseline_forecast,
            adjusted_forecast=adjusted_forecast,
            commission_delta=commission_delta,
            forecast_delta=forecast_delta,
            trace=trace,
        )

    @staticmethod
    def _scaled_weights(multiplier: float) -> dict[DealStage, float]:
        return {
            stage: max(0.0, min(1.0, weight * multiplier))
            for stage, weight in STAGE_WEIGHTS.items()
        }

    @staticmethod
    def _apply_quota_overrides(
        quotas: list[RepQuota], overrides: dict[str, float] | None
    ) -> list[RepQuota]:
        if not overrides:
            return quotas
        return [
            q.model_copy(update={"quota_amount": overrides[q.rep_id]}) if q.rep_id in overrides else q
            for q in quotas
        ]

    @staticmethod
    def _apply_rate_multiplier(
        rules: list[CommissionPlanRule], multiplier: float | None
    ) -> list[CommissionPlanRule]:
        if multiplier is None:
            return rules
        return [
            r.model_copy(update={
                "tiers": [
                    t.model_copy(update={"rate": round(t.rate * multiplier, 4)}) for t in r.tiers
                ],
            })
            for r in rules
        ]
