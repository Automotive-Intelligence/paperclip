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

    def list_pages_for_account(self, account_id: str) -> list[dict[str, Any]]:
        """List Facebook Pages associated with the ad account's business.

        AdCreatives must reference a Page (the brand voice for the ad). This
        helper surfaces all Pages the access token can post on behalf of so
        the operator can pick the right page_id for create_video_ad_creative.

        Returns: list of {id, name, category} dicts.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        normalized = _normalize_account_id(account_id)

        try:
            account = AdAccount(normalized).api_get(fields=["business"])
        except Exception as e:
            raise MetaAdsApiError(f"failed to read account {normalized}: {e}") from e

        biz = (dict(account).get("business") or {})
        biz_id = biz.get("id") if isinstance(biz, dict) else None
        if not biz_id:
            return []

        from facebook_business.adobjects.business import Business
        try:
            pages = Business(biz_id).get_owned_pages(fields=["id", "name", "category"])
        except Exception as e:
            logger.warning("Meta API: failed to list owned pages for business %s: %s", biz_id, e)
            return []

        return [
            {"id": p.get("id"), "name": p.get("name"), "category": p.get("category")}
            for p in pages
        ]

    def upload_video_from_url(
        self,
        account_id: str,
        video_url: str,
        name: str,
    ) -> dict[str, Any]:
        """Upload a video to Meta's AdVideo library by URL.

        Meta fetches the video from the provided URL — must be publicly reachable
        (HeyGen's CDN URLs work, R2 public URLs work, signed S3 URLs work).
        Returns the AdVideo `id` which is used as `video_id` in
        create_video_ad_creative.

        Args:
            account_id: Ad account ID (with or without 'act_' prefix).
            video_url: Publicly reachable HTTP(S) URL of the video.
            name: Human-readable video name shown in Ads Manager.

        Returns: {video_id, name, account_id}.
        """
        from facebook_business.adobjects.adaccount import AdAccount

        if not video_url or not video_url.strip():
            raise ValueError("video_url is required")
        if not name or not name.strip():
            raise ValueError("name is required")

        normalized = _normalize_account_id(account_id)
        try:
            video = AdAccount(normalized).create_ad_video(
                fields=["id"],
                params={"file_url": video_url.strip(), "name": name.strip()},
            )
        except Exception as e:
            logger.exception("Meta API: failed to upload video to %s", normalized)
            raise MetaAdsApiError(f"failed to upload video to {normalized}: {e}") from e

        return {
            "video_id": video.get("id"),
            "name": name.strip(),
            "account_id": normalized,
        }

    def create_video_ad_creative(
        self,
        account_id: str,
        video_id: str,
        page_id: str,
        name: str,
        link_url: str | None = None,
        message: str = "",
        headline: str = "",
        call_to_action_type: str | None = None,
        thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        """Create an AdCreative referencing an uploaded video.

        AdCreatives are reusable — one creative can be attached to multiple
        ads. For Book'd Launch 2, each (script, aspect) combination produces
        one creative; the orchestrator (PR 24) creates 3 creatives per hook.

        Args:
            account_id: Ad account ID.
            video_id: video_id from upload_video_from_url.
            page_id: Facebook Page ID (the voice posting the ad). Get from
                list_pages_for_account.
            name: Internal AdCreative name (shown in Ads Manager).
            link_url: Where the ad clicks through to (the landing page).
                Required when call_to_action_type is set.
            message: Primary text body of the ad post.
            headline: Headline shown below the video.
            call_to_action_type: e.g. 'LEARN_MORE', 'SIGN_UP', 'SHOP_NOW',
                'GET_OFFER', 'BOOK_TRAVEL'. Empty = no CTA button.
            thumbnail_url: Optional preview thumbnail URL. If omitted, Meta
                auto-extracts a frame.

        Returns: {creative_id, name, account_id}.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.adcreative import AdCreative

        if not video_id or not video_id.strip():
            raise ValueError("video_id is required")
        if not page_id or not page_id.strip():
            raise ValueError("page_id is required")
        if not name or not name.strip():
            raise ValueError("name is required")
        if call_to_action_type and not link_url:
            raise ValueError("link_url is required when call_to_action_type is set")

        normalized = _normalize_account_id(account_id)

        video_data: dict[str, Any] = {
            "video_id": video_id.strip(),
        }
        if message:
            video_data["message"] = message
        if headline:
            video_data["title"] = headline
        if thumbnail_url:
            video_data["image_url"] = thumbnail_url
        if link_url and call_to_action_type:
            video_data["call_to_action"] = {
                "type": call_to_action_type,
                "value": {"link": link_url},
            }
        elif link_url:
            video_data["call_to_action"] = {
                "type": "LEARN_MORE",
                "value": {"link": link_url},
            }

        object_story_spec: dict[str, Any] = {
            "page_id": page_id.strip(),
            "video_data": video_data,
        }

        try:
            creative = AdAccount(normalized).create_ad_creative(
                fields=[AdCreative.Field.id, AdCreative.Field.name],
                params={
                    AdCreative.Field.name: name.strip(),
                    AdCreative.Field.object_story_spec: object_story_spec,
                },
            )
        except Exception as e:
            logger.exception("Meta API: failed to create video creative on %s", normalized)
            raise MetaAdsApiError(f"failed to create creative on {normalized}: {e}") from e

        return {
            "creative_id": creative.get("id"),
            "name": creative.get("name") or name,
            "account_id": normalized,
        }

    def create_ad_set(
        self,
        account_id: str,
        campaign_id: str,
        name: str,
        daily_budget_cents: int,
        optimization_goal: str = "LINK_CLICKS",
        billing_event: str = "IMPRESSIONS",
        bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
        targeting: dict[str, Any] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """Create an ad set under an existing campaign in PAUSED state.

        Ad sets sit between campaigns and ads in Meta's hierarchy. They own
        the budget, schedule, targeting, and optimization. One campaign can
        have many ad sets (typically one per audience or geo split).

        Args:
            account_id: Ad account ID.
            campaign_id: Existing campaign ID (from create_campaign).
            name: Ad set name.
            daily_budget_cents: Daily budget in account currency cents
                (e.g. 5000 = $50/day USD).
            optimization_goal: 'LINK_CLICKS' / 'OFFSITE_CONVERSIONS' /
                'LANDING_PAGE_VIEWS' / 'IMPRESSIONS' / 'POST_ENGAGEMENT' /
                'VIDEO_VIEWS' / 'REACH'. Default 'LINK_CLICKS'.
            billing_event: 'IMPRESSIONS' (most common) or 'LINK_CLICKS' /
                'POST_ENGAGEMENT' / 'VIDEO_VIEWS'. Must be compatible with
                optimization_goal. Default 'IMPRESSIONS'.
            bid_strategy: 'LOWEST_COST_WITHOUT_CAP' (default) /
                'LOWEST_COST_WITH_BID_CAP' / 'COST_CAP'.
            targeting: Targeting spec dict (geo, age, interests, custom audiences).
                If None, defaults to broad US 18-65 (smoke-test only — replace
                with real audience targeting before scale).
            start_time: ISO 8601 start. None = immediate (when un-paused).
            end_time: ISO 8601 end. None = no end date.

        Returns: {ad_set_id, name, campaign_id, status, account_id}.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.adset import AdSet

        if not campaign_id or not campaign_id.strip():
            raise ValueError("campaign_id is required")
        if not name or not name.strip():
            raise ValueError("name is required")
        if daily_budget_cents <= 0:
            raise ValueError("daily_budget_cents must be positive")

        normalized = _normalize_account_id(account_id)

        if targeting is None:
            targeting = {
                "geo_locations": {"countries": ["US"]},
                "age_min": 18,
                "age_max": 65,
            }

        params: dict[str, Any] = {
            AdSet.Field.name: name.strip(),
            AdSet.Field.campaign_id: campaign_id.strip(),
            AdSet.Field.daily_budget: int(daily_budget_cents),
            AdSet.Field.billing_event: billing_event,
            AdSet.Field.optimization_goal: optimization_goal,
            AdSet.Field.bid_strategy: bid_strategy,
            AdSet.Field.targeting: targeting,
            AdSet.Field.status: AdSet.Status.paused,
        }
        if start_time:
            params[AdSet.Field.start_time] = start_time
        if end_time:
            params[AdSet.Field.end_time] = end_time

        try:
            ad_set = AdAccount(normalized).create_ad_set(
                fields=[AdSet.Field.id, AdSet.Field.name, AdSet.Field.status],
                params=params,
            )
        except Exception as e:
            logger.exception("Meta API: failed to create ad set on %s", normalized)
            raise MetaAdsApiError(f"failed to create ad set on {normalized}: {e}") from e

        return {
            "ad_set_id": ad_set.get("id"),
            "name": ad_set.get("name") or name,
            "campaign_id": campaign_id,
            "status": ad_set.get("status") or "PAUSED",
            "account_id": normalized,
        }

    def create_paused_ad(
        self,
        account_id: str,
        ad_set_id: str,
        creative_id: str,
        name: str,
    ) -> dict[str, Any]:
        """Create an Ad referencing an AdCreative inside an AdSet, in PAUSED state.

        This is the final step — once the operator un-pauses the ad in Ads
        Manager (or via API), it goes live and starts serving impressions.
        Phase 1 forces PAUSED so nothing ever auto-spends without a human tap.

        Args:
            account_id: Ad account ID.
            ad_set_id: Ad set ID (from create_ad_set).
            creative_id: AdCreative ID (from create_video_ad_creative).
            name: Ad name as shown in Ads Manager.

        Returns: {ad_id, name, ad_set_id, creative_id, status, account_id}.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.ad import Ad

        if not ad_set_id or not ad_set_id.strip():
            raise ValueError("ad_set_id is required")
        if not creative_id or not creative_id.strip():
            raise ValueError("creative_id is required")
        if not name or not name.strip():
            raise ValueError("name is required")

        normalized = _normalize_account_id(account_id)
        try:
            ad = AdAccount(normalized).create_ad(
                fields=[Ad.Field.id, Ad.Field.name, Ad.Field.status],
                params={
                    Ad.Field.name: name.strip(),
                    Ad.Field.adset_id: ad_set_id.strip(),
                    Ad.Field.creative: {"creative_id": creative_id.strip()},
                    Ad.Field.status: Ad.Status.paused,
                },
            )
        except Exception as e:
            logger.exception("Meta API: failed to create ad on %s", normalized)
            raise MetaAdsApiError(f"failed to create ad on {normalized}: {e}") from e

        return {
            "ad_id": ad.get("id"),
            "name": ad.get("name") or name,
            "ad_set_id": ad_set_id,
            "creative_id": creative_id,
            "status": ad.get("status") or "PAUSED",
            "account_id": normalized,
        }

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
