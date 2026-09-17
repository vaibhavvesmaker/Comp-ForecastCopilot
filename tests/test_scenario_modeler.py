from datetime import date

import pytest

from app.data_quality.gate import DataQualityError
from app.models.schemas import Deal, DealStage
from app.scenarios.modeler import BEST_CASE_MULTIPLIER, WORST_CASE_MULTIPLIER, ScenarioModeler
from sample_data.demo_dataset import COMMISSION_RULES, DEALS, QUOTAS, TODAY

ENTERPRISE_QUOTA = QUOTAS[0]  # R-01, Jordan Lee, Enterprise, quota 250,000, Q3 2026

SCENARIO_DEALS = [
    Deal(
        deal_id="S-101", rep_id="R-01", account_name="A", amount=50000,
        stage=DealStage.PROSPECTING, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
    Deal(
        deal_id="S-102", rep_id="R-01", account_name="B", amount=50000,
        stage=DealStage.NEGOTIATION, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
]
# commit: 50000*0.10 + 50000*0.75 = 42500
EXPECTED_COMMIT_TOTAL = 5000 + 37500
# best: weights scaled x1.3 -> 0.13, 0.975
EXPECTED_BEST_TOTAL = 50000 * (0.10 * BEST_CASE_MULTIPLIER) + 50000 * (0.75 * BEST_CASE_MULTIPLIER)
# worst: weights scaled x0.7 -> 0.07, 0.525
EXPECTED_WORST_TOTAL = 50000 * (0.10 * WORST_CASE_MULTIPLIER) + 50000 * (0.75 * WORST_CASE_MULTIPLIER)


def test_generate_scenarios_runs_gate_first_and_blocks_on_dirty_data():
    modeler = ScenarioModeler()
    with pytest.raises(DataQualityError):
        modeler.generate_scenarios(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_generate_scenarios_orders_worst_commit_best_correctly():
    modeler = ScenarioModeler()
    scenarios = modeler.generate_scenarios(SCENARIO_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert scenarios.worst.blended_forecast == pytest.approx(EXPECTED_WORST_TOTAL)
    assert scenarios.commit.blended_forecast == pytest.approx(EXPECTED_COMMIT_TOTAL)
    assert scenarios.best.blended_forecast == pytest.approx(EXPECTED_BEST_TOTAL)
    assert scenarios.worst.blended_forecast < scenarios.commit.blended_forecast < scenarios.best.blended_forecast


def test_generate_scenarios_does_not_mutate_default_stage_weights():
    modeler = ScenarioModeler()
    modeler.generate_scenarios(SCENARIO_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    from app.forecasting.engine import STAGE_WEIGHTS
    assert STAGE_WEIGHTS[DealStage.PROSPECTING] == 0.10
    assert STAGE_WEIGHTS[DealStage.NEGOTIATION] == 0.75


WHAT_IF_DEALS = [
    Deal(
        deal_id="W-01", rep_id="R-01", account_name="Closed Co", amount=100000,
        stage=DealStage.CLOSED_WON, stage_entered_date=date(2026, 7, 10),
        actual_close_date=date(2026, 7, 15),
    ),
    Deal(
        deal_id="W-02", rep_id="R-01", account_name="Open Co", amount=20000,
        stage=DealStage.NEGOTIATION, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
]


def test_what_if_runs_gate_first_and_blocks_on_dirty_data():
    modeler = ScenarioModeler()
    with pytest.raises(DataQualityError):
        modeler.what_if(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY, quota_overrides={"R-01": 1})


def test_what_if_quota_override_changes_commission_but_not_forecast():
    modeler = ScenarioModeler()
    result = modeler.what_if(
        WHAT_IF_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY,
        quota_overrides={"R-01": 50000},
    )

    # baseline: 100,000 / 250,000 = 40% -> tier 0-100% @ 5% -> $5,000
    assert result.baseline_commission.total_payout_amount == pytest.approx(5000.0)
    # adjusted: 100,000 / 50,000 = 200% -> tier 150%+ @ 12% -> $12,000
    assert result.adjusted_commission.total_payout_amount == pytest.approx(12000.0)
    assert result.commission_delta == pytest.approx(7000.0)

    # the open deal's stage/amount didn't change, so the forecast doesn't move
    assert result.baseline_forecast.blended_forecast == result.adjusted_forecast.blended_forecast
    assert result.forecast_delta == 0
    assert "revenue forecast itself" in result.trace.lower()


def test_what_if_commission_rate_multiplier_changes_commission_but_not_forecast():
    modeler = ScenarioModeler()
    result = modeler.what_if(
        WHAT_IF_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY,
        commission_rate_multiplier=2.0,
    )

    # baseline tier rate 5% -> $5,000; doubled rate 10% -> $10,000
    assert result.baseline_commission.total_payout_amount == pytest.approx(5000.0)
    assert result.adjusted_commission.total_payout_amount == pytest.approx(10000.0)
    assert result.commission_delta == pytest.approx(5000.0)
    assert result.forecast_delta == 0


def test_what_if_original_quotas_and_rules_are_not_mutated():
    modeler = ScenarioModeler()
    modeler.what_if(
        WHAT_IF_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY,
        quota_overrides={"R-01": 50000}, commission_rate_multiplier=2.0,
    )

    assert ENTERPRISE_QUOTA.quota_amount == 250000
    assert COMMISSION_RULES[0].tiers[0].rate == 0.05
