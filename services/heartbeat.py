"""
services/heartbeat.py

Outbound dead-man's-switch heartbeat.

WHY THIS EXISTS
---------------
Paperclip runs on a single Railway service. APScheduler runs in-process,
so when the process dies (OOM, deploy crash, Railway outage), nothing
fires — including the daily agent jobs, the cockpit-bridge poller, and
the morning briefing. Without an external observer, those failures are
silent. The first signal is usually "huh, why didn't I get my briefing
this morning?" hours later.

This module sends a heartbeat ping to an external watcher (healthchecks.io,
Cronitor, BetterStack — anything that accepts an HTTP GET as a liveness
beat). The watcher alerts when the expected ping doesn't arrive within
the grace window.

A heartbeat that runs ON the scheduler proves three things at once:
  1. The Python process is alive
  2. APScheduler is firing jobs (not just registered, actually firing)
  3. The container has outbound network

External HTTP pinging from a monitoring service only proves #1 (and even
that, only that the HTTP server thread is responsive — APScheduler can be
silently dead while uvicorn still answers).

WIRING
------
Set HEARTBEAT_URL in Railway env vars. Recommended value: a healthchecks.io
check URL (free tier, no signup required to create one) configured with
a 10-min schedule + 2-min grace. App.py registers a 5-min interval job
that calls ping_heartbeat() — that gives 2x headroom against the 10-min
schedule so a single missed tick doesn't fire a false alert.

When HEARTBEAT_URL is empty, ping_heartbeat() is a no-op + logs once at
startup. No behavior change in dev environments that don't need it.

SETUP RUNBOOK
-------------
See ~/avo-telemetry/paperclip_uptime_setup.md
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)


HEARTBEAT_ENV_VAR = "HEARTBEAT_URL"
HEARTBEAT_TIMEOUT_SECONDS = 10


def get_heartbeat_url() -> Optional[str]:
    url = os.environ.get(HEARTBEAT_ENV_VAR, "").strip()
    return url or None


def ping_heartbeat(extra_query: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """GET the configured heartbeat URL. Never raises.

    Returns a small status dict so the caller / tests / observability
    endpoints can introspect.

    Outcomes:
      - disabled: HEARTBEAT_URL not set
      - ok:       2xx response
      - http_err: non-2xx response
      - net_err:  request failed (timeout, DNS, etc.)
    """
    url = get_heartbeat_url()
    if url is None:
        return {"outcome": "disabled"}

    start = time.time()
    try:
        resp = requests.get(url, params=extra_query or {}, timeout=HEARTBEAT_TIMEOUT_SECONDS)
        elapsed_ms = int((time.time() - start) * 1000)
        if 200 <= resp.status_code < 300:
            logger.debug("[Heartbeat] ok %dms (status=%d)", elapsed_ms, resp.status_code)
            return {"outcome": "ok", "status": resp.status_code, "elapsed_ms": elapsed_ms}
        logger.warning(
            "[Heartbeat] non-2xx %d after %dms — watcher will treat as failed ping",
            resp.status_code, elapsed_ms,
        )
        return {"outcome": "http_err", "status": resp.status_code, "elapsed_ms": elapsed_ms}
    except requests.RequestException as e:
        elapsed_ms = int((time.time() - start) * 1000)
        logger.warning("[Heartbeat] network error after %dms: %s", elapsed_ms, e)
        return {"outcome": "net_err", "error": str(e), "elapsed_ms": elapsed_ms}


def heartbeat_status() -> Dict[str, object]:
    """Snapshot for /heartbeat/status endpoint."""
    url = get_heartbeat_url()
    return {
        "configured": url is not None,
        # Never return the full URL — healthchecks.io URLs contain a UUID
        # that acts as a secret. Return a fingerprint instead.
        "url_fingerprint": _fingerprint(url) if url else None,
        "env_var": HEARTBEAT_ENV_VAR,
    }


def _fingerprint(url: str) -> str:
    """Stable short tag so Michael can confirm WHICH URL is configured
    without exposing the secret UUID portion."""
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
