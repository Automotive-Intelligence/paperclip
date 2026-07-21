"""services/growth_monitor_engine.py -- outbound health monitor, Railway port.

Cloud port of avo-telemetry/scripts/growth_outbound_monitor.py (launchd
com.avo.growth-monitor, 18:00 CT). The alarm logic (bounce/reply/warmup via
Instantly) is vendored VERBATIM; only the I/O moves to a GitHub-API append so the
entry lands in growth_analytics_state.md with the Mac closed. Idempotent per day.

NOTE: despite the WS6 brief calling this "GA4/instrumentation health", the actual
script is INSTANTLY-only (no GA4, no pull_ga4) -- so no GA4 creds are needed in the
cloud. Verified against the code 2026-07-20.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from services.avo_state_commit import update_state
from services.tp_daily_engine import IB, classify

logger = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")  # date by Central time (matches the laptop + the cron)

_STATE_PATH = "growth_analytics_state.md"
BRANDS = {"AIPG": "INSTANTLY_API_KEY_AIPG", "Book'd": "INSTANTLY_API_KEY_BOOKD",
          "P&P": "INSTANTLY_API_KEY_PAPERANDPURPOSE", "AvI": "INSTANTLY_API_KEY_AVI"}
BOUNCE_ALARM = 0.03   # >3% bounce = list quality problem, pause and re-verify
WARMUP_FLOOR = 90     # a warmed mailbox dropping below this is a reputation signal


def build_block(today: str) -> str:
    """Build the outbound-monitor block. (Alarm logic vendored verbatim.)"""
    out, alarms = [f"\n## 📈 Outbound monitor -- {today}\n"], []
    for brand, env in BRANDS.items():
        key = os.getenv(env, "").strip()
        if not key:
            continue
        H = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        r = requests.get(f"{IB}/campaigns", headers=H, params={"limit": 50}, timeout=25)
        if r.status_code == 401:
            out.append(f"- **{brand}: API key INVALID (401).** Blind, not zero.")
            alarms.append(f"{brand} key is dead -- we cannot see this brand at all")
            continue
        if not r.ok:
            continue
        a = requests.get(f"{IB}/accounts", headers=H, params={"limit": 50}, timeout=25)
        for acct in (a.json().get("items", []) if a.ok else []):
            score = acct.get("stat_warmup_score")
            if score is not None and score < WARMUP_FLOOR:
                alarms.append(f"{brand}: {acct['email']} warmup dropped to {score} "
                              f"(floor {WARMUP_FLOOR}) -- reputation slipping")
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
            replies = sum(l.get("email_reply_count") or 0 for l in leads)
            bounced = sum(1 for l in leads if l.get("status") == -1 or l.get("esp_code") in (550, 551, 553))
            done = sum(1 for l in leads if l.get("status") == 3)
            rate = (bounced / sent) if sent else 0.0
            out.append(f"- **{brand} / {c['name'][:38]}**: {sent} sent, **{replies} replies**, "
                       f"{bounced} bounced ({rate:.1%}), {done} finished the sequence")
            if rate > BOUNCE_ALARM and sent >= 20:
                alarms.append(f"{brand}: bounce rate {rate:.1%} exceeds {BOUNCE_ALARM:.0%} "
                              f"on {sent} sends -- PAUSE and re-verify the list")
            if replies:
                interested = classify(H, [l for l in leads if (l.get("email_reply_count") or 0) > 0])
                if interested:
                    alarms.append(f"{brand}: {interested} INTERESTED reply(ies) -- the closest "
                                  f"thing to money we have. Work it today.")
                elif sent >= 300:
                    alarms.append(f"{brand}: {sent} sent, {replies} replies, ALL negative or "
                                  f"auto -- zero interest. Offer/list problem, not volume.")
    if alarms:
        out.append("\n**🚨 ACT ON THESE:**")
        out += [f"- {a}" for a in alarms]
    else:
        out.append("\nNo alarms. Sending is healthy and nobody has replied yet.")
    return "\n".join(out) + "\n"


def _append_transform(block: str, today: str):
    def transform(content: str) -> Optional[str]:
        if f"## 📈 Outbound monitor -- {today}" in content or f"## 📈 Outbound monitor — {today}" in content:
            return None  # already written today (idempotent)
        return content + block
    return transform


def run_growth_monitor(*, token: Optional[str] = None, commit: bool = True,
                       now: Optional[datetime] = None) -> dict:
    """Build the outbound-health block from LIVE Instantly data and append it to
    growth_analytics_state.md (idempotent). Returns a receipt."""
    token = token or os.getenv("SLIPSTREAM_GH_TOKEN", "").strip()
    today = (now or datetime.now(_CT)).strftime("%Y-%m-%d")
    block = build_block(today)
    alarm_count = block.count("\n- ") if "ACT ON THESE" in block else 0
    receipt = {"ok": True, "date": today, "has_alarms": "ACT ON THESE" in block, "committed": False}
    if not commit:
        receipt["preview"] = block
        return receipt
    res = update_state(_STATE_PATH, _append_transform(block, today),
                       f"growth: outbound monitor {today}", token)
    receipt["committed"] = res.get("committed", False)
    receipt["skipped_idempotent"] = res.get("skipped", False)
    logger.info("[growth-monitor] %s committed=%s skipped=%s alarms=%s",
                today, receipt["committed"], receipt.get("skipped_idempotent"), receipt["has_alarms"])
    return receipt
