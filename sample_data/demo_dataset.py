"""
Intentionally imperfect sample dataset.

This is deliberately NOT clean data — it's built to trip every rule in
app/data_quality/rules.py so the gate's output is demonstrable on its own,
before the calculation engine exists. Good demo data for a live walkthrough:
run this through the gate and read the report top to bottom.
"""

from datetime import date

from app.models.schemas import AcceleratorTier, CommissionPlanRule, Deal, DealStage, RepQuota

TODAY = date(2026, 9, 16)

DEALS = [
    # Clean, healthy open deal.
    Deal(
        deal_id="D-1001",
        rep_id="R-01",
        account_name="Northwind Traders",
        amount=42000,
        stage=DealStage.PROPOSAL,
        stage_entered_date=date(2026, 9, 5),
        expected_close_date=date(2026, 9, 30),
        last_activity_date=date(2026, 9, 14),
    ),
    # BLOCKER: closed_won with no actual_close_date.
    Deal(
        deal_id="D-1002",
        rep_id="R-01",
        account_name="Acme Corp",
        amount=18000,
        stage=DealStage.CLOSED_WON,
        stage_entered_date=date(2026, 8, 20),
        actual_close_date=None,
    ),
    # BLOCKER: open deal with no expected_close_date.
    Deal(
        deal_id="D-1003",
        rep_id="R-02",
        account_name="Globex LLC",
        amount=27500,
        stage=DealStage.NEGOTIATION,
        stage_entered_date=date(2026, 9, 1),
        expected_close_date=None,
        last_activity_date=date(2026, 9, 10),
    ),
    # BLOCKER: references a rep who doesn't exist in the roster.
    Deal(
        deal_id="D-1004",
        rep_id="R-99",
        account_name="Initech",
        amount=9000,
        stage=DealStage.QUALIFICATION,
        stage_entered_date=date(2026, 9, 8),
        expected_close_date=date(2026, 10, 15),
        last_activity_date=date(2026, 9, 12),
    ),
    # WARNING: stale stage duration (in Proposal for 45+ days).
    Deal(
        deal_id="D-1005",
        rep_id="R-02",
        account_name="Umbrella Group",
        amount=61000,
        stage=DealStage.PROPOSAL,
        stage_entered_date=date(2026, 7, 25),
        expected_close_date=date(2026, 9, 20),
        last_activity_date=date(2026, 8, 30),
    ),
    # WARNING: no logged activity at all.
    Deal(
        deal_id="D-1006",
        rep_id="R-01",
        account_name="Soylent Inc",
        amount=15500,
        stage=DealStage.QUALIFICATION,
        stage_entered_date=date(2026, 9, 12),
        expected_close_date=date(2026, 10, 5),
        last_activity_date=None,
    ),
]

QUOTAS = [
    RepQuota(
        rep_id="R-01",
        rep_name="Jordan Lee",
        team="Enterprise",
        quota_amount=250000,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        start_date=date(2024, 3, 1),
    ),
    RepQuota(
        rep_id="R-02",
        rep_name="Sam Rivera",
        team="Mid-Market",
        quota_amount=0,  # WARNING: zero quota
        period_start=date(2026, 7, 1),
        period_end=date(2026, 9, 30),
        start_date=date(2026, 8, 1),
    ),
]

COMMISSION_RULES = [
    CommissionPlanRule(
        plan_id="P-ENT-01",
        plan_name="Enterprise Standard Plan",
        applies_to_team="Enterprise",
        tiers=[
            AcceleratorTier(attainment_floor_pct=0, attainment_ceiling_pct=100, rate=0.05),
            AcceleratorTier(attainment_floor_pct=100, attainment_ceiling_pct=150, rate=0.08),
            AcceleratorTier(attainment_floor_pct=150, attainment_ceiling_pct=None, rate=0.12),
        ],
    ),
]
