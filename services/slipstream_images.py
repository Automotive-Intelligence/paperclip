"""services/slipstream_images.py -- generate the post's images via fal and return
their bytes for committing to the brand repo.

Reuses services/blog_image.blog_image (which holds FAL_KEY on Railway). The hero
is mandatory (zero-image = auto-HOLD); a failed in-body image is skipped, not fatal,
as long as the validate gate's >=2-image floor still holds downstream.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

import requests

logger = logging.getLogger(__name__)


class ImageError(Exception):
    pass


def _default_fetch(prompt: str, business_key: str, aspect_ratio: str = "16:9", pro: bool = True) -> Dict[str, Any]:
    from services.blog_image import blog_image
    return blog_image(prompt, business_key=business_key, aspect_ratio=aspect_ratio, pro=pro)


def _default_download(url: str) -> bytes:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def generate_images(
    image_prompts: List[Dict[str, str]],
    business_key: str,
    *,
    fetch: Callable = _default_fetch,
    download: Callable = _default_download,
) -> Dict[str, bytes]:
    """Return {name: png_bytes}. Raises ImageError if the hero cannot be produced."""
    out: Dict[str, bytes] = {}
    for spec in image_prompts:
        name = spec.get("name", "")
        prompt = spec.get("prompt", "")
        if not name or not prompt:
            continue
        res = fetch(prompt, business_key, aspect_ratio="16:9", pro=True)
        if not res.get("ok") or not res.get("urls"):
            if name == "hero":
                raise ImageError(f"hero image generation failed: {res.get('error')}")
            logger.warning("[slipstream] in-body image %s failed, skipping: %s", name, res.get("error"))
            continue
        try:
            out[name] = download(res["urls"][0])
        except Exception as e:
            if name == "hero":
                raise ImageError(f"hero image download failed: {e}")
            logger.warning("[slipstream] in-body image %s download failed, skipping: %s", name, e)
    if "hero" not in out:
        raise ImageError("no hero image produced")
    return out
