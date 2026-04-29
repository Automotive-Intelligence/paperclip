"""tools/higgsfield.py — Higgsfield Cloud API wrapper for Internal Marketing.

Wraps the official `higgsfield-client` Python SDK so Internal Marketing can
generate lipsync videos (talking-avatar Book'd ad creative), cinematic videos
(B-roll + pattern interrupts), and images for ad campaigns. All output flows
through Ryan's prepaid Higgsfield credit pool.

Auth (env vars):
  HF_KEY                       single combined key in form 'api_key:api_secret'
  --or--
  HF_API_KEY                   public key
  HF_API_SECRET                secret

The SDK reads these directly at first call (CredentialsMissedError if absent).
This module just confirms they're set before each call and surfaces clear errors.

Application paths (env vars — no defaults; must be set after Ryan confirms
the Cloud-API-accessible application IDs from his dashboard):
  HF_APP_LIPSYNC                   e.g. 'higgsfield/lipsync-2/v1'
  HF_APP_TEXT2VIDEO                e.g. 'higgsfield/sora/v2/text-to-video'
  HF_APP_IMAGE2VIDEO               e.g. 'higgsfield/sora/v2/image-to-video'

Storage:
  HF_DOWNLOAD_DIR                  local dir for download_output (default /tmp/higgsfield_outputs)
  HF_R2_BUCKET / HF_R2_ENDPOINT /  if set, finished outputs also upload to R2
  HF_R2_ACCESS_KEY / HF_R2_SECRET_KEY

Per-process credit budget guard:
  HF_MAX_CALLS_PER_PROCESS         default 25; submit calls beyond cap return error string

Tools exposed:
  - generate_lipsync_video(script_text, reference_image_url, voice_options="")
  - generate_video(prompt, mode, image_url, duration, aspect_ratio, resolution)
  - poll_higgsfield_status(generation_id)
  - download_higgsfield_output(generation_id, filename="")
  - list_higgsfield_models()

Errors return as strings (matches web_search_tool / keyapi.py / shopify.py /
klaviyo.py pattern) so the LLM gets useful feedback instead of crashed Crew.
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

DEFAULT_DOWNLOAD_DIR = "/tmp/higgsfield_outputs"
DEFAULT_MAX_CALLS = 25
DEFAULT_TIMEOUT = 30
# API host is platform.higgsfield.ai (cloud.higgsfield.ai is the dashboard).
# Auth scheme is `Authorization: Key <api_key:api_secret>`, NOT Bearer.
# Both confirmed by inspecting higgsfield_client SDK source 2026-04-28.
HIGGSFIELD_API_BASE = "https://platform.higgsfield.ai"

# Status names mirror higgsfield_client.types_ — kept as plain strings so
# this module doesn't crash at import time if the SDK is missing.
TERMINAL_STATUSES = {"Completed", "Failed", "NSFW", "Cancelled"}


_call_count = 0
_call_lock = threading.Lock()


def _credentials_set() -> bool:
    if (os.environ.get("HF_KEY") or "").strip():
        return True
    return bool(
        (os.environ.get("HF_API_KEY") or "").strip()
        and (os.environ.get("HF_API_SECRET") or "").strip()
    )


def _max_calls() -> int:
    raw = (os.environ.get("HF_MAX_CALLS_PER_PROCESS") or "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_CALLS
    except ValueError:
        return DEFAULT_MAX_CALLS


def _check_and_increment_budget() -> str | None:
    """Return None if under budget, else error string."""
    global _call_count
    with _call_lock:
        if _call_count >= _max_calls():
            return (
                f"ERROR: Higgsfield per-process call budget exceeded "
                f"({_call_count}/{_max_calls()}). Raise HF_MAX_CALLS_PER_PROCESS "
                f"or restart the worker if this was intentional."
            )
        _call_count += 1
        return None


def _truncate_json(obj: Any, max_chars: int = 4000) -> str:
    out = json.dumps(obj, indent=2, default=str)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n\n[...truncated for context budget...]"
    return out


def _import_sdk():
    """Lazy import — keeps this module importable even before the SDK is installed."""
    try:
        import higgsfield_client  # noqa: F401
        return higgsfield_client
    except ImportError as e:
        return f"ERROR: higgsfield-client SDK not installed; add 'higgsfield-client' to requirements.txt and redeploy. ({e})"


def _resolve_application(env_var: str, override: str | None, label: str) -> str | None:
    """Resolve an application path from env var or explicit override.
    Returns either the resolved path string or None (with the caller emitting an error)."""
    val = (override or os.environ.get(env_var) or "").strip()
    return val or None


# ---------------------------------------------------------------------------
# Submit / generate tools
# ---------------------------------------------------------------------------

@tool("Generate Lipsync Video (Higgsfield)")
def generate_lipsync_video(
    script_text: str,
    reference_image_url: str,
    voice_options: str = "",
    application: str = "",
) -> str:
    """Submit a lipsync (talking-avatar) video generation to Higgsfield.

    Use this for the Book'd Launch 2 ad creative pipeline: pass a hook script
    and Ryan's reference photo URL, get back a request_id. Poll with
    poll_higgsfield_status until completed, then download_higgsfield_output.

    Args:
        script_text: The spoken text for the avatar.
        reference_image_url: Publicly reachable URL of the reference photo
            (use higgsfield_client.upload_file to get a URL if you only have
            a local path).
        voice_options: JSON string with optional voice config (e.g.
            '{"voice_id": "ryan_voice_clone", "speed": 1.0}'). Empty = SDK defaults.
        application: Override HF_APP_LIPSYNC env var with a specific app path
            (e.g. 'higgsfield/lipsync-2/v1'). Empty = use env var.

    Returns: JSON string with `request_id` and `application` on success;
        error string on failure.
    """
    script_text = (script_text or "").strip()
    reference_image_url = (reference_image_url or "").strip()
    if not script_text:
        return "ERROR: script_text is required."
    if not reference_image_url:
        return "ERROR: reference_image_url is required."
    if not _credentials_set():
        return "ERROR: HF_KEY (or HF_API_KEY + HF_API_SECRET) env var(s) not set."

    app_path = _resolve_application("HF_APP_LIPSYNC", application, "lipsync")
    if not app_path:
        return (
            "ERROR: HF_APP_LIPSYNC env var not set and no application override "
            "passed. Run list_higgsfield_models() to discover the lipsync app "
            "path your account exposes, then set HF_APP_LIPSYNC on Railway."
        )

    over_budget = _check_and_increment_budget()
    if over_budget:
        return over_budget

    sdk = _import_sdk()
    if isinstance(sdk, str):
        return sdk

    arguments: dict[str, Any] = {
        "script": script_text,
        "reference_image_url": reference_image_url,
    }
    if voice_options.strip():
        try:
            arguments["voice_options"] = json.loads(voice_options)
        except json.JSONDecodeError as e:
            return f"ERROR: voice_options is not valid JSON: {e}"

    try:
        controller = sdk.submit(app_path, arguments=arguments)
    except Exception as e:
        logger.exception("higgsfield submit (lipsync) failed")
        return f"ERROR: Higgsfield submit failed: {type(e).__name__}: {e}"

    request_id = getattr(controller, "request_id", None) or getattr(controller, "id", None)
    return _truncate_json({
        "ok": True,
        "request_id": request_id,
        "application": app_path,
        "kind": "lipsync",
    })


@tool("Generate Video (Higgsfield)")
def generate_video(
    prompt: str,
    mode: str = "text2video",
    image_url: str = "",
    duration: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "2K",
    application: str = "",
) -> str:
    """Submit a cinematic video generation to Higgsfield (B-roll, pattern interrupts).

    Args:
        prompt: Text description of desired video content.
        mode: 'text2video' or 'image2video'. Default 'text2video'.
        image_url: First-frame image URL — REQUIRED when mode='image2video'.
        duration: Video duration in seconds. Higgsfield typically supports 5/10/15.
        aspect_ratio: '16:9' / '9:16' / '1:1'. Default '16:9'.
        resolution: '720p' / '1080p' / '2K'. Default '2K'.
        application: Override HF_APP_TEXT2VIDEO / HF_APP_IMAGE2VIDEO env vars.

    Returns: JSON string with `request_id` and `application`; error string on failure.
    """
    prompt = (prompt or "").strip()
    mode = (mode or "text2video").strip().lower().replace("-", "")
    if mode in ("texttovideo", "txt2vid"):
        mode = "text2video"
    if mode in ("imagetovideo", "img2vid", "i2v"):
        mode = "image2video"
    if mode not in ("text2video", "image2video"):
        return f"ERROR: mode must be 'text2video' or 'image2video', got {mode!r}"

    if not prompt:
        return "ERROR: prompt is required."
    if mode == "image2video" and not (image_url or "").strip():
        return "ERROR: image_url is required when mode='image2video'."
    if not _credentials_set():
        return "ERROR: HF_KEY (or HF_API_KEY + HF_API_SECRET) env var(s) not set."

    env_var = "HF_APP_IMAGE2VIDEO" if mode == "image2video" else "HF_APP_TEXT2VIDEO"
    app_path = _resolve_application(env_var, application, mode)
    if not app_path:
        return (
            f"ERROR: {env_var} env var not set and no application override passed. "
            "Run list_higgsfield_models() to discover available app paths, then "
            f"set {env_var} on Railway."
        )

    over_budget = _check_and_increment_budget()
    if over_budget:
        return over_budget

    sdk = _import_sdk()
    if isinstance(sdk, str):
        return sdk

    arguments: dict[str, Any] = {
        "prompt": prompt,
        "duration": int(duration) if duration else 5,
        "aspect_ratio": aspect_ratio or "16:9",
        "resolution": resolution or "2K",
    }
    if mode == "image2video":
        arguments["image_url"] = image_url.strip()

    try:
        controller = sdk.submit(app_path, arguments=arguments)
    except Exception as e:
        logger.exception("higgsfield submit (%s) failed", mode)
        return f"ERROR: Higgsfield submit failed: {type(e).__name__}: {e}"

    request_id = getattr(controller, "request_id", None) or getattr(controller, "id", None)
    return _truncate_json({
        "ok": True,
        "request_id": request_id,
        "application": app_path,
        "kind": mode,
    })


# ---------------------------------------------------------------------------
# Status / result tools
# ---------------------------------------------------------------------------

@tool("Poll Higgsfield Generation Status")
def poll_higgsfield_status(generation_id: str) -> str:
    """Check the status of a Higgsfield generation by request_id.

    Args:
        generation_id: The request_id returned from generate_lipsync_video or
            generate_video.

    Returns: JSON string with `status` (one of queued/in_progress/completed/
        failed/nsfw/cancelled) and, when completed, the `result` dict (which
        typically contains an output URL); error string on failure.
    """
    rid = (generation_id or "").strip()
    if not rid:
        return "ERROR: generation_id is required."
    if not _credentials_set():
        return "ERROR: HF_KEY (or HF_API_KEY + HF_API_SECRET) env var(s) not set."

    sdk = _import_sdk()
    if isinstance(sdk, str):
        return sdk

    try:
        status_obj = sdk.status(request_id=rid)
    except Exception as e:
        logger.exception("higgsfield status failed for %s", rid)
        return f"ERROR: Higgsfield status check failed: {type(e).__name__}: {e}"

    status_name = type(status_obj).__name__  # e.g. 'Queued' / 'InProgress' / 'Completed'
    payload: dict[str, Any] = {
        "request_id": rid,
        "status": status_name.lower(),
    }

    if status_name == "Completed":
        try:
            result = sdk.result(request_id=rid)
            payload["result"] = result
            # Surface common URL fields for the LLM's convenience
            for k in ("video_url", "url", "videos", "images", "output_url"):
                if isinstance(result, dict) and k in result:
                    payload["primary_output_hint"] = {k: result[k]}
                    break
        except Exception as e:
            payload["result_error"] = f"{type(e).__name__}: {e}"
    elif status_name in ("Failed", "NSFW", "Cancelled"):
        payload["terminal"] = True

    return _truncate_json(payload)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_dir() -> str:
    return (os.environ.get("HF_DOWNLOAD_DIR") or "").strip() or DEFAULT_DOWNLOAD_DIR


def _r2_configured() -> bool:
    needed = ("HF_R2_BUCKET", "HF_R2_ENDPOINT", "HF_R2_ACCESS_KEY", "HF_R2_SECRET_KEY")
    return all((os.environ.get(k) or "").strip() for k in needed)


def _upload_to_r2(local_path: str, key: str) -> str | None:
    """Upload local file to R2 if configured. Returns public URL or None."""
    if not _r2_configured():
        return None
    try:
        import boto3  # boto3 isn't in requirements; only succeeds if added
    except ImportError:
        logger.info("[higgsfield] boto3 not installed; skipping R2 upload")
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
        logger.warning("[higgsfield] R2 upload failed for %s: %s", key, e)
        return None

    public_base = (os.environ.get("HF_R2_PUBLIC_BASE") or "").strip().rstrip("/")
    if public_base:
        return f"{public_base}/{key}"
    return f"r2://{os.environ['HF_R2_BUCKET']}/{key}"


@tool("Download Higgsfield Output")
def download_higgsfield_output(generation_id: str, filename: str = "") -> str:
    """Download a completed Higgsfield generation to local storage.

    Looks up the result for the given generation_id, finds the primary output
    URL (video_url / url / first item in videos/images), HTTP-GETs the bytes,
    saves to HF_DOWNLOAD_DIR. If R2 is configured (HF_R2_BUCKET +
    HF_R2_ACCESS_KEY etc.), also uploads to R2 and returns the R2 URL.

    Args:
        generation_id: request_id from a completed generation.
        filename: Optional override for the saved filename. Empty = derive
            from URL extension.

    Returns: JSON string with `local_path` and (when R2 configured)
        `r2_url`; error string on failure.
    """
    rid = (generation_id or "").strip()
    if not rid:
        return "ERROR: generation_id is required."
    if not _credentials_set():
        return "ERROR: HF_KEY (or HF_API_KEY + HF_API_SECRET) env var(s) not set."

    sdk = _import_sdk()
    if isinstance(sdk, str):
        return sdk

    try:
        status_obj = sdk.status(request_id=rid)
    except Exception as e:
        return f"ERROR: status check failed: {type(e).__name__}: {e}"

    if type(status_obj).__name__ != "Completed":
        return (
            f"ERROR: cannot download — status is {type(status_obj).__name__!r}, "
            "must be 'Completed'. Poll until completed first."
        )

    try:
        result = sdk.result(request_id=rid)
    except Exception as e:
        return f"ERROR: result fetch failed: {type(e).__name__}: {e}"

    output_url = None
    if isinstance(result, dict):
        if isinstance(result.get("video_url"), str):
            output_url = result["video_url"]
        elif isinstance(result.get("url"), str):
            output_url = result["url"]
        elif isinstance(result.get("videos"), list) and result["videos"]:
            first = result["videos"][0]
            output_url = first.get("url") if isinstance(first, dict) else first
        elif isinstance(result.get("images"), list) and result["images"]:
            first = result["images"][0]
            output_url = first.get("url") if isinstance(first, dict) else first

    if not output_url:
        return f"ERROR: could not find output URL in result. Raw: {json.dumps(result)[:300]}"

    # Derive filename from URL if not provided
    if not filename:
        from urllib.parse import urlparse
        path = urlparse(output_url).path
        ext = os.path.splitext(path)[1] or ".bin"
        filename = f"{rid}{ext}"

    local_dir = _download_dir()
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)

    try:
        with requests.get(output_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        return f"ERROR: download failed: {type(e).__name__}: {e}"

    payload: dict[str, Any] = {
        "ok": True,
        "request_id": rid,
        "local_path": local_path,
        "source_url": output_url,
    }

    r2_url = _upload_to_r2(local_path, key=filename)
    if r2_url:
        payload["r2_url"] = r2_url

    return _truncate_json(payload)


# ---------------------------------------------------------------------------
# Discovery: what models does this account expose via Cloud API?
# ---------------------------------------------------------------------------

# Curated application paths to probe during discovery. Verified or plausible
# based on Higgsfield's public model lineup (Speak v2, Lipsync-2, InfiniteTalk,
# Kling AI Avatar/Lipsync, Veo 3, Sora 2, Soul, FLUX, Seedream, Seedance) and
# the documented pattern '<vendor>/<model>/<variant>'. Probe with empty body —
# valid paths return 422 (validation) or 400 (missing fields), invalid paths
# return 404 'Model not found'. Verified working as of 2026-04-28:
#   bytedance/seedream/v4/text-to-image
KNOWN_VERIFIED_PATHS = [
    "bytedance/seedream/v4/text-to-image",  # ✓ confirmed live 2026-04-28
]
CANDIDATE_PATHS = [
    # text/image generation
    "bytedance/seedream/v4/image-to-image",
    "bytedance/seedream/v3/text-to-image",
    "black-forest-labs/flux/v1/text-to-image",
    "black-forest-labs/flux/v2/text-to-image",
    "higgsfield/soul/v1/text-to-image",
    "higgsfield/nano-banana-pro/v1/text-to-image",
    # video generation
    "bytedance/seedance/v1/text-to-video",
    "bytedance/seedance/v1/image-to-video",
    "kling/v3/text-to-video",
    "kling/v3/image-to-video",
    "openai/sora/v2/text-to-video",
    "openai/sora/v2/image-to-video",
    "google/veo/v3/text-to-video",
    "google/veo/v3.1/text-to-video",
    # lipsync / talking avatar
    "higgsfield/lipsync/v2",
    "higgsfield/speak/v2",
    "kling/avatar/v1",
    "kling/lipsync/v1",
    # avatar / digital twin
    "higgsfield/soul-id/train",
    "higgsfield/soul-id/generate",
]


@tool("List Higgsfield Models")
def list_higgsfield_models() -> str:
    """Probe Higgsfield's API for application paths your account can access.

    The official Higgsfield Cloud API does not expose a list-applications
    endpoint. This tool probes a curated list of known + plausible application
    path strings via POST with an empty body. Valid paths return 4xx-with-body
    (e.g. 422 'missing fields'); invalid paths return 404 'Model not found'.
    Costs no credits — generation is not started since no required fields
    are passed.

    Use during onboarding to discover the real path strings for HF_APP_LIPSYNC,
    HF_APP_TEXT2VIDEO, HF_APP_IMAGE2VIDEO. Once the dashboard's API examples
    panel is consulted, set those env vars directly and skip this discovery.

    Returns: JSON string with valid + invalid paths separated.
    """
    if not _credentials_set():
        return "ERROR: HF_KEY (or HF_API_KEY + HF_API_SECRET) env var(s) not set."

    combined = (os.environ.get("HF_KEY") or "").strip()
    if not combined:
        api_key = (os.environ.get("HF_API_KEY") or "").strip()
        api_secret = (os.environ.get("HF_API_SECRET") or "").strip()
        if api_key and api_secret:
            combined = f"{api_key}:{api_secret}"

    # Higgsfield uses 'Authorization: Key <combined>', NOT Bearer
    headers = {
        "Authorization": f"Key {combined}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    valid: list[str] = []
    invalid: list[str] = []
    other: list[dict[str, Any]] = []

    for path in KNOWN_VERIFIED_PATHS + CANDIDATE_PATHS:
        url = f"{HIGGSFIELD_API_BASE}/{path}"
        try:
            resp = requests.post(url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT)
        except requests.exceptions.RequestException as e:
            other.append({"path": path, "error": f"{type(e).__name__}: {e}"})
            continue

        body_lower = resp.text.lower() if resp.text else ""
        if resp.status_code == 404 and "model not found" in body_lower:
            invalid.append(path)
        elif resp.status_code == 200 or (resp.status_code >= 400 and "model not found" not in body_lower):
            # 200 = somehow accepted (would burn credits — empty body shouldn't, but flag)
            # 4xx with non-"Model not found" body usually means valid path with bad inputs
            valid.append(path)
        else:
            other.append({"path": path, "status": resp.status_code, "body": resp.text[:120]})

    return _truncate_json({
        "ok": True,
        "verified_paths": valid,
        "model_not_found": invalid,
        "other_responses": other,
        "hint": (
            "Set HF_APP_LIPSYNC / HF_APP_TEXT2VIDEO / HF_APP_IMAGE2VIDEO env vars on Railway "
            "to one of the verified_paths. If the path you need isn't listed, check the API "
            "examples panel in the Higgsfield Cloud dashboard for the exact application string."
        ),
    })


# ---------------------------------------------------------------------------
# Status / observability
# ---------------------------------------------------------------------------

def higgsfield_status() -> dict[str, Any]:
    """Lightweight observability for /admin endpoints — reports config state
    without leaking secrets."""
    return {
        "credentials_set": _credentials_set(),
        "auth_form": (
            "HF_KEY" if (os.environ.get("HF_KEY") or "").strip()
            else "HF_API_KEY+SECRET" if _credentials_set()
            else "none"
        ),
        "applications": {
            "lipsync": (os.environ.get("HF_APP_LIPSYNC") or "") or None,
            "text2video": (os.environ.get("HF_APP_TEXT2VIDEO") or "") or None,
            "image2video": (os.environ.get("HF_APP_IMAGE2VIDEO") or "") or None,
        },
        "max_calls_per_process": _max_calls(),
        "calls_this_process": _call_count,
        "download_dir": _download_dir(),
        "r2_configured": _r2_configured(),
    }


HIGGSFIELD_TOOLS = [
    generate_lipsync_video,
    generate_video,
    poll_higgsfield_status,
    download_higgsfield_output,
    list_higgsfield_models,
]
