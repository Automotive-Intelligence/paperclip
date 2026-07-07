"""Apify wrapper — generic Actor runner.

Apify hosts pre-built "Actors" (scrapers, crawlers, automations) at apify.com/store.
This module provides a thin sync wrapper over Apify's REST API so paperclip / CrewAI
agents can invoke any public Actor by ID and pull back dataset items.

Auth: APIFY_API_KEY env var, Bearer token style.
Docs: https://docs.apify.com/api/v2
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APIFY_API_BASE = "https://api.apify.com/v2"
DEFAULT_TIMEOUT = 30
DEFAULT_RUN_POLL_INTERVAL = 3
DEFAULT_RUN_TIMEOUT_SECS = 300


def _api_key() -> str:
    key = (os.getenv("APIFY_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("APIFY_API_KEY not set. Add it to ~/paperclip/.env.")
    return key


def _headers() -> dict:
    return {"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"}


def me() -> dict:
    """Sanity check — return the authed account's profile."""
    r = httpx.get(f"{APIFY_API_BASE}/users/me", headers=_headers(), timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.json().get("data", {})


def search_actors(query: str, limit: int = 10) -> list[dict]:
    """Search Apify's public Actor store by keyword."""
    r = httpx.get(
        f"{APIFY_API_BASE}/store",
        params={"search": query, "limit": limit},
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("data", {}).get("items", [])


def get_actor(actor_id: str) -> dict:
    """Get metadata for a specific Actor (input schema, default run options, etc.)."""
    r = httpx.get(
        f"{APIFY_API_BASE}/acts/{actor_id}",
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("data", {})


def run_actor(
    actor_id: str,
    input_payload: dict | None = None,
    *,
    wait_for_finish: bool = True,
    timeout_secs: int = DEFAULT_RUN_TIMEOUT_SECS,
    poll_interval: int = DEFAULT_RUN_POLL_INTERVAL,
) -> dict:
    """Trigger an Actor run. If wait_for_finish, poll until SUCCEEDED/FAILED/ABORTED."""
    r = httpx.post(
        f"{APIFY_API_BASE}/acts/{actor_id}/runs",
        json=input_payload or {},
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    run = r.json().get("data", {})

    if not wait_for_finish:
        return run

    run_id = run["id"]
    deadline = time.time() + timeout_secs
    terminal = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
    while time.time() < deadline:
        status = get_run(run_id)
        if status.get("status") in terminal:
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"Actor run {run_id} did not finish within {timeout_secs}s.")


def get_run(run_id: str) -> dict:
    r = httpx.get(
        f"{APIFY_API_BASE}/actor-runs/{run_id}",
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("data", {})


def get_dataset_items(dataset_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
    """Pull items from a dataset (the default landing spot for Actor output)."""
    r = httpx.get(
        f"{APIFY_API_BASE}/datasets/{dataset_id}/items",
        params={"limit": limit, "offset": offset, "format": "json", "clean": "true"},
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def run_and_collect(
    actor_id: str,
    input_payload: dict | None = None,
    *,
    limit: int = 1000,
    timeout_secs: int = DEFAULT_RUN_TIMEOUT_SECS,
) -> list[dict]:
    """Convenience: run Actor, wait, return dataset items in one call."""
    run = run_actor(actor_id, input_payload, wait_for_finish=True, timeout_secs=timeout_secs)
    if run.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Actor run ended with status {run.get('status')}: {run.get('exitCode')}")
    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        return []
    return get_dataset_items(dataset_id, limit=limit)


__all__ = [
    "me",
    "search_actors",
    "get_actor",
    "run_actor",
    "get_run",
    "get_dataset_items",
    "run_and_collect",
]
