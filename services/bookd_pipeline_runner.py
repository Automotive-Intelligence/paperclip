"""services/bookd_pipeline_runner.py — Runner for the bookd_pipeline pseudo-agent.

When the cockpit bridge routes a flag to to_agent='bookd_pipeline', this
runner picks up the pending agent_handoffs row(s), extracts the script
from the flag payload, and fires services.book_d_ad_pipeline.run() for
each one.

Plugged into APScheduler via app.py's scheduler block AND registered as
an event-driven runner via services.agent_triggers.register_runner so the
bridge can fire it on flag receipt when BRIDGE_EVENT_DRIVEN=true.

Hook label derivation:
  - First 5 alphanumeric words of the script, kebab-cased
  - Suffixed with the flag's posted timestamp (date only) for uniqueness
  - Capped at 60 chars

So a flag with `what` = "Hey, I almost became one of the 90%..." posted on
2026-04-29 yields hook_label = "hey-i-almost-became-one-2026-04-29".
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

AGENT_NAME = "bookd_pipeline"


def _derive_hook_label(script: str, posted: str | None) -> str:
    """Build a stable, human-readable hook label from the script + flag timestamp."""
    words = re.findall(r"[A-Za-z0-9]+", (script or "").lower())[:5]
    base = "-".join(words) if words else "untitled"
    date = ""
    if posted:
        try:
            # Accept ISO 8601 like '2026-04-29T17:30:00Z' or '2026-04-29T17:30:00-07:00'
            dt = datetime.fromisoformat((posted or "").replace("Z", "+00:00"))
            date = dt.date().isoformat()
        except ValueError:
            pass
    label = f"{base}-{date}" if date else base
    return label[:60]


def _fetch_pending_handoffs(limit: int = 5) -> list[dict[str, Any]]:
    """Pull pending bookd_pipeline handoffs, oldest first."""
    from services.database import fetch_all
    try:
        rows = fetch_all(
            "SELECT id, payload FROM agent_handoffs "
            "WHERE to_agent = %s AND status = 'pending' "
            "ORDER BY created_at ASC LIMIT %s",
            (AGENT_NAME, limit),
        )
    except Exception as e:
        logger.warning("[bookd_runner] failed to fetch pending handoffs: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        handoff_id = row[0]
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        out.append({"handoff_id": handoff_id, "payload": payload or {}})
    return out


def _mark_handoff_complete(handoff_id: int, status: str, note: str = "") -> None:
    from services.database import execute_query
    try:
        execute_query(
            "UPDATE agent_handoffs SET status = %s, completed_at = NOW(), "
            "result_note = %s WHERE id = %s",
            (status, note[:500] if note else "", handoff_id),
        )
    except Exception as e:
        # Schema may differ — try without result_note
        try:
            execute_query(
                "UPDATE agent_handoffs SET status = %s WHERE id = %s",
                (status, handoff_id),
            )
        except Exception as e2:
            logger.warning(
                "[bookd_runner] failed to mark handoff %s as %s: %s / %s",
                handoff_id, status, e, e2,
            )


def runner() -> None:
    """The function registered via agent_triggers.register_runner.

    Picks up pending bookd_pipeline handoffs, fires the pipeline for each,
    marks them complete (or failed). Idempotent — safe to call multiple
    times; only processes rows still in 'pending'.
    """
    pending = _fetch_pending_handoffs(limit=5)
    if not pending:
        logger.info("[bookd_runner] no pending bookd_pipeline handoffs")
        return

    logger.info("[bookd_runner] processing %d pending handoff(s)", len(pending))
    from services.book_d_ad_pipeline import run as run_pipeline

    for h in pending:
        handoff_id = h["handoff_id"]
        payload = h["payload"] or {}
        what = (payload.get("what") or "").strip()
        posted = (payload.get("posted") or "").strip()

        if not what:
            logger.warning("[bookd_runner] handoff %s has no 'what' field — skipping", handoff_id)
            _mark_handoff_complete(handoff_id, "failed", "no script in flag.what")
            continue

        hook_label = _derive_hook_label(what, posted)
        logger.info(
            "[bookd_runner] firing pipeline handoff_id=%s hook_label=%s",
            handoff_id, hook_label,
        )
        try:
            result = run_pipeline(script=what, hook_label=hook_label)
        except Exception as e:
            logger.exception("[bookd_runner] pipeline crashed for handoff %s", handoff_id)
            _mark_handoff_complete(handoff_id, "failed", f"pipeline crashed: {type(e).__name__}: {e}")
            continue

        if result.ok:
            note = f"ok artifact_id={result.artifact_id} ad_set={result.meta_ad_set.get('ad_set_id')} ads={list(result.meta_ads.keys())}"
            _mark_handoff_complete(handoff_id, "complete", note)
            logger.info(
                "[bookd_runner] ✓ handoff_id=%s artifact_id=%s",
                handoff_id, result.artifact_id,
            )
        else:
            _mark_handoff_complete(handoff_id, "failed", "; ".join(result.errors)[:500])
            logger.warning(
                "[bookd_runner] ✗ handoff_id=%s errors=%s",
                handoff_id, result.errors,
            )
