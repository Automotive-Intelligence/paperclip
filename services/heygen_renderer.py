"""services/heygen_renderer.py — Multi-aspect HeyGen ad renderer with built-in captions.

Sits on top of tools/heygen.py. Takes one script + avatar + voice and renders
the same script at multiple aspect ratios in parallel (9:16 Reels/TikTok,
1:1 Feed, 16:9 in-stream). Uses HeyGen's built-in `video_url_caption` field
so captions are baked in without Submagic.

This is the input ingestion side of the autonomous Book'd ad pipeline:
  Script → Multi-aspect HeyGen render (this module) → Meta upload (PR 23) →
  Approval digest (existing) → live ads (after operator tap)

Public API:
  render_ad_variants(script, avatar_id, voice_id, hook_label, aspects=...,
                     captioned=True, max_wait_seconds=600)
      → {aspect: {video_url, video_url_caption, video_id, duration, ...}}

The renderer submits each aspect ratio as a separate HeyGen generation
concurrently (ThreadPoolExecutor), then polls them all in parallel until
each terminates. Failed aspects return their failure reason; successful
aspects return the URL operators should use.

`captioned=True` (default) returns `video_url_caption` (captions burned into
the video). `captioned=False` returns the bare `video_url`. Operators in
this codebase want captions because the autonomous pipeline ships ads
direct to Meta.

Errors at the per-aspect level surface as strings inside the result dict,
mirroring the rest of tools/. The orchestrator (PR 24) decides whether
partial-success is acceptable.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

# Aspect ratio → HeyGen dimensions. HeyGen accepts width+height in pixels.
# These match Meta's recommended creative specs.
ASPECT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),   # Reels, Stories, TikTok, Shorts
    "1:1":  (1080, 1080),   # Feed (Instagram, Facebook)
    "16:9": (1920, 1080),   # In-stream, Display
    "4:5":  (1080, 1350),   # Older IG Feed (kept for legacy support)
}

DEFAULT_ASPECTS = ("9:16", "1:1", "16:9")
DEFAULT_MAX_WAIT_SECONDS = 600  # 10 min — typical render is 1-5 min, give buffer
DEFAULT_POLL_INTERVAL = 8


def _submit_one_aspect(
    aspect: str,
    script: str,
    avatar_id: str,
    voice_id: str,
    hook_label: str,
    background_color: str,
) -> dict[str, Any]:
    """Submit one HeyGen generation for one aspect ratio. Returns
    {ok: bool, aspect, video_id (if ok), error (if not ok)}."""
    if aspect not in ASPECT_DIMENSIONS:
        return {
            "ok": False,
            "aspect": aspect,
            "error": f"unsupported aspect ratio {aspect!r}; valid: {sorted(ASPECT_DIMENSIONS)}",
        }
    width, height = ASPECT_DIMENSIONS[aspect]

    # Lazy import to keep services/* importable without crewai installed
    from tools.heygen import _heygen_request

    title = f"Book'd Launch — {hook_label} — {aspect}".strip()
    body = {
        "video_inputs": [{
            "character": {
                "type": "avatar",
                "avatar_id": avatar_id,
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": script,
                "voice_id": voice_id,
            },
            "background": {"type": "color", "value": background_color},
        }],
        "dimension": {"width": width, "height": height},
        "title": title,
        "caption": True,  # request captions baked in (returned via video_url_caption)
    }
    resp = _heygen_request("POST", "/v2/video/generate", json_body=body)
    if isinstance(resp, str):
        return {"ok": False, "aspect": aspect, "error": resp}

    video_id = (resp.get("data") or {}).get("video_id")
    if not video_id:
        return {"ok": False, "aspect": aspect, "error": f"no video_id in response: {str(resp)[:200]}"}
    return {"ok": True, "aspect": aspect, "video_id": video_id, "title": title}


def _poll_one_video(
    video_id: str,
    aspect: str,
    max_wait_seconds: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Poll one HeyGen video until terminal status or timeout. Returns the
    full status payload + aspect."""
    from tools.heygen import _heygen_request

    deadline = time.time() + max_wait_seconds
    last_status = "unknown"
    while time.time() < deadline:
        resp = _heygen_request(
            "GET", "/v1/video_status.get",
            params={"video_id": video_id},
            count_against_budget=False,
        )
        if isinstance(resp, str):
            return {
                "ok": False, "aspect": aspect, "video_id": video_id,
                "error": f"poll error: {resp}",
            }
        sd = resp.get("data") or {}
        status = sd.get("status") or "unknown"
        last_status = status
        if status == "completed":
            return {
                "ok": True,
                "aspect": aspect,
                "video_id": video_id,
                "status": "completed",
                "video_url": sd.get("video_url"),
                "video_url_caption": sd.get("video_url_caption"),
                "thumbnail_url": sd.get("thumbnail_url"),
                "gif_url": sd.get("gif_url"),
                "caption_url": sd.get("caption_url"),
                "duration": sd.get("duration"),
            }
        if status in ("failed", "canceled"):
            return {
                "ok": False, "aspect": aspect, "video_id": video_id,
                "status": status,
                "error": str(sd.get("error") or "render failed"),
            }
        time.sleep(poll_interval)
    return {
        "ok": False, "aspect": aspect, "video_id": video_id,
        "error": f"timed out after {max_wait_seconds}s (last status: {last_status})",
    }


def render_ad_variants(
    script: str,
    avatar_id: str,
    voice_id: str,
    hook_label: str = "",
    aspects: tuple[str, ...] | list[str] = DEFAULT_ASPECTS,
    captioned: bool = True,
    background_color: str = "#FFFFFF",
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Render the same script at multiple aspect ratios in parallel.

    Args:
        script: Spoken text. Same script across all aspect variants.
        avatar_id: HeyGen avatar_id (use list_heygen_avatars to discover).
        voice_id: HeyGen voice_id.
        hook_label: Short label for traceability ('hook-1-founder-anxiety',
            'vsl-segment-2', etc.). Stamped into HeyGen video titles.
        aspects: Tuple/list of aspect ratio strings. Default ('9:16','1:1','16:9').
        captioned: When True (default), the result selects video_url_caption
            (captions baked in) as the operator-facing URL. When False, the
            bare video_url is selected. Both are always returned; this just
            controls which is highlighted as `recommended_url`.
        background_color: Hex background for HeyGen render. Default white.
        max_wait_seconds: How long to poll each render before timing out.
            Default 600s.
        poll_interval: Seconds between polls per render. Default 8s.

    Returns: dict with shape:
      {
        "ok": bool,            # True if every requested aspect succeeded
        "hook_label": str,
        "script": str,         # echoed back for downstream artifact creation
        "results": {
          "9:16": {
            "ok": True, "video_id": "...", "video_url": "...",
            "video_url_caption": "...", "thumbnail_url": "...",
            "duration": float, "recommended_url": "..."  # video_url_caption if captioned else video_url
          },
          "1:1": {...},
          "16:9": {...},
        },
        "submit_count": int,
        "success_count": int,
        "errors": ["list of error strings for any aspects that failed"],
      }
    """
    script = (script or "").strip()
    avatar_id = (avatar_id or "").strip()
    voice_id = (voice_id or "").strip()
    hook_label = (hook_label or "").strip() or "untitled"
    if not script:
        return {"ok": False, "errors": ["script is required"]}
    if not avatar_id:
        return {"ok": False, "errors": ["avatar_id is required"]}
    if not voice_id:
        return {"ok": False, "errors": ["voice_id is required"]}

    aspects = tuple(aspects)
    if not aspects:
        return {"ok": False, "errors": ["at least one aspect ratio required"]}

    # Phase 1: submit all aspect variants concurrently
    submissions: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(len(aspects), 4)) as pool:
        futures = [
            pool.submit(_submit_one_aspect, asp, script, avatar_id, voice_id, hook_label, background_color)
            for asp in aspects
        ]
        for f in as_completed(futures):
            submissions.append(f.result())

    submitted_ok = [s for s in submissions if s["ok"]]
    submission_errors = [
        f"submit failed for {s['aspect']}: {s.get('error')}"
        for s in submissions if not s["ok"]
    ]

    if not submitted_ok:
        return {
            "ok": False,
            "hook_label": hook_label,
            "script": script,
            "results": {},
            "submit_count": 0,
            "success_count": 0,
            "errors": submission_errors,
        }

    logger.info(
        "[heygen_renderer] submitted %d/%d aspects for %r",
        len(submitted_ok), len(aspects), hook_label,
    )

    # Phase 2: poll all submitted aspects concurrently until each terminates
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(len(submitted_ok), 4)) as pool:
        futures = [
            pool.submit(
                _poll_one_video,
                s["video_id"], s["aspect"],
                max_wait_seconds, poll_interval,
            )
            for s in submitted_ok
        ]
        for f in as_completed(futures):
            r = f.result()
            results[r["aspect"]] = r

    # Compose final result
    success_count = sum(1 for r in results.values() if r["ok"])
    errors: list[str] = list(submission_errors)
    for asp, r in results.items():
        if not r["ok"]:
            errors.append(f"{asp}: {r.get('error')}")
        else:
            r["recommended_url"] = (
                r.get("video_url_caption") if captioned and r.get("video_url_caption")
                else r.get("video_url")
            )

    overall_ok = success_count == len(aspects)
    logger.info(
        "[heygen_renderer] hook_label=%r ok=%s success=%d/%d",
        hook_label, overall_ok, success_count, len(aspects),
    )

    return {
        "ok": overall_ok,
        "hook_label": hook_label,
        "script": script,
        "results": results,
        "submit_count": len(submitted_ok),
        "success_count": success_count,
        "errors": errors,
    }
