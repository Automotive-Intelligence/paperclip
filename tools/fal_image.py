"""
tools/fal_image.py — Image generation via fal.ai (Google Nano Banana family).

The current-model, direct-via-fal replacement for the stale kie.ai Nano Banana
path. Two tiers (prompt-economy: prototype cheap, finalize premium):
  - "fal-ai/nano-banana"     -> Nano Banana (Gemini Flash Image). Fast/cheap default.
  - "fal-ai/nano-banana-pro" -> Nano Banana Pro (Gemini 3 Pro Image). Hero/finals,
    up to 14 reference images, 4K, localized edits.

Auth: FAL_KEY env var. Uses fal's synchronous REST endpoint (requests-only, no
SDK dependency) so it runs identically locally and on Railway.

Returns a dict {urls, model, aspect_ratio, prompt_used} on success or an error
string on failure, matching the repo's generate_image_hybrid contract.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Union

import requests

from tools.image_gen import BRAND_PROMPT_STYLES, PLATFORM_ASPECT_RATIOS

logger = logging.getLogger(__name__)

FAL_SYNC_BASE = "https://fal.run"
FLASH_SLUG = "fal-ai/nano-banana"
PRO_SLUG = "fal-ai/nano-banana-pro"

# Quality language appended to every prompt. Directly counters the known
# Nano Banana failure modes (yellow tint, airbrushed skin) found in research.
QUALITY_SUFFIX = (
    " Neutral daylight white balance, true-to-life color, natural skin and "
    "material texture (no airbrushed or plastic look), sharp focus, high detail, 4k."
)


def fal_image_ready() -> bool:
    """True if fal.ai is configured for image generation."""
    return bool(os.getenv("FAL_KEY", "").strip())


def generate_nano_banana_image(
    prompt: str,
    *,
    business_key: str = "",
    platform: str = "default",
    aspect_ratio: str = "",
    source_image_url: Optional[str] = None,
    reference_image_urls: Optional[List[str]] = None,
    pro: bool = False,
) -> Union[Dict[str, Any], str]:
    """Generate an on-brand still via fal.ai Nano Banana (Flash) or Pro.

    Args:
        prompt: Visual description.
        business_key: Brand key for the style prefix (BRAND_PROMPT_STYLES).
        platform: Resolves aspect_ratio when not explicit.
        aspect_ratio: Override (e.g. "1:1", "16:9"). Empty = platform default.
        source_image_url: A single reference/source image for image-to-image
            (the fidelity unlock). Routes to the model's /edit endpoint.
        reference_image_urls: Additional brand reference images (Pro blends
            up to 14). Combined with source_image_url.
        pro: Use Nano Banana Pro (hero/finals) instead of Flash (default).

    Returns:
        dict {urls, model, aspect_ratio, prompt_used} or error string.
    """
    key = os.getenv("FAL_KEY", "").strip()
    if not key:
        return "FAL_KEY not configured. Set it in Doppler (paperclip/prd)."

    brand_prefix = BRAND_PROMPT_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip() + QUALITY_SUFFIX

    ar = aspect_ratio or PLATFORM_ASPECT_RATIOS.get(
        platform.lower(), PLATFORM_ASPECT_RATIOS["default"]
    )

    # Assemble reference images (image-to-image = the consistency/fidelity lever).
    refs: List[str] = []
    if source_image_url:
        refs.append(source_image_url)
    if reference_image_urls:
        refs.extend(reference_image_urls)

    base_slug = PRO_SLUG if pro else FLASH_SLUG
    if refs:
        slug = f"{base_slug}/edit"
        body: Dict[str, Any] = {"prompt": full_prompt, "image_urls": refs[:14]}
    else:
        slug = base_slug
        body = {"prompt": full_prompt, "num_images": 1, "aspect_ratio": ar}

    try:
        resp = requests.post(
            f"{FAL_SYNC_BASE}/{slug}",
            headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
            json=body,
            timeout=180,
        )
    except requests.exceptions.RequestException as e:
        return f"fal.ai request failed: {e}"

    if resp.status_code != 200:
        return f"fal.ai error {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    images = data.get("images") or []
    urls = [img.get("url") for img in images if isinstance(img, dict) and img.get("url")]
    if not urls:
        return f"fal.ai returned no image urls (keys: {list(data.keys())})"

    logger.info(
        "[fal_image] %s generated %d image(s) for %s (%s%s)",
        slug, len(urls), business_key or "generic", ar,
        ", image-to-image" if refs else "",
    )
    return {
        "urls": urls,
        "model": slug,
        "aspect_ratio": ar,
        "prompt_used": full_prompt,
    }
