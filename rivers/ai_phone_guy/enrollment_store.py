"""Durable enrollment state for Randy (AI Phone Guy).

Why this exists
---------------
Randy's enrollment state used to live in a module-level ``_enrolled = {}`` dict
in ``workflow.py``. That dict RESETS on every process restart (deploy, crash,
scheduler reboot). Because ``_find_new_prospects`` used "cid not in _enrolled"
as its "is this contact new?" test, every restart made Randy treat already-
enrolled contacts as brand-new and re-process them. ``scripts/
reconcile_randy_enrollments.py`` only exists because of this churn.

This module replaces the volatile dict with a Postgres-backed store that
survives restarts, following the same ``services.database`` /
``crm_push_logs`` pattern already used elsewhere in the repo.

Design
------
- Source of truth is the ``randy_enrollments`` table (one row per enrolled
  GHL contact id). ``is_enrolled`` / ``record_enrollment`` / ``all_enrollments``
  read and write it.
- The table is created lazily on first use (CREATE TABLE IF NOT EXISTS), so the
  store works whether or not ``app.init_db()`` has run.
- When ``DATABASE_URL`` is not configured (local dry-runs, unit tests), the
  store transparently falls back to an in-process dict so behaviour is still
  correct within a single process and nothing raises. The fallback is the only
  path that does NOT survive a restart, and that is acceptable for the
  no-database case.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from services.database import execute_query, fetch_all
from services.errors import DatabaseError

logger = logging.getLogger(__name__)

# In-process fallback used only when DATABASE_URL is not configured. Keyed by
# contact_id -> the same dict shape workflow.py keeps in memory.
_memory_fallback: Dict[str, dict] = {}

_table_ready = False


def _db_enabled() -> bool:
    from services.database import _get_url  # local import: avoids import cycle
    return bool(_get_url())


def _ensure_table() -> None:
    """Create randy_enrollments if it doesn't exist. Idempotent, cached."""
    global _table_ready
    if _table_ready or not _db_enabled():
        return
    execute_query(
        """
        CREATE TABLE IF NOT EXISTS randy_enrollments (
            contact_id   TEXT        PRIMARY KEY,
            vertical     TEXT        NOT NULL,
            last_step    INTEGER     NOT NULL DEFAULT -1,
            contact      TEXT        NOT NULL DEFAULT '{}',
            channel      TEXT        NOT NULL DEFAULT 'email',
            enrolled_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_randy_enrollments_vertical
            ON randy_enrollments (vertical);
        -- Backfill the channel column on tables created before the
        -- data-quality/channel-routing guardrails (2026-07-01). Idempotent.
        ALTER TABLE randy_enrollments
            ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'email';
        """
    )
    _table_ready = True


def is_enrolled(contact_id: str) -> bool:
    """True if this contact has already been enrolled (durable check)."""
    if not _db_enabled():
        return contact_id in _memory_fallback
    try:
        _ensure_table()
        rows = fetch_all(
            "SELECT 1 FROM randy_enrollments WHERE contact_id = %s LIMIT 1",
            (contact_id,),
        )
        return bool(rows)
    except DatabaseError as e:
        # Fail safe: if the DB is unreachable, fall back to in-memory knowledge
        # rather than re-enrolling (which would re-spam the contact).
        logger.error("[randy_enrollments] is_enrolled DB error for %s: %s", contact_id, e)
        return contact_id in _memory_fallback


def record_enrollment(contact_id: str, vertical: str, contact: dict, last_step: int = -1,
                      channel: str = "email") -> None:
    """Persist an enrollment. Upsert so re-enrollment attempts are idempotent.

    ``channel`` is the routed outreach lane ("email" | "sms") so the send loop
    still knows, after a restart, to suppress EMAIL steps for SMS/CALL-lane
    (no-business-email) contacts.
    """
    _memory_fallback[contact_id] = {
        "vertical": vertical,
        "channel": channel,
        "enrolled_at": datetime.now(),
        "last_step": last_step,
        "contact": contact,
    }
    if not _db_enabled():
        return
    try:
        _ensure_table()
        execute_query(
            """
            INSERT INTO randy_enrollments (contact_id, vertical, last_step, contact, channel)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (contact_id) DO UPDATE
                SET vertical = EXCLUDED.vertical,
                    contact  = EXCLUDED.contact,
                    channel  = EXCLUDED.channel,
                    updated_at = NOW()
            """,
            (contact_id, vertical, last_step, json.dumps(contact or {}), channel),
        )
    except DatabaseError as e:
        logger.error("[randy_enrollments] record_enrollment DB error for %s: %s", contact_id, e)


def update_last_step(contact_id: str, last_step: int) -> None:
    """Advance the sequence cursor for an enrolled contact."""
    if contact_id in _memory_fallback:
        _memory_fallback[contact_id]["last_step"] = last_step
    if not _db_enabled():
        return
    try:
        _ensure_table()
        execute_query(
            "UPDATE randy_enrollments SET last_step = %s, updated_at = NOW() WHERE contact_id = %s",
            (last_step, contact_id),
        )
    except DatabaseError as e:
        logger.error("[randy_enrollments] update_last_step DB error for %s: %s", contact_id, e)


def all_enrollments() -> Dict[str, dict]:
    """Return every enrollment as {contact_id: {vertical, enrolled_at, last_step, contact}}.

    Used by sequence processing + hot-lead enrichment. Reads from the durable
    store so a freshly-restarted process still sees prior enrollments.
    """
    if not _db_enabled():
        return dict(_memory_fallback)
    try:
        _ensure_table()
        rows = fetch_all(
            "SELECT contact_id, vertical, last_step, contact, enrolled_at, channel FROM randy_enrollments"
        )
    except DatabaseError as e:
        logger.error("[randy_enrollments] all_enrollments DB error: %s", e)
        return dict(_memory_fallback)

    out: Dict[str, dict] = {}
    for contact_id, vertical, last_step, contact_json, enrolled_at, channel in rows:
        try:
            contact = json.loads(contact_json) if contact_json else {}
        except (TypeError, ValueError):
            contact = {}
        out[contact_id] = {
            "vertical": vertical,
            "channel": channel or "email",
            "last_step": last_step if last_step is not None else -1,
            "contact": contact,
            "enrolled_at": enrolled_at if isinstance(enrolled_at, datetime) else datetime.now(),
        }
    return out


def get_enrollment(contact_id: str) -> Optional[dict]:
    """Return a single enrollment record, or None."""
    return all_enrollments().get(contact_id)


def _reset_for_tests() -> None:
    """Clear in-process fallback + table cache. Test-only helper."""
    global _table_ready
    _memory_fallback.clear()
    _table_ready = False
