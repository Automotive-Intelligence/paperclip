"""On-demand multi-account Gmail access for AVO (Postal Agent — Option A).

The autonomous Postal sweep (agents/postal/postal_agent.py) polls inboxes on a
schedule. This module is the *interactive* counterpart: thin, AVO-friendly
wrappers over tools/gmail_multi so AVO can search / read / triage ANY connected
inbox on demand, addressed by account_label (avi, wd, salesdroid, aipg,
agentempire, bookd). It is exposed over HTTP in app.py under /postal/inbox/*.

Design notes:
- Read ops (search, read_thread, labels) and modify ops (apply_label, archive,
  mark_read) are driven by an explicit human request via AVO, so they are NOT
  gated behind POSTAL_WRITES_ENABLED (that flag guards the *autonomous* writer
  fan-out only).
- gmail_multi is imported lazily so this module loads without google-auth in
  test/lint environments.
- Errors are normalised: ValueError → bad input (unknown account), RuntimeError
  → no active token for the account, anything else bubbles up for a 502.

Plan: ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md (Phase 4 / Option A)
"""

from __future__ import annotations

import base64
from typing import Any

# Canonical inbox set. Sourced from postal_oauth when importable (single source
# of truth); falls back to a literal so this module stays importable without the
# OAuth module's heavy deps (fastapi / google-auth-oauthlib).
try:  # pragma: no cover - exercised in prod where deps exist
    from services.postal_oauth import VALID_ACCOUNT_LABELS as ACCOUNT_LABELS
except BaseException:  # noqa: BLE001 — fall back if heavy deps (crypto/google) absent
    ACCOUNT_LABELS = {"avi", "wd", "salesdroid", "aipg", "agentempire", "bookd"}


def _validate(account_label: str) -> str:
    acct = (account_label or "").strip().lower()
    if acct not in ACCOUNT_LABELS:
        raise ValueError(
            f"unknown account '{account_label}'. valid: {sorted(ACCOUNT_LABELS)}"
        )
    return acct


# ---------- header / body extraction ----------

def _headers_map(payload: dict[str, Any]) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in (payload.get("headers") or [])
    }


def _decode_b64url(data: str) -> str:
    try:
        # Gmail uses URL-safe base64 without padding.
        return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk a Gmail message payload for the best text body (plain > html)."""
    plain = _walk_for_mime(payload, "text/plain")
    if plain:
        return plain
    html = _walk_for_mime(payload, "text/html")
    return html or ""


def _walk_for_mime(part: dict[str, Any], target: str) -> str:
    if not part:
        return ""
    if part.get("mimeType") == target:
        data = (part.get("body") or {}).get("data")
        if data:
            return _decode_b64url(data)
    for child in part.get("parts") or []:
        found = _walk_for_mime(child, target)
        if found:
            return found
    return ""


# ---------- public API ----------

def search(account_label: str, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search threads in an inbox. Returns [{id, snippet, historyId}]."""
    acct = _validate(account_label)
    from tools import gmail_multi
    threads = gmail_multi.search(acct, query or "", limit=limit)
    return [
        {"id": t.get("id"), "snippet": t.get("snippet"), "historyId": t.get("historyId")}
        for t in threads
    ]


def read_thread(account_label: str, thread_id: str, body_chars: int = 4000) -> dict[str, Any]:
    """Read a full thread, simplified to AVO-friendly per-message dicts."""
    acct = _validate(account_label)
    if not thread_id:
        raise ValueError("thread_id is required")
    from tools import gmail_multi
    thread = gmail_multi.get_thread(acct, thread_id, message_format="full")
    messages = []
    for m in thread.get("messages") or []:
        payload = m.get("payload") or {}
        h = _headers_map(payload)
        messages.append({
            "id": m.get("id"),
            "from": h.get("from", ""),
            "to": h.get("to", ""),
            "cc": h.get("cc", ""),
            "subject": h.get("subject", ""),
            "date": h.get("date", ""),
            "snippet": m.get("snippet", ""),
            "body": _extract_body(payload)[:body_chars],
            "label_ids": m.get("labelIds") or [],
        })
    return {"account": acct, "thread_id": thread_id, "message_count": len(messages), "messages": messages}


def labels(account_label: str) -> list[dict[str, Any]]:
    """List labels for an inbox. Returns [{id, name, type}]."""
    acct = _validate(account_label)
    from tools import gmail_multi
    return [
        {"id": l.get("id"), "name": l.get("name"), "type": l.get("type")}
        for l in gmail_multi.list_labels(acct)
    ]


def apply_label(account_label: str, thread_id: str, label: str) -> dict[str, Any]:
    """Ensure a label exists and add it to a thread. Returns {ok, label_id}."""
    acct = _validate(account_label)
    if not thread_id or not label:
        raise ValueError("thread_id and label are required")
    from tools import gmail_multi
    label_id = gmail_multi.ensure_label(acct, label)
    gmail_multi.add_label(acct, thread_id, label_id)
    return {"ok": True, "account": acct, "thread_id": thread_id, "label": label, "label_id": label_id}


def archive(account_label: str, thread_id: str) -> dict[str, Any]:
    """Archive a thread (remove INBOX)."""
    acct = _validate(account_label)
    if not thread_id:
        raise ValueError("thread_id is required")
    from tools import gmail_multi
    gmail_multi.archive(acct, thread_id)
    return {"ok": True, "account": acct, "thread_id": thread_id, "action": "archived"}


def mark_read(account_label: str, thread_id: str) -> dict[str, Any]:
    """Mark a thread as read (remove UNREAD)."""
    acct = _validate(account_label)
    if not thread_id:
        raise ValueError("thread_id is required")
    from tools import gmail_multi
    gmail_multi.mark_read(acct, thread_id)
    return {"ok": True, "account": acct, "thread_id": thread_id, "action": "marked_read"}
