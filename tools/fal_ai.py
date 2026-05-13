"""
tools/fal_ai.py — Premium video generation via fal.ai (ByteDance Seedance Pro).

Seedance Pro is the cinematic, high-cost B-roll model used for premium shots
where motion quality matters (P&P beauty shots, brand video, Book'd ad B-roll).
The default Kling path (tools/video_gen.py) stays in place for cheap, fast
short-form. This module is opt-in via `sofia_generate_video(model="seedance_pro")`.

Auth: FAL_KEY env var (fal-client SDK default).
Docs: https://fal.ai/models/fal-ai/bytedance/seedance/v1/pro/text-to-video
       https://fal.ai/models/fal-ai/bytedance/seedance/v1/pro/image-to-video

Error pattern: this module RAISES on failure. The CrewAI wrapper in
tools/sofia_tools.py catches and converts to error strings (matches the
existing keyapi.py / shopify.py / klaviyo.py pattern).
"""

# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

FAL_KEY = os.getenv("FAL_KEY", "").strip()

SEEDANCE_PRO_T2V = "fal-ai/bytedance/seedance/v1/pro/text-to-video"
SEEDANCE_PRO_I2V = "fal-ai/bytedance/seedance/v1/pro/image-to-video"

# Brand-aware video prompt styles. Mirrors tools/video_gen.BRAND_VIDEO_STYLES
# so the two lanes produce visually-consistent output.
BRAND_VIDEO_STYLES = {
    "callingdigital": (
        "Professional digital marketing motion graphic. "
        "Smooth transitions, green accents, modern corporate feel. "
    ),
    "autointelligence": (
        "Sleek automotive technology showcase. "
        "Blue accent lighting, data visualizations, dealership environments. "
    ),
    "aiphoneguy": (
        "Energetic small-business communication visual. "
        "Warm orange tones, phone/technology imagery, friendly and approachable. "
    ),
}


def fal_ai_ready() -> bool:
    """Check if fal.ai is configured for premium video generation."""
    return bool(FAL_KEY)


def generate_seedance_pro_video(
    prompt: str,
    aspect_ratio: str = "16:9",
    duration: int = 5,
    start_image_url: Optional[str] = None,
    business_key: str = "",
) -> Dict[str, Any]:
    """
    Generate a cinematic video clip via ByteDance Seedance Pro on fal.ai.

    Use this lane for premium B-roll where motion quality matters. Text-to-video
    by default; pass start_image_url to switch to image-to-video.

    Args:
        prompt: Description of the desired video content / motion.
        aspect_ratio: '16:9', '9:16', '1:1', etc. Defaults to '16:9'.
        duration: Clip length in seconds. Seedance Pro supports ~5-10s.
        start_image_url: Optional first-frame image URL for image-to-video.
        business_key: Brand key for style prefixing (matches Kling path).

    Returns:
        Dict with keys: url, model, aspect_ratio, duration, prompt_used.

    Raises:
        RuntimeError: If FAL_KEY is missing or fal-client is not installed.
        Exception: Any fal_client error propagates up; sofia_tools converts
            to an error string for the LLM.
    """
    if not FAL_KEY:
        raise RuntimeError(
            "FAL_KEY not configured. Set it in Railway environment variables."
        )

    try:
        import fal_client
    except ImportError as e:
        raise RuntimeError(
            "fal-client not installed. Add 'fal-client' to requirements.txt."
        ) from e

    # fal-client reads FAL_KEY from env automatically, but be explicit so the
    # SDK doesn't silently fall back to an empty key if the env was set after
    # import elsewhere in the process.
    os.environ.setdefault("FAL_KEY", FAL_KEY)

    brand_prefix = BRAND_VIDEO_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip()

    arguments: Dict[str, Any] = {
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "duration": int(duration),
    }

    if start_image_url:
        model_id = SEEDANCE_PRO_I2V
        arguments["image_url"] = start_image_url
    else:
        model_id = SEEDANCE_PRO_T2V

    logger.info(
        "[FalAI] Submitting Seedance Pro %s job (%s, %ds, %s)",
        "i2v" if start_image_url else "t2v",
        aspect_ratio,
        duration,
        business_key or "generic",
    )

    # subscribe() blocks until the queued job completes and returns the result.
    result = fal_client.subscribe(model_id, arguments=arguments)

    video_block = (result or {}).get("video") or {}
    video_url = video_block.get("url") if isinstance(video_block, dict) else None

    if not video_url:
        raise RuntimeError(
            f"fal.ai Seedance Pro returned no video URL. Raw result: {result!r}"
        )

    logger.info(
        "[FalAI] Generated Seedance Pro clip for %s (%s, %ds)",
        business_key or "generic",
        aspect_ratio,
        duration,
    )

    return {
        "url": video_url,
        "model": model_id,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "prompt_used": full_prompt,
    }
