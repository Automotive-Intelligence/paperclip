"""
tools/screenshot.py — Iris's eyes for the web.

Renders a live URL to a full-page screenshot PNG that a vision model (Iris's
perception layer) can actually look at and critique, top to bottom, like a
creative director scrolling the page. This is the missing half of her eyes:
the VLM critic can judge an image, this is what hands it the image of a live page.

Provider-swappable (mirrors services/generators/image_gen.py):
  - "thumio"     (default, keyless): https://image.thum.io single-GET -> PNG.
  - "microlink"  (keyless fallback): JSON API -> screenshot URL -> download.
  - "screenshotone" (keyed, optional): set SCREENSHOT_API_KEY for production
    reliability/scale. Chosen via SCREENSHOT_PROVIDER env.

On the default path it needs NO credentials, so Iris can see any page today.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = os.getenv("SCREENSHOT_PROVIDER", "thumio").strip().lower()


def _thumio(url: str, width: int, full_page: bool, timeout: int) -> bytes:
    seg = "fullpage/" if full_page else ""
    shot = f"https://image.thum.io/get/width/{width}/{seg}{url}"
    r = requests.get(shot, timeout=timeout)
    r.raise_for_status()
    if "image" not in r.headers.get("content-type", ""):
        raise ValueError(f"thum.io returned non-image ({r.headers.get('content-type')})")
    return r.content


def _microlink(url: str, full_page: bool, timeout: int) -> bytes:
    api = ("https://api.microlink.io/?screenshot=true&meta=false"
           f"&fullPage={'true' if full_page else 'false'}&url={url}")
    r = requests.get(api, timeout=timeout)
    r.raise_for_status()
    shot_url = (((r.json() or {}).get("data") or {}).get("screenshot") or {}).get("url")
    if not shot_url:
        raise ValueError("microlink returned no screenshot url")
    img = requests.get(shot_url, timeout=timeout)
    img.raise_for_status()
    return img.content


def _screenshotone(url: str, width: int, full_page: bool, timeout: int) -> bytes:
    key = os.getenv("SCREENSHOT_API_KEY", "").strip()
    if not key:
        raise ValueError("SCREENSHOT_API_KEY not set for screenshotone provider")
    params = {
        "access_key": key, "url": url, "viewport_width": width,
        "full_page": "true" if full_page else "false", "format": "png",
        "block_ads": "true", "block_cookie_banners": "true",
    }
    r = requests.get("https://api.screenshotone.com/take", params=params, timeout=timeout)
    r.raise_for_status()
    return r.content


def screenshot_ready() -> bool:
    """The default (thumio/microlink) needs no credentials, so always ready."""
    return True


def capture_url(
    url: str,
    *,
    full_page: bool = True,
    width: int = 1280,
    provider: Optional[str] = None,
    save_path: Optional[str] = None,
    timeout: int = 90,
) -> Dict[str, Any]:
    """Capture a live page to a PNG that Iris can view.

    Args:
        url: The page to capture (include scheme).
        full_page: Full scrolling page (default) vs above-the-fold viewport.
        width: Viewport width in px.
        provider: Override SCREENSHOT_PROVIDER ("thumio"|"microlink"|"screenshotone").
        save_path: Where to write the PNG. Defaults to a temp file.
        timeout: Per-request seconds.

    Returns:
        {ok, path, provider, width, full_page, error}. On the keyless default,
        thumio is tried first and microlink is the automatic fallback.
    """
    if "://" not in url:
        url = "https://" + url
    chosen = (provider or DEFAULT_PROVIDER).strip().lower()

    # Build the attempt order: chosen first, then the other keyless one as fallback.
    order = [chosen] + [p for p in ("thumio", "microlink") if p != chosen]
    runners = {"thumio": lambda: _thumio(url, width, full_page, timeout),
               "microlink": lambda: _microlink(url, full_page, timeout),
               "screenshotone": lambda: _screenshotone(url, width, full_page, timeout)}

    last_err = ""
    for prov in order:
        runner = runners.get(prov)
        if not runner:
            last_err = f"unknown provider '{prov}'"
            continue
        try:
            data = runner()
            if not data or len(data) < 1000:
                raise ValueError(f"{prov} returned too little data ({len(data)} bytes)")
            path = save_path or tempfile.mkstemp(prefix="iris_shot_", suffix=".png")[1]
            with open(path, "wb") as f:
                f.write(data)
            logger.info("[screenshot] captured %s via %s -> %s (%d bytes)",
                        url, prov, path, len(data))
            return {"ok": True, "path": path, "provider": prov,
                    "width": width, "full_page": full_page, "error": ""}
        except Exception as e:
            last_err = f"{prov}: {e}"
            logger.warning("[screenshot] %s failed: %s", prov, e)
            continue

    return {"ok": False, "path": None, "provider": chosen,
            "width": width, "full_page": full_page, "error": last_err}
