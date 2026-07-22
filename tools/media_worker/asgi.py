"""ASGI entrypoint for the media worker.

paperclip's railway.toml sets the deploy startCommand to
`uvicorn app:app --host 0.0.0.0 --port $PORT`, and Railway applies that to
THIS service too (it wins over the dashboard setting). The lean media-worker
image has no uvicorn and no paperclip app.py, so that command crashed with
"uvicorn cannot be found". Instead of fighting Railway's config precedence, we
make that exact command valid: uvicorn is installed in the image, and this file
is COPYd to /app/app.py so `uvicorn app:app` imports THIS app.

It binds the port (so Railway's healthcheck passes), renders one take ONCE on
startup in a background thread, and pushes the master + sheet to Blob. Stage-
and-flag only; it never schedules anything outward.
"""
from __future__ import annotations

import os
import threading
import traceback

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


# kick the render as soon as uvicorn imports this module
_kickoff()


async def app(scope, receive, send):
    """Minimal ASGI app: 200 on any HTTP path so the healthcheck passes."""
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
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})
