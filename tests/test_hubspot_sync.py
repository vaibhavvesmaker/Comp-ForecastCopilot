from unittest.mock import MagicMock

import pytest

from app.integrations.hubspot import HubSpotClient, HubSpotConfigError, sync_deals
from app.models.schemas import DealStage


def _fake_response(body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = body
    response.raise_for_status.return_value = None
    return response


def _record(deal_id, dealstage="closedwon", amount="120000", owner="R-01", **overrides):
    properties = {
        "dealname": f"Account {deal_id}",
        "amount": amount,
        "dealstage": dealstage,
        "closedate": "2026-07-20T00:00:00.000Z",
        "hs_lastmodifieddate": "2026-07-10T00:00:00.000Z",
        "notes_last_contacted": "2026-07-15T00:00:00.000Z",
        "hubspot_owner_id": owner,
    }
    properties.update(overrides)
    return {"id": deal_id, "properties": properties}


def test_client_raises_without_token(monkeypatch):
    monkeypatch.delenv("HUBSPOT_PRIVATE_APP_TOKEN", raising=False)
    with pytest.raises(HubSpotConfigError):
        HubSpotClient()


def test_fetch_deals_follows_pagination():
    page1 = _fake_response({
        "results": [_record("D-1"), _record("D-2")],
        "paging": {"next": {"after": "cursor-123"}},
    })
    page2 = _fake_response({"results": [_record("D-3")]})

    fake_http = MagicMock()
    fake_http.get.side_effect = [page1, page2]

    client = HubSpotClient(token="fake-token", http_client=fake_http)
    records = client.fetch_deals()

    assert [r["id"] for r in records] == ["D-1", "D-2", "D-3"]
    assert fake_http.get.call_count == 2
    second_call_params = fake_http.get.call_args_list[1].kwargs["params"]
    assert second_call_params["after"] == "cursor-123"


def test_sync_deals_maps_closed_won_deal_correctly():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({"results": [_record("D-1", dealstage="closedwon")]})
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    assert len(result.deals) == 1
    deal = result.deals[0]
    assert deal.deal_id == "D-1"
    assert deal.rep_id == "R-01"
    assert deal.stage == DealStage.CLOSED_WON
    assert deal.amount == 120000.0
    assert deal.actual_close_date is not None
    assert deal.expected_close_date is None
    assert result.skipped == []


def test_sync_deals_maps_open_deal_with_expected_close_date():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-2", dealstage="contractsent")],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    deal = result.deals[0]
    assert deal.stage == DealStage.NEGOTIATION
    assert deal.expected_close_date is not None
    assert deal.actual_close_date is None


def test_sync_deals_skips_unmapped_stage():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-3", dealstage="some_custom_stage_id")],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    assert result.deals == []
    assert len(result.skipped) == 1
    assert result.skipped[0].hubspot_deal_id == "D-3"
    assert "UNMAPPED_STAGE" in result.skipped[0].reason


def test_sync_deals_skips_missing_dealstage():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-4", dealstage=None)],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    assert result.deals == []
    assert result.skipped[0].reason == "MISSING_DEALSTAGE"


def test_sync_deals_skips_missing_owner():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-5", owner=None)],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    assert result.deals == []
    assert result.skipped[0].reason == "MISSING_OWNER"


def test_sync_deals_skips_invalid_amount():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-6", amount="0")],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client)

    assert result.deals == []
    assert "VALIDATION_ERROR" in result.skipped[0].reason


def test_sync_deals_uses_custom_stage_mapping():
    fake_http = MagicMock()
    fake_http.get.return_value = _fake_response({
        "results": [_record("D-7", dealstage="custom_won_stage")],
    })
    client = HubSpotClient(token="fake-token", http_client=fake_http)

    result = sync_deals(client, stage_mapping={"custom_won_stage": DealStage.CLOSED_WON})

    assert len(result.deals) == 1
    assert result.deals[0].stage == DealStage.CLOSED_WON
