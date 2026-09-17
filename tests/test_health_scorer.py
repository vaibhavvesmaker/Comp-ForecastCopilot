from datetime import date, timedelta

import pytest

from app.data_quality.gate import DataQualityError
from app.health.scorer import DealHealthScorer
from app.models.schemas import CommissionPlanRule, AcceleratorTier, Deal, DealStage, RepQuota
from sample_data.demo_dataset import DEALS, QUOTAS, COMMISSION_RULES, TODAY

REP_QUOTAS = [
    RepQuota(
        rep_id="R-01", rep_name="Jordan Lee", team="Enterprise",
        quota_amount=100000, period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30), start_date=date(2024, 3, 1),
    ),
]
PLAN_RULES = [
    CommissionPlanRule(
        plan_id="P-ENT-01", plan_name="Enterprise Standard Plan",
        applies_to_team="Enterprise",
        tiers=[AcceleratorTier(attainment_floor_pct=0, attainment_ceiling_pct=None, rate=0.05)],
    ),
]


def _open_deal(deal_id, stage_entered_date, last_activity_date, stage=DealStage.QUALIFICATION):
    return Deal(
        deal_id=deal_id, rep_id="R-01", account_name=f"Account {deal_id}", amount=10000,
        stage=stage, stage_entered_date=stage_entered_date,
        expected_close_date=TODAY + timedelta(days=30),
        last_activity_date=last_activity_date,
    )


def test_score_runs_gate_first_and_blocks_on_dirty_data():
    scorer = DealHealthScorer()
    with pytest.raises(DataQualityError):
        scorer.score(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_healthy_deal_scores_100_and_is_not_at_risk():
    deal = _open_deal("HD-001", TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    scorer = DealHealthScorer()
    report = scorer.score([deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    assert len(report.scores) == 1
    result = report.scores[0]
    assert result.score == 100
    assert result.is_at_risk is False
    assert result.flags == []
    assert report.at_risk_count == 0


def test_stale_stage_only_deducts_30_points():
    deal = _open_deal("HD-002", TODAY - timedelta(days=35), TODAY - timedelta(days=1))
    scorer = DealHealthScorer()
    report = scorer.score([deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    result = report.scores[0]
    assert result.score == 70
    assert result.is_at_risk is True
    assert {f.rule_code for f in result.flags} == {"STALE_STAGE_DURATION"}


def test_stale_activity_only_deducts_30_points():
    deal = _open_deal("HD-003", TODAY - timedelta(days=5), TODAY - timedelta(days=20))
    scorer = DealHealthScorer()
    report = scorer.score([deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    result = report.scores[0]
    assert result.score == 70
    assert {f.rule_code for f in result.flags} == {"STALE_ACTIVITY"}


def test_no_logged_activity_deducts_40_points():
    deal = _open_deal("HD-004", TODAY - timedelta(days=5), None)
    scorer = DealHealthScorer()
    report = scorer.score([deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    result = report.scores[0]
    assert result.score == 60
    assert result.days_since_last_activity is None
    assert {f.rule_code for f in result.flags} == {"NO_LOGGED_ACTIVITY"}


def test_multiple_risk_factors_stack_penalties():
    deal = _open_deal("HD-005", TODAY - timedelta(days=40), None)
    scorer = DealHealthScorer()
    report = scorer.score([deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    result = report.scores[0]
    # STALE_STAGE_DURATION (30) + NO_LOGGED_ACTIVITY (40) = 70 penalty
    assert result.score == 30
    assert {f.rule_code for f in result.flags} == {"STALE_STAGE_DURATION", "NO_LOGGED_ACTIVITY"}
    assert report.at_risk_count == 1


def test_closed_deals_are_excluded_from_scoring():
    open_deal = _open_deal("HD-006", TODAY - timedelta(days=5), TODAY - timedelta(days=1))
    closed_deal = Deal(
        deal_id="HD-007", rep_id="R-01", account_name="Closed Account", amount=10000,
        stage=DealStage.CLOSED_WON, stage_entered_date=TODAY - timedelta(days=60),
        actual_close_date=TODAY - timedelta(days=5),
    )
    scorer = DealHealthScorer()
    report = scorer.score([open_deal, closed_deal], REP_QUOTAS, PLAN_RULES, as_of_date=TODAY)

    assert len(report.scores) == 1
    assert report.scores[0].deal_id == "HD-006"
