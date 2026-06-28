"""services/wd_crm_push_responder.py — WD "CRM push" reply-trigger automation.

Deliverable 3 of the WD v3 CRM-push upsell (avo-telemetry file 72; flag IM-WD).

Sequence 3 Email 2 tells prospects: reply "CRM push" and we send a scoping form.
Loops cannot branch on reply *content* (no "if reply contains X"), and WD's send
stack is Loops, not the Gmail API. So this runs as a poll over the WD inbox
(read-only Gmail, account label "wd"), detects an inbound reply that actually
says "CRM push", and auto-sends the 2a scoping form.

Why a poll, not Gmail Pub/Sub push: the read-only Gmail tool (tools/gmail_multi,
scopes = readonly + modify, NO send) is already wired for the "wd" account, and
this codebase already sweeps inboxes on an interval (_run_postal_sweep, 15min).
Pub/Sub would add a GCP topic + weekly watch-renewal cron for no behavioural gain
at this volume.

THE CORRECTNESS TRAP: Email 2's own body contains the words "CRM push" ("Reply
'CRM push'..."). A naive thread/full-text match fires on our own sent copy. So we
match ONLY on the de-quoted body of an INBOUND message (from != worshipdigital.co),
with the quoted prior message stripped, exactly like services/ape_reply_parser.

Idempotency: two Gmail labels, applied at thread level, checked on every pass.
  WD/crm-push-handled  -> scoping form auto-sent. Never reprocessed.
  WD/crm-push-pending  -> detected but no sender configured (manual 2a fallback).
                          Logged once, then skipped so we never re-alert or dupe.

Sender resolution (first ready wins), so the automation degrades gracefully:
  1. LOOPS_TRANSACTIONAL_ID_WD_CRM_PUSH set -> Loops transactional (WD's own
     authenticated sending domain; matches the file-72 spec). The template must
     be created once in the Loops dashboard with the 2a body -> its id.
  2. else MAIL_FROM_WORSHIPDIGITAL + a Resend key ready -> Resend, 2a body inline.
  3. else -> "pending" mode: label + log, human sends the 2a body by hand. This is
     the file-72 day-one fallback, so v3 is NOT blocked on this build.

Run from the scheduler (_run_wd_crm_push_responder in app.py) or by hand:
    python -m services.wd_crm_push_responder            # live
    python -m services.wd_crm_push_responder --dry-run  # detect only, no send/label
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ACCOUNT_LABEL = "wd"
HANDLED_LABEL = "WD/crm-push-handled"
PENDING_LABEL = "WD/crm-push-pending"

# WD's own sending identity. Anything from this domain is OUR message in the
# thread (the sequence email), never the prospect's reply.
WD_DOMAIN = "worshipdigital.co"

# Trigger match. Tolerates "crm push", "crm-push", and extra inner whitespace.
_TRIGGER_RE = re.compile(r"\bcrm[\s\-]+push\b", re.IGNORECASE)

# Gmail full-text prefilter. The authoritative check is _inbound_trigger_message;
# this only narrows what we fetch. 14d covers a sequence-length reply window.
_SEARCH_QUERY = '"crm push" newer_than:14d in:inbox'

LOOPS_TRANSACTIONAL_URL = "https://app.loops.so/api/v1/transactional"

# 2a scoping-form body, verbatim from avo-telemetry file 72 (gate-passed:
# voice / claims / mechanics / hero-metrics, no em-dashes). Single source of
# truth for the Resend fallback; the Loops template should carry this same text.
SCOPING_FORM_SUBJECT = "Let's scope your CRM push"
SCOPING_FORM_BODY = """Great, let's scope your CRM push. Answer these and we'll send a quote within 24 hours:

1. Which CRM do you use? (HubSpot, Salesforce, Pipedrive, Zoho, GoHighLevel, Twenty, other)
2. Roughly how many records are in this audience? (under 1k / 1k-5k / 5k-25k / 25k+)
3. Any custom field mapping needed? (specific fields, owners, lifecycle stages, custom objects)
4. Dedupe rules: if a record already exists in your CRM, should we skip it, update it, or create it anyway? And what do we match on (email, domain, phone)?
5. One-time push, or ongoing sync? (if ongoing, how often: daily, weekly, on each new audience)
6. When do you need this live by?
7. Anything else about your setup we should know? (API access, approvals, integrations)

Reply right here. We'll turn a quote around within 24 hours."""


# ---------- parsing ----------

def _email_addr(from_header: str) -> str:
    """Extract bare address from a 'Name <a@b.com>' or 'a@b.com' From header."""
    if not from_header:
        return ""
    m = re.search(r"<([^>]+)>", from_header)
    addr = (m.group(1) if m else from_header).strip().lower()
    # Guard against trailing display junk if no angle brackets were present.
    m2 = re.search(r"[\w.+-]+@[\w.-]+", addr)
    return m2.group(0) if m2 else addr


def _strip_quoted(body: str) -> str:
    """Drop the quoted prior message + signature so we only read what the
    prospect actually typed. Mirrors ape_reply_parser.classify_reply."""
    if not body:
        return ""
    cleaned = re.split(r"\n\s*On\s+.+wrote:", body, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.split(r"\n-{2,}\s*\n", cleaned, maxsplit=1)[0]  # "--" signature delimiter
    cleaned = "\n".join(
        line for line in cleaned.splitlines() if not line.lstrip().startswith(">")
    )
    return cleaned.strip()


def _is_inbound(from_header: str) -> bool:
    return WD_DOMAIN not in (from_header or "").lower()


def _inbound_trigger_message(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the most-recent INBOUND message whose freshly-typed (de-quoted)
    body says "crm push", or None. Never matches our own sent sequence copy."""
    for msg in reversed(messages or []):  # newest last in a Gmail thread
        if not _is_inbound(msg.get("from", "")):
            continue
        if _TRIGGER_RE.search(_strip_quoted(msg.get("body", ""))):
            return msg
    return None


# ---------- sender resolution ----------

def _loops_transactional_id() -> str:
    return os.getenv("LOOPS_TRANSACTIONAL_ID_WD_CRM_PUSH", "").strip()


def _send_via_loops(to_email: str) -> bool:
    api_key = os.getenv("LOOPS_API_KEY", "").strip()
    txn_id = _loops_transactional_id()
    if not api_key or not txn_id:
        return False
    import requests
    try:
        resp = requests.post(
            LOOPS_TRANSACTIONAL_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"transactionalId": txn_id, "email": to_email},
            timeout=20,
        )
        if resp.status_code in (200, 201):
            return True
        logger.error(
            "[wd_crm_push] Loops transactional error for %s: HTTP %s %s",
            to_email, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        logger.error("[wd_crm_push] Loops request failed for %s: %s", to_email, exc)
        return False


def _send_via_resend(to_email: str, original_subject: str) -> bool:
    from tools.outbound_email import send_unified_email, unified_email_ready
    if not unified_email_ready("worshipdigital"):
        return False
    base = re.sub(r"^\s*re:\s*", "", original_subject or "", flags=re.IGNORECASE).strip()
    subject = f"Re: {base}" if base else SCOPING_FORM_SUBJECT
    return send_unified_email(to_email, subject, SCOPING_FORM_BODY, "worshipdigital")


def _send_scoping_form(to_email: str, original_subject: str) -> str:
    """Try each configured sender in priority order. Returns the mode used:
    'loops' | 'resend' | 'unconfigured'."""
    if _send_via_loops(to_email):
        return "loops"
    if _send_via_resend(to_email, original_subject):
        return "resend"
    return "unconfigured"


# ---------- main pass ----------

def run(dry_run: bool = False, limit: int = 25) -> Dict[str, Any]:
    """One sweep of the WD inbox. Returns a summary dict.

    dry_run: detect + report only. No email sent, no label applied.
    """
    from services import postal_inbox
    from tools import gmail_multi

    summary: Dict[str, Any] = {
        "scanned": 0, "matched": 0, "sent": 0, "pending": 0,
        "already_done": 0, "dry_run": dry_run, "details": [],
    }

    threads = postal_inbox.search(ACCOUNT_LABEL, _SEARCH_QUERY, limit=limit)
    summary["scanned"] = len(threads)
    if not threads:
        return summary

    # Resolve idempotency label ids once. ensure_label is create-or-get.
    handled_id = gmail_multi.ensure_label(ACCOUNT_LABEL, HANDLED_LABEL)
    pending_id = gmail_multi.ensure_label(ACCOUNT_LABEL, PENDING_LABEL)
    skip_ids = {handled_id, pending_id}

    for t in threads:
        thread_id = t.get("id")
        if not thread_id:
            continue
        thread = postal_inbox.read_thread(ACCOUNT_LABEL, thread_id)
        messages = thread.get("messages") or []

        # Thread-level labels land on every message; one check is enough.
        if messages and skip_ids.intersection(messages[0].get("label_ids") or []):
            summary["already_done"] += 1
            continue

        hit = _inbound_trigger_message(messages)
        if not hit:
            continue  # full-text prefilter matched our own Email 2 copy only

        to_email = _email_addr(hit.get("from", ""))
        if not to_email:
            logger.warning("[wd_crm_push] trigger in thread %s but no sender address", thread_id)
            continue

        summary["matched"] += 1
        detail = {"thread_id": thread_id, "to": to_email, "subject": hit.get("subject", "")}

        if dry_run:
            detail["mode"] = "dry_run"
            summary["details"].append(detail)
            continue

        mode = _send_scoping_form(to_email, hit.get("subject", ""))
        detail["mode"] = mode

        if mode in ("loops", "resend"):
            gmail_multi.add_label(ACCOUNT_LABEL, thread_id, handled_id)
            summary["sent"] += 1
            logger.info("[wd_crm_push] scoping form sent to %s via %s (thread %s)",
                        to_email, mode, thread_id)
        else:
            # No sender wired: queue for the manual 2a fallback, label so we
            # neither re-alert nor double-send once a sender is configured.
            gmail_multi.add_label(ACCOUNT_LABEL, thread_id, pending_id)
            summary["pending"] += 1
            logger.warning(
                "[wd_crm_push] CRM-push reply from %s needs MANUAL 2a send "
                "(no Loops template / Resend sender configured) — thread %s",
                to_email, thread_id,
            )

        summary["details"].append(detail)

    return summary


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run(dry_run="--dry-run" in sys.argv)
    print(json.dumps(result, indent=2))
