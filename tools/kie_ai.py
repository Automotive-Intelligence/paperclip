"""
tools/kie_ai.py — Google Nano Banana image generation via kie.ai

Hybrid companion to tools/image_gen.py (Replicate FLUX). Nano Banana is
Google's photoreal image model exposed through kie.ai's unified Market API.
We use it for photoreal use cases (P&P product / lifestyle shots, Sofia
social images) while FLUX remains the default workhorse.

API surface (verified against https://docs.kie.ai/market/google/nano-banana
and https://docs.kie.ai/market/common/get-task-detail on 2026-05-13):

  POST https://api.kie.ai/api/v1/jobs/createTask
    headers: Authorization: Bearer <KIE_AI_API_KEY>, Content-Type: application/json
    body: {"model": "google/nano-banana", "input": {prompt, output_format, image_size}}
    returns: {"code": 200, "data": {"taskId": "..."}}

  GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=<id>
    returns: {"code": 200, "data": {state, resultJson, failMsg, ...}}
    state in {waiting, queuing, generating, success, fail}
    resultJson is a JSON STRING that parses to {"resultUrls": [...]}

Errors return as strings (matches tools/keyapi.py / tools/shopify.py /
tools/klaviyo.py pattern) so callers don't crash on missing config.
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

import json
import logging
import os
import time
from typing import Any, Dict, Union

import requests

logger = logging.getLogger(__name__)

KIE_AI_API_KEY = os.getenv("KIE_AI_API_KEY", "").strip()
KIE_AI_BASE_URL = "https://api.kie.ai/api/v1"
NANO_BANANA_MODEL = "google/nano-banana"

# kie.ai accepts these aspect ratios for Nano Banana per docs.
# We map any unsupported value (e.g. FLUX-style "4:5") to the nearest legal
# value so callers can pass the same aspect_ratio they'd use for FLUX.
SUPPORTED_IMAGE_SIZES = {
    "1:1", "9:16", "16:9", "3:4", "4:3", "3:2", "2:3",
    "5:4", "4:5", "21:9", "auto",
}

# Brand-aware prompt prefixes — mirrors tools/image_gen.BRAND_PROMPT_STYLES
# so Nano Banana output stays visually consistent with FLUX output across
# the same brand surface.
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
    "paper_and_purpose": (
        "Warm, faith-rooted lifestyle aesthetic. Soft natural light, "
        "neutral linen palette, handcrafted feel. Photoreal product "
        "and lifestyle photography. "
    ),
}


def nano_banana_ready() -> bool:
    """Check if kie.ai API key is configured."""
    return bool(KIE_AI_API_KEY)


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {KIE_AI_API_KEY}",
        "Content-Type": "application/json",
    }


def _normalize_aspect_ratio(aspect_ratio: str) -> str:
    """Map caller's aspect_ratio to a kie.ai-supported image_size."""
    if not aspect_ratio:
        return "1:1"
    if aspect_ratio in SUPPORTED_IMAGE_SIZES:
        return aspect_ratio
    # Fallback: assume square for anything unrecognized.
    logger.warning(
        "[kie.ai] aspect_ratio %r not in supported set; falling back to 1:1",
        aspect_ratio,
    )
    return "1:1"


def _poll_task(task_id: str, timeout: int = 180, interval: float = 2.0) -> Dict[str, Any]:
    """Poll recordInfo until task reaches a terminal state or timeout."""
    url = f"{KIE_AI_BASE_URL}/jobs/recordInfo"
    deadline = time.time() + timeout
    last_payload: Dict[str, Any] = {}

    while time.time() < deadline:
        resp = requests.get(
            url,
            params={"taskId": task_id},
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        last_payload = payload

        if payload.get("code") != 200:
            raise RuntimeError(
                f"kie.ai recordInfo non-200 code: {payload.get('code')} — "
                f"{payload.get('msg', '')}"
            )

        data = payload.get("data") or {}
        state = data.get("state", "")

        if state == "success":
            return data
        if state == "fail":
            raise RuntimeError(
                f"kie.ai task {task_id} failed: "
                f"{data.get('failCode', '')} {data.get('failMsg', '')}".strip()
            )

        time.sleep(interval)

    raise TimeoutError(
        f"kie.ai task {task_id} did not finish within {timeout}s. "
        f"Last state: {(last_payload.get('data') or {}).get('state', 'unknown')}"
    )


def _extract_urls(task_data: Dict[str, Any]) -> list[str]:
    """Pull resultUrls out of the resultJson string field."""
    raw = task_data.get("resultJson") or ""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("[kie.ai] resultJson was not valid JSON: %r", raw[:200])
        return []
    urls = parsed.get("resultUrls") or []
    if isinstance(urls, str):
        urls = [urls]
    return [u for u in urls if isinstance(u, str) and u]


def generate_nano_banana_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    business_key: str = "",
    output_format: str = "png",
) -> Union[Dict[str, Any], str]:
    """
    Generate an image via Google Nano Banana on kie.ai.

    Returns the same dict shape as tools/image_gen.generate_image on success:
        {urls, model, aspect_ratio, prompt_used}

    On error, returns a string starting with "ERROR:" — matching the
    error-string pattern used by tools/keyapi.py, tools/shopify.py, and
    tools/klaviyo.py. Callers (e.g. sofia_generate_image) should check
    isinstance(result, str) to detect failure.

    Args:
        prompt: Visual description (max 5000 chars per kie.ai docs).
        aspect_ratio: One of SUPPORTED_IMAGE_SIZES; unrecognized values
            fall back to "1:1".
        business_key: Brand key for style prefixing.
        output_format: "png" (default) or "jpeg".
    """
    if not KIE_AI_API_KEY:
        return "ERROR: KIE_AI_API_KEY not set"

    prompt = (prompt or "").strip()
    if not prompt:
        return "ERROR: prompt is required"

    brand_prefix = BRAND_PROMPT_STYLES.get(business_key, "")
    full_prompt = f"{brand_prefix}{prompt}".strip()
    # kie.ai caps prompt at 5000 chars.
    if len(full_prompt) > 5000:
        full_prompt = full_prompt[:5000]

    image_size = _normalize_aspect_ratio(aspect_ratio)
    fmt = output_format.lower() if output_format else "png"
    if fmt not in ("png", "jpeg"):
        fmt = "png"

    body = {
        "model": NANO_BANANA_MODEL,
        "input": {
            "prompt": full_prompt,
            "output_format": fmt,
            "image_size": image_size,
        },
    }

    create_url = f"{KIE_AI_BASE_URL}/jobs/createTask"
    try:
        resp = requests.post(create_url, json=body, headers=_headers(), timeout=60)
        resp.raise_for_status()
        create_payload = resp.json()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        body_snip = e.response.text[:500] if e.response is not None else ""
        return f"ERROR: kie.ai createTask HTTP {status}: {body_snip}"
    except requests.exceptions.RequestException as e:
        return f"ERROR: kie.ai createTask request failed: {e}"

    if create_payload.get("code") != 200:
        return (
            f"ERROR: kie.ai createTask returned code "
            f"{create_payload.get('code')}: {create_payload.get('msg', '')}"
        )

    task_id = (create_payload.get("data") or {}).get("taskId")
    if not task_id:
        return f"ERROR: kie.ai createTask missing taskId in response: {create_payload}"

    try:
        task_data = _poll_task(task_id)
    except TimeoutError as e:
        return f"ERROR: {e}"
    except RuntimeError as e:
        return f"ERROR: {e}"
    except requests.exceptions.RequestException as e:
        return f"ERROR: kie.ai polling request failed: {e}"

    urls = _extract_urls(task_data)
    if not urls:
        return f"ERROR: kie.ai task {task_id} succeeded but returned no resultUrls"

    logger.info(
        "[kie.ai] Generated %d Nano Banana image(s) for %s (%s, task=%s)",
        len(urls),
        business_key or "generic",
        image_size,
        task_id,
    )

    return {
        "urls": urls,
        "model": NANO_BANANA_MODEL,
        "aspect_ratio": image_size,
        "prompt_used": full_prompt,
    }
