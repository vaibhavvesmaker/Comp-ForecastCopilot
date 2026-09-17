from datetime import date
from unittest.mock import MagicMock

import pytest

from app.calculation.commission import CommissionCalculator
from app.forecasting.engine import RollingForecastEngine
from app.models.schemas import Deal, DealStage
from app.narrative.client import AnthropicNarrativeClient, NarrativeConfigError
from app.narrative.narrative_layer import NarrativeLayer
from sample_data.demo_dataset import COMMISSION_RULES, QUOTAS, TODAY


def _fake_client(reply: str = "This is a mocked narrative.") -> MagicMock:
    client = MagicMock(spec=AnthropicNarrativeClient)
    client.generate.return_value = reply
    return client


def _sample_commission_result():
    deal = Deal(
        deal_id="N-001", rep_id="R-01", account_name="Narrative Co", amount=100000,
        stage=DealStage.CLOSED_WON, stage_entered_date=date(2026, 7, 10),
        actual_close_date=date(2026, 7, 15),
    )
    return CommissionCalculator().calculate([deal], QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def _sample_forecast_result():
    deal = Deal(
        deal_id="N-002", rep_id="R-01", account_name="Pipeline Co", amount=50000,
        stage=DealStage.NEGOTIATION, stage_entered_date=date(2026, 9, 1),
        expected_close_date=date(2026, 10, 1),
    )
    return RollingForecastEngine().forecast([deal], QUOTAS, COMMISSION_RULES, as_of_date=TODAY)


def test_anthropic_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(NarrativeConfigError):
        AnthropicNarrativeClient()


def test_anthropic_client_generate_calls_sdk_and_extracts_text(monkeypatch):
    fake_text_block = MagicMock(type="text", text="Hello from the model.")
    fake_response = MagicMock(content=[fake_text_block])
    fake_messages = MagicMock()
    fake_messages.create.return_value = fake_response
    fake_sdk_client = MagicMock(messages=fake_messages)

    monkeypatch.setattr("app.narrative.client.anthropic.Anthropic", lambda api_key: fake_sdk_client)

    client = AnthropicNarrativeClient(api_key="fake-key-for-test")
    result = client.generate("system prompt", "user prompt")

    assert result == "Hello from the model."
    fake_messages.create.assert_called_once()
    call_kwargs = fake_messages.create.call_args.kwargs
    assert call_kwargs["system"] == "system prompt"
    assert call_kwargs["messages"] == [{"role": "user", "content": "user prompt"}]


def test_explain_commission_run_cites_deal_from_audit_trail():
    result = _sample_commission_result()
    fake = _fake_client("Deal N-001 paid $5,000.00 at the 5% tier.")

    narrative = NarrativeLayer(client=fake)
    output = narrative.explain_commission_run(result)

    assert output == "Deal N-001 paid $5,000.00 at the 5% tier."
    fake.generate.assert_called_once()
    system_arg, prompt_arg = fake.generate.call_args.args
    assert "N-001" in prompt_arg
    assert "5000.0" in prompt_arg or "5000" in prompt_arg


def test_explain_forecast_variance_includes_both_snapshots():
    previous = _sample_forecast_result()
    current = _sample_forecast_result()
    fake = _fake_client()

    narrative = NarrativeLayer(client=fake)
    narrative.explain_forecast_variance(previous, current)

    _, prompt_arg = fake.generate.call_args.args
    assert "previous_forecast" in prompt_arg
    assert "current_forecast" in prompt_arg
    assert "N-002" in prompt_arg


def test_ask_requires_at_least_one_context_result():
    narrative = NarrativeLayer(client=_fake_client())
    with pytest.raises(ValueError):
        narrative.ask("Why did commission go up?")


def test_ask_passes_question_and_context_to_client():
    result = _sample_commission_result()
    fake = _fake_client("Because deal N-001 closed at a higher tier.")

    narrative = NarrativeLayer(client=fake)
    answer = narrative.ask("Why did commission go up?", commission=result)

    assert answer == "Because deal N-001 closed at a higher tier."
    _, prompt_arg = fake.generate.call_args.args
    assert "Why did commission go up?" in prompt_arg
    assert "N-001" in prompt_arg


def test_ask_rejects_raw_deal_objects_passed_as_context():
    # Type hints alone don't stop a caller from passing a raw Deal where a
    # CommissionRunResult is expected — this asserts the boundary is
    # enforced at runtime, not just documented.
    fake_deal = Deal(
        deal_id="RAW-1", rep_id="R-01", account_name="Should Not Appear", amount=99999,
        stage=DealStage.CLOSED_WON, stage_entered_date=date(2026, 7, 1),
        actual_close_date=date(2026, 7, 2),
    )
    narrative = NarrativeLayer(client=_fake_client())
    with pytest.raises(TypeError, match="never accepts raw deal"):
        narrative.ask("Explain this deal", commission=fake_deal)
