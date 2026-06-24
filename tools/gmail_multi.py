"""Multi-account Gmail API wrapper for the Postal Agent.

Per-account search / read / label / archive — agents call these tools to
operate across any connected inbox without managing OAuth themselves.

V1 is READ + MODIFY only (per Postal Agent Q1=A locked 2026-06-22). NO SEND —
that's v1.5. The OAuth scope set we requested (gmail.readonly + gmail.modify
+ gmail.labels) doesn't include gmail.send so even if a call slipped through,
Google would reject.

Usage from an agent:
    from tools.gmail_multi import search, get_thread, add_label, archive

    threads = search("avi", "from:datamoon", limit=10)
    msg = get_thread("avi", threads[0]["id"])
    add_label("avi", thread_id, label_id)
    archive("avi", thread_id)

Env vars expected (Doppler paperclip/prd):
    POSTAL_GOOGLE_CLIENT_ID
    POSTAL_GOOGLE_CLIENT_SECRET
    APP_SECRET   (used by postal_oauth to decrypt the refresh_token)

On refresh failure (invalid_grant — user revoked or token expired), the
account is marked `needs_reauth` in postal_tokens. The exception is
re-raised so the caller knows; the Postal Agent's classifier loop catches
+ surfaces to Pit Wall.

Plan: ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md (Phase 2)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from services.postal_oauth import (
    SCOPES,
    get_refresh_token,
    mark_needs_reauth,
)

logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"


# ---------- Credentials + service builders ----------

def _build_credentials(account_label: str) -> Credentials:
    """Build google.oauth2.credentials.Credentials for an account.

    Decrypts the refresh_token from postal_tokens via postal_oauth helpers.
    """
    client_id = os.environ.get("POSTAL_GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("POSTAL_GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise RuntimeError("POSTAL_GOOGLE_CLIENT_ID / CLIENT_SECRET not configured")

    refresh_token = get_refresh_token(account_label)

    return Credentials(
        token=None,  # access_token populated on first refresh
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def _build_service(account_label: str):
    """Build a Gmail API service client. Refreshes the access_token on demand."""
    try:
        creds = _build_credentials(account_label)
        creds.refresh(GoogleAuthRequest())
    except Exception as e:
        msg = str(e).lower()
        if "invalid_grant" in msg or "token has been expired or revoked" in msg:
            mark_needs_reauth(account_label, reason=str(e)[:200])
        raise
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ---------- Public API ----------

def get_profile(account_label: str) -> dict[str, Any]:
    """Returns {emailAddress, messagesTotal, threadsTotal, historyId}."""
    svc = _build_service(account_label)
    return svc.users().getProfile(userId="me").execute()


def search(account_label: str, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search threads matching Gmail query syntax (e.g. 'from:datamoon is:unread').

    Returns a list of {id, historyId, snippet?} dicts — call get_thread() for full content.
    """
    svc = _build_service(account_label)
    res = svc.users().threads().list(userId="me", q=query, maxResults=limit).execute()
    return res.get("threads", [])


def get_thread(account_label: str, thread_id: str, message_format: str = "full") -> dict[str, Any]:
    """Get full thread including all messages (headers, body, attachments)."""
    svc = _build_service(account_label)
    return svc.users().threads().get(
        userId="me",
        id=thread_id,
        format=message_format,
    ).execute()


def list_labels(account_label: str) -> list[dict[str, Any]]:
    """List user + system labels. Returns [{id, name, type, ...}]."""
    svc = _build_service(account_label)
    res = svc.users().labels().list(userId="me").execute()
    return res.get("labels", [])


def create_label(
    account_label: str,
    name: str,
    label_list_visibility: str = "labelShow",
    message_list_visibility: str = "show",
) -> dict[str, Any]:
    """Create a new user label. Returns the created label dict including its id.

    Use this once per account to set up Postal/<category> labels for the classifier.
    """
    svc = _build_service(account_label)
    body = {
        "name": name,
        "labelListVisibility": label_list_visibility,
        "messageListVisibility": message_list_visibility,
    }
    return svc.users().labels().create(userId="me", body=body).execute()


def ensure_label(account_label: str, name: str) -> str:
    """Find an existing label by name OR create it. Returns label_id."""
    labels = list_labels(account_label)
    for lbl in labels:
        if lbl.get("name") == name:
            return lbl["id"]
    created = create_label(account_label, name)
    return created["id"]


def add_label(account_label: str, thread_id: str, label_id: str) -> dict[str, Any]:
    """Add a label to a thread."""
    svc = _build_service(account_label)
    return svc.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def remove_label(account_label: str, thread_id: str, label_id: str) -> dict[str, Any]:
    """Remove a label from a thread."""
    svc = _build_service(account_label)
    return svc.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"removeLabelIds": [label_id]},
    ).execute()


def mark_read(account_label: str, thread_id: str) -> dict[str, Any]:
    """Mark a thread as read (removes UNREAD system label)."""
    svc = _build_service(account_label)
    return svc.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def archive(account_label: str, thread_id: str) -> dict[str, Any]:
    """Archive a thread (removes INBOX system label — Gmail's definition of archive)."""
    svc = _build_service(account_label)
    return svc.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()


# ---------- History API (for efficient sync polling — Phase 3 will use this) ----------

def list_history_since(account_label: str, start_history_id: int | str) -> dict[str, Any]:
    """List changes since a known historyId. Used for incremental sync.

    Returns {history: [...], historyId: <new_cursor>, nextPageToken?: ...}.
    """
    svc = _build_service(account_label)
    return svc.users().history().list(
        userId="me",
        startHistoryId=str(start_history_id),
        historyTypes=["messageAdded", "labelAdded", "labelRemoved"],
    ).execute()
