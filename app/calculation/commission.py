"""
The Commission Calculator — Layer 2 of the architecture described in the
README.

This is deterministic, rule-driven code: nothing here involves an AI
model or any judgment call that isn't traceable to a specific deal, plan,
or tier. Every dollar paid out carries a full trace back to the deal,
the plan, the tier, and the rate that produced it — the same
audit-trail philosophy the Data Quality Gate (Layer 0) is built around.
`calculate()` calls that gate's `run_or_raise()` first; nothing below it
runs on unvalidated data.

Key design decision — "cliff" tiering, not marginal/incremental tiering:
    A rep's period attainment % is computed once per rep per quota
    period, from the total of all their closed_won deals that closed
    within that period. That single attainment % is then used to
    resolve the tier (and therefore the rate) applied to EVERY
    closed_won deal of theirs in that period — not just the dollars that
    pushed them over a threshold. This mirrors how many real comp plans
    pay out (a rep who finishes the quarter at 118% attainment gets the
    100-150% tier rate on the whole quarter, not a blended marginal
    rate), and it keeps the audit trail a single sentence per rep-period
    ("attainment X% -> tier Y -> rate Z") instead of a per-dollar
    waterfall. Marginal/incremental tiering is a legitimate alternative
    model and could be added later as a second resolution strategy
    without changing the shape of the audit trail.

Key design decision — clawbacks are flagged, never auto-reversed:
    If a previously paid deal moves to closed_lost within the plan's
    clawback_window_days, `check_clawbacks()` surfaces it as a
    ClawbackFlag. It never mutates or reverses the original
    CommissionPayout — reversing pay is a decision for a human, not this
    engine.
"""

from __future__ import annotations

from datetime import date

from app.calculation.models import ClawbackFlag, CommissionPayout, CommissionRunResult, SkippedDeal
from app.data_quality.gate import DataQualityGate
from app.models.schemas import AcceleratorTier, CommissionPlanRule, Deal, DealStage, RepQuota


class CommissionCalculator:
    def __init__(self, gate: DataQualityGate | None = None):
        self.gate = gate or DataQualityGate()

    def calculate(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        as_of_date: date | None = None,
    ) -> CommissionRunResult:
        as_of_date = as_of_date or date.today()

        # Nothing below this line runs on unvalidated data.
        self.gate.run_or_raise(deals, quotas, rules, as_of_date)

        closed_won = [d for d in deals if d.stage == DealStage.CLOSED_WON]

        payouts: list[CommissionPayout] = []
        skipped: list[SkippedDeal] = []

        for deal in closed_won:
            quota = self._find_quota_for_deal(deal, quotas)
            if quota is None:
                skipped.append(SkippedDeal(
                    deal_id=deal.deal_id,
                    rep_id=deal.rep_id,
                    reason_code="NO_QUOTA_PERIOD_FOR_DEAL",
                    message=f"No quota period covers deal {deal.deal_id}'s close date "
                    f"({deal.actual_close_date}) for rep {deal.rep_id}.",
                ))
                continue

            if quota.quota_amount == 0:
                skipped.append(SkippedDeal(
                    deal_id=deal.deal_id,
                    rep_id=deal.rep_id,
                    reason_code="ZERO_QUOTA_ATTAINMENT_UNDEFINED",
                    message=f"Rep {deal.rep_id} has a quota of 0 for "
                    f"{quota.period_start} - {quota.period_end}; attainment % is "
                    "undefined, so no tier can be resolved for this deal.",
                ))
                continue

            plan = self._find_plan_for_team(quota.team, rules)
            if plan is None:
                skipped.append(SkippedDeal(
                    deal_id=deal.deal_id,
                    rep_id=deal.rep_id,
                    reason_code="NO_PLAN_FOR_TEAM",
                    message=f"No CommissionPlanRule found for team '{quota.team}' "
                    f"(rep {deal.rep_id}). Deal {deal.deal_id} cannot be resolved.",
                ))
                continue

            attainment_pct = self._period_attainment_pct(deal.rep_id, quota, deals)
            tier = self._resolve_tier(plan.tiers, attainment_pct)

            payout_amount = round(deal.amount * tier.rate, 2)
            ceiling_desc = (
                f"{tier.attainment_ceiling_pct}%"
                if tier.attainment_ceiling_pct is not None
                else "no ceiling"
            )

            payouts.append(CommissionPayout(
                deal_id=deal.deal_id,
                rep_id=deal.rep_id,
                rep_name=quota.rep_name,
                team=quota.team,
                plan_id=plan.plan_id,
                plan_name=plan.plan_name,
                period_start=quota.period_start,
                period_end=quota.period_end,
                close_date=deal.actual_close_date,
                rep_period_attainment_pct=attainment_pct,
                tier_floor_pct=tier.attainment_floor_pct,
                tier_ceiling_pct=tier.attainment_ceiling_pct,
                tier_rate=tier.rate,
                deal_amount=deal.amount,
                payout_amount=payout_amount,
                clawback_window_days=plan.clawback_window_days,
                trace=(
                    f"Deal {deal.deal_id} (${deal.amount:,.2f}) closed_won for rep "
                    f"{deal.rep_id} ({quota.rep_name}, {quota.team}) on "
                    f"{deal.actual_close_date}. Rep's attainment for "
                    f"{quota.period_start}-{quota.period_end} was {attainment_pct:.1f}% "
                    f"of ${quota.quota_amount:,.2f} quota, resolving to plan "
                    f"'{plan.plan_name}' tier [{tier.attainment_floor_pct}%-{ceiling_desc}] "
                    f"at a {tier.rate:.1%} rate -> payout ${payout_amount:,.2f}."
                ),
            ))

        return CommissionRunResult(as_of_date=as_of_date, payouts=payouts, skipped=skipped)

    def check_clawbacks(
        self,
        current_deals: list[Deal],
        previous_payouts: list[CommissionPayout],
        as_of_date: date | None = None,
    ) -> list[ClawbackFlag]:
        as_of_date = as_of_date or date.today()
        deals_by_id = {d.deal_id: d for d in current_deals}

        flags: list[ClawbackFlag] = []
        for payout in previous_payouts:
            deal = deals_by_id.get(payout.deal_id)
            if deal is None or deal.stage != DealStage.CLOSED_LOST:
                continue

            flipped_close_date = deal.actual_close_date or as_of_date
            days_since_close = (flipped_close_date - payout.close_date).days
            if days_since_close < 0:
                # Data anomaly: the recorded closed_lost date is earlier
                # than the commission's original close date. Nothing sane
                # to compute here — leave it for the gate to catch instead
                # of guessing at a clawback.
                continue

            if days_since_close <= payout.clawback_window_days:
                flags.append(ClawbackFlag(
                    deal_id=deal.deal_id,
                    rep_id=deal.rep_id,
                    plan_id=payout.plan_id,
                    original_close_date=payout.close_date,
                    flipped_close_date=flipped_close_date,
                    days_since_close=days_since_close,
                    clawback_window_days=payout.clawback_window_days,
                    payout_amount=payout.payout_amount,
                    message=(
                        f"Deal {deal.deal_id} was paid ${payout.payout_amount:,.2f} in "
                        f"commission on close ({payout.close_date}), but moved to "
                        f"closed_lost on {flipped_close_date} — {days_since_close} day(s) "
                        f"later, within the plan's {payout.clawback_window_days}-day "
                        "clawback window. Flagged for review; commission not reversed."
                    ),
                ))
        return flags

    @staticmethod
    def _find_quota_for_deal(deal: Deal, quotas: list[RepQuota]) -> RepQuota | None:
        if deal.actual_close_date is None:
            return None
        for q in quotas:
            if q.rep_id == deal.rep_id and q.period_start <= deal.actual_close_date <= q.period_end:
                return q
        return None

    @staticmethod
    def _find_plan_for_team(team: str, rules: list[CommissionPlanRule]) -> CommissionPlanRule | None:
        for r in rules:
            if r.applies_to_team == team:
                return r
        return None

    @staticmethod
    def _period_attainment_pct(rep_id: str, quota: RepQuota, all_deals: list[Deal]) -> float:
        total_closed_won = sum(
            d.amount
            for d in all_deals
            if d.rep_id == rep_id
            and d.stage == DealStage.CLOSED_WON
            and d.actual_close_date is not None
            and quota.period_start <= d.actual_close_date <= quota.period_end
        )
        return (total_closed_won / quota.quota_amount) * 100

    @staticmethod
    def _resolve_tier(tiers: list[AcceleratorTier], attainment_pct: float) -> AcceleratorTier:
        sorted_tiers = sorted(tiers, key=lambda t: t.attainment_floor_pct)
        for tier in sorted_tiers:
            if attainment_pct < tier.attainment_floor_pct:
                continue
            if tier.attainment_ceiling_pct is None or attainment_pct < tier.attainment_ceiling_pct:
                return tier
        # check_commission_tier_coverage (Layer 0) guarantees tiers cover
        # 0%-> with no gaps before this code ever runs, so this line is
        # unreachable in practice — but never silently return nothing.
        return sorted_tiers[-1]
