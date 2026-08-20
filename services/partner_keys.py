"""services/partner_keys.py -- the door and the key for partner agents.

Michael's requirement: give a co-founder full-time access with his harness of choice,
and be able to REVOKE INSTANTLY if anything goes sideways. That rules out env-var keys
(revoking one needs a redeploy, minutes of exposure). Keys live in Postgres instead:

  - Only a SHA-256 HASH is stored. The raw key is shown exactly once, at issue time.
  - Every request looks the key up live. No cache, so revoke takes effect on the NEXT
    request, not the next deploy.
  - Each key carries a SCOPE ('bookd' = one venture, 'avo' = the whole operation) so
    access can be narrowed without being cut, and a status ('active' | 'revoked').
  - last_used_at / use_count make the door observable: Michael can see whether a key
    is being used, how much, and when it was last touched.

FAIL CLOSED: if the store cannot be read, authentication DENIES. A security surface
that opens when its database hiccups is not a security surface.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

SCOPES = ("bookd", "avo")

_CREATE = """
CREATE TABLE IF NOT EXISTS partner_agent_keys (
    id             BIGSERIAL PRIMARY KEY,
    label          TEXT NOT NULL,
    key_hash       TEXT NOT NULL UNIQUE,
    scope          TEXT NOT NULL DEFAULT 'bookd',
    status         TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    can_act        BOOLEAN NOT NULL DEFAULT FALSE,   -- may REQUEST actions at all
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at   TIMESTAMPTZ,
    use_count      BIGINT NOT NULL DEFAULT 0,
    revoked_at     TIMESTAMPTZ,
    revoked_reason TEXT
);
"""


def _hash(raw: str) -> str:
    return hashlib.sha256((raw or "").strip().encode()).hexdigest()


def ensure_table() -> None:
    execute_query(_CREATE)


def issue(label: str, scope: str = "bookd", *, can_act: bool = False,
          raw_key: Optional[str] = None) -> Dict[str, Any]:
    """Mint (or adopt) a key. The raw value is returned ONCE and never stored.
    `raw_key` adopts an already-distributed key so a scope change does not force the
    partner to reconfigure their harness."""
    if scope not in SCOPES:
        return {"ok": False, "error": f"scope must be one of {SCOPES}"}
    raw = (raw_key or secrets.token_hex(24)).strip()
    ensure_table()
    rows = fetch_all(
        "INSERT INTO partner_agent_keys (label, key_hash, scope, can_act) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (key_hash) DO UPDATE "
        "SET label=EXCLUDED.label, scope=EXCLUDED.scope, can_act=EXCLUDED.can_act, "
        "status='active', revoked_at=NULL, revoked_reason=NULL RETURNING id",
        (label[:80], _hash(raw), scope, bool(can_act)))
    kid = int(rows[0][0]) if rows else 0
    logger.info("[partner-keys] issued/updated key #%d (%s, scope=%s, can_act=%s)",
                kid, label, scope, can_act)
    return {"ok": True, "id": kid, "label": label, "scope": scope,
            "can_act": bool(can_act), "key": raw,
            "note": "raw key shown once; only its hash is stored"}


def resolve(raw: str) -> Optional[Dict[str, Any]]:
    """Look up a presented bearer token. Returns the key's grant, or None for
    unknown/revoked/unreadable (fail closed). Records use for observability."""
    if not raw:
        return None
    h = _hash(raw)
    try:
        ensure_table()
        rows = fetch_all(
            "SELECT id, label, scope, status, can_act FROM partner_agent_keys "
            "WHERE key_hash=%s", (h,))
    except Exception:
        logger.exception("[partner-keys] store unreadable -- DENYING (fail closed)")
        return None
    if not rows:
        return None
    kid, label, scope, status, can_act = rows[0]
    if status != "active":
        logger.warning("[partner-keys] revoked key #%s presented (%s)", kid, label)
        return None
    try:
        execute_query(
            "UPDATE partner_agent_keys SET last_used_at=NOW(), use_count=use_count+1 "
            "WHERE id=%s", (kid,))
    except Exception:
        logger.warning("[partner-keys] use-tracking write failed (auth still valid)")
    return {"id": int(kid), "label": label, "scope": scope, "can_act": bool(can_act)}


def revoke(key_id: int, reason: str = "") -> Dict[str, Any]:
    """THE KILL SWITCH. Takes effect on the partner's next request, no deploy."""
    ensure_table()
    execute_query(
        "UPDATE partner_agent_keys SET status='revoked', revoked_at=NOW(), "
        "revoked_reason=%s WHERE id=%s", (reason[:300], key_id))
    logger.warning("[partner-keys] REVOKED key #%d (%s)", key_id, reason or "no reason given")
    return {"ok": True, "id": key_id, "status": "revoked", "reason": reason}


def set_scope(key_id: int, scope: str) -> Dict[str, Any]:
    """Narrow or widen a live key without cutting it off (e.g. avo -> bookd)."""
    if scope not in SCOPES:
        return {"ok": False, "error": f"scope must be one of {SCOPES}"}
    ensure_table()
    execute_query("UPDATE partner_agent_keys SET scope=%s WHERE id=%s", (scope, key_id))
    return {"ok": True, "id": key_id, "scope": scope}


def set_can_act(key_id: int, can_act: bool) -> Dict[str, Any]:
    """Turn the action channel on or off for a key, leaving read access intact."""
    ensure_table()
    execute_query("UPDATE partner_agent_keys SET can_act=%s WHERE id=%s",
                  (bool(can_act), key_id))
    return {"ok": True, "id": key_id, "can_act": bool(can_act)}


def list_keys() -> List[Dict[str, Any]]:
    """Every key and its activity. Hashes are never returned."""
    ensure_table()
    rows = fetch_all(
        "SELECT id, label, scope, status, can_act, created_at, last_used_at, "
        "use_count, revoked_reason FROM partner_agent_keys ORDER BY id")
    return [{"id": int(r[0]), "label": r[1], "scope": r[2], "status": r[3],
             "can_act": bool(r[4]), "created_at": str(r[5]),
             "last_used_at": str(r[6]) if r[6] else None,
             "use_count": int(r[7]), "revoked_reason": r[8]} for r in rows]
