"""services/blog_image.py -- normalizer around tools/fal_image.py for the
web-based Slipstream engine.

The claude.ai blog routines cannot hold FAL_KEY, so they call paperclip's
POST /admin/blog-image, which calls this. This wraps generate_nano_banana_image
(which returns a dict on success or an error STRING on failure) into a single
predictable JSON shape the routine can branch on, and never raises so the route
returns a clean {ok:false} instead of a 500.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from tools.fal_image import generate_nano_banana_image


def blog_image(
    prompt: str,
    *,
    business_key: str = "",
    aspect_ratio: str = "",
    pro: bool = False,
    reference_image_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate on-brand blog image(s) and return a normalized result.

    Returns {ok: True, urls, model, aspect_ratio} or {ok: False, error, urls: []}.
    """
    if not (prompt or "").strip():
        return {"ok": False, "error": "prompt is required", "urls": []}

    try:
        result = generate_nano_banana_image(
            prompt,
            business_key=business_key,
            aspect_ratio=aspect_ratio,
            pro=pro,
            reference_image_urls=reference_image_urls,
        )
    except Exception as e:  # never surface a 500 to the routine
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "urls": []}

    if isinstance(result, str):
        # generate_nano_banana_image signals failure with an error string.
        return {"ok": False, "error": result, "urls": []}

    urls = result.get("urls") or []
    if not urls:
        return {"ok": False, "error": "no image urls returned", "urls": []}
    return {
        "ok": True,
        "urls": urls,
        "model": result.get("model", ""),
        "aspect_ratio": result.get("aspect_ratio", ""),
    }
