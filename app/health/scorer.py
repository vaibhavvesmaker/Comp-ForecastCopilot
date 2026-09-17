"""
The Deal Health Scorer — Layer 2 of the architecture described in the
README.

Flags open deals that are stalling, using the exact same thresholds the
Data Quality Gate already warns about (STALE_STAGE_DAYS,
STALE_ACTIVITY_DAYS from app/data_quality/rules.py) rather than
redefining separate "health" thresholds. One number means one thing
everywhere in this system: a deal the gate warns about and a deal this
scorer flags as at-risk are the same deal, for the same reason, every
time — no second, silently-diverging definition of "stale" to keep in
sync.

Like the Commission Calculator and Rolling Forecast Engine, this calls
DataQualityGate.run_or_raise() before doing anything else.
"""

from __future__ import annotations

from datetime import date

from app.data_quality.gate import DataQualityGate
from app.data_quality.rules import STALE_ACTIVITY_DAYS, STALE_STAGE_DAYS
from app.health.models import DealHealthScore, HealthFlag, HealthReport
from app.models.schemas import CommissionPlanRule, Deal, OPEN_STAGES, RepQuota

# Points deducted per risk factor. Named constants, same spirit as the
# thresholds themselves — easy to defend or tune in an interview.
STALE_STAGE_PENALTY = 30
NO_ACTIVITY_PENALTY = 40
STALE_ACTIVITY_PENALTY = 30


class DealHealthScorer:
    def __init__(self, gate: DataQualityGate | None = None):
        self.gate = gate or DataQualityGate()

    def score(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        as_of_date: date | None = None,
    ) -> HealthReport:
        as_of_date = as_of_date or date.today()

        # Nothing below this line runs on unvalidated data.
        self.gate.run_or_raise(deals, quotas, rules, as_of_date)

        scores: list[DealHealthScore] = []
        for d in deals:
            if d.stage not in OPEN_STAGES:
                continue  # health scoring only applies to still-open deals

            flags: list[HealthFlag] = []
            days_in_stage = (as_of_date - d.stage_entered_date).days
            days_since_activity = (
                (as_of_date - d.last_activity_date).days if d.last_activity_date else None
            )

            if days_in_stage > STALE_STAGE_DAYS:
                flags.append(HealthFlag(
                    rule_code="STALE_STAGE_DURATION",
                    message=f"In '{d.stage.value}' for {days_in_stage} days "
                    f"(threshold: {STALE_STAGE_DAYS}).",
                ))

            if d.last_activity_date is None:
                flags.append(HealthFlag(
                    rule_code="NO_LOGGED_ACTIVITY",
                    message="No CRM activity logged at all.",
                ))
            elif days_since_activity > STALE_ACTIVITY_DAYS:
                flags.append(HealthFlag(
                    rule_code="STALE_ACTIVITY",
                    message=f"No CRM activity in {days_since_activity} days "
                    f"(threshold: {STALE_ACTIVITY_DAYS}).",
                ))

            penalty_by_code = {
                "STALE_STAGE_DURATION": STALE_STAGE_PENALTY,
                "NO_LOGGED_ACTIVITY": NO_ACTIVITY_PENALTY,
                "STALE_ACTIVITY": STALE_ACTIVITY_PENALTY,
            }
            total_penalty = sum(penalty_by_code[f.rule_code] for f in flags)
            health_score = max(0, 100 - total_penalty)

            reasons = "; ".join(f.message for f in flags) if flags else "no risk factors flagged"
            trace = (
                f"Deal {d.deal_id} ({d.stage.value}, rep {d.rep_id}) scored "
                f"{health_score}/100 as of {as_of_date}: {reasons}"
            )

            scores.append(DealHealthScore(
                deal_id=d.deal_id,
                rep_id=d.rep_id,
                stage=d.stage,
                days_in_stage=days_in_stage,
                days_since_last_activity=days_since_activity,
                score=health_score,
                is_at_risk=len(flags) > 0,
                flags=flags,
                trace=trace,
            ))

        return HealthReport(
            as_of_date=as_of_date,
            scores=scores,
            at_risk_count=sum(1 for s in scores if s.is_at_risk),
        )
