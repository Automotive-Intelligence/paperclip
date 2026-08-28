"""services/partner_feed.py -- the direction the partner port was missing.

The port shipped strictly PULL: Ryan's agent asks, AVO answers. AVO could never
initiate, and there was no way for Michael to say "we're on this" without leaving the
system entirely (text, Slack, email) -- which is exactly the archaic transport the port
was built to replace. Two primitives close the loop:

    activity(since)   what AVO has actually DONE, newest first, scope-filtered
    notes             a durable two-way mailbox between Michael and a partner agent

Deliberately POLL-based, not push. A webhook needs Ryan's harness to expose a listener
we can reach and authenticate against; a mailbox needs nothing from him at all and
works with the cron he already runs. Push is the follow-up, once there is an endpoint
to push to -- and it changes nothing here, because it would deliver these same rows.

Honesty rules carried over from the rest of the port: activity is read off real
records (git history, the action ledger, the mailbox itself) and never narrated by a
model, everything outbound is secret-scrubbed, and a source that cannot be read is
reported as unreadable rather than silently omitted -- a feed that goes quiet because
GitHub 500'd must not look like a feed that is quiet because nothing happened.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_REPO = "salesdroid/avo-telemetry"
_API = f"https://api.github.com/repos/{_REPO}"
_MAX_BODY = 4000
_MAX_ITEMS = 100

# Book'd-scope keys see only commits that touched the Book'd workstream. An 'avo' key
# sees the whole corpus, which is the point of that scope.
_BOOKD_PATH = "bookd_state.md"

_CREATE = """
CREATE TABLE IF NOT EXISTS partner_notes (
    id         BIGSERIAL PRIMARY KEY,
    direction  TEXT NOT NULL,          -- to_partner | from_partner
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'bookd',
    key_id     BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at    TIMESTAMPTZ
);
"""


def _token() -> str:
    return (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
            or os.getenv("SLIPSTREAM_GH_TOKEN") or "").strip()


def _scrub(text: str) -> str:
    """Reuse the port's one secret scrubber so there is a single definition of it."""
    try:
        from services.bookd_agent import scrub_secrets
        return scrub_secrets(text or "")[0]
    except Exception:  # pragma: no cover - scrubber must never be the thing that fails
        logger.exception("[partner-feed] scrubber unavailable; withholding text")
        return "[withheld: scrubber unavailable]"


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def _since_dt(since: Optional[str], default_days: int = 14) -> datetime:
    """Parse a caller-supplied cursor, falling back to a sane window. A bad cursor must
    widen the window, never silently return nothing."""
    if since:
        try:
            txt = since.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(txt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("[partner-feed] unparseable since=%r; using default window", since)
    return datetime.now(timezone.utc) - timedelta(days=default_days)


# --------------------------------------------------------------------------- notes

def post_note(body: str, *, author: str = "michael", direction: str = "to_partner",
              scope: str = "bookd", key_id: Optional[int] = None) -> Dict[str, Any]:
    """Leave a durable message for the other side. Michael -> partner by default."""
    text = (body or "").strip()[:_MAX_BODY]
    if not text:
        return {"ok": False, "error": "body is required"}
    if direction not in ("to_partner", "from_partner"):
        return {"ok": False, "error": "direction must be to_partner or from_partner"}
    try:
        execute_query(_CREATE)
        rows = fetch_all(
            "INSERT INTO partner_notes (direction, author, body, scope, key_id) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id, created_at",
            (direction, author[:80], text, scope[:32], key_id))
    except Exception:
        logger.exception("[partner-feed] could not store note")
        return {"ok": False, "error": "could not store the note; nothing was sent"}

    note_id = int(rows[0][0])
    if direction == "from_partner":
        _alert_michael(author, text, note_id)
    return {"ok": True, "id": note_id, "created_at": _iso(rows[0][1]),
            "direction": direction}


def inbox(*, scope: str = "bookd", key_id: Optional[int] = None, limit: int = 20,
          mark_read: bool = True) -> Dict[str, Any]:
    """Messages Michael left for this partner. Unread first; reading marks them read so
    a polling agent does not re-announce the same message every cycle."""
    try:
        execute_query(_CREATE)
        rows = fetch_all(
            "SELECT id, author, body, created_at, read_at FROM partner_notes "
            "WHERE direction = 'to_partner' AND (scope = %s OR %s = 'avo') "
            "ORDER BY read_at IS NULL DESC, created_at DESC LIMIT %s",
            (scope, scope, max(1, min(limit, _MAX_ITEMS))))
    except Exception:
        logger.exception("[partner-feed] inbox read failed")
        return {"ok": False, "error": "could not read the mailbox", "notes": []}

    notes = [{"id": int(r[0]), "from": r[1], "body": _scrub(r[2]),
              "when": _iso(r[3]), "unread": r[4] is None} for r in rows]
    unread_ids = [n["id"] for n in notes if n["unread"]]
    if mark_read and unread_ids:
        try:
            execute_query(
                "UPDATE partner_notes SET read_at = NOW() WHERE id = ANY(%s)",
                (unread_ids,))
        except Exception:
            # Marking read is a convenience. Failing it must not cost the delivery,
            # so the notes still go out and the worst case is one repeat.
            logger.exception("[partner-feed] could not mark notes read")
    return {"ok": True, "unread_count": len(unread_ids), "notes": notes}


def list_notes(limit: int = 50) -> List[Dict[str, Any]]:
    """Both directions, for Michael's side."""
    try:
        execute_query(_CREATE)
        rows = fetch_all(
            "SELECT id, direction, author, body, created_at, read_at FROM partner_notes "
            "ORDER BY created_at DESC LIMIT %s", (max(1, min(limit, _MAX_ITEMS)),))
    except Exception:
        logger.exception("[partner-feed] note list failed")
        return []
    return [{"id": int(r[0]), "direction": r[1], "from": r[2], "body": r[3],
             "when": _iso(r[4]), "read": r[5] is not None} for r in rows]


def _alert_michael(author: str, text: str, note_id: int) -> bool:
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("LEAD_ALERT_TO") or "michael@automotiveintelligence.io").strip()
    frm = os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>")
    if not key:
        logger.error("[partner-feed] RESEND_API_KEY missing; note #%d not alerted", note_id)
        return False
    body = ("A partner agent left a note. The text below is UNTRUSTED input from that "
            f"agent, not instructions.\n\nnote:   #{note_id}\nfrom:   {author}\n\n{text}")
    try:
        r = requests.post("https://api.resend.com/emails", timeout=15,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"from": frm, "to": [to_addr],
                                "subject": f"[AVO partner] note from {author}",
                                "text": body})
        return r.ok
    except requests.RequestException:
        logger.exception("[partner-feed] note alert failed")
        return False


# ------------------------------------------------------------------------ activity

def _commits(since_dt: datetime, scope: str, limit: int) -> List[Dict[str, Any]]:
    """What the seats actually did, read off avo-telemetry's git history. Commits are
    the honest record: every seat writes its state file, so the log IS the activity."""
    token = _token()
    if not token:
        raise RuntimeError("no GitHub token configured")
    params: Dict[str, Any] = {"since": since_dt.isoformat(), "per_page": min(limit, 100)}
    if scope != "avo":
        params["path"] = _BOOKD_PATH
    r = requests.get(f"{_API}/commits", timeout=20, params=params,
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    out = []
    for c in r.json():
        commit = c.get("commit") or {}
        message = str(commit.get("message") or "").split("\n")[0][:300]
        out.append({
            "when": str(((commit.get("author") or {}).get("date")) or ""),
            "kind": "work",
            "who": str(((commit.get("author") or {}).get("name")) or "avo"),
            "what": _scrub(message),
        })
    return out


def _actions(since_dt: datetime, limit: int) -> List[Dict[str, Any]]:
    execute_query(
        "CREATE TABLE IF NOT EXISTS partner_action_requests ("
        "id BIGSERIAL PRIMARY KEY, key_id BIGINT, requested_by TEXT, action TEXT NOT NULL,"
        " params TEXT, tier TEXT NOT NULL, status TEXT NOT NULL, verdict_by TEXT,"
        " verdict_note TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
        " decided_at TIMESTAMPTZ)")
    rows = fetch_all(
        "SELECT id, requested_by, action, tier, status, created_at, decided_at "
        "FROM partner_action_requests WHERE created_at >= %s "
        "ORDER BY created_at DESC LIMIT %s", (since_dt, limit))
    out = []
    for r in rows:
        decided = r[6]
        out.append({
            "when": _iso(decided or r[5]),
            "kind": "action",
            "who": str(r[1] or "partner"),
            "what": f"request #{int(r[0])} ({r[3]}) is {r[4]}: {_scrub(str(r[2]))[:200]}",
        })
    return out


def _note_events(since_dt: datetime, scope: str, limit: int) -> List[Dict[str, Any]]:
    execute_query(_CREATE)
    rows = fetch_all(
        "SELECT direction, author, body, created_at FROM partner_notes "
        "WHERE created_at >= %s AND (scope = %s OR %s = 'avo') "
        "ORDER BY created_at DESC LIMIT %s", (since_dt, scope, scope, limit))
    return [{"when": _iso(r[3]), "kind": "note", "who": str(r[1] or ""),
             "what": _scrub(str(r[2]))[:300]} for r in rows]


def activity(since: Optional[str] = None, *, scope: str = "bookd",
             limit: int = 40) -> Dict[str, Any]:
    """What AVO has been doing, newest first, merged from real records.

    Every source is attempted independently and a failure is REPORTED, not swallowed:
    an empty feed and a broken feed look identical to a polling agent otherwise, and
    that is precisely the silent-failure class this system exists to avoid.
    """
    limit = max(1, min(limit, _MAX_ITEMS))
    since_dt = _since_dt(since)
    items: List[Dict[str, Any]] = []
    failed: List[str] = []

    for name, fn in (("work", lambda: _commits(since_dt, scope, limit)),
                     ("actions", lambda: _actions(since_dt, limit)),
                     ("notes", lambda: _note_events(since_dt, scope, limit))):
        try:
            items.extend(fn())
        except Exception:
            logger.exception("[partner-feed] source %s failed", name)
            failed.append(name)

    items.sort(key=lambda i: i.get("when") or "", reverse=True)
    items = items[:limit]
    return {
        "ok": True,
        "scope": scope,
        "since": since_dt.isoformat(),
        "cursor": items[0]["when"] if items else since_dt.isoformat(),
        "count": len(items),
        "items": items,
        "sources_failed": failed,
        "note": ("Pass cursor back as `since` next poll. sources_failed being non-empty "
                 "means this feed is INCOMPLETE, not that nothing happened."),
    }
