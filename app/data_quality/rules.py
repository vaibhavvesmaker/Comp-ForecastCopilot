"""
Individual data-quality checks.

Each check is a pure function: (deals, quotas, rules, as_of_date) -> list[ValidationIssue]

Keeping them isolated like this means each rule can be unit-tested on its
own, and it's easy to add a new check without touching the orchestration
logic in gate.py. This mirrors how the calculation engine's audit trail
works: every issue traces to exactly one rule and one record.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel

from app.models.schemas import CommissionPlanRule, Deal, DealStage, OPEN_STAGES, RepQuota

# Thresholds are named constants, not magic numbers, so they're easy to
# defend or tune in an interview conversation.
STALE_STAGE_DAYS = 30          # a deal sitting in one stage this long is "stuck"
STALE_ACTIVITY_DAYS = 14       # no CRM activity logged in this window is a warning sign
MISSING_EXPECTED_CLOSE_GRACE_DAYS = 0  # open deals must always have an expected close date


class Severity(str, Enum):
    BLOCKER = "blocker"   # calculation engine will not run until this is fixed
    WARNING = "warning"   # calculation proceeds, but this is surfaced to the user


class ValidationIssue(BaseModel):
    severity: Severity
    rule_code: str
    message: str
    deal_id: Optional[str] = None
    rep_id: Optional[str] = None


def check_terminal_deals_have_close_dates(deals: list[Deal], **_) -> list[ValidationIssue]:
    issues = []
    for d in deals:
        if d.stage in (DealStage.CLOSED_WON, DealStage.CLOSED_LOST) and not d.actual_close_date:
            issues.append(
                ValidationIssue(
                    severity=Severity.BLOCKER,
                    rule_code="MISSING_ACTUAL_CLOSE_DATE",
                    message=f"Deal {d.deal_id} is {d.stage.value} but has no actual_close_date. "
                    "Commission calculation cannot resolve a payout period without this.",
                    deal_id=d.deal_id,
                    rep_id=d.rep_id,
                )
            )
    return issues


def check_open_deals_have_expected_close_dates(deals: list[Deal], **_) -> list[ValidationIssue]:
    issues = []
    for d in deals:
        if d.stage in OPEN_STAGES and not d.expected_close_date:
            issues.append(
                ValidationIssue(
                    severity=Severity.BLOCKER,
                    rule_code="MISSING_EXPECTED_CLOSE_DATE",
                    message=f"Open deal {d.deal_id} has no expected_close_date. "
                    "The forecast engine cannot bucket this deal into a period.",
                    deal_id=d.deal_id,
                    rep_id=d.rep_id,
                )
            )
    return issues


def check_deals_reference_known_reps(
    deals: list[Deal], quotas: list[RepQuota], **_
) -> list[ValidationIssue]:
    known_reps = {q.rep_id for q in quotas}
    issues = []
    for d in deals:
        if d.rep_id not in known_reps:
            issues.append(
                ValidationIssue(
                    severity=Severity.BLOCKER,
                    rule_code="ORPHANED_DEAL",
                    message=f"Deal {d.deal_id} references rep_id '{d.rep_id}', "
                    "which does not exist in the roster/quota table. "
                    "Commission cannot be attributed.",
                    deal_id=d.deal_id,
                    rep_id=d.rep_id,
                )
            )
    return issues


def check_duplicate_deal_ids(deals: list[Deal], **_) -> list[ValidationIssue]:
    seen: dict[str, int] = {}
    for d in deals:
        seen[d.deal_id] = seen.get(d.deal_id, 0) + 1
    return [
        ValidationIssue(
            severity=Severity.BLOCKER,
            rule_code="DUPLICATE_DEAL_ID",
            message=f"Deal ID '{deal_id}' appears {count} times. "
            "This will double-count revenue and commission.",
            deal_id=deal_id,
        )
        for deal_id, count in seen.items()
        if count > 1
    ]


def check_stale_stage_duration(
    deals: list[Deal], as_of_date: date, **_
) -> list[ValidationIssue]:
    issues = []
    for d in deals:
        if d.stage in OPEN_STAGES:
            days_in_stage = (as_of_date - d.stage_entered_date).days
            if days_in_stage > STALE_STAGE_DAYS:
                issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        rule_code="STALE_STAGE_DURATION",
                        message=f"Deal {d.deal_id} has been in '{d.stage.value}' for "
                        f"{days_in_stage} days (threshold: {STALE_STAGE_DAYS}). "
                        "Flagged for deal-health review; not excluded from forecast.",
                        deal_id=d.deal_id,
                        rep_id=d.rep_id,
                    )
                )
    return issues


def check_stale_activity(deals: list[Deal], as_of_date: date, **_) -> list[ValidationIssue]:
    issues = []
    for d in deals:
        if d.stage in OPEN_STAGES:
            if d.last_activity_date is None:
                issues.append(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        rule_code="NO_LOGGED_ACTIVITY",
                        message=f"Open deal {d.deal_id} has no logged CRM activity at all.",
                        deal_id=d.deal_id,
                        rep_id=d.rep_id,
                    )
                )
            else:
                days_since = (as_of_date - d.last_activity_date).days
                if days_since > STALE_ACTIVITY_DAYS:
                    issues.append(
                        ValidationIssue(
                            severity=Severity.WARNING,
                            rule_code="STALE_ACTIVITY",
                            message=f"Deal {d.deal_id} has no CRM activity in {days_since} days "
                            f"(threshold: {STALE_ACTIVITY_DAYS}).",
                            deal_id=d.deal_id,
                            rep_id=d.rep_id,
                        )
                    )
    return issues


def check_reps_have_positive_quota(quotas: list[RepQuota], **_) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity=Severity.WARNING,
            rule_code="ZERO_QUOTA",
            message=f"Rep {q.rep_id} ({q.rep_name}) has a quota of 0 for "
            f"{q.period_start} - {q.period_end}. Attainment % is undefined for this rep.",
            rep_id=q.rep_id,
        )
        for q in quotas
        if q.quota_amount == 0
    ]


def check_commission_tier_coverage(rules: list[CommissionPlanRule], **_) -> list[ValidationIssue]:
    """Confirms each plan's tiers cover 0% attainment upward with no gaps.

    A gap here means some attainment percentage would resolve to NO rate,
    which is exactly the kind of silent bug that produces a wrong,
    unexplainable commission payout.
    """
    issues = []
    for plan in rules:
        sorted_tiers = sorted(plan.tiers, key=lambda t: t.attainment_floor_pct)
        if sorted_tiers[0].attainment_floor_pct != 0:
            issues.append(
                ValidationIssue(
                    severity=Severity.BLOCKER,
                    rule_code="TIER_GAP_AT_ZERO",
                    message=f"Plan '{plan.plan_name}' does not start coverage at 0% attainment.",
                )
            )
        for prev, curr in zip(sorted_tiers, sorted_tiers[1:]):
            if prev.attainment_ceiling_pct is None:
                issues.append(
                    ValidationIssue(
                        severity=Severity.BLOCKER,
                        rule_code="TIER_ORDER_ERROR",
                        message=f"Plan '{plan.plan_name}' has a tier with no ceiling "
                        "that is not the top tier.",
                    )
                )
            elif prev.attainment_ceiling_pct != curr.attainment_floor_pct:
                issues.append(
                    ValidationIssue(
                        severity=Severity.BLOCKER,
                        rule_code="TIER_GAP",
                        message=f"Plan '{plan.plan_name}' has a gap or overlap between "
                        f"{prev.attainment_ceiling_pct}% and {curr.attainment_floor_pct}%.",
                    )
                )
    return issues


# The full registry the Gate runs, in order. Order doesn't affect
# correctness here (checks are independent) but keeping BLOCKER-heavy
# structural checks first makes the report easier to read top-to-bottom.
ALL_CHECKS = [
    check_duplicate_deal_ids,
    check_deals_reference_known_reps,
    check_terminal_deals_have_close_dates,
    check_open_deals_have_expected_close_dates,
    check_commission_tier_coverage,
    check_reps_have_positive_quota,
    check_stale_stage_duration,
    check_stale_activity,
]
