from datetime import date

import pytest

from app.data_quality.gate import DataQualityError, DataQualityGate
from app.data_quality.rules import Severity
from app.models.schemas import AcceleratorTier, CommissionPlanRule
from sample_data.demo_dataset import COMMISSION_RULES, DEALS, QUOTAS, TODAY


def test_gate_flags_all_expected_blockers_on_demo_dataset():
    gate = DataQualityGate()
    report = gate.run(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    blocker_codes = {i.rule_code for i in report.issues if i.severity == Severity.BLOCKER}

    assert "MISSING_ACTUAL_CLOSE_DATE" in blocker_codes  # D-1002
    assert "MISSING_EXPECTED_CLOSE_DATE" in blocker_codes  # D-1003
    assert "ORPHANED_DEAL" in blocker_codes  # D-1004
    assert not report.passed


def test_gate_flags_expected_warnings_on_demo_dataset():
    gate = DataQualityGate()
    report = gate.run(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    warning_codes = {i.rule_code for i in report.issues if i.severity == Severity.WARNING}

    assert "STALE_STAGE_DURATION" in warning_codes  # D-1005
    assert "NO_LOGGED_ACTIVITY" in warning_codes  # D-1006
    assert "ZERO_QUOTA" in warning_codes  # R-02


def test_gate_passes_on_clean_subset():
    gate = DataQualityGate()
    clean_deals = [DEALS[0]]  # D-1001 only — the healthy one
    report = gate.run(clean_deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert report.blocker_count == 0
    assert report.passed


def test_run_or_raise_blocks_calculation_on_dirty_data():
    gate = DataQualityGate()
    with pytest.raises(DataQualityError):
        gate.run_or_raise(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_run_or_raise_succeeds_on_clean_data():
    gate = DataQualityGate()
    clean_deals = [DEALS[0]]
    report = gate.run_or_raise(clean_deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)
    assert report.passed


def test_tier_gap_detection():
    bad_plan = CommissionPlanRule(
        plan_id="P-BAD",
        plan_name="Broken Plan",
        applies_to_team="Test",
        tiers=[
            AcceleratorTier(attainment_floor_pct=0, attainment_ceiling_pct=90, rate=0.05),
            # Gap: 90-100 is uncovered.
            AcceleratorTier(attainment_floor_pct=100, attainment_ceiling_pct=None, rate=0.10),
        ],
    )
    gate = DataQualityGate()
    report = gate.run([], QUOTAS, [bad_plan], as_of_date=TODAY)
    codes = {i.rule_code for i in report.issues}
    assert "TIER_GAP" in codes
