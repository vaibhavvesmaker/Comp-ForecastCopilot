"""
The Data Quality Gate.

This is Layer 0 of the architecture described in the README: nothing in
the Calculation Engine runs until data has passed through here. The gate
does not "clean" data silently — it reports exactly what's wrong and
blocks on anything that would make a downstream number unexplainable.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.data_quality.rules import ALL_CHECKS, Severity, ValidationIssue
from app.models.schemas import CommissionPlanRule, Deal, RepQuota


class ValidationReport(BaseModel):
    as_of_date: date
    total_issues: int
    blocker_count: int
    warning_count: int
    issues: list[ValidationIssue]

    @property
    def passed(self) -> bool:
        """True only if there are zero BLOCKER-severity issues.

        WARNINGs (stale deals, zero-quota reps) do not block calculation —
        they're surfaced to the user as part of deal-health/data-hygiene
        reporting, but they don't make a commission or forecast number
        wrong the way a missing close date or an orphaned deal would.
        """
        return self.blocker_count == 0


class DataQualityGate:
    def __init__(self, checks=None):
        self.checks = checks if checks is not None else ALL_CHECKS

    def run(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        as_of_date: date | None = None,
    ) -> ValidationReport:
        as_of_date = as_of_date or date.today()

        all_issues: list[ValidationIssue] = []
        for check in self.checks:
            all_issues.extend(
                check(deals=deals, quotas=quotas, rules=rules, as_of_date=as_of_date)
            )

        blocker_count = sum(1 for i in all_issues if i.severity == Severity.BLOCKER)
        warning_count = sum(1 for i in all_issues if i.severity == Severity.WARNING)

        return ValidationReport(
            as_of_date=as_of_date,
            total_issues=len(all_issues),
            blocker_count=blocker_count,
            warning_count=warning_count,
            issues=all_issues,
        )

    def run_or_raise(
        self,
        deals: list[Deal],
        quotas: list[RepQuota],
        rules: list[CommissionPlanRule],
        as_of_date: date | None = None,
    ) -> ValidationReport:
        """Convenience method for the Calculation Engine to call.

        Raises if there are any BLOCKER issues, so the calculation engine
        never has to remember to check `.passed` itself — it just calls
        this and proceeds only if it returns.
        """
        report = self.run(deals, quotas, rules, as_of_date)
        if not report.passed:
            blocker_messages = "\n".join(
                f"  - [{i.rule_code}] {i.message}"
                for i in report.issues
                if i.severity == Severity.BLOCKER
            )
            raise DataQualityError(
                f"{report.blocker_count} blocking data quality issue(s) found. "
                f"Calculation halted.\n{blocker_messages}"
            )
        return report


class DataQualityError(Exception):
    """Raised when blocker-level issues prevent calculation from running."""
    pass
