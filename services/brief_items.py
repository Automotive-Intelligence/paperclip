"""services/brief_items.py -- the durable "needs you" ledger behind the CEO Brief.

Every stream that used to email Michael the moment something needed him (Sonar
escalations, Postal prospects, script-engine failures, infrastructure reds, lead
reconcile deltas) now files a row here instead. The CEO Brief reads the open rows
once a day and lists them under "Needs you today". Nothing in this module sends.

Rules:
  * Deduped by fingerprint, so a sweep that sees the same item twice files it once.
  * Never raises. A ledger failure must not sink the job that found the item; the
    caller gets False and logs it, and the Brief's absence check catches a dead DB.
  * Rows age out of the Brief after BRIEF_NEEDS_YOU_DAYS (default 3). Michael acts
    on the platform itself (replies on Instagram, answers the prospect), which we
    cannot observe, so the Brief shows an item for a few days and then stops.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS brief_items (
    id          SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    brand       TEXT,
    title       TEXT NOT NULL,
    detail      TEXT,
    url         TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
"""

KINDS = ("sonar", "postal", "portal", "lead", "failure")


def _ensure() -> None:
    execute_query(_DDL)


def add(kind: str, title: str, *, fingerprint: str, brand: Optional[str] = None,
        detail: Optional[str] = None, url: Optional[str] = None) -> bool:
    """File one item. Returns True only when a NEW row was inserted."""
    if not fingerprint or not title:
        return False
    try:
        _ensure()
        rows = fetch_all(
            "INSERT INTO brief_items (kind, fingerprint, brand, title, detail, url) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (fingerprint) DO NOTHING "
            "RETURNING id",
            (kind, fingerprint[:300], brand, title[:400], (detail or "")[:2000] or None,
             (url or "")[:1000] or None),
        )
        return bool(rows)
    except Exception as e:  # noqa: BLE001 - never sink the caller
        logger.warning("[brief_items] add failed (non-fatal) kind=%s: %s", kind, e)
        return False


def open_items(days: int = 3, kinds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Unresolved items created in the last `days`. Raises on DB failure so the
    Brief can report the ledger itself as a broken signal."""
    _ensure()
    sql = ("SELECT id, kind, fingerprint, brand, title, detail, url, created_at "
           "FROM brief_items WHERE resolved_at IS NULL "
           "AND created_at > NOW() - make_interval(days => %s)")
    params: tuple = (int(days),)
    if kinds:
        sql += " AND kind = ANY(%s)"
        params = params + (list(kinds),)
    sql += " ORDER BY created_at DESC LIMIT 200"
    out = []
    for r in fetch_all(sql, params):
        out.append({"id": r[0], "kind": r[1], "fingerprint": r[2], "brand": r[3],
                    "title": r[4], "detail": r[5], "url": r[6], "created_at": r[7]})
    return out


def resolve(fingerprint: str) -> bool:
    try:
        execute_query("UPDATE brief_items SET resolved_at = NOW() "
                      "WHERE fingerprint = %s AND resolved_at IS NULL", (fingerprint,))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[brief_items] resolve failed: %s", e)
        return False


def last_detail(fingerprint_prefix: str) -> Optional[str]:
    """Detail of the most recent row whose fingerprint starts with the prefix, or
    None. Used to tell a NEW failure reason from the same one repeating."""
    try:
        _ensure()
        rows = fetch_all(
            "SELECT detail FROM brief_items WHERE fingerprint LIKE %s "
            "ORDER BY created_at DESC LIMIT 1",
            (fingerprint_prefix.replace("%", "") + "%",),
        )
        return rows[0][0] if rows else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[brief_items] last_detail failed: %s", e)
        return None


def same_root_cause(prev: Optional[str], now: str, width: int = 120) -> bool:
    """Two failure details share a root cause when their leading text matches."""
    if not prev:
        return False
    return (prev or "").strip()[:width] == (now or "").strip()[:width]
