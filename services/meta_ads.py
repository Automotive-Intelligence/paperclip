"""Meta Marketing API module — Phase 1.

Single-tenant prototype: reads account state and creates paused campaigns
against one Meta ad account configured via env vars.

Phase 2 will introduce per-tenant credential resolution from a tenants table.
For now, credentials are read directly from environment:
    META_APP_ID
    META_APP_SECRET
    META_ACCESS_TOKEN          long-lived user/system access token
    META_API_VERSION           optional, defaults to v21.0
    META_AD_ACCOUNT_ID         optional default for endpoints (numeric, no "act_" prefix)

All write operations create campaigns in PAUSED state by design — Phase 1 is
diagnostic, not a launch tool. Operators must un-pause manually in Ads Manager
after reviewing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v21.0"

ALLOWED_OBJECTIVES = {
    "OUTCOME_AWARENESS",
    "OUTCOME_TRAFFIC",
    "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS",
    "OUTCOME_APP_PROMOTION",
    "OUTCOME_SALES",
}


class MetaAdsConfigError(RuntimeError):
    """Missing or invalid Meta Marketing API config."""


class MetaAdsApiError(RuntimeError):
    """Meta API call failed; original SDK exception is the cause."""


@dataclass
class MetaAdsCredentials:
    app_id: str
    app_secret: str
    access_token: str
    api_version: str = DEFAULT_API_VERSION


@dataclass
class AccountState:
    """Diagnostic snapshot of an ad account at one point in time."""
    account_id: str
    name: str | None
    currency: str | None
    timezone_name: str | None
    account_status: int | None  # 1=active, 2=disabled, 3=unsettled, 7=pending_risk_review, etc.
    disable_reason: int | None
    spend_cap: str | None
    amount_spent: str | None
    business_id: str | None
    business_name: str | None
    pixels: list[dict[str, Any]] = field(default_factory=list)
    funding_source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _load_credentials() -> MetaAdsCredentials:
    app_id = os.environ.get("META_APP_ID", "").strip()
    app_secret = os.environ.get("META_APP_SECRET", "").strip()
    access_token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    api_version = os.environ.get("META_API_VERSION", DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION

    missing = [
        name for name, val in (
            ("META_APP_ID", app_id),
            ("META_APP_SECRET", app_secret),
            ("META_ACCESS_TOKEN", access_token),
        ) if not val
    ]
    if missing:
        raise MetaAdsConfigError(f"missing env vars: {', '.join(missing)}")

    return MetaAdsCredentials(
        app_id=app_id,
        app_secret=app_secret,
        access_token=access_token,
        api_version=api_version,
    )


def _normalize_account_id(account_id: str) -> str:
    """Meta SDK expects 'act_<numeric>' format for ad account references."""
    aid = str(account_id).strip()
    if not aid:
        raise ValueError("account_id is required")
    if aid.startswith("act_"):
        return aid
    return f"act_{aid}"


def _ensure_sdk_initialized(creds: MetaAdsCredentials) -> None:
    """Initialize facebook-business SDK with credentials. Idempotent per process."""
    try:
        from facebook_business.api import FacebookAdsApi
    except ImportError as e:
        raise MetaAdsConfigError(
            "facebook-business SDK not installed; add 'facebook-business' to requirements.txt"
        ) from e

    FacebookAdsApi.init(
        app_id=creds.app_id,
        app_secret=creds.app_secret,
        access_token=creds.access_token,
        api_version=creds.api_version,
    )


class MetaAdsClient:
    """Thin wrapper around facebook-business SDK for Phase 1 diagnostics + paused campaigns."""

    def __init__(self, credentials: MetaAdsCredentials | None = None):
        self.credentials = credentials or _load_credentials()
        _ensure_sdk_initialized(self.credentials)

    def read_account_state(self, account_id: str) -> AccountState:
        """Fetch a diagnostic snapshot of the ad account.

        Returns identifying info, status, currency, balance, owning business,
        and connected pixels. Use this to verify the access token has the
        permissions we expect before attempting any write operations.
        """
        from facebook_business.adobjects.adaccount import AdAccount

        normalized = _normalize_account_id(account_id)

        account_fields = [
            "id",
            "account_id",
            "name",
            "currency",
            "timezone_name",
            "account_status",
            "disable_reason",
            "spend_cap",
            "amount_spent",
            "business",
            "funding_source",
        ]

        try:
            account = AdAccount(normalized).api_get(fields=account_fields)
        except Exception as e:
            logger.exception("Meta API: failed to read account %s", normalized)
            raise MetaAdsApiError(f"failed to read account {normalized}: {e}") from e

        raw = dict(account)
        business = raw.get("business") or {}

        pixels: list[dict[str, Any]] = []
        try:
            pixel_iter = AdAccount(normalized).get_ads_pixels(fields=["id", "name", "code", "last_fired_time"])
            for px in pixel_iter:
                pixels.append({
                    "id": px.get("id"),
                    "name": px.get("name"),
                    "last_fired_time": px.get("last_fired_time"),
                })
        except Exception as e:
            logger.warning("Meta API: failed to list pixels for %s: %s", normalized, e)

        return AccountState(
            account_id=raw.get("account_id") or normalized.replace("act_", ""),
            name=raw.get("name"),
            currency=raw.get("currency"),
            timezone_name=raw.get("timezone_name"),
            account_status=raw.get("account_status"),
            disable_reason=raw.get("disable_reason"),
            spend_cap=raw.get("spend_cap"),
            amount_spent=raw.get("amount_spent"),
            business_id=business.get("id") if isinstance(business, dict) else None,
            business_name=business.get("name") if isinstance(business, dict) else None,
            pixels=pixels,
            funding_source=raw.get("funding_source"),
            raw=raw,
        )

    def create_campaign(
        self,
        account_id: str,
        name: str,
        objective: str,
        daily_budget_cents: int | None = None,
        special_ad_categories: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a campaign in PAUSED state. Returns {id, name, status}.

        daily_budget_cents is optional; if omitted, the campaign is created
        without a budget and budget must be set at the ad set level.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.campaign import Campaign

        if not name or not name.strip():
            raise ValueError("campaign name is required")
        if objective not in ALLOWED_OBJECTIVES:
            raise ValueError(
                f"objective {objective!r} not in allowed set: {sorted(ALLOWED_OBJECTIVES)}"
            )
        if daily_budget_cents is not None and daily_budget_cents <= 0:
            raise ValueError("daily_budget_cents must be positive")

        normalized = _normalize_account_id(account_id)

        params: dict[str, Any] = {
            Campaign.Field.name: name.strip(),
            Campaign.Field.objective: objective,
            Campaign.Field.status: Campaign.Status.paused,
            Campaign.Field.special_ad_categories: special_ad_categories or [],
        }
        if daily_budget_cents is not None:
            params[Campaign.Field.daily_budget] = daily_budget_cents

        try:
            campaign = AdAccount(normalized).create_campaign(
                fields=[Campaign.Field.id, Campaign.Field.name, Campaign.Field.status],
                params=params,
            )
        except Exception as e:
            logger.exception("Meta API: failed to create campaign on %s", normalized)
            raise MetaAdsApiError(f"failed to create campaign on {normalized}: {e}") from e

        return {
            "id": campaign.get("id"),
            "name": campaign.get("name") or name,
            "status": campaign.get("status") or "PAUSED",
            "account_id": normalized,
        }


def get_default_account_id() -> str:
    """Resolve the default ad account id from env, for endpoints that don't pass one."""
    aid = os.environ.get("META_AD_ACCOUNT_ID", "").strip()
    if not aid:
        raise MetaAdsConfigError("META_AD_ACCOUNT_ID not set")
    return aid


def status_summary() -> dict[str, Any]:
    """Lightweight observability: which env vars are set, no secrets leaked."""
    return {
        "configured": all(
            os.environ.get(k, "").strip()
            for k in ("META_APP_ID", "META_APP_SECRET", "META_ACCESS_TOKEN")
        ),
        "app_id_set": bool(os.environ.get("META_APP_ID", "").strip()),
        "app_secret_set": bool(os.environ.get("META_APP_SECRET", "").strip()),
        "access_token_set": bool(os.environ.get("META_ACCESS_TOKEN", "").strip()),
        "api_version": os.environ.get("META_API_VERSION", DEFAULT_API_VERSION),
        "default_account_id": os.environ.get("META_AD_ACCOUNT_ID", "") or None,
    }
