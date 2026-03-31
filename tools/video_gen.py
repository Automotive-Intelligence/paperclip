"""
tools/video_gen.py — AI Video Generation via Replicate
Text-to-video and image-to-video for short-form social content.
Uses Kling v2.0 for high-quality 5-10s clips, or MiniMax for fast generation.
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

import logging
import os
import time
from typing import Any, Dict, Optional

import requests

from services.errors import ServiceCallError, ServiceError

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "").strip()
REPLICATE_BASE_URL = "https://api.replicate.com/v1"

# Video models on Replicate.
KLING_V2 = "kwaivgi/kling-v2.0-master"  # High quality, 5-10s, ~$0.05/video
MINIMAX_VIDEO = "minimax/video-01"  # Fast, ~$0.03/video

DEFAULT_VIDEO_MODEL = MINIMAX_VIDEO

# Platform-optimized video settings.
PLATFORM_VIDEO_SETTINGS = {
    "instagram": {"aspect_ratio": "1:1", "duration": 5},
    "instagram_story": {"aspect_ratio": "9:16", "duration": 5},
    "instagram_reel": {"aspect_ratio": "9:16", "duration": 5},
    "tiktok": {"aspect_ratio": "9:16", "duration": 5},
    "facebook": {"aspect_ratio": "16:9", "duration": 5},
    "linkedin": {"aspect_ratio": "16:9", "duration": 5},
    "twitter": {"aspect_ratio": "16:9", "duration": 5},
    "x": {"aspect_ratio": "16:9", "duration": 5},
    "youtube_short": {"aspect_ratio": "9:16", "duration": 5},
    "youtube": {"aspect_ratio": "16:9", "duration": 5},
    "default": {"aspect_ratio": "16:9", "duration": 5},
}

# Brand-aware video prompt styles.
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


def _replicate_error(
    operation: str,
    message: str,
    *,
    status_code: Optional[int] = None,
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> ServiceCallError:
    return ServiceCallError(
        ServiceError(
            provider="replicate",
            operation=operation,
            message=message,
            status_code=status_code,
            retryable=retryable,
            details=details,
        )
    )


def video_gen_ready() -> bool:
    """Check if Replicate API is configured for video generation."""
    return bool(REPLICATE_API_TOKEN)


def _replicate_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _poll_prediction(prediction_id: str, timeout: int = 300) -> Dict[str, Any]:
    """
    Poll a Replicate prediction until it completes.
    Video generation takes longer than images — default 5 min timeout.
    """
    url = f"{REPLICATE_BASE_URL}/predictions/{prediction_id}"
    headers = _replicate_headers()
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")

        if status == "succeeded":
            return data
        if status in ("failed", "canceled"):
            raise _replicate_error(
                "poll_prediction",
                f"Video prediction {prediction_id} {status}: {data.get('error', 'unknown')}",
                details=data,
            )

        # Video gen is slower — poll less aggressively.
        time.sleep(5)

    raise _replicate_error(
        "poll_prediction",
        f"Video prediction {prediction_id} timed out after {timeout}s",
        retryable=True,
    )


def generate_video(
    prompt: str,
    business_key: str = "",
    platform: str = "default",
    model: str = "",
    duration: int = 0,
    aspect_ratio: str = "",
    start_image_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a short video clip using AI.

    Args:
        prompt: Text description of the desired video content.
        business_key: Brand key for style prefixing.
        platform: Target platform for aspect ratio / duration defaults.
        model: Override model (defaults to MiniMax).
        duration: Video duration in seconds (0 = platform default).
        aspect_ratio: Override aspect ratio.
        start_image_url: Optional first-frame image URL for image-to-video.

    Returns:
        Dict with keys: url (video URL), model, aspect_ratio, duration, prompt_used.

    Raises:
        ServiceCallError: On API failure or timeout.
    """
    if not REPLICATE_API_TOKEN:
        raise _replicate_error(
            "generate_video",
            "REPLICATE_API_TOKEN not configured. Set it in Railway environment variables.",
        )

    # Resolve platform defaults.
    platform_settings = PLATFORM_VIDEO_SETTINGS.get(
        platform.lower(), PLATFORM_VIDEO_SETTINGS["default"]
    )
    if not aspect_ratio:
        aspect_ratio = platform_settings["aspect_ratio"]
    if not duration:
        duration = platform_settings["duration"]

    # Build brand-aware prompt.
    brand_prefix = BRAND_VIDEO_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip()

    selected_model = model or DEFAULT_VIDEO_MODEL

    # Build prediction input based on model.
    prediction_input = {
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
    }

    # Image-to-video: use the start image as the first frame.
    if start_image_url:
        prediction_input["first_frame_image"] = start_image_url

    url = f"{REPLICATE_BASE_URL}/models/{selected_model}/predictions"
    try:
        resp = requests.post(
            url,
            json={"input": prediction_input},
            headers=_replicate_headers(),
            timeout=30,  # Just submit — we'll poll.
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        raise _replicate_error(
            "generate_video",
            f"Replicate API error: {e.response.status_code} — {e.response.text[:500]}",
            status_code=e.response.status_code,
            retryable=e.response.status_code >= 500,
        )
    except requests.exceptions.RequestException as e:
        raise _replicate_error(
            "generate_video",
            f"Replicate API request failed: {e}",
            retryable=True,
        )

    # Video generation always requires polling.
    data = _poll_prediction(data["id"], timeout=300)

    output = data.get("output")
    if isinstance(output, list):
        video_url = output[0] if output else None
    else:
        video_url = output

    if not video_url:
        raise _replicate_error(
            "generate_video",
            "Replicate returned empty video output",
            details=data,
        )

    logging.info(
        "[VideoGen] Generated %ds video via %s for %s (%s)",
        duration,
        selected_model,
        business_key or "generic",
        aspect_ratio,
    )

    return {
        "url": video_url,
        "model": selected_model,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "prompt_used": full_prompt,
    }


def generate_video_from_image(
    image_url: str,
    prompt: str = "",
    business_key: str = "",
    platform: str = "default",
    duration: int = 5,
) -> Dict[str, Any]:
    """
    Animate a static image into a short video clip (image-to-video).

    This is the recommended workflow for branded content:
    1. Generate a branded image with image_gen.generate_image()
    2. Animate it with this function for video platforms.

    Args:
        image_url: URL of the source image (first frame).
        prompt: Optional motion description (e.g. "gentle zoom in, subtle particle effects").
        business_key: Brand key for style context.
        platform: Target platform for aspect ratio / duration.
        duration: Video duration in seconds.

    Returns:
        Dict with keys: url, model, aspect_ratio, duration, prompt_used.
    """
    motion_prompt = prompt or "Subtle, professional motion. Gentle zoom and pan. Smooth transitions."

    return generate_video(
        prompt=motion_prompt,
        business_key=business_key,
        platform=platform,
        duration=duration,
        start_image_url=image_url,
    )


def download_video_bytes(video_url: str) -> bytes:
    """Download generated video and return raw bytes (for Zernio upload)."""
    try:
        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        return resp.content
    except requests.exceptions.RequestException as e:
        raise _replicate_error(
            "download_video",
            f"Failed to download generated video: {e}",
            retryable=True,
        )
