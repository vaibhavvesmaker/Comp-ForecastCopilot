"""
Export the calculation engine's output to a Google Sheet.

This is the "analyst-facing model" data foundation from README section 4:
a plain export of what the deterministic engines (Layers 0-2) already
computed, structured into tabs an analyst can build PivotTables and
XLOOKUP formulas on top of by hand. This script writes RAW VALUES only —
no formulas, no formatting — that part is the analyst-skill work the
README calls out as genuinely manual.

Tabs written (one per worksheet, created if missing):
    - Quota & Attainment      — roster, quota, and period attainment % per rep
    - Comp Plan Rules         — every plan's tiers, one row per tier
    - Commission Calculator   — every payout's full audit trail, plus skipped deals
    - Pipeline Forecast       — per-deal stage-weighted forecast, plus the blend summary
    - Variance & Capacity     — best/commit/worst scenario spread, plus a per-rep
                                pipeline-coverage/health rollup (there's no single
                                "workforce capacity" engine in this codebase yet, so
                                this tab is assembled from the Scenario Modeler and
                                Deal Health Scorer outputs — see _variance_capacity_rows)

Nothing is exported until the Data Quality Gate passes. The demo dataset
in sample_data/demo_dataset.py (DEALS) is intentionally dirty, so a
default run against it will fail the gate and print the validation
report instead of exporting anything — that's the correct behavior, not
a bug. Pass --demo to run against a small, clean, curated sample instead,
so you can see every tab populated end to end.

-------------------------------------------------------------------------
Google service account setup (one-time):
-------------------------------------------------------------------------
1. In Google Cloud Console, create (or pick) a project, then enable the
   "Google Sheets API" and "Google Drive API" for it.
2. Create a Service Account (IAM & Admin -> Service Accounts -> Create),
   then create a JSON key for it and download it.
3. Share the target Google Sheet with the service account's email
   address (it looks like `something@project-id.iam.gserviceaccount.com`),
   giving it Editor access. If you want this script to CREATE the sheet
   instead of updating an existing one, share the destination Drive
   folder with that same service account email instead.
4. Point this script at the downloaded JSON key, either via
   --credentials /path/to/key.json or by setting the
   GOOGLE_APPLICATION_CREDENTIALS environment variable to that path.

-------------------------------------------------------------------------
Usage:
-------------------------------------------------------------------------
    # Update an existing sheet (must be shared with the service account):
    python scripts/export_to_sheets.py --sheet-id <SPREADSHEET_ID> --demo

    # Create a new sheet titled "Comp Forecast Copilot - Demo":
    python scripts/export_to_sheets.py --create "Comp Forecast Copilot - Demo" --demo

    # Run against the real (intentionally dirty) demo dataset, to see the
    # Data Quality Gate block the export and explain why:
    python scripts/export_to_sheets.py --sheet-id <SPREADSHEET_ID>
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

from app.calculation.commission import CommissionCalculator
from app.calculation.models import CommissionRunResult
from app.data_quality.gate import DataQualityError
from app.forecasting.engine import RollingForecastEngine
from app.forecasting.models import ForecastRunResult
from app.health.models import HealthReport
from app.health.scorer import DealHealthScorer
from app.models.schemas import AcceleratorTier, CommissionPlanRule, Deal, DealStage, RepQuota
from app.scenarios.modeler import ScenarioModeler
from app.scenarios.models import ScenarioSet
from sample_data.demo_dataset import COMMISSION_RULES, DEALS, HISTORICAL_DEALS, QUOTAS, TODAY

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ---------------------------------------------------------------------------
# A small, clean sample used for --demo, so a first-time run has data to
# show in every tab without first having to fix the intentionally-dirty
# demo dataset. Distinct from sample_data.demo_dataset.DEALS on purpose.
# ---------------------------------------------------------------------------
def _clean_demo_deals() -> list[Deal]:
    return [
        Deal(
            deal_id="EXP-001", rep_id="R-01", account_name="Wayne Enterprises",
            amount=120000, stage=DealStage.CLOSED_WON,
            stage_entered_date=date(2026, 7, 10), actual_close_date=date(2026, 7, 20),
        ),
        Deal(
            deal_id="EXP-002", rep_id="R-01", account_name="Northwind Traders",
            amount=42000, stage=DealStage.PROPOSAL,
            stage_entered_date=date(2026, 9, 5), expected_close_date=date(2026, 9, 30),
            last_activity_date=date(2026, 9, 14),
        ),
        Deal(
            deal_id="EXP-003", rep_id="R-02", account_name="Globex LLC",
            amount=27500, stage=DealStage.NEGOTIATION,
            stage_entered_date=date(2026, 9, 1), expected_close_date=date(2026, 9, 25),
            last_activity_date=date(2026, 9, 10),
        ),
    ]


def _clean_demo_quotas() -> list[RepQuota]:
    return [
        RepQuota(
            rep_id="R-01", rep_name="Jordan Lee", team="Enterprise",
            quota_amount=250000, period_start=date(2026, 7, 1),
            period_end=date(2026, 9, 30), start_date=date(2024, 3, 1),
        ),
        RepQuota(
            rep_id="R-02", rep_name="Sam Rivera", team="Enterprise",
            quota_amount=150000, period_start=date(2026, 7, 1),
            period_end=date(2026, 9, 30), start_date=date(2026, 1, 1),
        ),
    ]


# ---------------------------------------------------------------------------
# Row builders — each returns a list[list] ready for worksheet.update(),
# header row included.
# ---------------------------------------------------------------------------
def _quota_attainment_rows(quotas: list[RepQuota], commission: CommissionRunResult) -> list[list]:
    attainment_by_rep = {p.rep_id: p.rep_period_attainment_pct for p in commission.payouts}
    rows = [["rep_id", "rep_name", "team", "quota_amount", "period_start", "period_end",
              "period_attainment_pct"]]
    for q in quotas:
        attainment = attainment_by_rep.get(q.rep_id)
        rows.append([
            q.rep_id, q.rep_name, q.team, q.quota_amount,
            q.period_start.isoformat(), q.period_end.isoformat(),
            f"{attainment:.1f}" if attainment is not None else "no closed_won deals this period",
        ])
    return rows


def _comp_plan_rules_rows(rules: list[CommissionPlanRule]) -> list[list]:
    rows = [["plan_id", "plan_name", "applies_to_team", "tier_floor_pct", "tier_ceiling_pct",
              "tier_rate", "clawback_window_days"]]
    for plan in rules:
        for tier in plan.tiers:
            rows.append([
                plan.plan_id, plan.plan_name, plan.applies_to_team,
                tier.attainment_floor_pct,
                tier.attainment_ceiling_pct if tier.attainment_ceiling_pct is not None else "no ceiling",
                tier.rate, plan.clawback_window_days,
            ])
    return rows


def _commission_rows(commission: CommissionRunResult) -> list[list]:
    rows = [["deal_id", "rep_id", "rep_name", "team", "plan_id", "tier_floor_pct",
              "tier_ceiling_pct", "tier_rate", "deal_amount", "payout_amount", "trace"]]
    for p in commission.payouts:
        rows.append([
            p.deal_id, p.rep_id, p.rep_name, p.team, p.plan_id, p.tier_floor_pct,
            p.tier_ceiling_pct if p.tier_ceiling_pct is not None else "no ceiling",
            p.tier_rate, p.deal_amount, p.payout_amount, p.trace,
        ])
    rows.append([])
    rows.append(["TOTAL", "", "", "", "", "", "", "", "", commission.total_payout_amount, ""])
    if commission.skipped:
        rows.append([])
        rows.append(["-- skipped deals --"])
        rows.append(["deal_id", "rep_id", "reason_code", "message"])
        for s in commission.skipped:
            rows.append([s.deal_id, s.rep_id, s.reason_code, s.message])
    return rows


def _forecast_rows(forecast: ForecastRunResult) -> list[list]:
    rows = [["deal_id", "rep_id", "stage", "deal_amount", "stage_weight", "weighted_amount"]]
    for f in forecast.deal_forecasts:
        rows.append([f.deal_id, f.rep_id, f.stage.value, f.deal_amount, f.stage_weight, f.weighted_amount])
    rows.append([])
    rows.append(["stage_weighted_total", forecast.stage_weighted_total])
    rows.append(["open_pipeline_total", forecast.open_pipeline_total])
    rows.append(["historical_win_rate",
                  f"{forecast.historical_win_rate:.1%}" if forecast.historical_win_rate is not None else "n/a"])
    rows.append(["trend_pipeline_estimate", forecast.trend_pipeline_estimate
                  if forecast.trend_pipeline_estimate is not None else "n/a"])
    rows.append(["blended_forecast", forecast.blended_forecast])
    return rows


def _variance_capacity_rows(
    scenarios: ScenarioSet, health: HealthReport, quotas: list[RepQuota]
) -> list[list]:
    rows = [["-- revenue scenario spread --"]]
    rows.append(["case", "blended_forecast"])
    rows.append(["worst", scenarios.worst.blended_forecast])
    rows.append(["commit", scenarios.commit.blended_forecast])
    rows.append(["best", scenarios.best.blended_forecast])
    rows.append([])
    rows.append(["-- per-rep pipeline coverage & deal health --"])
    rows.append(["rep_id", "open_deal_count", "at_risk_deal_count", "open_pipeline_amount", "quota_amount"])

    scores_by_rep: dict[str, list] = {}
    for s in health.scores:
        scores_by_rep.setdefault(s.rep_id, []).append(s)

    open_amount_by_rep: dict[str, float] = {}
    for f in scenarios.commit.deal_forecasts:
        open_amount_by_rep[f.rep_id] = open_amount_by_rep.get(f.rep_id, 0) + f.deal_amount

    for q in quotas:
        rep_scores = scores_by_rep.get(q.rep_id, [])
        rows.append([
            q.rep_id,
            len(rep_scores),
            sum(1 for s in rep_scores if s.is_at_risk),
            open_amount_by_rep.get(q.rep_id, 0),
            q.quota_amount,
        ])
    return rows


# ---------------------------------------------------------------------------
# Sheets I/O
# ---------------------------------------------------------------------------
def _get_client(credentials_path: str | None) -> gspread.Client:
    path = credentials_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise SystemExit(
            "No service account credentials found. Pass --credentials /path/to/key.json "
            "or set GOOGLE_APPLICATION_CREDENTIALS. See this script's docstring for setup."
        )
    creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return gspread.authorize(creds)


def _write_tab(spreadsheet: gspread.Spreadsheet, title: str, rows: list[list]) -> None:
    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=max(len(rows), 10) + 5, cols=15)
    if rows:
        ws.update(values=rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--credentials", help="Path to the service account JSON key.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sheet-id", help="Spreadsheet ID of an existing sheet to update.")
    target.add_argument("--create", metavar="TITLE", help="Create a new spreadsheet with this title.")
    parser.add_argument(
        "--demo", action="store_true",
        help="Use a small, clean, curated sample instead of the intentionally-dirty demo dataset.",
    )
    args = parser.parse_args()

    if args.demo:
        deals, quotas, rules = _clean_demo_deals(), _clean_demo_quotas(), COMMISSION_RULES
    else:
        deals, quotas, rules = DEALS, QUOTAS, COMMISSION_RULES

    try:
        commission = CommissionCalculator().calculate(deals, quotas, rules, as_of_date=TODAY)
        forecast = RollingForecastEngine().forecast(
            deals, quotas, rules, historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
        )
        health = DealHealthScorer().score(deals, quotas, rules, as_of_date=TODAY)
        scenarios = ScenarioModeler().generate_scenarios(
            deals, quotas, rules, historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
        )
    except DataQualityError as e:
        print("Export blocked: the Data Quality Gate found blocking issues.\n", file=sys.stderr)
        print(str(e), file=sys.stderr)
        print(
            "\nNothing was written to Google Sheets. Fix the data, or re-run with --demo "
            "to see a full export using a clean curated sample.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = _get_client(args.credentials)
    spreadsheet = client.create(args.create) if args.create else client.open_by_key(args.sheet_id)

    _write_tab(spreadsheet, "Quota & Attainment", _quota_attainment_rows(quotas, commission))
    _write_tab(spreadsheet, "Comp Plan Rules", _comp_plan_rules_rows(rules))
    _write_tab(spreadsheet, "Commission Calculator", _commission_rows(commission))
    _write_tab(spreadsheet, "Pipeline Forecast", _forecast_rows(forecast))
    _write_tab(spreadsheet, "Variance & Capacity", _variance_capacity_rows(scenarios, health, quotas))

    print(f"Exported successfully: {spreadsheet.url}")


if __name__ == "__main__":
    main()
