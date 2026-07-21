"""tools/media_worker/server.py -- the Railway-friendly entrypoint.

A one-shot render that just exits does not fit Railway's service model:
Railway waits for the container to bind $PORT and stay healthy, and kills it
if it does not (the render takes minutes and binds nothing). So this binds
$PORT immediately (health passes, Railway keeps the container alive), runs the
render ONCE in a background thread on startup, and logs progress + MASTER_URL
so the run is observable in the Railway deploy log. Stage-and-flag only; it
renders and pushes to Blob, it never schedules anything outward.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

from tools.media_worker.job import run_job


def _render_once() -> None:
    try:
        print("[server] render job starting", flush=True)
        urls = run_job(os.environ)
        print(f"[server] render job DONE master={urls.get('master_url')}", flush=True)
    except Exception:
        print("[server] render job FAILED", flush=True)
        traceback.print_exc()
        sys.stdout.flush()


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # keep the deploy log clean
        return


def main() -> int:
    # render once on startup, in the background, so the health port is up immediately
    threading.Thread(target=_render_once, daemon=True).start()
    port = int(os.environ.get("PORT", "8080"))
    print(f"[server] health server listening on :{port}", flush=True)
    HTTPServer(("0.0.0.0", port), _Health).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
