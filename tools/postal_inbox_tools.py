"""tools/postal_inbox_tools.py — AVO tools for the multi-inbox Postal API.

Wires the six `/postal/inbox/*` endpoints (documented in
docs/POSTAL_INBOX_API.md) into AVO as one tool per endpoint, so AVO can
search / read / triage ANY connected inbox on demand, addressed by
`account` label (avi, wd, salesdroid, aipg, agentempire, bookd).

Why HTTP (not a direct import of services.postal_inbox)?
  AVO (avo-slack / the Paperclip MCP) runs as a separate service from the
  Paperclip backend. It can't share the backend's google-auth / Postgres /
  token state, so it talks to the inboxes the same way any client does:
  plain HTTP against the backend, authenticated with the Paperclip API key.
  The doc calls these "plain HTTP tools" for exactly this reason.

Config (env):
  PAPERCLIP_BASE_URL   — backend base, e.g. https://paperclip-production-ba14.up.railway.app
  PAPERCLIP_API_KEY    — one of the backend's API_KEYS (sent as Bearer)

Surface for the Paperclip MCP:
  `POSTAL_INBOX_TOOLS` is a list of {name, description, input_schema,
  handler} — iterate it to register one MCP tool per endpoint. Or call the
  module-level functions (inbox_search, inbox_thread, ...) directly; each
  returns the parsed JSON body and raises PostalInboxToolError on failure
  with the upstream status code + detail attached.

Errors mirror the backend contract: 400 unknown account / bad input, 404 no
active token for that account (re-run OAuth), 502 upstream Gmail error.
"""

from __future__ import annotations

import os
from typing import Any

import requests

_REQUEST_TIMEOUT = 30

# Canonical inbox set (mirrors services.postal_inbox / postal_oauth). Used only
# to give callers a clear, fast error before a round-trip; the backend remains
# the source of truth and re-validates.
ACCOUNT_LABELS = ("avi", "wd", "salesdroid", "aipg", "agentempire", "bookd")


class PostalInboxToolError(RuntimeError):
    """A Postal inbox tool call failed.

    `status_code` is the upstream HTTP status when the backend answered
    (400/404/502 per the API contract), or None for transport/config errors.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _base_url() -> str:
    base = (os.getenv("PAPERCLIP_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise PostalInboxToolError(
            "PAPERCLIP_BASE_URL is not set — cannot reach the Postal inbox API."
        )
    return base


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = (os.getenv("PAPERCLIP_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _raise_for_response(resp: requests.Response) -> dict[str, Any]:
    """Return the JSON body, or raise PostalInboxToolError with status + detail."""
    if resp.ok:
        try:
            return resp.json()
        except ValueError:
            raise PostalInboxToolError(
                f"Postal inbox API returned non-JSON ({resp.status_code}).",
                status_code=resp.status_code,
            )
    # FastAPI errors are {"detail": "..."}; fall back to raw text.
    detail = ""
    try:
        detail = (resp.json() or {}).get("detail", "")
    except ValueError:
        detail = (resp.text or "")[:300]
    raise PostalInboxToolError(
        f"Postal inbox API error {resp.status_code}: {detail}",
        status_code=resp.status_code,
    )


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{_base_url()}{path}",
            params=params,
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise PostalInboxToolError(f"Postal inbox API unreachable: {e}") from e
    return _raise_for_response(resp)


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{_base_url()}{path}",
            json=body,
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise PostalInboxToolError(f"Postal inbox API unreachable: {e}") from e
    return _raise_for_response(resp)


def _require(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v:
        raise PostalInboxToolError(f"{name} is required.")
    return v


# ---------------------------------------------------------------------------
# Tools — one per endpoint
# ---------------------------------------------------------------------------

def inbox_search(account: str, q: str, limit: int = 25) -> dict[str, Any]:
    """Search threads in a connected inbox using Gmail search syntax.

    `account` is the inbox label (avi, wd, salesdroid, aipg, agentempire,
    bookd). `q` uses Gmail query syntax, e.g.
    'from:datamoon is:unread newer_than:7d'. Returns
    {account, query, threads:[{id, snippet, historyId}]}.
    """
    return _get(
        "/postal/inbox/search",
        {"account": _require(account, "account"), "q": q or "", "limit": limit},
    )


def inbox_thread(account: str, thread_id: str) -> dict[str, Any]:
    """Read a full thread from an inbox, simplified to AVO-friendly messages.

    Returns {account, thread_id, message_count, messages:[{id, from, to, cc,
    subject, date, snippet, body, label_ids}]}.
    """
    return _get(
        "/postal/inbox/thread",
        {
            "account": _require(account, "account"),
            "thread_id": _require(thread_id, "thread_id"),
        },
    )


def inbox_labels(account: str) -> dict[str, Any]:
    """List the labels available in an inbox.

    Returns {account, labels:[{id, name, type}]}.
    """
    return _get("/postal/inbox/labels", {"account": _require(account, "account")})


def inbox_apply_label(account: str, thread_id: str, label: str) -> dict[str, Any]:
    """Ensure a label exists in the inbox and apply it to a thread.

    Acts immediately (a human asked AVO to triage). Returns
    {ok, account, thread_id, label, label_id}.
    """
    return _post(
        "/postal/inbox/label",
        {
            "account": _require(account, "account"),
            "thread_id": _require(thread_id, "thread_id"),
            "label": _require(label, "label"),
        },
    )


def inbox_archive(account: str, thread_id: str) -> dict[str, Any]:
    """Archive a thread (remove it from INBOX).

    Acts immediately. Returns {ok, account, thread_id, action:'archived'}.
    """
    return _post(
        "/postal/inbox/archive",
        {
            "account": _require(account, "account"),
            "thread_id": _require(thread_id, "thread_id"),
        },
    )


def inbox_mark_read(account: str, thread_id: str) -> dict[str, Any]:
    """Mark a thread as read (remove the UNREAD label).

    Acts immediately. Returns {ok, account, thread_id, action:'marked_read'}.
    """
    return _post(
        "/postal/inbox/mark_read",
        {
            "account": _require(account, "account"),
            "thread_id": _require(thread_id, "thread_id"),
        },
    )


# ---------------------------------------------------------------------------
# MCP registration surface — one tool per endpoint
# ---------------------------------------------------------------------------

_ACCOUNT_SCHEMA = {
    "type": "string",
    "description": "Connected inbox label.",
    "enum": list(ACCOUNT_LABELS),
}

POSTAL_INBOX_TOOLS: list[dict[str, Any]] = [
    {
        "name": "postal_inbox_search",
        "description": (
            "Search threads in a connected inbox by Gmail query "
            "(e.g. 'from:bob is:unread newer_than:7d')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": _ACCOUNT_SCHEMA,
                "q": {"type": "string", "description": "Gmail search query."},
                "limit": {
                    "type": "integer",
                    "description": "Max threads to return.",
                    "default": 25,
                },
            },
            "required": ["account", "q"],
        },
        "handler": inbox_search,
    },
    {
        "name": "postal_inbox_thread",
        "description": "Read a full thread from a connected inbox, simplified per message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": _ACCOUNT_SCHEMA,
                "thread_id": {"type": "string", "description": "Gmail thread id."},
            },
            "required": ["account", "thread_id"],
        },
        "handler": inbox_thread,
    },
    {
        "name": "postal_inbox_labels",
        "description": "List the labels available in a connected inbox.",
        "input_schema": {
            "type": "object",
            "properties": {"account": _ACCOUNT_SCHEMA},
            "required": ["account"],
        },
        "handler": inbox_labels,
    },
    {
        "name": "postal_inbox_apply_label",
        "description": "Ensure a label exists and apply it to a thread (acts immediately).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": _ACCOUNT_SCHEMA,
                "thread_id": {"type": "string", "description": "Gmail thread id."},
                "label": {"type": "string", "description": "Label name to ensure + apply."},
            },
            "required": ["account", "thread_id", "label"],
        },
        "handler": inbox_apply_label,
    },
    {
        "name": "postal_inbox_archive",
        "description": "Archive a thread (remove INBOX). Acts immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": _ACCOUNT_SCHEMA,
                "thread_id": {"type": "string", "description": "Gmail thread id."},
            },
            "required": ["account", "thread_id"],
        },
        "handler": inbox_archive,
    },
    {
        "name": "postal_inbox_mark_read",
        "description": "Mark a thread as read (remove UNREAD). Acts immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": _ACCOUNT_SCHEMA,
                "thread_id": {"type": "string", "description": "Gmail thread id."},
            },
            "required": ["account", "thread_id"],
        },
        "handler": inbox_mark_read,
    },
]

# name -> handler, for MCP dispatch by tool name.
POSTAL_INBOX_HANDLERS = {t["name"]: t["handler"] for t in POSTAL_INBOX_TOOLS}


def dispatch(name: str, **kwargs: Any) -> dict[str, Any]:
    """Call a Postal inbox tool by its registered MCP name."""
    handler = POSTAL_INBOX_HANDLERS.get(name)
    if handler is None:
        raise PostalInboxToolError(f"unknown Postal inbox tool '{name}'.")
    return handler(**kwargs)


__all__ = [
    "ACCOUNT_LABELS",
    "PostalInboxToolError",
    "inbox_search",
    "inbox_thread",
    "inbox_labels",
    "inbox_apply_label",
    "inbox_archive",
    "inbox_mark_read",
    "POSTAL_INBOX_TOOLS",
    "POSTAL_INBOX_HANDLERS",
    "dispatch",
]
