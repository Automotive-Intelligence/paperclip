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

_started = False
_lock = threading.Lock()


def _render_once() -> None:
    try:
        print("[asgi] render job starting", flush=True)
        urls = run_job(os.environ)
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

    try:
        urls = run_job(env)
    except Exception as exc:
        await _send_json(send, 500, {"error": str(exc)})
        return

    await _send_json(send, 200, {
        "status": "staged",
        "master_url": urls.get("master_url"),
        "sheet_url": urls.get("sheet_url"),
    })


async def app(scope, receive, send):
    """Raw ASGI app: GET (any path, including /health) -> 200 for Railway's
    healthcheck; POST /run-video -> the authenticated render trigger."""
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

    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})
