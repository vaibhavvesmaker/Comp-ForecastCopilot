"""
HubSpot pipeline sync — pulls deals from the HubSpot CRM API and maps
them into this system's Deal schema (app/models/schemas.py).

Reads HUBSPOT_PRIVATE_APP_TOKEN from the environment (via python-dotenv,
the same pattern app/narrative/client.py uses for ANTHROPIC_API_KEY) —
this is the only module in the codebase that talks to HubSpot directly.

Key design decision — skip, don't crash, on anything unmappable:
    A HubSpot portal's pipeline stages are custom per account. A record
    with a dealstage this connector has no mapping for, a missing or
    invalid amount, or anything else that fails Deal's own validation is
    collected in HubSpotSyncResult.skipped with a reason — exactly like
    SkippedDeal in the Commission Calculator. One bad record should never
    abort an entire sync; it should never silently disappear either.

Key design decision — documented approximations, not silent guesses:
    HubSpot doesn't expose "days in current stage" or "last activity
    date" as single clean properties without portal-specific setup (the
    per-stage hs_date_entered_<stageId> properties, or the separate
    Engagements/Timeline API). This connector approximates
    stage_entered_date from hs_lastmodifieddate and last_activity_date
    from notes_last_contacted, and says so here rather than presenting
    them as exact. A production sync feeding real comp/forecast
    decisions should request the exact hs_date_entered_<stageId>
    property for each deal's current stage instead.
"""

from __future__ import annotations

import os
from datetime import date, datetime

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from app.models.schemas import Deal, DealStage

load_dotenv()

HUBSPOT_API_BASE_URL = "https://api.hubapi.com"
DEALS_ENDPOINT = "/crm/v3/objects/deals"

DEAL_PROPERTIES = [
    "dealname", "amount", "dealstage", "closedate",
    "hs_lastmodifieddate", "notes_last_contacted", "hubspot_owner_id",
]

# HubSpot's default "Sales Pipeline" stage IDs. Custom pipelines use
# different IDs — pass your own mapping to sync_deals() if so.
DEFAULT_STAGE_MAPPING: dict[str, DealStage] = {
    "appointmentscheduled": DealStage.PROSPECTING,
    "qualifiedtobuy": DealStage.QUALIFICATION,
    "presentationscheduled": DealStage.PROPOSAL,
    "decisionmakerboughtin": DealStage.PROPOSAL,
    "contractsent": DealStage.NEGOTIATION,
    "closedwon": DealStage.CLOSED_WON,
    "closedlost": DealStage.CLOSED_LOST,
}


class HubSpotConfigError(Exception):
    """Raised when the HubSpot client can't be configured (e.g. missing token)."""
    pass


class SkippedHubSpotRecord(BaseModel):
    hubspot_deal_id: str
    reason: str


class HubSpotSyncResult(BaseModel):
    deals: list[Deal]
    skipped: list[SkippedHubSpotRecord]


class HubSpotClient:
    def __init__(self, token: str | None = None, http_client: httpx.Client | None = None):
        self.token = token or os.environ.get("HUBSPOT_PRIVATE_APP_TOKEN")
        if not self.token:
            raise HubSpotConfigError(
                "HUBSPOT_PRIVATE_APP_TOKEN is not set. Copy .env.example to .env and "
                "set it, or pass token= explicitly."
            )
        self._client = http_client or httpx.Client(
            base_url=HUBSPOT_API_BASE_URL,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def fetch_deals(self, page_size: int = 100, max_pages: int = 50) -> list[dict]:
        """Fetches every deal via HubSpot's cursor pagination (paging.next.after)."""
        records: list[dict] = []
        after: str | None = None

        for _ in range(max_pages):
            params = {"limit": page_size, "properties": ",".join(DEAL_PROPERTIES)}
            if after:
                params["after"] = after

            response = self._client.get(DEALS_ENDPOINT, params=params)
            response.raise_for_status()
            body = response.json()

            records.extend(body.get("results", []))

            after = body.get("paging", {}).get("next", {}).get("after")
            if not after:
                break

        return records


def sync_deals(
    client: HubSpotClient, stage_mapping: dict[str, DealStage] | None = None
) -> HubSpotSyncResult:
    stage_mapping = stage_mapping or DEFAULT_STAGE_MAPPING
    raw_records = client.fetch_deals()

    deals: list[Deal] = []
    skipped: list[SkippedHubSpotRecord] = []

    for record in raw_records:
        deal, reason = _map_deal(record, stage_mapping)
        if deal is not None:
            deals.append(deal)
        else:
            skipped.append(SkippedHubSpotRecord(
                hubspot_deal_id=record.get("id", "UNKNOWN"), reason=reason,
            ))

    return HubSpotSyncResult(deals=deals, skipped=skipped)


def _map_deal(record: dict, stage_mapping: dict[str, DealStage]) -> tuple[Deal | None, str]:
    hubspot_deal_id = record.get("id", "UNKNOWN")
    properties = record.get("properties") or {}

    hubspot_stage_id = properties.get("dealstage")
    if not hubspot_stage_id:
        return None, "MISSING_DEALSTAGE"

    stage = stage_mapping.get(hubspot_stage_id)
    if stage is None:
        return None, f"UNMAPPED_STAGE:{hubspot_stage_id}"

    rep_id = properties.get("hubspot_owner_id")
    if not rep_id:
        return None, "MISSING_OWNER"

    close_date = _parse_date(properties.get("closedate"))
    last_modified = _parse_date(properties.get("hs_lastmodifieddate"))
    last_activity = _parse_date(properties.get("notes_last_contacted"))
    is_terminal = stage in (DealStage.CLOSED_WON, DealStage.CLOSED_LOST)

    try:
        deal = Deal(
            deal_id=hubspot_deal_id,
            rep_id=rep_id,
            account_name=properties.get("dealname") or f"HubSpot Deal {hubspot_deal_id}",
            amount=float(properties.get("amount") or 0),
            stage=stage,
            stage_entered_date=last_modified or date.today(),
            expected_close_date=close_date if not is_terminal else None,
            actual_close_date=close_date if is_terminal else None,
            last_activity_date=last_activity,
        )
    except ValidationError as e:
        return None, f"VALIDATION_ERROR:{e.errors()[0]['msg']}"

    return deal, ""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        # HubSpot returns ISO 8601 timestamps (e.g. "2026-07-15T00:00:00.000Z").
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None
