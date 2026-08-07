"""services/tp_daily_engine.py -- the Team Principal daily heartbeat, Railway port.

Cloud port of avo-telemetry/scripts/tp_daily.py (launchd com.avo.tp-daily, 07:15 CT).
The FETCH + CLASSIFY + DECISION logic is vendored VERBATIM from that script (keep the
function unchanged, per the charter); only the I/O moves to a GitHub-API read-modify-
write so the heartbeat lands in the repo with the Mac closed. Idempotent: it refuses to
write a second heartbeat block for a day that already has one.

Reads LIVE Instantly numbers (never the state-file narrative) and writes ONE honest
block into team_principal_state.md: the one number, the table, blind spots, the
decision, the default, the deadline.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from services.avo_state_commit import update_state
# The classifier lives in ONE place now (services/reply_classify.py) so the TP
# heartbeat and the growth monitor can never drift apart. AUTOREPLY/NEGATIVE/
# POSITIVE and classify_text are re-exported so `tp_daily_engine.<name>` stays a
# valid reference for anything that imported them from here historically.
from services.reply_classify import (  # noqa: F401  (re-export for back-compat)
    AUTOREPLY,
    IB,
    NEGATIVE,
    POSITIVE,
    classify,
    classify_detailed,
    classify_text,
)

logger = logging.getLogger(__name__)

# Date the heartbeat by Central time (the laptop used naive local = CT, and the
# cron fires on CT). Dating by UTC would mis-stamp the evening as tomorrow and
# break idempotency against the laptop's CT-dated block.
_CT = ZoneInfo("America/Chicago")

_STATE_PATH = "team_principal_state.md"
BRANDS = {  # brand -> Instantly key env var
    "AIPG": "INSTANTLY_API_KEY_AIPG",
    "Book'd": "INSTANTLY_API_KEY_BOOKD",
    "P&P": "INSTANTLY_API_KEY_PAPERANDPURPOSE",
    "AvI": "INSTANTLY_API_KEY_AVI",
}


def outbound_truth() -> Tuple[list, list, list, list]:
    """Live send/reply counts per brand.

    Returns (rows, blind, needs_review, interested_emails): rows are
    per-campaign tuples (brand, campaign, leads, sent, replies, interested);
    blind is the unverifiable brands; needs_review is the de-duped list of
    replier emails we could NOT confidently classify (surfaced so a human
    confirms none is a lead); interested_emails NAMES every POSITIVE replier --
    an unnamed count made the one real lead unhuntable for 24 days while a
    phantom bounce wore Instantly's Interested label (2026-08-07 correction)."""
    rows, blind, needs_review, interested_emails = [], [], [], []
    for brand, env in BRANDS.items():
        key = os.getenv(env, "").strip()
        if not key:
            blind.append(f"{brand} (no key set)"); continue
        H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            r = requests.get(f"{IB}/campaigns", headers=H, params={"limit": 50}, timeout=25)
        except Exception as e:
            blind.append(f"{brand} ({type(e).__name__})"); continue
        if r.status_code == 401:
            blind.append(f"{brand} (key INVALID -- 401)"); continue
        if not r.ok:
            blind.append(f"{brand} ({r.status_code})"); continue
        for c in r.json().get("items", []):
            if c.get("status") != 1:
                continue
            leads, start = [], None
            while True:
                b = {"campaign": c["id"], "limit": 100}
                if start:
                    b["starting_after"] = start
                j = requests.post(f"{IB}/leads/list", headers=H, json=b, timeout=25).json()
                it = j.get("items", []); leads += it
                start = j.get("next_starting_after")
                if not start or not it:
                    break
            sent = sum(1 for l in leads if l.get("timestamp_last_contact"))
            repliers = [l for l in leads if (l.get("email_reply_count") or 0) > 0]
            positives, review = classify_detailed(H, repliers)
            for em in review:
                if em not in needs_review:
                    needs_review.append(em)
            for em in positives:
                if em not in interested_emails:
                    interested_emails.append(em)
            rows.append((brand, c["name"][:40], len(leads), sent, len(repliers), len(positives)))
    return rows, blind, needs_review, interested_emails


def build_block(rows: list, blind: list, today: str, needs_review: Optional[list] = None,
                interested_emails: Optional[list] = None) -> str:
    """The fixed Foundation-Bible heartbeat format.

    `needs_review` is the fail-closed safety net: replies we could not classify are
    NOT counted as interested, but they are surfaced on a "Needs eyes" line so an
    odd-worded real lead still gets a human look instead of vanishing."""
    needs_review = needs_review or []
    total_sent = sum(r[3] for r in rows)
    total_replies = sum(r[4] for r in rows)
    total_interested = sum(r[5] for r in rows)
    lines = [f"\n## 🏁 TP daily -- {today}\n"]
    lines.append(f"**The one number: interested humans = {total_interested}.** "
                 f"({total_sent} emails in the field, {total_replies} raw replies -- "
                 f"a rejection is not a lead.)\n")
    if rows:
        lines.append("| brand | campaign | leads | sent | replies | INTERESTED |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for b, n, l, s, rp, iv in rows:
            lines.append(f"| {b} | {n} | {l} | {s} | {rp} | **{iv}** |")
        lines.append("")
    else:
        lines.append("**Nothing is sending. Zero live campaigns.** That is the only fact "
                     "that matters today; everything else is motion.\n")
    if blind:
        lines.append(f"**Blind spots (cannot verify, treat as UNKNOWN not as zero):** "
                     f"{', '.join(blind)}.\n")
    if needs_review:
        n = len(needs_review)
        lines.append(
            f"**Needs eyes: {n} unclassified repl{'y' if n == 1 else 'ies'} "
            f"(NOT counted as interested; a human must confirm none is a real lead):** "
            f"{', '.join(needs_review)}.\n")
    rejections = total_replies - total_interested
    if total_interested > 0:
        who = ", ".join(interested_emails or []) or "(names unavailable -- fix the pipe)"
        decision = (f"WORK THE {total_interested} INTERESTED REPLY(IES) TODAY: {who}. "
                    f"That is the closest thing to money in this company. Everything else waits.")
        default = "If Michael says nothing: CRO drafts responses and surfaces them for send."
    elif total_sent == 0:
        decision = ("Get something sending. No live campaign means no possible revenue, "
                    "and no amount of building changes that.")
        default = "If Michael says nothing: CRO picks the nearest vetted list and ships it."
    elif rejections >= 3 and total_interested == 0 and total_sent >= 300:
        decision = (f"{total_sent} sent, {rejections} rejections, ZERO interest. That is not "
                    f"a volume problem, it is an offer/list problem. Stop adding volume and "
                    f"fix the message or the audience.")
        default = "If Michael says nothing: CRO audits the worst-performing campaign's copy + ICP."
    else:
        decision = ("Hold. Sends are live and it is too early to read the result. Do NOT add "
                    "volume, do NOT rewrite copy on thin data.")
        default = "If Michael says nothing: keep sending, re-read at the next reply or in 48h."
    lines += [f"**Decision:** {decision}\n", f"**Default:** {default}\n",
              f"**Deadline:** end of day {today}.\n"]
    return "\n".join(lines)


def _insert_transform(block: str, today: str):
    """Return a transform(content)->new|None that updates Last-updated and inserts
    the block above the first '## ' section (newest heartbeat on top), or None if
    today's heartbeat is already present (idempotent)."""
    def transform(content: str) -> Optional[str]:
        if f"## 🏁 TP daily -- {today}" in content or f"## 🏁 TP daily — {today}" in content:
            return None  # already written today
        s = re.sub(r"\*\*Last updated:\*\*.*",
                   f"**Last updated:** {today} (TP daily heartbeat)", content, count=1)
        i = s.find("\n## ", s.find("**Last updated:**"))
        return s[:i] + block + s[i:] if i > 0 else s + block
    return transform


def run_tp_daily(*, token: Optional[str] = None, commit: bool = True,
                 now: Optional[datetime] = None) -> dict:
    """Build the heartbeat from LIVE Instantly data and commit it to
    team_principal_state.md (idempotent). Returns a receipt."""
    token = token or os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    today = (now or datetime.now(_CT)).strftime("%Y-%m-%d")
    rows, blind, needs_review, interested_emails = outbound_truth()
    block = build_block(rows, blind, today, needs_review, interested_emails)
    total_interested = sum(r[5] for r in rows)
    total_sent = sum(r[3] for r in rows)
    receipt = {"ok": True, "date": today, "brands_read": len(rows), "blind": blind,
               "interested": total_interested, "sent": total_sent,
               "needs_review": needs_review, "needs_review_count": len(needs_review),
               "committed": False}
    if not commit:
        receipt["preview"] = block
        return receipt
    res = update_state(_STATE_PATH, _insert_transform(block, today),
                       f"TP daily {today}: sent={total_sent}, interested={total_interested}, "
                       f"needs_review={len(needs_review)}",
                       token)
    receipt["committed"] = res.get("committed", False)
    receipt["skipped_idempotent"] = res.get("skipped", False)
    logger.info("[tp-daily] %s committed=%s skipped=%s interested=%s needs_review=%s",
                today, receipt["committed"], receipt.get("skipped_idempotent"),
                total_interested, len(needs_review))
    return receipt
