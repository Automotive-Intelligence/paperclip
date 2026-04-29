"""tools/heygen.py — HeyGen REST API wrapper for talking-avatar / lipsync video.

Wraps HeyGen's REST API (api.heygen.com) for the Book'd Launch 2 ad creative
pipeline. Higgsfield's Cloud API does NOT expose lipsync, so HeyGen owns the
talking-avatar pieces (5 hook scripts, VSL, founder-story content); Higgsfield
keeps image gen + B-roll image-to-video. Two complementary tools, not
competing replacements.

Auth (env var):
  HEYGEN_API_KEY          single key from HeyGen Cloud console
                          (HeyGen uses 'x-api-key' header, NOT Bearer)

Pricing model (verified 2026-04-28): pay-as-you-go API wallet, no monthly
subscription required. ~$1/min standard 720p/1080p, ~$4/min Avatar IV 1080p.
Top up wallet starting at $5.

Per-process call budget guard:
  HEYGEN_MAX_CALLS_PER_PROCESS    default 25 — protects wallet from runaway Crew

Storage:
  HEYGEN_DOWNLOAD_DIR             default /tmp/heygen_outputs
  Phase 2 R2 upload: shares HF_R2_* env vars from tools/higgsfield.py since
  R2 storage is workflow-agnostic. (Both video-gen tools land outputs in the
  same R2 bucket if configured.)

Tools exposed:
  - list_heygen_avatars()
  - list_heygen_voices()
  - generate_avatar_video(script, avatar_id, voice_id, dimension, ...)
  - poll_heygen_video_status(video_id)
  - download_heygen_video(video_id, filename)

Errors return as strings (matches web_search_tool / keyapi.py / shopify.py /
klaviyo.py / higgsfield.py pattern) so the LLM gets useful feedback instead
of a crashed Crew.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import requests
from crewai.tools import tool

logger = logging.getLogger(__name__)

HEYGEN_API_BASE = "https://api.heygen.com"
DEFAULT_DOWNLOAD_DIR = "/tmp/heygen_outputs"
DEFAULT_MAX_CALLS = 25
DEFAULT_TIMEOUT = 60  # generation submit can be slower than other APIs

_call_count = 0
_call_lock = threading.Lock()


def _api_key() -> str | None:
    val = (os.environ.get("HEYGEN_API_KEY") or "").strip()
    return val or None


def _max_calls() -> int:
    raw = (os.environ.get("HEYGEN_MAX_CALLS_PER_PROCESS") or "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_CALLS
    except ValueError:
        return DEFAULT_MAX_CALLS


def _check_and_increment_budget() -> str | None:
    global _call_count
    with _call_lock:
        if _call_count >= _max_calls():
            return (
                f"ERROR: HeyGen per-process call budget exceeded "
                f"({_call_count}/{_max_calls()}). Raise HEYGEN_MAX_CALLS_PER_PROCESS "
                f"or restart the worker if intentional."
            )
        _call_count += 1
        return None


def _truncate_json(obj: Any, max_chars: int = 6000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[...truncated for context budget...]"
    return out


def _heygen_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    count_against_budget: bool = True,
) -> dict[str, Any] | str:
    """Low-level HeyGen HTTP. Returns parsed JSON dict on success, or a
    human-readable error string on failure. Never raises."""
    api_key = _api_key()
    if not api_key:
        return "ERROR: HEYGEN_API_KEY env var not set."

    if count_against_budget:
        over_budget = _check_and_increment_budget()
        if over_budget:
            return over_budget

    url = f"{HEYGEN_API_BASE}{path}"
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params or {},
            json=json_body,
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: HeyGen timeout on {method} {path} (>{DEFAULT_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        return f"ERROR: HeyGen request failed on {method} {path}: {type(e).__name__}: {e}"

    if resp.status_code in (401, 403):
        return f"ERROR: HeyGen rejected the API key (HTTP {resp.status_code}). Verify HEYGEN_API_KEY is valid."
    if resp.status_code == 402:
        return "ERROR: HeyGen wallet out of funds (402). Top up at https://app.heygen.com/api/wallet."
    if resp.status_code == 429:
        return "ERROR: HeyGen rate limit hit (429). Slow down or wait a few seconds."
    if resp.status_code >= 400:
        return f"ERROR: HeyGen HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}"

    if resp.status_code == 204 or not resp.content:
        return {"ok": True, "status": resp.status_code}

    try:
        body = resp.json()
    except ValueError:
        return f"ERROR: HeyGen returned non-JSON on {method} {path}: {resp.text[:300]}"

    # HeyGen v2 wraps responses as {error: null, data: {...}}; v1 status uses {code, data, message}
    if isinstance(body, dict):
        err = body.get("error")
        if err and isinstance(err, dict) and err.get("message"):
            return f"ERROR: HeyGen logical error on {path}: {err.get('code')} {err['message']}"
        if err and isinstance(err, str):
            return f"ERROR: HeyGen logical error on {path}: {err}"
    return body


# ---------------------------------------------------------------------------
# List avatars / voices
# ---------------------------------------------------------------------------

@tool("List HeyGen Avatars")
def list_heygen_avatars() -> str:
    """List all avatars + talking photos available in the HeyGen account.

    Use this to discover the avatar_id needed for generate_avatar_video.
    HeyGen's library includes stock avatars (free with API usage), premium
    avatars (extra cost), and any custom avatars trained from Ryan's
    reference photos (talking_photo entries — created in the web UI).

    Returns: JSON with avatars[] (avatar_id, avatar_name, gender, preview URLs,
    default_voice_id, premium flag) and talking_photos[] (talking_photo_id,
    talking_photo_name, preview_image_url). Or error string.
    """
    resp = _heygen_request("GET", "/v2/avatars", count_against_budget=False)
    if isinstance(resp, str):
        return resp
    data = resp.get("data") or {}
    avatars = data.get("avatars") or []
    talking_photos = data.get("talking_photos") or []
    return _truncate_json({
        "avatars_count": len(avatars),
        "talking_photos_count": len(talking_photos),
        "avatars": [
            {
                "avatar_id": a.get("avatar_id"),
                "avatar_name": a.get("avatar_name"),
                "gender": a.get("gender"),
                "premium": a.get("premium"),
                "default_voice_id": a.get("default_voice_id"),
                "preview_image_url": a.get("preview_image_url"),
                "tags": a.get("tags"),
            }
            for a in avatars[:60]
        ],
        "talking_photos": [
            {
                "talking_photo_id": t.get("talking_photo_id"),
                "talking_photo_name": t.get("talking_photo_name"),
                "preview_image_url": t.get("preview_image_url"),
            }
            for t in talking_photos[:60]
        ],
    })


@tool("List HeyGen Voices")
def list_heygen_voices() -> str:
    """List all voices available for HeyGen avatar video generation.

    Use this to find a voice_id matching the desired tone for the script —
    or to confirm Ryan's custom voice clone (if uploaded) is accessible.
    Voice IDs feed into generate_avatar_video.

    Returns: JSON with voices[] (voice_id, language, gender, name, preview URL)
    or error string.
    """
    resp = _heygen_request("GET", "/v2/voices", count_against_budget=False)
    if isinstance(resp, str):
        return resp
    data = resp.get("data") or {}
    voices = data.get("voices") or []
    return _truncate_json({
        "voices_count": len(voices),
        "voices": [
            {
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "language": v.get("language"),
                "gender": v.get("gender"),
                "preview_audio": v.get("preview_audio"),
                "support_pause": v.get("support_pause"),
                "emotion_support": v.get("emotion_support"),
            }
            for v in voices[:120]
        ],
    })


# ---------------------------------------------------------------------------
# Generate avatar video
# ---------------------------------------------------------------------------

@tool("Generate HeyGen Avatar Video")
def generate_avatar_video(
    script: str,
    avatar_id: str,
    voice_id: str,
    width: int = 1280,
    height: int = 720,
    avatar_style: str = "normal",
    background_color: str = "#FFFFFF",
    title: str = "",
) -> str:
    """Submit a talking-avatar lipsync video generation to HeyGen.

    Core Book'd Launch 2 workflow tool. Pass a script + an avatar_id (from
    list_heygen_avatars) + a voice_id (from list_heygen_voices). Returns
    a video_id that you poll with poll_heygen_video_status until completed,
    then download with download_heygen_video.

    For a custom avatar trained from Ryan's reference photo, use the
    talking_photo_id from list_heygen_avatars but pass it as avatar_id with
    avatar_style='normal'. (HeyGen's API treats both stock avatars and
    custom talking photos through the same `character.type=avatar` envelope
    in the v2 generate endpoint when using avatar_id; the talking_photo
    flow has its own `character.type=talking_photo` envelope handled
    separately if needed in a future PR.)

    Args:
        script: The spoken text. Max ~1500 characters per scene.
        avatar_id: HeyGen avatar_id from list_heygen_avatars().
        voice_id: HeyGen voice_id from list_heygen_voices(). For Ryan's
            cloned voice, pass that voice_id once it's listed.
        width: Output width in pixels. Default 1280 (720p widescreen).
            Use 1080 for 9:16 vertical (Reels/TikTok), 1920 for 1080p.
        height: Output height. Default 720. Use 1920 for 9:16 vertical,
            1080 for 1080p widescreen.
        avatar_style: 'normal' (default), 'closeUp', or 'circle' depending
            on framing.
        background_color: Hex color for solid background. Default '#FFFFFF'.
            For transparent / video backgrounds, future PR — Phase 1 is
            solid color only.
        title: Optional video title for HeyGen dashboard organization.

    Returns: JSON with `video_id` and submission metadata; error string on failure.
    """
    script = (script or "").strip()
    avatar_id = (avatar_id or "").strip()
    voice_id = (voice_id or "").strip()
    if not script:
        return "ERROR: script is required."
    if not avatar_id:
        return "ERROR: avatar_id is required (call list_heygen_avatars first)."
    if not voice_id:
        return "ERROR: voice_id is required (call list_heygen_voices first)."

    body: dict[str, Any] = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": avatar_style or "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice_id,
                },
                "background": {
                    "type": "color",
                    "value": background_color or "#FFFFFF",
                },
            }
        ],
        "dimension": {
            "width": int(width) if width else 1280,
            "height": int(height) if height else 720,
        },
    }
    if title.strip():
        body["title"] = title.strip()

    resp = _heygen_request("POST", "/v2/video/generate", json_body=body)
    if isinstance(resp, str):
        return resp

    data = resp.get("data") or {}
    video_id = data.get("video_id")
    if not video_id:
        return f"ERROR: HeyGen accepted the request but did not return video_id. Raw: {json.dumps(resp)[:300]}"

    return _truncate_json({
        "ok": True,
        "video_id": video_id,
        "submitted_avatar_id": avatar_id,
        "submitted_voice_id": voice_id,
        "dimension": body["dimension"],
    })


# ---------------------------------------------------------------------------
# Poll status
# ---------------------------------------------------------------------------

@tool("Poll HeyGen Video Status")
def poll_heygen_video_status(video_id: str) -> str:
    """Check the status of a HeyGen avatar video generation.

    HeyGen status values: pending (queued) | processing (rendering) |
    completed (done — video_url present, valid 7 days) | failed.

    For long renders (VSL-length), poll periodically rather than holding open.
    Typical render time: 1-3 minutes for short hooks, longer for VSL.

    Args:
        video_id: ID returned from generate_avatar_video.

    Returns: JSON with `status`, `duration`, `video_url` (when completed,
    valid for 7 days), `thumbnail_url`, etc.; error string on failure.
    """
    vid = (video_id or "").strip()
    if not vid:
        return "ERROR: video_id is required."

    resp = _heygen_request(
        "GET", "/v1/video_status.get",
        params={"video_id": vid},
        count_against_budget=False,
    )
    if isinstance(resp, str):
        return resp

    data = resp.get("data") or {}
    return _truncate_json({
        "video_id": vid,
        "status": data.get("status"),
        "duration": data.get("duration"),
        "video_url": data.get("video_url"),
        "thumbnail_url": data.get("thumbnail_url"),
        "gif_url": data.get("gif_url"),
        "caption_url": data.get("caption_url"),
        "video_url_caption": data.get("video_url_caption"),
        "error": data.get("error"),
        "created_at": data.get("created_at"),
    })


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_dir() -> str:
    return (os.environ.get("HEYGEN_DOWNLOAD_DIR") or "").strip() or DEFAULT_DOWNLOAD_DIR


def _r2_configured() -> bool:
    """Reuse Higgsfield R2 vars — both video-gen tools land in same bucket if configured."""
    needed = ("HF_R2_BUCKET", "HF_R2_ENDPOINT", "HF_R2_ACCESS_KEY", "HF_R2_SECRET_KEY")
    return all((os.environ.get(k) or "").strip() for k in needed)


def _upload_to_r2(local_path: str, key: str) -> str | None:
    if not _r2_configured():
        return None
    try:
        import boto3  # lazy import — boto3 not in requirements until R2 enabled
    except ImportError:
        logger.info("[heygen] boto3 not installed; skipping R2 upload")
        return None
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["HF_R2_ENDPOINT"],
            aws_access_key_id=os.environ["HF_R2_ACCESS_KEY"],
            aws_secret_access_key=os.environ["HF_R2_SECRET_KEY"],
            region_name=os.environ.get("HF_R2_REGION", "auto"),
        )
        s3.upload_file(local_path, os.environ["HF_R2_BUCKET"], key)
    except Exception as e:
        logger.warning("[heygen] R2 upload failed for %s: %s", key, e)
        return None
    public_base = (os.environ.get("HF_R2_PUBLIC_BASE") or "").strip().rstrip("/")
    if public_base:
        return f"{public_base}/{key}"
    return f"r2://{os.environ['HF_R2_BUCKET']}/{key}"


@tool("Download HeyGen Video")
def download_heygen_video(video_id: str, filename: str = "") -> str:
    """Download a completed HeyGen video to local storage.

    Looks up the video_url, downloads to HEYGEN_DOWNLOAD_DIR, optionally
    uploads to R2 if HF_R2_* env vars are configured (shares the same R2
    bucket as Higgsfield outputs — both video-gen tools land together).

    HeyGen's video_url expires 7 days after render. Download promptly OR
    re-call this tool within the window — re-polling status_url before
    7 days elapse refreshes the link.

    Args:
        video_id: ID from generate_avatar_video.
        filename: Optional filename override. Empty = derive as
            heygen_<video_id>.mp4.

    Returns: JSON with `local_path` and (if R2 configured) `r2_url`;
    error string on failure.
    """
    vid = (video_id or "").strip()
    if not vid:
        return "ERROR: video_id is required."

    # Get the latest status (free — does not count toward budget)
    status_resp = _heygen_request(
        "GET", "/v1/video_status.get",
        params={"video_id": vid},
        count_against_budget=False,
    )
    if isinstance(status_resp, str):
        return status_resp

    data = status_resp.get("data") or {}
    if data.get("status") != "completed":
        return (
            f"ERROR: cannot download — status is {data.get('status')!r}, "
            "must be 'completed'. Poll until completed first."
        )

    video_url = data.get("video_url")
    if not video_url:
        return f"ERROR: completed video has no video_url. Raw: {json.dumps(data)[:300]}"

    local_dir = _download_dir()
    os.makedirs(local_dir, exist_ok=True)
    if not filename:
        filename = f"heygen_{vid}.mp4"
    local_path = os.path.join(local_dir, filename)

    try:
        with requests.get(video_url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        return f"ERROR: download failed: {type(e).__name__}: {e}"

    payload: dict[str, Any] = {
        "ok": True,
        "video_id": vid,
        "local_path": local_path,
        "source_url": video_url,
        "duration": data.get("duration"),
    }
    r2_url = _upload_to_r2(local_path, key=filename)
    if r2_url:
        payload["r2_url"] = r2_url
    return _truncate_json(payload)


# ---------------------------------------------------------------------------
# Status / observability
# ---------------------------------------------------------------------------

def heygen_status() -> dict[str, Any]:
    """Lightweight observability for /admin endpoints — reports config state
    without leaking secrets."""
    return {
        "credentials_set": bool(_api_key()),
        "max_calls_per_process": _max_calls(),
        "calls_this_process": _call_count,
        "download_dir": _download_dir(),
        "r2_configured": _r2_configured(),
    }


@tool("Render HeyGen Ad Variants (multi-aspect)")
def render_heygen_ad_variants(
    script: str,
    avatar_id: str,
    voice_id: str,
    hook_label: str = "",
    aspects: str = "9:16,1:1,16:9",
    captioned: bool = True,
    background_color: str = "#FFFFFF",
) -> str:
    """Render the same script at multiple HeyGen aspect ratios in parallel
    with captions baked in (HeyGen's built-in caption feature — no Submagic
    required). One call returns 9:16 (Reels/TikTok), 1:1 (Feed), and 16:9
    (in-stream) versions of the same talking-avatar ad.

    This is the input ingestion side of the autonomous Book'd ad pipeline —
    used by the orchestrator to produce all aspect variants of one hook
    before handing off to Meta Ads upload.

    Args:
        script: Spoken text. Same script used across all aspect variants.
        avatar_id: HeyGen avatar_id (use list_heygen_avatars first).
        voice_id: HeyGen voice_id (use list_heygen_voices first).
        hook_label: Short label for traceability (e.g. 'hook-1-founder',
            'vsl-segment-2'). Stamped into video titles. Empty = 'untitled'.
        aspects: Comma-separated aspect list. Default '9:16,1:1,16:9'.
            Supported: 9:16, 1:1, 16:9, 4:5.
        captioned: When True (default), recommended_url per aspect is the
            captioned variant (video_url_caption). False = bare video_url.
        background_color: Hex background. Default '#FFFFFF'.

    Returns: JSON string with per-aspect URLs and overall ok flag, or error.
    """
    from services.heygen_renderer import render_ad_variants

    aspect_list = [a.strip() for a in (aspects or "").split(",") if a.strip()]
    if not aspect_list:
        return "ERROR: aspects is required (e.g. '9:16,1:1,16:9')."

    result = render_ad_variants(
        script=script,
        avatar_id=avatar_id,
        voice_id=voice_id,
        hook_label=hook_label or "untitled",
        aspects=tuple(aspect_list),
        captioned=captioned,
        background_color=background_color or "#FFFFFF",
    )
    return _truncate_json(result)


HEYGEN_TOOLS = [
    list_heygen_avatars,
    list_heygen_voices,
    generate_avatar_video,
    poll_heygen_video_status,
    download_heygen_video,
    render_heygen_ad_variants,
]
