from datetime import date

import pytest

from app.calculation.commission import CommissionCalculator
from app.data_quality.gate import DataQualityError
from app.models.schemas import (
    AcceleratorTier,
    CommissionPlanRule,
    Deal,
    DealStage,
    RepQuota,
)
from sample_data.demo_dataset import COMMISSION_RULES, DEALS, QUOTAS, TODAY


def _closed_won(deal_id: str, rep_id: str, amount: float, close_date: date) -> Deal:
    return Deal(
        deal_id=deal_id,
        rep_id=rep_id,
        account_name=f"Account {deal_id}",
        amount=amount,
        stage=DealStage.CLOSED_WON,
        stage_entered_date=close_date,
        actual_close_date=close_date,
    )


def test_calculate_runs_gate_first_and_blocks_on_dirty_data():
    calculator = CommissionCalculator()
    with pytest.raises(DataQualityError):
        calculator.calculate(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_calculate_resolves_base_tier_and_full_audit_trail():
    # R-01 (Jordan Lee, Enterprise) quota is 250,000 for Q3 2026.
    deals = [_closed_won("D-2001", "R-01", 100000, date(2026, 7, 15))]
    calculator = CommissionCalculator()
    result = calculator.calculate(deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert len(result.payouts) == 1
    payout = result.payouts[0]

    # 100,000 / 250,000 = 40% attainment -> tier 0-100% @ 5%
    assert payout.rep_period_attainment_pct == pytest.approx(40.0)
    assert payout.tier_floor_pct == 0
    assert payout.tier_ceiling_pct == 100
    assert payout.tier_rate == 0.05
    assert payout.payout_amount == pytest.approx(5000.0)
    assert payout.plan_id == "P-ENT-01"
    assert payout.deal_id == "D-2001"
    assert "D-2001" in payout.trace
    assert "40.0%" in payout.trace
    assert result.total_payout_amount == pytest.approx(5000.0)


def test_calculate_applies_higher_cliff_tier_to_all_deals_once_threshold_crossed():
    deals = [
        _closed_won("D-2002", "R-01", 150000, date(2026, 7, 10)),
        _closed_won("D-2003", "R-01", 130000, date(2026, 8, 20)),
    ]
    # total = 280,000 / 250,000 = 112% attainment -> tier 100-150% @ 8%,
    # applied to BOTH deals (cliff tiering, see module docstring).
    calculator = CommissionCalculator()
    result = calculator.calculate(deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert len(result.payouts) == 2
    for payout in result.payouts:
        assert payout.rep_period_attainment_pct == pytest.approx(112.0)
        assert payout.tier_rate == 0.08

    payout_by_id = {p.deal_id: p for p in result.payouts}
    assert payout_by_id["D-2002"].payout_amount == pytest.approx(12000.0)
    assert payout_by_id["D-2003"].payout_amount == pytest.approx(10400.0)


def test_calculate_skips_deal_with_zero_quota_rep():
    zero_quota = RepQuota(
        rep_id="R-20",
        rep_name="Casey Kim",
        team="Mid-Market",
        quota_amount=0,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        start_date=date(2026, 7, 1),
    )
    plan = CommissionPlanRule(
        plan_id="P-MM-01",
        plan_name="Mid-Market Plan",
        applies_to_team="Mid-Market",
        tiers=[AcceleratorTier(attainment_floor_pct=0, attainment_ceiling_pct=None, rate=0.05)],
    )
    deals = [_closed_won("D-2004", "R-20", 20000, date(2026, 8, 1))]

    calculator = CommissionCalculator()
    result = calculator.calculate(deals, [zero_quota], [plan], as_of_date=TODAY)

    assert result.payouts == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason_code == "ZERO_QUOTA_ATTAINMENT_UNDEFINED"


def test_calculate_skips_deal_when_no_plan_for_team():
    quota = RepQuota(
        rep_id="R-21",
        rep_name="Drew Park",
        team="SMB",
        quota_amount=50000,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        start_date=date(2026, 7, 1),
    )
    deals = [_closed_won("D-2005", "R-21", 20000, date(2026, 8, 1))]

    calculator = CommissionCalculator()
    result = calculator.calculate(deals, [quota], COMMISSION_RULES, as_of_date=TODAY)

    assert result.payouts == []
    assert result.skipped[0].reason_code == "NO_PLAN_FOR_TEAM"


def test_calculate_skips_deal_outside_any_quota_period():
    # R-01's only quota period is Q3 2026 (Jul-Sep); this deal closes in December.
    deals = [_closed_won("D-2006", "R-01", 20000, date(2026, 12, 1))]

    calculator = CommissionCalculator()
    result = calculator.calculate(deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert result.payouts == []
    assert result.skipped[0].reason_code == "NO_QUOTA_PERIOD_FOR_DEAL"


def test_check_clawbacks_flags_deal_that_flips_to_closed_lost_within_window():
    won_deal = [_closed_won("D-2007", "R-01", 50000, date(2026, 7, 5))]
    calculator = CommissionCalculator()
    result = calculator.calculate(won_deal, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)
    payout = result.payouts[0]

    flipped_deal = Deal(
        deal_id="D-2007",
        rep_id="R-01",
        account_name="Account D-2007",
        amount=50000,
        stage=DealStage.CLOSED_LOST,
        stage_entered_date=date(2026, 7, 5),
        actual_close_date=date(2026, 8, 1),  # 27 days later, within the 90-day window
    )

    flags = calculator.check_clawbacks([flipped_deal], [payout], as_of_date=date(2026, 8, 2))

    assert len(flags) == 1
    flag = flags[0]
    assert flag.deal_id == "D-2007"
    assert flag.days_since_close == 27
    assert flag.payout_amount == payout.payout_amount
    assert "not reversed" in flag.message.lower()


def test_check_clawbacks_ignores_flip_outside_window():
    won_deal = [_closed_won("D-2008", "R-01", 50000, date(2026, 7, 5))]
    calculator = CommissionCalculator()
    result = calculator.calculate(won_deal, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)
    payout = result.payouts[0]

    flipped_deal = Deal(
        deal_id="D-2008",
        rep_id="R-01",
        account_name="Account D-2008",
        amount=50000,
        stage=DealStage.CLOSED_LOST,
        stage_entered_date=date(2026, 7, 5),
        actual_close_date=date(2026, 12, 1),  # well beyond the 90-day window
    )

    flags = calculator.check_clawbacks([flipped_deal], [payout], as_of_date=date(2026, 12, 2))
    assert flags == []


def test_check_clawbacks_ignores_deals_still_closed_won():
    won_deal = [_closed_won("D-2009", "R-01", 50000, date(2026, 7, 5))]
    calculator = CommissionCalculator()
    result = calculator.calculate(won_deal, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)
    payout = result.payouts[0]

    flags = calculator.check_clawbacks(won_deal, [payout], as_of_date=TODAY)
    assert flags == []
