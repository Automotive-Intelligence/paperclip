"""Postal Agent — the loop that polls connected accounts, classifies, routes.

Per Phase 3 of ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md
Scope: manually-triggerable processing pass. APS scheduling = Phase 4.

Public API:
    process_account(account_label, limit=20)  → dict with stats
    process_all_accounts(limit_per_account=20) → dict with per-account stats

Loop per account:
    1. Read postal_state.last_history_id
    2. If null  → seed with current historyId from gmail_multi.get_profile()
    3. Otherwise call gmail_multi.list_history_since(history_id)
    4. For each new message id:
         a. Skip if already in postal_processed
         b. Fetch metadata via get_thread
         c. classify() → category, confidence, reason
         d. route_to_destinations() → list of destination codes
         e. INSERT INTO postal_processed
    5. Update postal_state.last_history_id

V1 NEVER writes to Twenty/Slack — only logs intent in postal_processed
for audit. Phase 4 adds actual destination writers.
"""

from __future__ import annotations

import logging
from typing import Any

from services.database import execute_query, fetch_all
from services.postal_classifier import classify
from services.postal_oauth import list_connected_accounts
from services.postal_router import route_to_destinations
from tools import gmail_multi

logger = logging.getLogger(__name__)


# ----- Helpers -----

def _get_last_history_id(account_label: str) -> int | None:
    rows = fetch_all(
        "SELECT last_history_id FROM postal_state WHERE account_label = %s",
        (account_label,),
    )
    if not rows:
        return None
    return rows[0][0]


def _set_last_history_id(account_label: str, history_id: int | str) -> None:
    execute_query(
        """
        UPDATE postal_state
           SET last_history_id = %s,
               last_synced_at = now(),
               sync_count = sync_count + 1,
               last_error = NULL,
               last_error_at = NULL,
               updated_at = now()
         WHERE account_label = %s
        """,
        (int(history_id), account_label),
    )


def _record_error(account_label: str, msg: str) -> None:
    execute_query(
        """
        UPDATE postal_state
           SET last_error = %s,
               last_error_at = now(),
               updated_at = now()
         WHERE account_label = %s
        """,
        (msg[:500], account_label),
    )


def _already_processed(account_label: str, msg_id: str) -> bool:
    rows = fetch_all(
        "SELECT 1 FROM postal_processed WHERE account_label = %s AND msg_id = %s LIMIT 1",
        (account_label, msg_id),
    )
    return bool(rows)


def _record_processed(
    account_label: str,
    msg_id: str,
    thread_id: str,
    category: str,
    routed_to: list[str],
    confidence: float,
) -> None:
    execute_query(
        """
        INSERT INTO postal_processed (msg_id, account_label, thread_id, classified_as, routed_to, confidence)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_label, msg_id) DO NOTHING
        """,
        (msg_id, account_label, thread_id, category, ",".join(routed_to), confidence),
    )


def _extract_thread_meta(thread_obj: dict[str, Any]) -> dict[str, Any]:
    """From a get_thread response, pull the first message's headers + snippet."""
    messages = thread_obj.get("messages") or []
    if not messages:
        return {"id": thread_obj.get("id"), "sender": "", "subject": "", "snippet": ""}
    first = messages[0]
    payload = first.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "") for h in (payload.get("headers") or [])}
    return {
        "id": thread_obj.get("id"),
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "snippet": first.get("snippet", ""),
    }


# ----- Public API -----

def process_account(account_label: str, limit: int = 20) -> dict[str, Any]:
    """One sync pass for the given account. Returns stats."""
    stats = {
        "account_label": account_label,
        "fetched": 0,
        "already_processed_skipped": 0,
        "classified": 0,
        "by_category": {},
        "errors": [],
    }

    try:
        last_id = _get_last_history_id(account_label)
        if last_id is None:
            prof = gmail_multi.get_profile(account_label)
            current = int(prof.get("historyId", 0))
            _set_last_history_id(account_label, current)
            logger.info(f"postal: seeded {account_label} historyId={current} (no new mail processed this pass)")
            stats["seeded"] = current
            return stats

        history = gmail_multi.list_history_since(account_label, last_id)
    except Exception as e:
        emsg = str(e)
        if "404" in emsg or "Invalid startHistoryId" in emsg or "historyId expired" in emsg.lower():
            # Cursor expired (>7d). Re-seed.
            prof = gmail_multi.get_profile(account_label)
            current = int(prof.get("historyId", 0))
            _set_last_history_id(account_label, current)
            logger.warning(f"postal: {account_label} historyId expired, re-seeded to {current}")
            stats["reseeded"] = current
            return stats
        _record_error(account_label, emsg)
        stats["errors"].append(f"history_list_failed: {emsg[:200]}")
        return stats

    # Collect added-message ids from history
    added_ids: list[tuple[str, str]] = []  # (msg_id, thread_id)
    for entry in history.get("history", []):
        for added in entry.get("messagesAdded", []):
            m = added.get("message", {})
            mid = m.get("id")
            tid = m.get("threadId")
            if mid and tid:
                added_ids.append((mid, tid))

    stats["fetched"] = len(added_ids)

    # Cap per-pass work; the next pass continues from the same cursor
    for mid, tid in added_ids[:limit]:
        try:
            if _already_processed(account_label, mid):
                stats["already_processed_skipped"] += 1
                continue

            # Fetch thread metadata (minimal to save quota)
            thread_obj = gmail_multi.get_thread(account_label, tid, message_format="metadata")
            meta = _extract_thread_meta(thread_obj)
            meta["account_label"] = account_label

            category, confidence, reason, used_llm = classify(meta)
            _destinations, taken = route_to_destinations(account_label, category, meta)

            # Record the destinations that actually completed (Phase 4 writers),
            # so postal_processed.routed_to is a true audit trail of side effects.
            _record_processed(account_label, mid, tid, category, taken, confidence)

            stats["classified"] += 1
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
        except Exception as e:
            stats["errors"].append(f"msg {mid}: {type(e).__name__}: {str(e)[:160]}")
            continue

    # Advance cursor only if we got that far without raising at the history-list layer.
    new_cursor = history.get("historyId")
    if new_cursor:
        try:
            _set_last_history_id(account_label, new_cursor)
        except Exception as e:
            stats["errors"].append(f"cursor_update_failed: {e}")

    return stats


def process_all_accounts(limit_per_account: int = 20) -> dict[str, Any]:
    """One sync pass across every active connected account."""
    accounts = list_connected_accounts()
    out = {"accounts_total": len(accounts), "results": []}
    for a in accounts:
        if a.get("status") != "active":
            out["results"].append({"account_label": a["account_label"], "skipped": a.get("status")})
            continue
        try:
            r = process_account(a["account_label"], limit=limit_per_account)
            out["results"].append(r)
        except Exception as e:
            out["results"].append({"account_label": a["account_label"], "fatal": str(e)[:200]})
    return out


def prune_processed(ttl_days: int = 90) -> int:
    """Delete postal_processed rows older than ttl_days. Returns rows removed.

    The idempotency log only needs to outlive Gmail's 7-day history cursor; 90d
    is a generous safety margin. Called occasionally by the scheduled sweep.
    """
    rows = fetch_all(
        """
        WITH deleted AS (
            DELETE FROM postal_processed
             WHERE processed_at < now() - (%s || ' days')::interval
            RETURNING 1
        )
        SELECT count(*) FROM deleted
        """,
        (ttl_days,),
    )
    return int(rows[0][0]) if rows else 0
