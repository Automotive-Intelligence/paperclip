"""services/instantly_reply_sync.py -- replies OUT of Instantly's paywalled
Unibox, INTO the brand's Twenty. Michael's ask (2026-08-07): "I dislike this
Instantly system... move these responses into Twenty."

The poller closes the loop the paywall blocks: pull every received reply from
the Instantly API (which we already pay for), classify it with THE fail-closed
classifier (services/reply_classify), and feed the real ones through the
EXISTING intent_inbound pipeline -- which already owns idempotency (explicit
key: "instantly:<message_id>"), the Postgres audit row, the Twenty Person
upsert + Signal record, and the hot-reply SMS. This module adds only (a) the
poll and (b) reply-text VISIBILITY: a Twenty Note on the person carrying the
subject, the classification label, and the reply body, so a reply can be read
and worked from Twenty without ever opening Instantly.

What syncs, by label (fail-closed doctrine):
  positive  -> Twenty (person + signal + note titled POSITIVE) + hot-reply SMS
               fires via the pipeline. The money path.
  unknown   -> Twenty (person + signal + note) -- a human-possible reply gets
               eyes in the CRM, never /dev/null.
  autoreply / bounce / negative -> NOT synced to Twenty (counted in the digest
               only). OOO machines and dead mailboxes are exactly the noise
               Michael is escaping; a clear "no" is dropped per doctrine (the
               suppression rail owns do-not-contact, not this module).

Brands: avi + bookd (live Instantly + a Twenty workspace). wd cold is dormant
(Smartlead when it wakes), aipg is GHL-backed, pp has no Twenty workspace --
all skipped honestly, never errored.

Shadow-safe: commit=False (default) reads + classifies + reports and writes
NOTHING (no handle_event, no note). Mirrors the sdr_engine dry-run pattern.
Sending replies stays wherever it lives today (Instantly / the mailbox);
tools/brand_send.py remains gated off -- this module reads, never sends.
"""
from __future__ import annotations

import html
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import requests

from services.reply_classify import IB, classify_text, message_text

logger = logging.getLogger(__name__)

# brand key (intent_inbound's vocabulary) -> Instantly API key env var.
_SYNC_BRANDS: Dict[str, str] = {
    "avi":   "INSTANTLY_API_KEY_AVI",
    "bookd": "INSTANTLY_API_KEY_BOOKD",
}

_SKIP_REASONS = {
    "wd":   "cold dormant (Smartlead when live); inbound already lands in Twenty",
    "aipg": "GHL-backed; replies live in GHL, not Twenty",
    "pp":   "no P&P Twenty workspace provisioned",
}

_NOTE_BODY_LIMIT = 2000  # keep notes readable; full text stays in the audit row


def _clean_text(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw or "")
    return html.unescape(re.sub(r"\s+", " ", txt)).strip()


def _fetch_repliers(H: dict) -> List[Tuple[str, str]]:
    """(campaign_name, lead_email) for every replier across ACTIVE campaigns."""
    out: List[Tuple[str, str]] = []
    r = requests.get(f"{IB}/campaigns", headers=H, params={"limit": 50}, timeout=25)
    r.raise_for_status()
    for c in r.json().get("items", []):
        if c.get("status") != 1:
            continue
        start = None
        while True:
            body = {"campaign": c["id"], "limit": 100}
            if start:
                body["starting_after"] = start
            j = requests.post(f"{IB}/leads/list", headers=H, json=body, timeout=25).json()
            items = j.get("items", [])
            for l in items:
                if (l.get("email_reply_count") or 0) > 0 and l.get("email"):
                    out.append((c.get("name", ""), l["email"]))
            start = j.get("next_starting_after")
            if not start or not items:
                break
    return out


def _fetch_received(H: dict, lead_email: str) -> List[dict]:
    """Messages actually authored by the lead (not our sends, not alerts)."""
    r = requests.get(f"{IB}/emails", headers=H, timeout=25,
                     params={"lead": lead_email, "email_type": "received", "limit": 10})
    if not r.ok:
        return []
    return [m for m in r.json().get("items", [])
            if (m.get("from_address_email") or "").lower() == lead_email.lower()]


def _event_payload(brand: str, campaign: str, lead_email: str, msg: dict,
                   label: str) -> dict:
    text = _clean_text((msg.get("body") or {}).get("text")
                       or (msg.get("body") or {}).get("html") or "")
    return {
        "brand": brand,
        "person_ref": {"email": lead_email},
        "channel": "cold_email",
        "response_type": "inbound_reply",
        "subtype": label,
        "idempotency_key": f"instantly:{msg.get('id') or msg.get('message_id') or ''}",
        "timestamp": (msg.get("timestamp_email") or msg.get("timestamp_created")),
        "raw_body": {
            "source_name": "instantly_reply_sync",
            "campaign": campaign,
            "subject": msg.get("subject") or "",
            "reply_text": text[:4000],
            "message_id": msg.get("id") or "",
        },
    }


def _note_for(payload: dict, label: str) -> Tuple[str, str]:
    subject = payload["raw_body"]["subject"]
    tag = "POSITIVE reply" if label == "positive" else "Reply (needs eyes)"
    title = f"{tag} via cold email: {subject}"[:120]
    body = (f"Label: {label.upper()} (fail-closed classifier)\n"
            f"Campaign: {payload['raw_body']['campaign']}\n"
            f"Received: {payload['timestamp']}\n\n"
            f"{payload['raw_body']['reply_text'][:_NOTE_BODY_LIMIT]}")
    return title, body


def _sync_one(payload: dict, label: str) -> Tuple[str, Optional[str]]:
    """Feed the pipeline + attach the visibility note. Returns (status, twenty_id)."""
    from services.intent_inbound import handle_event

    res = handle_event(payload)
    if (res.get("audit") or {}).get("deduped"):
        return "deduped", None
    twenty = res.get("twenty") or {}
    person_id = twenty.get("twenty_id") or ""
    if person_id:
        try:
            from tools.twenty import create_note_for_person
            title, body = _note_for(payload, label)
            create_note_for_person(
                business_key=twenty.get("business_key") or "",
                person_id=person_id, title=title, body=body)
        except Exception as e:  # note is visibility, not the load-bearing write
            logger.warning("[reply-sync] note write non-fatal: %s", e)
    return "synced", person_id or None


def run_reply_sync(commit: bool = False) -> dict:
    """One sweep. commit=False (default) = read-only report; commit=True feeds
    the intent_inbound pipeline (idempotent per message) + writes the note."""
    counts = {"replies_seen": 0, "synced": 0, "deduped": 0,
              "skipped_noise": 0, "errors": 0}
    lines: List[str] = []
    for brand, env in _SYNC_BRANDS.items():
        key = (os.getenv(env) or "").strip()
        if not key:
            lines.append(f"- {brand}: SKIP (no {env} set)")
            continue
        H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            repliers = _fetch_repliers(H)
        except Exception as e:
            counts["errors"] += 1
            lines.append(f"- {brand}: campaign read failed: {type(e).__name__}: {e}")
            continue
        for campaign, lead_email in repliers:
            try:
                for msg in _fetch_received(H, lead_email):
                    counts["replies_seen"] += 1
                    label = classify_text(message_text(msg))
                    if label in ("bounce", "autoreply", "negative"):
                        counts["skipped_noise"] += 1
                        lines.append(f"- {brand}: {lead_email} [{label}] noise, not synced")
                        continue
                    payload = _event_payload(brand, campaign, lead_email, msg, label)
                    if commit:
                        status, pid = _sync_one(payload, label)
                        counts["synced" if status == "synced" else "deduped"] += 1
                        lines.append(f"- {brand}: {lead_email} [{label}] {status}"
                                     + (f" -> person {pid}" if pid else ""))
                    else:
                        lines.append(f"- {brand}: {lead_email} [{label}] would sync")
            except Exception as e:  # one replier never kills the sweep
                counts["errors"] += 1
                lines.append(f"- {brand}: {lead_email} error: {type(e).__name__}: {e}")
    for brand, why in _SKIP_REASONS.items():
        lines.append(f"- {brand}: out of scope ({why})")
    return {**counts, "digest": "\n".join(lines) or "(no repliers found)"}
