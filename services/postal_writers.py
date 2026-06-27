"""Postal Agent destination writers — Phase 4.

Phase 3 (postal_router.route_to_destinations) only *logged* the intended
destinations. This module turns those intents into real side effects:

    twenty:<workspace>   → create/dedupe a Person in the business's Twenty workspace
    avo_chat:<channel>   → post a Slack message into the persona channel
    pit_wall             → post a Slack message into #pit-wall (telemetry)
    label_only           → apply the Gmail label  Postal/<category>  to the thread
    archive              → apply the label, then remove INBOX (Gmail "archive")
    skip                 → no-op

Safety gate
-----------
Real writes are gated behind POSTAL_WRITES_ENABLED. When it's unset/false the
module is a no-op that returns the would-be destinations tagged as "dry-run",
preserving the conservative Phase 3 behaviour so a deploy never starts mutating
inboxes / CRM until Michael flips the switch:

    POSTAL_WRITES_ENABLED=true   # in Doppler paperclip/prd

Iron rules:
- Each destination writes independently; one failing must not abort the others.
- Idempotency for Gmail (label/archive) and Twenty (email dedupe) is handled by
  the underlying APIs; the agent's postal_processed table prevents re-processing
  the same message at all.
- Never raise out of execute_destinations — the agent loop relies on it returning.

Plan: ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md (Phase 4)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api/chat.postMessage"
_SLACK_TIMEOUT = 15

# Slack channel that backs the `pit_wall` destination (matches flag_router's digest).
PIT_WALL_CHANNEL = os.environ.get("POSTAL_PITWALL_CHANNEL", "pit-wall")

# Maps the router's `twenty:<workspace>` code to tools/twenty.py business_key.
# Only workspaces that the routing table actually targets need an entry.
TWENTY_WORKSPACE_TO_BUSINESS = {
    "wd": "callingdigital",
    "avi": "autointelligence",
    "bookd": "bookd",
}


def writes_enabled() -> bool:
    """True iff POSTAL_WRITES_ENABLED is a truthy string. Default: disabled."""
    return (os.environ.get("POSTAL_WRITES_ENABLED", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ---------- sender parsing ----------

_ADDR_RE = re.compile(r"<\s*([^>]+?)\s*>")


def parse_sender(raw: str) -> tuple[str, str, str]:
    """Split a From header into (display_name, email, domain).

    Handles "Jane Doe <jane@acme.com>" and bare "jane@acme.com".
    Returns ("", "", "") when no email can be extracted.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", "", ""
    m = _ADDR_RE.search(raw)
    if m:
        email = m.group(1).strip()
        name = raw[: m.start()].strip().strip('"').strip()
    elif "@" in raw:
        email = raw.strip().strip("<>").strip()
        name = ""
    else:
        return "", "", ""
    email = email.lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    return name, email, domain


# ---------- Gmail label / archive ----------

def _label_name(category: str) -> str:
    return f"Postal/{category}"


def _apply_label(account_label: str, category: str, thread_meta: dict[str, Any]) -> None:
    from tools import gmail_multi

    thread_id = thread_meta.get("id")
    if not thread_id:
        raise ValueError("thread_meta missing 'id' — cannot apply label")
    label_id = gmail_multi.ensure_label(account_label, _label_name(category))
    gmail_multi.add_label(account_label, thread_id, label_id)


def _archive(account_label: str, category: str, thread_meta: dict[str, Any]) -> None:
    from tools import gmail_multi

    # Label first so archived mail is still findable under Postal/<category>.
    _apply_label(account_label, category, thread_meta)
    thread_id = thread_meta.get("id")
    gmail_multi.archive(account_label, thread_id)


# ---------- Slack ----------

def _slack_token() -> str | None:
    return (os.environ.get("SLACK_BOT_TOKEN") or "").strip() or None


def _post_slack(channel: str, text: str, blocks: list[dict] | None = None) -> None:
    token = _slack_token()
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN missing — cannot post to Slack")
    payload: dict[str, Any] = {"channel": channel, "text": text, "unfurl_links": False}
    if blocks:
        payload["blocks"] = blocks
    r = requests.post(
        _SLACK_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=_SLACK_TIMEOUT,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not (r.ok and body.get("ok")):
        raise RuntimeError(f"Slack post failed (channel={channel}): http={r.status_code} body={body}")


def _mail_blocks(account_label: str, category: str, thread_meta: dict[str, Any]) -> tuple[str, list[dict]]:
    name, email, _domain = parse_sender(thread_meta.get("sender", ""))
    subject = (thread_meta.get("subject") or "(no subject)")[:140]
    snippet = (thread_meta.get("snippet") or "")[:280]
    who = f"{name} <{email}>" if name and email else (email or thread_meta.get("sender", "?"))
    headline = f":envelope_with_arrow: *{category}* in *{account_label}* inbox"
    fields = f"*From:* {who}\n*Subject:* {subject}"
    if snippet:
        fields += f"\n*Preview:* {snippet}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": headline}},
        {"type": "section", "text": {"type": "mrkdwn", "text": fields}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"Postal Agent · {account_label} · thread {thread_meta.get('id', '?')[:16]}"}
        ]},
    ]
    fallback = f"{category} in {account_label}: {who} — {subject}"
    return fallback, blocks


def _write_slack(channel: str, account_label: str, category: str, thread_meta: dict[str, Any]) -> None:
    fallback, blocks = _mail_blocks(account_label, category, thread_meta)
    _post_slack(channel, fallback, blocks)


def _write_pit_wall(account_label: str, category: str, thread_meta: dict[str, Any]) -> None:
    fallback, blocks = _mail_blocks(account_label, category, thread_meta)
    _post_slack(PIT_WALL_CHANNEL, fallback, blocks)


# ---------- Twenty CRM ----------

def _write_twenty(workspace: str, category: str, thread_meta: dict[str, Any]) -> None:
    business_key = TWENTY_WORKSPACE_TO_BUSINESS.get(workspace)
    if not business_key:
        raise ValueError(f"no Twenty business mapping for workspace '{workspace}'")
    name, email, domain = parse_sender(thread_meta.get("sender", ""))
    if not email:
        raise ValueError("no sender email — cannot create Twenty person")

    # Lazy import: keeps twenty's `requests` import off the hot path when writes
    # are disabled, and avoids a hard module dependency at import time.
    from tools.twenty import push_prospects_to_twenty

    prospect = {
        "contact": name,
        "email": email,
        "business_name": domain,   # company dedupes on domain; bare host is fine
        "domain": domain,
    }
    results = push_prospects_to_twenty(
        [prospect], source_agent="postal", business_key=business_key
    )
    r = results[0] if results else {}
    if r.get("status") == "failed":
        raise RuntimeError(f"Twenty write failed: {r.get('error', 'unknown')}")


# ---------- dispatcher ----------

def execute_destinations(
    account_label: str,
    category: str,
    destinations: list[str],
    thread_meta: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Execute each destination. Returns (completed, failed).

    `completed` is the list of destination codes whose side effect succeeded
    (what gets recorded in postal_processed.routed_to). `failed` carries
    "<dest>: <error>" strings for the agent's stats. Never raises.

    When POSTAL_WRITES_ENABLED is off, nothing is written: every destination is
    returned in `completed` tagged with a `dry-run:` prefix so the audit trail
    shows intent without implying a real side effect happened.
    """
    if not writes_enabled():
        logger.info(
            "postal writers DRY-RUN (POSTAL_WRITES_ENABLED off): account=%s category=%s would_route=%s",
            account_label, category, destinations,
        )
        return [f"dry-run:{d}" for d in destinations], []

    completed: list[str] = []
    failed: list[str] = []
    for dest in destinations:
        try:
            if dest == "skip":
                pass
            elif dest == "label_only":
                _apply_label(account_label, category, thread_meta)
            elif dest == "archive":
                _archive(account_label, category, thread_meta)
            elif dest == "pit_wall":
                _write_pit_wall(account_label, category, thread_meta)
            elif dest.startswith("avo_chat:"):
                _write_slack(dest.split(":", 1)[1], account_label, category, thread_meta)
            elif dest.startswith("twenty:"):
                _write_twenty(dest.split(":", 1)[1], category, thread_meta)
            else:
                raise ValueError(f"unknown destination code '{dest}'")
            completed.append(dest)
        except Exception as e:  # noqa: BLE001 — isolate per-destination failures
            logger.warning(
                "postal writer failed: account=%s category=%s dest=%s err=%s",
                account_label, category, dest, e,
            )
            failed.append(f"{dest}: {type(e).__name__}: {str(e)[:160]}")
    return completed, failed
