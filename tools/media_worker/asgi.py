"""ASGI entrypoint for the media worker.

paperclip's railway.toml sets the deploy startCommand to
`uvicorn app:app --host 0.0.0.0 --port $PORT`, and Railway applies that to
THIS service too (it wins over the dashboard setting). The lean media-worker
image has no uvicorn and no paperclip app.py, so that command crashed with
"uvicorn cannot be found". Instead of fighting Railway's config precedence, we
make that exact command valid: uvicorn is installed in the image, and this file
is COPYd to /app/app.py so `uvicorn app:app` imports THIS app.

It binds the port (so Railway's healthcheck passes) and exposes an
authenticated POST /run-video trigger that renders one take on demand and
pushes the master + sheet to Blob. Stage-and-flag only; the handler never
schedules or publishes anything outward -- it renders, stages to Blob, and
appends a REVIEW_LOG entry for a human/watchdog to act on.

The one-shot startup render (the original v1 behavior: render a single take
as soon as the container boots) is now OPTIONAL, gated on RENDER_ON_STARTUP,
because the worker is trigger-driven going forward. Raw ASGI (no fastapi
dependency in this lean image), so the request body is read straight off the
ASGI `receive` channel.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
from typing import Optional

from services.routine_auth import routine_token_valid
from tools.media_worker.job import run_job
from tools.media_worker.poller import poll_once

_started = False
_lock = threading.Lock()

# Serializes actual renders across BOTH trigger paths (manual /run-video and the
# auto /poll) so two ffmpeg/whisper jobs never contend for one container's CPU +
# disk. Non-blocking: a second render request while one is in flight is told
# "busy" rather than queued, since the poll is idempotent and just retries next
# cycle, and a manual caller would rather get an immediate answer than hang.
_render_lock = threading.Lock()


def _render_once() -> None:
    # The startup render is the THIRD render entrypoint (with /run-video and
    # /poll); it must share _render_lock too, or a boot render could run
    # concurrently with a poll/manual render and contend for the container.
    try:
        print("[asgi] render job starting", flush=True)
        _render_lock.acquire()
        try:
            urls = run_job(os.environ)
        finally:
            _render_lock.release()
        print(f"[asgi] render job DONE master={urls.get('master_url')}", flush=True)
    except Exception:
        print("[asgi] render job FAILED", flush=True)
        traceback.print_exc()


def _kickoff() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_render_once, daemon=True).start()


# Trigger-driven by default: only auto-render on import when explicitly asked
# for (e.g. a one-off boot-render deploy). POST /run-video is the normal path.
if os.getenv("RENDER_ON_STARTUP"):
    _kickoff()


def _header(scope: dict, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []) or []:
        if key.lower() == name:
            return value.decode("utf-8")
    return None


async def _read_body(receive) -> bytes:
    body = b""
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        more_body = message.get("more_body", False)
    return body


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


async def _handle_run_video(scope: dict, receive, send) -> None:
    """POST /run-video: authenticate via the scoped VIDEO_ROUTINE_TOKEN, render
    the posted take, and stage it to Blob. Body: {take, edit}. `take` is the
    Blob pathname of the raw take; `edit` is the edit dict (optional -- when
    omitted, run_job falls back to its own {"brand": BRAND} default). Never
    schedules or publishes; a human/watchdog reads the REVIEW_LOG flag."""
    authorization = _header(scope, b"authorization")
    expected_token = os.getenv("VIDEO_ROUTINE_TOKEN", "")
    if not routine_token_valid(authorization, expected_token):
        await _send_json(send, 401, {"error": "unauthorized"})
        return

    raw_body = await _read_body(receive)
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        await _send_json(send, 400, {"error": "invalid JSON body"})
        return

    take = payload.get("take")
    if not take:
        await _send_json(send, 400, {"error": "missing 'take'"})
        return
    edit = payload.get("edit")

    env = dict(os.environ)
    env["TAKE_PATHNAME"] = take
    if edit is not None:
        env["EDIT_JSON"] = json.dumps(edit)
    else:
        env.pop("EDIT_JSON", None)

    if not _render_lock.acquire(blocking=False):
        await _send_json(send, 409, {"status": "busy",
                                     "error": "a render is already in progress"})
        return
    try:
        urls = run_job(env)
    except Exception as exc:
        await _send_json(send, 500, {"error": str(exc)})
        return
    finally:
        _render_lock.release()

    await _send_json(send, 200, {
        "status": "staged",
        "master_url": urls.get("master_url"),
        "sheet_url": urls.get("sheet_url"),
    })


async def _handle_poll(scope: dict, receive, send) -> None:
    """POST /poll: the auto-trigger. Authenticate (same VIDEO_ROUTINE_TOKEN as
    /run-video), then render up to POLL_MAX_PER_CYCLE unrendered takes from the
    Blob render queue in a background thread, returning immediately. Holds the
    shared render lock for the whole cycle; if a render is already in flight,
    reports 'busy' and does nothing (the poll is idempotent -- next tick retries).
    Stage-and-flag only, exactly like /run-video: it renders + stages, never
    schedules or publishes."""
    authorization = _header(scope, b"authorization")
    expected_token = os.getenv("VIDEO_ROUTINE_TOKEN", "")
    if not routine_token_valid(authorization, expected_token):
        await _send_json(send, 401, {"error": "unauthorized"})
        return

    if not _render_lock.acquire(blocking=False):
        await _send_json(send, 200, {"status": "busy"})
        return

    def _run_poll() -> None:
        try:
            summary = poll_once(dict(os.environ))
            print(f"[asgi] poll cycle done: {summary}", flush=True)
        except Exception:
            print("[asgi] poll cycle FAILED", flush=True)
            traceback.print_exc()
        finally:
            _render_lock.release()

    # The background thread's finally releases the lock -- but only if the thread
    # actually starts. If start() itself raises (e.g. thread exhaustion), release
    # here so the lock is never leaked into a permanent 'busy' wedge.
    try:
        threading.Thread(target=_run_poll, daemon=True).start()
    except BaseException:
        _render_lock.release()
        raise
    await _send_json(send, 200, {"status": "polling"})


async def app(scope, receive, send):
    """Raw ASGI app: GET /health (and /) -> 200 for Railway's healthcheck;
    POST /run-video -> the authenticated manual render trigger; POST /poll ->
    the authenticated auto-trigger (render the next unrendered queued take);
    anything else -> 404. Only the real health path answers 200 so a monitor
    hitting a typo'd path cannot read "healthy" for a route that does not exist."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return
    if scope["type"] != "http":
        return

    method = scope.get("method", "GET")
    path = scope.get("path", "/")

    if method == "POST" and path == "/run-video":
        await _handle_run_video(scope, receive, send)
        return

    if method == "POST" and path == "/poll":
        await _handle_poll(scope, receive, send)
        return

    if method == "GET" and path in ("/health", "/"):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
        return

    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"not found"})
