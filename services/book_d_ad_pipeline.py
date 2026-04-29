"""services/book_d_ad_pipeline.py — End-to-end autonomous Book'd ad pipeline.

Chains all the upstream pieces (HeyGen multi-aspect renderer + Meta Ads
write surface + approval queue) into one call:

  script → 3 captioned HeyGen videos (9:16/1:1/16:9)
         → 3 Meta video uploads
         → 3 Meta AdCreatives (one per aspect)
         → 1 Meta AdSet (under the configured campaign)
         → 3 Meta Ads (one per aspect, all PAUSED)
         → 1 Artifact queued to approval digest

After this runs, operator gets the artifact in their next weekly digest with
all 3 ad URLs visible. Tap Approve → Sofia un-pauses the ads via Meta Ads
Manager (manual for v1; PR 25 may wire automated un-pause).

Public API:
  run(script, hook_label, ...) → PipelineResult

Configuration (env vars):
  META_AD_ACCOUNT_ID            target ad account (numeric)
  META_BOOKD_CAMPAIGN_ID        existing campaign to attach ad sets under
                                (create once via /meta/account/{id}/campaign)
  META_BOOKD_PAGE_ID            FB page that posts the ad
  META_BOOKD_LANDING_URL        where the ad clicks through to
  META_BOOKD_DAILY_BUDGET_CENTS default daily budget for new ad sets (e.g. 5000 = $50/day)
  META_BOOKD_OPTIMIZATION_GOAL  default optimization goal (LINK_CLICKS / OFFSITE_CONVERSIONS / etc.)
  META_BOOKD_AVATAR_ID          HeyGen avatar to use across all hooks
  META_BOOKD_VOICE_ID           HeyGen voice to use across all hooks
  META_BOOKD_BUSINESS_KEY       business_key for the queued artifact
                                (default 'book_d')

The pipeline fails loud if any required env var is missing — it's a
production tool, not a smoke test, and partial-success ads are worse than
no ads.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


REQUIRED_ENV_VARS = (
    "META_AD_ACCOUNT_ID",
    "META_BOOKD_CAMPAIGN_ID",
    "META_BOOKD_PAGE_ID",
    "META_BOOKD_AVATAR_ID",
    "META_BOOKD_VOICE_ID",
)


@dataclass
class PipelineResult:
    ok: bool
    hook_label: str
    script: str
    aspects: list[str] = field(default_factory=list)
    heygen_render: dict[str, Any] = field(default_factory=dict)
    meta_uploads: dict[str, dict[str, Any]] = field(default_factory=dict)  # aspect → {video_id, ...}
    meta_creatives: dict[str, dict[str, Any]] = field(default_factory=dict)  # aspect → {creative_id}
    meta_ad_set: dict[str, Any] = field(default_factory=dict)
    meta_ads: dict[str, dict[str, Any]] = field(default_factory=dict)  # aspect → {ad_id}
    artifact_id: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hook_label": self.hook_label,
            "script": self.script,
            "aspects": self.aspects,
            "heygen_render": self.heygen_render,
            "meta_uploads": self.meta_uploads,
            "meta_creatives": self.meta_creatives,
            "meta_ad_set": self.meta_ad_set,
            "meta_ads": self.meta_ads,
            "artifact_id": self.artifact_id,
            "errors": self.errors,
        }


def _check_env() -> list[str]:
    missing = [k for k in REQUIRED_ENV_VARS if not (os.environ.get(k) or "").strip()]
    return missing


def _get_env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _opt_env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def run(
    script: str,
    hook_label: str,
    *,
    aspects: tuple[str, ...] = ("9:16", "1:1", "16:9"),
    landing_url: str | None = None,
    daily_budget_cents: int | None = None,
    optimization_goal: str | None = None,
    call_to_action_type: str = "LEARN_MORE",
    headline: str = "",
    primary_message: str = "",
) -> PipelineResult:
    """Run the full Book'd ad pipeline for one script.

    Args:
        script: Spoken hook text fed to HeyGen.
        hook_label: Short label for naming + traceability (e.g. 'hook-1-anxiety').
        aspects: Aspect ratios to render. Default 9:16/1:1/16:9.
        landing_url: Click-through URL. Defaults to META_BOOKD_LANDING_URL env.
        daily_budget_cents: Override default daily budget per ad set.
        optimization_goal: Override default optimization.
        call_to_action_type: Meta CTA type (LEARN_MORE / SIGN_UP / SHOP_NOW).
        headline: Optional headline shown below the video.
        primary_message: Optional primary text body of the ad post.

    Returns: PipelineResult with everything queued. ok=False if any step
        breaks; errors list shows what failed where.
    """
    result = PipelineResult(
        ok=False,
        hook_label=(hook_label or "").strip() or "untitled",
        script=(script or "").strip(),
        aspects=list(aspects),
    )

    missing = _check_env()
    if missing:
        result.errors.append(f"missing required env vars: {', '.join(missing)}")
        return result
    if not result.script:
        result.errors.append("script is required")
        return result

    account_id = _get_env("META_AD_ACCOUNT_ID")
    campaign_id = _get_env("META_BOOKD_CAMPAIGN_ID")
    page_id = _get_env("META_BOOKD_PAGE_ID")
    avatar_id = _get_env("META_BOOKD_AVATAR_ID")
    voice_id = _get_env("META_BOOKD_VOICE_ID")
    landing_url = (landing_url or _get_env("META_BOOKD_LANDING_URL") or "").strip() or None
    daily_budget_cents = daily_budget_cents or _opt_env_int("META_BOOKD_DAILY_BUDGET_CENTS", 2000)
    optimization_goal = optimization_goal or _get_env("META_BOOKD_OPTIMIZATION_GOAL", "LINK_CLICKS")
    business_key = _get_env("META_BOOKD_BUSINESS_KEY", "book_d")

    # ── Step 1: Render 3 captioned variants on HeyGen ──────────────────────
    logger.info("[bookd_pipeline] step 1: HeyGen render hook=%r aspects=%s", result.hook_label, aspects)
    from services.heygen_renderer import render_ad_variants
    heygen_result = render_ad_variants(
        script=result.script,
        avatar_id=avatar_id,
        voice_id=voice_id,
        hook_label=result.hook_label,
        aspects=aspects,
        captioned=True,
    )
    result.heygen_render = heygen_result
    if not heygen_result.get("ok"):
        result.errors.append(f"heygen render failed: {heygen_result.get('errors')}")
        return result

    aspect_to_url: dict[str, str] = {}
    for aspect, info in heygen_result["results"].items():
        url = info.get("recommended_url") or info.get("video_url")
        if not url:
            result.errors.append(f"heygen aspect {aspect} returned no URL")
            return result
        aspect_to_url[aspect] = url

    # ── Step 2-4: For each aspect, upload to Meta + create AdCreative ──────
    logger.info("[bookd_pipeline] step 2: Meta video uploads + creatives")
    from services.meta_ads import MetaAdsClient, MetaAdsConfigError, MetaAdsApiError
    try:
        client = MetaAdsClient()
    except MetaAdsConfigError as e:
        result.errors.append(f"Meta API config error: {e}")
        return result

    for aspect, video_url in aspect_to_url.items():
        video_name = f"bookd-{result.hook_label}-{aspect.replace(':', 'x')}"
        try:
            uploaded = client.upload_video_from_url(account_id, video_url, video_name)
        except (MetaAdsApiError, ValueError) as e:
            result.errors.append(f"meta upload {aspect}: {e}")
            return result
        result.meta_uploads[aspect] = uploaded

        creative_name = f"creative-bookd-{result.hook_label}-{aspect.replace(':', 'x')}"
        try:
            creative = client.create_video_ad_creative(
                account_id=account_id,
                video_id=uploaded["video_id"],
                page_id=page_id,
                name=creative_name,
                link_url=landing_url,
                message=primary_message or "",
                headline=headline or "",
                call_to_action_type=call_to_action_type if landing_url else None,
            )
        except (MetaAdsApiError, ValueError) as e:
            result.errors.append(f"meta creative {aspect}: {e}")
            return result
        result.meta_creatives[aspect] = creative

    # ── Step 5: Create ONE ad set under the campaign for this hook ─────────
    logger.info("[bookd_pipeline] step 5: Meta ad set")
    ad_set_name = f"adset-bookd-{result.hook_label}"
    try:
        ad_set = client.create_ad_set(
            account_id=account_id,
            campaign_id=campaign_id,
            name=ad_set_name,
            daily_budget_cents=daily_budget_cents,
            optimization_goal=optimization_goal,
        )
    except (MetaAdsApiError, ValueError) as e:
        result.errors.append(f"meta ad set: {e}")
        return result
    result.meta_ad_set = ad_set

    # ── Step 6: Create one PAUSED Ad per aspect under the same ad set ──────
    logger.info("[bookd_pipeline] step 6: Meta ads")
    for aspect, creative in result.meta_creatives.items():
        ad_name = f"ad-bookd-{result.hook_label}-{aspect.replace(':', 'x')}"
        try:
            ad = client.create_paused_ad(
                account_id=account_id,
                ad_set_id=ad_set["ad_set_id"],
                creative_id=creative["creative_id"],
                name=ad_name,
            )
        except (MetaAdsApiError, ValueError) as e:
            result.errors.append(f"meta ad {aspect}: {e}")
            return result
        result.meta_ads[aspect] = ad

    # ── Step 7: Queue an Artifact to the approval digest ───────────────────
    logger.info("[bookd_pipeline] step 7: queue approval artifact business_key=%s", business_key)
    try:
        from services.artifact import create_artifact
        from services.approval_queue import queue_artifact

        # Build operator-friendly content snapshot for the digest email
        artifact_content_lines = [
            f"HOOK: {result.hook_label}",
            f"SCRIPT: {result.script}",
            "",
            "ADS (PAUSED — tap Approve to un-pause in Meta Ads Manager):",
        ]
        for aspect, ad in result.meta_ads.items():
            artifact_content_lines.append(
                f"  {aspect}: ad_id={ad['ad_id']}  preview_url={aspect_to_url[aspect]}"
            )
        artifact_content = "\n".join(artifact_content_lines)

        artifact = create_artifact(
            agent_id="book_d_pipeline",
            business_key=business_key,
            artifact_type="ad",
            audience="public",
            intent="close",
            content=artifact_content,
            subject=f"Book'd Ad ready for review — {result.hook_label}",
            channel_candidates=["meta"],
            confidence=0.85,
            risk_level="medium",  # force into pending_approval; client must tap to go live
            metadata={
                "platform": "Meta Ads",
                "hook_label": result.hook_label,
                "ad_set_id": ad_set["ad_set_id"],
                "ad_ids": {asp: ad["ad_id"] for asp, ad in result.meta_ads.items()},
                "preview_urls": aspect_to_url,
                "image_url": (heygen_result["results"].get("1:1") or {}).get("thumbnail_url"),
            },
        )
        artifact_id = queue_artifact(artifact)
        result.artifact_id = artifact_id
    except Exception as e:
        # Don't fail the pipeline if artifact queueing breaks — the ads are
        # already created on Meta. Operator can find them in Ads Manager.
        # But surface the error so the operator knows the digest won't show them.
        logger.exception("[bookd_pipeline] artifact queue failed (ads still created)")
        result.errors.append(f"artifact queue failed (ads exist on Meta): {type(e).__name__}: {e}")

    result.ok = not result.errors
    logger.info("[bookd_pipeline] complete ok=%s hook=%r", result.ok, result.hook_label)
    return result


def status_summary() -> dict[str, Any]:
    """Lightweight observability: which env vars are set, no values leaked."""
    return {
        "required_env_vars": {
            k: bool((os.environ.get(k) or "").strip()) for k in REQUIRED_ENV_VARS
        },
        "optional_env_vars": {
            k: bool((os.environ.get(k) or "").strip()) for k in (
                "META_BOOKD_LANDING_URL",
                "META_BOOKD_DAILY_BUDGET_CENTS",
                "META_BOOKD_OPTIMIZATION_GOAL",
                "META_BOOKD_BUSINESS_KEY",
            )
        },
        "ready": not _check_env(),
    }
