"""
tools/image_gen.py — AI Image Generation via Replicate (FLUX)
Text-to-image and image editing for branded social media content.
Supports FLUX Schnell (fast/cheap) and FLUX 1.1 Pro (high quality).
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

import io
import logging
import os
import time
from typing import Any, Dict, Optional

import requests

from services.errors import ServiceCallError, ServiceError

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "").strip()
REPLICATE_BASE_URL = "https://api.replicate.com/v1"

# Model versions — update these when newer versions ship.
FLUX_SCHNELL = "black-forest-labs/flux-schnell"  # Fast, ~$0.003/image
FLUX_PRO = "black-forest-labs/flux-1.1-pro"  # High quality, ~$0.04/image

# Default model — Schnell is fast and cheap for social content.
DEFAULT_MODEL = FLUX_SCHNELL

# Aspect ratios optimized for social platforms.
PLATFORM_ASPECT_RATIOS = {
    "instagram": "1:1",
    "instagram_story": "9:16",
    "facebook": "16:9",
    "linkedin": "16:9",
    "twitter": "16:9",
    "x": "16:9",
    "tiktok": "9:16",
    "pinterest": "2:3",
    "youtube": "16:9",
    "threads": "1:1",
    "default": "16:9",
}

# Brand-aware prompt prefixes to maintain visual consistency.
BRAND_PROMPT_STYLES = {
    "callingdigital": (
        "Professional, modern digital marketing aesthetic. "
        "Green accent tones (#39D38C). Clean, minimal design. "
        "Corporate but approachable. "
    ),
    "autointelligence": (
        "Automotive dealership technology aesthetic. "
        "Blue accent tones (#47A1FF). Sleek, data-driven visuals. "
        "Professional automotive imagery. "
    ),
    "aiphoneguy": (
        "Warm, energetic small-business aesthetic. "
        "Orange accent tones (#F2994A). Friendly, conversion-focused. "
        "Phone and communication imagery. "
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


def image_gen_ready() -> bool:
    """Check if Replicate API is configured."""
    return bool(REPLICATE_API_TOKEN)


def _replicate_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # Use sync mode for predictions under 60s
    }


def _poll_prediction(prediction_id: str, timeout: int = 120) -> Dict[str, Any]:
    """Poll a Replicate prediction until it completes or fails."""
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
                f"Prediction {prediction_id} {status}: {data.get('error', 'unknown')}",
                details=data,
            )

        time.sleep(2)

    raise _replicate_error(
        "poll_prediction",
        f"Prediction {prediction_id} timed out after {timeout}s",
        retryable=True,
    )


def generate_image(
    prompt: str,
    business_key: str = "",
    platform: str = "default",
    model: str = "",
    aspect_ratio: str = "",
    num_outputs: int = 1,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generate an image using FLUX on Replicate.

    Args:
        prompt: Text description of the desired image.
        business_key: Brand key for style prefixing (callingdigital, autointelligence, aiphoneguy).
        platform: Target social platform for aspect ratio selection.
        model: Override model (defaults to FLUX Schnell).
        aspect_ratio: Override aspect ratio (e.g. "1:1", "16:9").
        num_outputs: Number of images to generate (1-4).
        seed: Optional seed for reproducibility.

    Returns:
        Dict with keys: urls (list of image URLs), model, aspect_ratio, prompt_used.

    Raises:
        ServiceCallError: On API failure or timeout.
    """
    if not REPLICATE_API_TOKEN:
        raise _replicate_error(
            "generate_image",
            "REPLICATE_API_TOKEN not configured. Set it in Railway environment variables.",
        )

    # Build brand-aware prompt.
    brand_prefix = BRAND_PROMPT_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip()

    # Resolve aspect ratio from platform if not explicitly set.
    if not aspect_ratio:
        aspect_ratio = PLATFORM_ASPECT_RATIOS.get(
            platform.lower(), PLATFORM_ASPECT_RATIOS["default"]
        )

    selected_model = model or DEFAULT_MODEL

    # Build prediction input.
    prediction_input = {
        "prompt": full_prompt,
        "aspect_ratio": aspect_ratio,
        "num_outputs": min(max(num_outputs, 1), 4),
        "output_format": "png",
        "output_quality": 90,
    }
    if seed is not None:
        prediction_input["seed"] = seed

    # Create prediction via Replicate's official models API.
    url = f"{REPLICATE_BASE_URL}/models/{selected_model}/predictions"
    try:
        resp = requests.post(
            url,
            json={"input": prediction_input},
            headers=_replicate_headers(),
            timeout=90,  # Sync mode can take up to 60s
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        raise _replicate_error(
            "generate_image",
            f"Replicate API error: {e.response.status_code} — {e.response.text[:500]}",
            status_code=e.response.status_code,
            retryable=e.response.status_code >= 500,
        )
    except requests.exceptions.RequestException as e:
        raise _replicate_error(
            "generate_image",
            f"Replicate API request failed: {e}",
            retryable=True,
        )

    # If sync mode returned a completed prediction, use it directly.
    # Otherwise, poll for completion.
    if data.get("status") != "succeeded":
        data = _poll_prediction(data["id"])

    output = data.get("output", [])
    if isinstance(output, str):
        output = [output]

    if not output:
        raise _replicate_error(
            "generate_image",
            "Replicate returned empty output",
            details=data,
        )

    logging.info(
        "[ImageGen] Generated %d image(s) via %s for %s (%s)",
        len(output),
        selected_model,
        business_key or "generic",
        aspect_ratio,
    )

    return {
        "urls": output,
        "model": selected_model,
        "aspect_ratio": aspect_ratio,
        "prompt_used": full_prompt,
    }


def generate_image_bytes(
    prompt: str,
    business_key: str = "",
    platform: str = "default",
    model: str = "",
    aspect_ratio: str = "",
) -> bytes:
    """
    Generate an image and return raw PNG bytes (ready for Zernio upload).

    This is the convenience function used by the social pipeline.
    """
    result = generate_image(
        prompt=prompt,
        business_key=business_key,
        platform=platform,
        model=model,
        aspect_ratio=aspect_ratio,
        num_outputs=1,
    )

    image_url = result["urls"][0]
    try:
        resp = requests.get(image_url, timeout=60)
        resp.raise_for_status()
        return resp.content
    except requests.exceptions.RequestException as e:
        raise _replicate_error(
            "download_image",
            f"Failed to download generated image: {e}",
            retryable=True,
        )


def edit_image(
    image_url: str,
    prompt: str,
    business_key: str = "",
) -> Dict[str, Any]:
    """
    Edit/inpaint an existing image using FLUX.

    Useful for adding brand overlays, modifying backgrounds, or
    adapting stock images to match brand aesthetic.

    Args:
        image_url: URL of the source image to edit.
        prompt: Description of desired edits.
        business_key: Brand key for style prefixing.

    Returns:
        Dict with keys: urls, model, prompt_used.
    """
    if not REPLICATE_API_TOKEN:
        raise _replicate_error(
            "edit_image",
            "REPLICATE_API_TOKEN not configured.",
        )

    brand_prefix = BRAND_PROMPT_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip()

    # Use FLUX Fill for image editing / inpainting.
    model = "black-forest-labs/flux-fill-pro"
    url = f"{REPLICATE_BASE_URL}/models/{model}/predictions"

    prediction_input = {
        "image": image_url,
        "prompt": full_prompt,
        "output_format": "png",
    }

    try:
        resp = requests.post(
            url,
            json={"input": prediction_input},
            headers=_replicate_headers(),
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        raise _replicate_error(
            "edit_image",
            f"Replicate edit error: {e.response.status_code} — {e.response.text[:500]}",
            status_code=e.response.status_code,
            retryable=e.response.status_code >= 500,
        )
    except requests.exceptions.RequestException as e:
        raise _replicate_error(
            "edit_image",
            f"Replicate edit request failed: {e}",
            retryable=True,
        )

    if data.get("status") != "succeeded":
        data = _poll_prediction(data["id"])

    output = data.get("output", [])
    if isinstance(output, str):
        output = [output]

    logging.info("[ImageGen] Edited image via %s for %s", model, business_key or "generic")

    return {
        "urls": output,
        "model": model,
        "prompt_used": full_prompt,
    }


def build_image_prompt(
    headline: str,
    subhead: str = "",
    business_key: str = "",
    content_type: str = "social_post",
    platform: str = "",
) -> str:
    """
    Build a well-structured image generation prompt from content metadata.

    Agents can call this to convert their text output into a visual prompt
    without needing to know prompt engineering details.

    Args:
        headline: Main text/topic of the content.
        subhead: Supporting text or tagline.
        business_key: Brand key for style context.
        content_type: Type of content (social_post, ad, blog_header, carousel_slide).
        platform: Target platform for style hints.

    Returns:
        A prompt string optimized for FLUX image generation.
    """
    parts = []

    # Content type framing.
    type_frames = {
        "social_post": "A professional social media graphic",
        "ad": "A high-converting digital advertisement",
        "blog_header": "A clean blog header image",
        "carousel_slide": "A presentation-style slide graphic",
    }
    parts.append(type_frames.get(content_type, "A professional marketing graphic"))

    # Topic.
    if headline:
        parts.append(f"about: {headline}")
    if subhead:
        parts.append(f"with subtitle: {subhead}")

    # Platform-specific style hints.
    if platform.lower() in ("instagram", "threads"):
        parts.append("visually striking, social-media optimized, eye-catching composition")
    elif platform.lower() in ("linkedin",):
        parts.append("corporate, professional, trustworthy, clean layout")
    elif platform.lower() in ("tiktok", "youtube"):
        parts.append("bold, dynamic, high-energy, attention-grabbing")

    # Quality modifiers.
    parts.append("high quality, sharp details, professional photography or illustration style")
    parts.append("no text overlays, no watermarks, clean composition")

    return ". ".join(parts) + "."
