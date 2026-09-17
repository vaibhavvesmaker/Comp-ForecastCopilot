from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.api.main as api_main
from app.api.main import app
from app.narrative.client import NarrativeConfigError

client = TestClient(app)


def test_get_commission_returns_payouts_and_total():
    res = client.get("/api/commission")
    assert res.status_code == 200
    body = res.json()
    assert "payouts" in body
    assert body["total_payout_amount"] > 0


def test_get_forecast_returns_blended_forecast():
    res = client.get("/api/forecast")
    assert res.status_code == 200
    body = res.json()
    assert "blended_forecast" in body
    assert "deal_forecasts" in body


def test_get_health_returns_scores_and_at_risk_count():
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert "scores" in body
    assert "at_risk_count" in body


def test_get_scenarios_returns_best_commit_worst():
    res = client.get("/api/scenarios")
    assert res.status_code == 200
    body = res.json()
    assert set(["best", "commit", "worst"]).issubset(body.keys())
    assert body["worst"]["blended_forecast"] <= body["commit"]["blended_forecast"] <= body["best"]["blended_forecast"]


def test_post_narrative_returns_503_when_no_api_key_and_not_mocked(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post("/api/narrative", json={})
    assert res.status_code == 503
    assert "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_post_narrative_returns_generated_text_when_mocked(monkeypatch):
    fake_layer = MagicMock()
    fake_layer.ask.return_value = "Commission and forecast both look healthy this period."
    monkeypatch.setattr(api_main, "NarrativeLayer", lambda: fake_layer)

    res = client.post("/api/narrative", json={"question": "How are we doing?"})
    assert res.status_code == 200
    assert res.json()["narrative"] == "Commission and forecast both look healthy this period."

    fake_layer.ask.assert_called_once()
    call_args = fake_layer.ask.call_args
    assert call_args.args[0] == "How are we doing?"
    assert "commission" in call_args.kwargs
    assert "forecast" in call_args.kwargs


def test_post_narrative_uses_default_question_when_body_omits_it(monkeypatch):
    fake_layer = MagicMock()
    fake_layer.ask.return_value = "Default summary."
    monkeypatch.setattr(api_main, "NarrativeLayer", lambda: fake_layer)

    res = client.post("/api/narrative", json={})
    assert res.status_code == 200
    question_used = fake_layer.ask.call_args.args[0]
    assert question_used == api_main.DEFAULT_NARRATIVE_QUESTION


def test_dashboard_index_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "Generate Narrative" in res.text
