"""
tools/marketing_tools.py — CrewAI marketing tools for Zoe, Sofia, Chase.

Wires the marketing agents to the generation + distribution pipeline:
  - generate_image_tool / generate_video_tool  -> services/generators
  - distribute_post_tool / schedule_post_tool  -> approval queue (gated)
  - pull_analytics_tool                        -> Zernio analytics (read-only)

APPROVAL GATE (Phase 1 hard rule: nothing posts without Michael's review).
Generation and analytics are autonomous. distribute_post_tool and
schedule_post_tool do NOT post — they queue an artifact into the approval
queue at risk_level="medium" so it always lands in pending_approval. The
actual Zernio publish happens only after a human approves the artifact
(POST /admin/marketing/publish). An agent cannot reach Zernio directly.

Errors return as strings (matches tools/sofia_tools.py / keyapi.py) so the
LLM gets useful feedback instead of a crashed Crew.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict

from crewai.tools import tool

logger = logging.getLogger(__name__)


def _split_csv(s: str) -> list[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _truncate_json(obj: Any, max_chars: int = 4000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[...truncated for context budget...]"
    return out


def _dedupe_key(business_key: str, content: str, platforms: list[str],
                scheduled_for: str) -> str:
    """Deterministic dedupe key per content item. Same content + platforms +
    schedule always yields the same key, so a retry never double-posts."""
    basis = "|".join([
        business_key.strip().lower(),
        content.strip(),
        ",".join(sorted(p.strip().lower() for p in platforms)),
        (scheduled_for or "").strip(),
    ])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _parse_overrides(per_platform_overrides: str) -> Dict[str, str]:
    """Parse a JSON object of {platform: copy} per-platform overrides."""
    raw = (per_platform_overrides or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return {str(k).strip().lower(): str(v) for k, v in obj.items() if v}
    except Exception:
        logger.warning("marketing_tools: could not parse per_platform_overrides")
    return {}


# ---------------------------------------------------------------------------
# Generation (autonomous)
# ---------------------------------------------------------------------------

@tool("Generate Marketing Image")
def generate_image_tool(
    prompt: str,
    business_key: str = "",
    platform: str = "instagram",
    aspect_ratio: str = "",
    source_image_url: str = "",
) -> str:
    """Generate an on-brand marketing image via the best-of-breed generation
    layer (provider set by IMAGE_GEN_PROVIDER, default Nano Banana).

    Autonomous: generating an image does not post anything. The returned URL
    is later attached to a post via distribute_post_tool or schedule_post_tool.

    Args:
        prompt: Visual description of the image.
        business_key: Tenant key for brand styling (e.g. 'aiphoneguy',
            'calling_digital', 'automotive_intelligence').
        platform: Target platform — sets the default aspect ratio.
        aspect_ratio: Override aspect ratio (e.g. '1:1', '9:16', '4:5').
        source_image_url: Optional base image to generate off our own asset.

    Returns: JSON string with `ok`, `urls`, `provider` on success; error
        string on failure.
    """
    try:
        from services.generators import get_image_generator
        gen = get_image_generator()
        result = gen.generate(
            prompt=prompt,
            business_key=business_key,
            platform=platform or "instagram",
            aspect_ratio=aspect_ratio,
            source_image_url=source_image_url or None,
        )
        return _truncate_json(result.to_dict())
    except Exception as e:
        logger.exception("generate_image_tool failed")
        return f"ERROR: image generation failed: {type(e).__name__}: {e}"


@tool("Generate Marketing Video")
def generate_video_tool(
    prompt: str,
    business_key: str = "",
    platform: str = "instagram_reel",
    source_image_url: str = "",
    duration: int = 0,
) -> str:
    """Generate a short-form marketing video via the generation layer
    (provider set by VIDEO_GEN_PROVIDER, default Kling).

    Autonomous: generating a video does not post anything. Pass
    source_image_url to do image-to-video off one of our own assets (for
    example, animate a still made by generate_image_tool).

    Args:
        prompt: Description of the video content.
        business_key: Tenant key for brand styling.
        platform: 'instagram_reel', 'tiktok', or 'facebook'.
        source_image_url: Optional first-frame image for image-to-video.
        duration: Clip length in seconds. 0 = platform default.

    Returns: JSON string with `ok`, `urls`, `provider` on success; error
        string on failure.
    """
    try:
        from services.generators import get_video_generator
        gen = get_video_generator()
        result = gen.generate(
            prompt=prompt,
            business_key=business_key,
            platform=platform or "instagram_reel",
            source_image_url=source_image_url or None,
            duration=int(duration) if duration else 0,
        )
        return _truncate_json(result.to_dict())
    except Exception as e:
        logger.exception("generate_video_tool failed")
        return f"ERROR: video generation failed: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Distribution (gated — queues for approval, never posts)
# ---------------------------------------------------------------------------

def _queue_marketing_post(
    *, agent: str, business_key: str, subject: str, content: str,
    platforms: list[str], media_urls: list[str], overrides: Dict[str, str],
    scheduled_for: str, intent: str,
) -> str:
    """Shared path: build the artifact, tag it for Zernio, queue for approval."""
    from services.artifact import create_artifact
    from services.approval_queue import queue_artifact

    dedupe_key = _dedupe_key(business_key, content, platforms, scheduled_for)
    metadata: Dict[str, Any] = {
        "platform": ", ".join(platforms),
        "distribution": "zernio",
        "dedupe_key": dedupe_key,
        "media_urls": media_urls,
        "per_platform_overrides": overrides,
        "scheduled_for": scheduled_for or "",
    }
    # risk_level="medium" forces pending_approval regardless of confidence.
    # This is the hard gate: every marketing post reaches Michael first.
    artifact = create_artifact(
        agent_id=agent or "marketing_agent",
        business_key=business_key,
        artifact_type="social_post",
        audience="public",
        intent=intent or "inform",
        content=content,
        subject=subject,
        channel_candidates=platforms,
        confidence=0.85,
        risk_level="medium",
        metadata=metadata,
    )
    artifact_id = queue_artifact(artifact)
    return _truncate_json({
        "ok": True,
        "queued_for_approval": True,
        "artifact_id": artifact_id,
        "dedupe_key": dedupe_key,
        "status": artifact.status,
        "scheduled_for": scheduled_for or None,
        "note": "Queued for Michael's review. It posts only after approval.",
    })


@tool("Distribute Post via Zernio")
def distribute_post_tool(
    business_key: str,
    subject: str,
    content: str,
    platforms: str,
    media_urls: str = "",
    per_platform_overrides: str = "",
    intent: str = "inform",
    agent: str = "marketing_agent",
) -> str:
    """Queue a finished post for immediate distribution across platforms.

    This does NOT post. It queues the post into the approval queue. After
    Michael approves it, it publishes to Zernio. Nothing reaches a live
    feed without his review.

    Args:
        business_key: Tenant key (e.g. 'aiphoneguy', 'calling_digital').
        subject: One-line internal title for the approval digest.
        content: The post copy (caption + hashtags).
        platforms: Comma-separated platforms (e.g. 'instagram,linkedin,tiktok').
        media_urls: Optional comma-separated media URLs from the generation tools.
        per_platform_overrides: Optional JSON object of per-platform copy,
            e.g. '{"twitter": "short version", "linkedin": "long version"}'.
        intent: inform / educate / nurture / close / retain / alert.
        agent: The calling agent's name (zoe / sofia / chase).

    Returns: JSON string with `artifact_id` on success; error string on failure.
    """
    business_key = (business_key or "").strip()
    subject = (subject or "").strip()
    content = (content or "").strip()
    if not (business_key and subject and content):
        return "ERROR: business_key, subject, and content are all required."
    plats = _split_csv(platforms)
    if not plats:
        return "ERROR: at least one platform is required."
    try:
        return _queue_marketing_post(
            agent=agent, business_key=business_key, subject=subject,
            content=content, platforms=plats, media_urls=_split_csv(media_urls),
            overrides=_parse_overrides(per_platform_overrides),
            scheduled_for="", intent=intent,
        )
    except Exception as e:
        logger.exception("distribute_post_tool failed")
        return f"ERROR: queue failed: {type(e).__name__}: {e}"


@tool("Schedule Post via Zernio")
def schedule_post_tool(
    business_key: str,
    subject: str,
    content: str,
    platforms: str,
    scheduled_for: str,
    media_urls: str = "",
    per_platform_overrides: str = "",
    intent: str = "inform",
    agent: str = "marketing_agent",
) -> str:
    """Queue a finished post to publish at a future time.

    This does NOT schedule on a live feed. It queues the post into the
    approval queue with the intended time. After Michael approves it, it is
    scheduled on Zernio for that time. Nothing reaches a live feed without
    his review.

    Args:
        business_key: Tenant key.
        subject: One-line internal title for the approval digest.
        content: The post copy.
        platforms: Comma-separated platforms.
        scheduled_for: ISO 8601 time, e.g. '2026-06-01T14:00:00Z'.
        media_urls: Optional comma-separated media URLs.
        per_platform_overrides: Optional JSON object of per-platform copy.
        intent: inform / educate / nurture / close / retain / alert.
        agent: The calling agent's name (zoe / sofia / chase).

    Returns: JSON string with `artifact_id` on success; error string on failure.
    """
    business_key = (business_key or "").strip()
    subject = (subject or "").strip()
    content = (content or "").strip()
    scheduled_for = (scheduled_for or "").strip()
    if not (business_key and subject and content):
        return "ERROR: business_key, subject, and content are all required."
    if not scheduled_for:
        return "ERROR: scheduled_for (ISO 8601) is required for scheduling."
    plats = _split_csv(platforms)
    if not plats:
        return "ERROR: at least one platform is required."
    try:
        return _queue_marketing_post(
            agent=agent, business_key=business_key, subject=subject,
            content=content, platforms=plats, media_urls=_split_csv(media_urls),
            overrides=_parse_overrides(per_platform_overrides),
            scheduled_for=scheduled_for, intent=intent,
        )
    except Exception as e:
        logger.exception("schedule_post_tool failed")
        return f"ERROR: queue failed: {type(e).__name__}: {e}"


@tool("Pull Zernio Analytics")
def pull_analytics_tool(period: str = "30d") -> str:
    """Pull cross-platform social analytics from Zernio (read-only).

    Args:
        period: Lookback window, e.g. '7d', '30d', '90d'. Default '30d'.

    Returns: JSON string with the analytics payload; error string on failure.
    """
    try:
        from services.distribution import get_zernio_client
        client = get_zernio_client()
        if not client.ready():
            return "ERROR: ZERNIO_API_KEY not set"
        data = client.get_analytics(period=(period or "30d").strip())
        return _truncate_json(data)
    except Exception as e:
        logger.exception("pull_analytics_tool failed")
        return f"ERROR: analytics pull failed: {type(e).__name__}: {e}"


# Full set — for agents with no marketing tooling yet (Zoe, Chase).
MARKETING_TOOLS = [
    generate_image_tool,
    generate_video_tool,
    distribute_post_tool,
    schedule_post_tool,
    pull_analytics_tool,
]

# Distribution + analytics subset — for Sofia, who already has generation
# tools via tools/sofia_tools.py and only needs the Zernio surface.
MARKETING_DISTRIBUTION_TOOLS = [
    distribute_post_tool,
    schedule_post_tool,
    pull_analytics_tool,
]
