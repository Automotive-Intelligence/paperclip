"""Durable audit envelope for the send-as-brand rail.

Every intended send — whether it actually fired, was held by the authorization
gate, or degraded to a draft because no credential was configured — is recorded
here. This is the accountability spine of Michael's send-authority boundary:
nothing goes out as a brand identity without leaving a durable, queryable trace
of who asked, from which identity, to whom, with what attachment, and whether it
was authorized.

Design (mirrors rivers/ai_phone_guy/enrollment_store.py + the crm_push_logs
pattern already used in the repo):
- Source of truth is the ``brand_send_audit`` table (one row per intended send).
- The table is created lazily on first use (CREATE TABLE IF NOT EXISTS), so the
  store works whether or not app.init_db() has run.
- When DATABASE_URL is not configured (local dry-runs, unit tests), the store
  transparently falls back to an in-process list so the caller never crashes and
  behaviour is still observable within a single process. The fallback is the
  only path that does NOT survive a restart, and that is acceptable for the
  no-database case (which, by construction, is also a case that cannot fire a
  real send).

Outcome vocabulary (the ``outcome`` column):
    "sent"          transport accepted the message (only possible when authorized
                    AND a credential was present)
    "held"          authorization gate said no — DRAFTED/logged, never sent
    "drafted"       authorized but no credential/transport available — degraded
                    to a draft (or would-be draft) and logged
    "error"         transport raised after an authorized attempt; logged for triage
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)

# In-process fallback used only when DATABASE_URL is not configured.
_memory_fallback: List[dict] = []

_table_ready = False


def _db_enabled() -> bool:
    from services.database import _get_url  # local import: avoids import cycle
    return bool(_get_url())


def _ensure_table() -> None:
    """Create brand_send_audit if it doesn't exist. Idempotent, cached."""
    global _table_ready
    if _table_ready or not _db_enabled():
        return
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS brand_send_audit (
            id            BIGSERIAL   PRIMARY KEY,
            seat          TEXT        NOT NULL DEFAULT 'unknown',
            from_identity TEXT        NOT NULL,
            to_addr       TEXT        NOT NULL,
            subject       TEXT        NOT NULL DEFAULT '',
            attachment    TEXT,
            authorized    BOOLEAN     NOT NULL DEFAULT FALSE,
            outcome       TEXT        NOT NULL,
            detail        TEXT        NOT NULL DEFAULT '{}',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_brand_send_audit_from_identity
            ON brand_send_audit (from_identity);
        CREATE INDEX IF NOT EXISTS idx_brand_send_audit_created_at
            ON brand_send_audit (created_at);
        """
    )
    _table_ready = True


def record(
    *,
    seat: str,
    from_identity: str,
    to_addr: str,
    subject: str,
    attachment: Optional[str],
    authorized: bool,
    outcome: str,
    detail: Optional[dict] = None,
) -> None:
    """Persist one audit envelope. Never raises — auditing must not break sends.

    A record is written for EVERY intended send (fired, held, drafted, or
    errored). The write is best-effort: if the DB is unreachable we still keep
    the in-process copy and log, but we never propagate a DB error up into the
    send path (that would let an audit failure mask or abort a legitimate hold).
    """
    envelope = {
        "seat": seat or "unknown",
        "from_identity": from_identity,
        "to_addr": to_addr,
        "subject": subject or "",
        "attachment": attachment,
        "authorized": bool(authorized),
        "outcome": outcome,
        "detail": detail or {},
        "created_at": datetime.now(timezone.utc),
    }
    _memory_fallback.append(envelope)
    logger.info(
        "[brand_send_audit] seat=%s from=%s to=%s authorized=%s outcome=%s attachment=%s",
        envelope["seat"], from_identity, to_addr, authorized, outcome,
        attachment or "-",
    )

    if not _db_enabled():
        return
    try:
        _ensure_table()
        execute_query(
            """
            INSERT INTO brand_send_audit
                (seat, from_identity, to_addr, subject, attachment, authorized, outcome, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                envelope["seat"],
                from_identity,
                to_addr,
                envelope["subject"],
                attachment,
                bool(authorized),
                outcome,
                json.dumps(detail or {}),
            ),
        )
    except DatabaseError as e:
        # Fail safe: keep the in-memory copy + log, never propagate.
        logger.error("[brand_send_audit] DB write failed (kept in-memory): %s", e)


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent audit envelopes, newest first.

    Reads the durable store when DATABASE_URL is set, otherwise the in-process
    fallback. Used by Pit Wall / verification to prove what did (or did not) go
    out as a brand identity.
    """
    if not _db_enabled():
        return list(reversed(_memory_fallback))[:limit]
    try:
        _ensure_table()
        rows = fetch_all(
            """
            SELECT seat, from_identity, to_addr, subject, attachment,
                   authorized, outcome, detail, created_at
            FROM brand_send_audit
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    except DatabaseError as e:
        logger.error("[brand_send_audit] recent() DB error: %s", e)
        return list(reversed(_memory_fallback))[:limit]

    out: List[Dict[str, Any]] = []
    for seat, from_id, to_addr, subject, attachment, authorized, outcome, detail_json, created_at in rows:
        try:
            detail = json.loads(detail_json) if detail_json else {}
        except (TypeError, ValueError):
            detail = {}
        out.append({
            "seat": seat,
            "from_identity": from_id,
            "to_addr": to_addr,
            "subject": subject,
            "attachment": attachment,
            "authorized": authorized,
            "outcome": outcome,
            "detail": detail,
            "created_at": created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc),
        })
    return out


def _reset_for_tests() -> None:
    """Clear in-process fallback + table cache. Test-only helper."""
    global _table_ready
    _memory_fallback.clear()
    _table_ready = False
