from datetime import date

import pytest

from app.data_quality.gate import DataQualityError
from app.forecasting.engine import RollingForecastEngine
from app.models.schemas import Deal, DealStage
from sample_data.demo_dataset import COMMISSION_RULES, DEALS, HISTORICAL_DEALS, QUOTAS, TODAY

OPEN_DEALS = [
    Deal(
        deal_id="F-001", rep_id="R-01", account_name="A", amount=100000,
        stage=DealStage.PROSPECTING, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
    Deal(
        deal_id="F-002", rep_id="R-01", account_name="B", amount=50000,
        stage=DealStage.QUALIFICATION, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
    Deal(
        deal_id="F-003", rep_id="R-02", account_name="C", amount=80000,
        stage=DealStage.PROPOSAL, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
    Deal(
        deal_id="F-004", rep_id="R-02", account_name="D", amount=40000,
        stage=DealStage.NEGOTIATION, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    ),
]

# Expected: 100000*0.10 + 50000*0.25 + 80000*0.50 + 40000*0.75
EXPECTED_STAGE_WEIGHTED_TOTAL = 10000 + 12500 + 40000 + 30000  # 92500
EXPECTED_OPEN_PIPELINE_TOTAL = 100000 + 50000 + 80000 + 40000  # 270000


def test_forecast_runs_gate_first_and_blocks_on_dirty_data():
    engine = RollingForecastEngine()
    with pytest.raises(DataQualityError):
        engine.forecast(DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_forecast_applies_default_stage_weights_and_traces_each_deal():
    engine = RollingForecastEngine()
    result = engine.forecast(OPEN_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert result.stage_weighted_total == pytest.approx(EXPECTED_STAGE_WEIGHTED_TOTAL)
    assert result.open_pipeline_total == pytest.approx(EXPECTED_OPEN_PIPELINE_TOTAL)
    assert len(result.deal_forecasts) == 4

    by_id = {f.deal_id: f for f in result.deal_forecasts}
    assert by_id["F-001"].stage_weight == pytest.approx(0.10)
    assert by_id["F-001"].weighted_amount == pytest.approx(10000)
    assert by_id["F-004"].stage_weight == pytest.approx(0.75)
    assert by_id["F-004"].weighted_amount == pytest.approx(30000)
    assert "F-001" in by_id["F-001"].trace


def test_forecast_excludes_closed_deals_from_stage_weighting():
    closed_deal = Deal(
        deal_id="F-005", rep_id="R-01", account_name="E", amount=20000,
        stage=DealStage.CLOSED_WON, stage_entered_date=date(2026, 9, 1),
        actual_close_date=date(2026, 9, 10),
    )
    deals = OPEN_DEALS + [closed_deal]

    engine = RollingForecastEngine()
    result = engine.forecast(deals, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert result.stage_weighted_total == pytest.approx(EXPECTED_STAGE_WEIGHTED_TOTAL)
    assert len(result.deal_forecasts) == 4
    assert "F-005" not in {f.deal_id for f in result.deal_forecasts}


def test_forecast_with_no_historical_data_blends_to_stage_weighted_total():
    engine = RollingForecastEngine()
    result = engine.forecast(OPEN_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    assert result.historical_win_rate is None
    assert result.trend_pipeline_estimate is None
    assert result.blended_forecast == pytest.approx(result.stage_weighted_total)


def test_forecast_blends_stage_weighted_and_historical_trend():
    engine = RollingForecastEngine()
    result = engine.forecast(
        OPEN_DEALS, QUOTAS, COMMISSION_RULES,
        historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
    )

    # win rate = (80000+30000+60000+90000) / (80000+45000+30000+60000+25000+90000)
    expected_win_rate = 260000 / 330000
    expected_trend_estimate = EXPECTED_OPEN_PIPELINE_TOTAL * expected_win_rate
    expected_blended = 0.70 * EXPECTED_STAGE_WEIGHTED_TOTAL + 0.30 * expected_trend_estimate

    assert result.historical_win_rate == pytest.approx(expected_win_rate)
    assert result.trend_pipeline_estimate == pytest.approx(expected_trend_estimate, rel=1e-3)
    assert result.blended_forecast == pytest.approx(expected_blended, rel=1e-3)
    assert "trend estimate" in result.trace


def test_forecast_with_no_open_deals_is_zero_not_an_error():
    closed_deal = Deal(
        deal_id="F-006", rep_id="R-01", account_name="F", amount=20000,
        stage=DealStage.CLOSED_WON, stage_entered_date=date(2026, 9, 1),
        actual_close_date=date(2026, 9, 10),
    )

    engine = RollingForecastEngine()
    result = engine.forecast(
        [closed_deal], QUOTAS, COMMISSION_RULES,
        historical_deals=HISTORICAL_DEALS, as_of_date=TODAY,
    )

    assert result.deal_forecasts == []
    assert result.stage_weighted_total == 0
    assert result.trend_pipeline_estimate == pytest.approx(0)
    assert result.blended_forecast == pytest.approx(0)


def test_forecast_is_stateless_and_recomputes_fresh_each_call():
    engine = RollingForecastEngine()
    first = engine.forecast(OPEN_DEALS, QUOTAS, COMMISSION_RULES, as_of_date=TODAY)

    advanced_deal = OPEN_DEALS[0].model_copy(update={"stage": DealStage.NEGOTIATION})
    second = engine.forecast(
        [advanced_deal] + OPEN_DEALS[1:], QUOTAS, COMMISSION_RULES, as_of_date=TODAY
    )

    assert first.blended_forecast != second.blended_forecast
