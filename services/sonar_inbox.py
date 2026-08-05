"""services/sonar_inbox.py -- autonomous Sonar engagement-inbox monitor.

Hourly Railway cron, NO human prompt. COST-SCALED BY DESIGN: a lightweight poll (no
LLM) pulls NEW comments + mentions + ad-post comments across our owned accounts via the
Zernio REST inbox API, dedups against a durable Postgres ledger, and EXITS if nothing is
new (near-zero cost). ONLY on a new item does it invoke Sonar's classifier (the LLM
step) to triage, draft a gate-checked reply, route leads to CRO, and escalate
complaints/ambiguous to Michael. So spend scales with real engagement, not the clock.

DMs are OUT OF SCOPE: Zernio's DM API returns PLATFORM_NOT_SUPPORTED for IG/FB/LinkedIn
(where our DM traffic is). Those route through Meta / the Concierge -- a separate track.

Sonar owns services/sonar_classifier.classify(item). Until it exists, every new item
FAILS CLOSED to a human escalation (surfaced to Michael, never auto-replied). Owned
accounts only; Book'd/Ryan + Paper & Purpose are excluded. Every external effect is a
module seam so tests never touch Postgres, Zernio, or email.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Set

import requests

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_BASE = "https://zernio.com/api/v1"
_EXCLUDE = ("bookd", "book'd", "ryan", "velazquez", "paper", "purpose")  # Book'd/Ryan + P&P are not Sonar's
_SOURCES = (
    ("comment",    "/inbox/comments", {"limit": 100}),
    ("ad_comment", "/inbox/comments", {"limit": 100, "filter": "metaads"}),
    ("mention",    "/inbox/mentions", {"limit": 100}),
)

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS sonar_handled (
    item_id    TEXT PRIMARY KEY,
    kind       TEXT,
    account    TEXT,
    platform   TEXT,
    handled_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _pull(endpoint: str, params: dict) -> List[dict]:
    key = (os.getenv("ZERNIO_API_KEY") or "").strip()
    if not key:
        logger.error("[sonar] ZERNIO_API_KEY missing")
        return []
    try:
        r = requests.get(f"{_BASE}{endpoint}", headers={"Authorization": f"Bearer {key}"},
                         params=params, timeout=25)
    except requests.RequestException as e:
        logger.warning("[sonar] %s pull failed: %s", endpoint, e)
        return []
    if not r.ok:
        logger.warning("[sonar] %s %s: %s", endpoint, r.status_code, r.text[:120])
        return []
    return (r.json() or {}).get("data", []) or []


def _excluded(account: str) -> bool:
    a = (account or "").lower()
    return any(x in a for x in _EXCLUDE)


class _AllHandled(frozenset):
    """Sentinel: every id reads as already-handled, so a sweep processes nothing."""
    def __contains__(self, _):
        return True


def handled_ids() -> Set[str]:
    """Durable dedup ledger (Postgres, NOT a JSONL: Railway's FS is ephemeral, so a
    file ledger would be wiped on redeploy and the monitor would re-process/re-reply).
    Fail CLOSED: if the ledger cannot be read we cannot prove an item is new, so return
    the all-handled sentinel (do nothing this sweep) rather than risk a re-reply."""
    try:
        execute_query(_LEDGER_DDL)
        return {row[0] for row in fetch_all("SELECT item_id FROM sonar_handled")}
    except Exception as e:
        logger.error("[sonar] ledger read failed -- skipping this sweep: %s", e)
        return _AllHandled()


def _mark_handled(item: dict) -> None:
    execute_query(
        "INSERT INTO sonar_handled (item_id, kind, account, platform) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (item_id) DO NOTHING",
        (item["id"], item["kind"], item.get("account"), item.get("platform")))


def pull_new(handled: Set[str]) -> List[Dict[str, Any]]:
    """NEW items across owned accounts (excludes Book'd/P&P + already-handled). No LLM."""
    out, seen = [], set()
    for kind, ep, params in _SOURCES:
        for it in _pull(ep, params):
            iid = it.get("id")
            if not iid or iid in handled or iid in seen:
                continue
            if _excluded(it.get("accountUsername")):
                continue
            seen.add(iid)
            out.append({"id": iid, "kind": kind, "account": it.get("accountUsername"),
                        "platform": it.get("platform"),
                        "text": (it.get("content") or it.get("text") or "")[:1000],
                        "url": it.get("permalink") or it.get("url") or "",
                        "ts": it.get("createdTime") or it.get("updatedTime") or ""})
    return out


def _classify(item: dict) -> dict:
    """Sonar-owned classifier. Absent or raising -> FAIL CLOSED to human escalation."""
    try:
        from services.sonar_classifier import classify  # Sonar writes this
    except ImportError:
        return {"tier": "escalate", "reason": "classifier not yet installed (fail-closed)"}
    try:
        return classify(item) or {"tier": "escalate", "reason": "empty classification"}
    except Exception as e:
        logger.exception("[sonar] classifier raised; escalating")
        return {"tier": "escalate", "reason": f"classifier error: {e}"}


def _escalate_email(items: List[dict]) -> bool:
    """Ping Michael ONLY on escalations, via the proven Resend rail (verified domain)."""
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    if not key or not items:
        return False
    rows = "".join(
        f"<li><b>{i['kind']}</b> @{i.get('account')} ({i.get('platform')}): "
        f"{(i.get('text') or '')[:220]} &mdash; <a href='{i.get('url')}'>open</a></li>"
        for i in items)
    try:
        r = requests.post(
            "https://api.resend.com/emails", timeout=15,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>"),
                  "to": [os.getenv("SONAR_ESCALATE_TO", "michael@automotiveintelligence.io")],
                  "subject": f"[Sonar] {len(items)} engagement item(s) need a human",
                  "html": f"<p>Sonar could not auto-handle these engagement items:</p><ul>{rows}</ul>"})
        return r.ok
    except requests.RequestException:
        logger.exception("[sonar] escalation email failed")
        return False


def run_sweep(*, commit: bool = True) -> Dict[str, Any]:
    """One cost-scaled sweep. Pull -> (exit if none) -> classify each -> act -> mark
    handled -> escalate leftovers to Michael. Returns a receipt for sonar_state logging."""
    handled = handled_ids()
    new_items = pull_new(handled)
    if not new_items:
        return {"ok": True, "new": 0, "note": "nothing new (cheap exit, no LLM)"}

    # SEED on first run: an empty ledger means the whole historical backlog reads as
    # "new". Do NOT classify/escalate a backlog blast -- mark it handled silently, so
    # only genuinely-new items (arriving after this) ever reach a human.
    if commit and not handled:
        for it in new_items:
            _mark_handled(it)
        logger.info("[sonar] seeded %d backlog items (no escalation)", len(new_items))
        return {"ok": True, "seeded": len(new_items),
                "note": "seeded existing backlog; escalations begin next sweep"}

    escalations, leads, autos = [], 0, 0
    for it in new_items:
        res = _classify(it)
        tier = (res or {}).get("tier", "escalate")
        it["tier"] = tier
        if tier == "lead":
            leads += 1                      # Sonar's classifier owns the CRO route + reply draft
        elif tier == "auto":
            autos += 1                      # Sonar's classifier owns the gated auto-reply send
        else:
            escalations.append(it)          # escalate / complaint / ambiguous -> a human
        if commit:
            _mark_handled(it)               # NEVER re-process, whatever the outcome

    escalated = _escalate_email(escalations) if (escalations and commit) else False
    receipt = {"ok": True, "new": len(new_items), "leads": leads, "auto": autos,
               "escalations": len(escalations), "escalation_emailed": escalated,
               "accounts": sorted({i.get("account") for i in new_items if i.get("account")})}
    logger.info("[sonar] sweep receipt: %s", receipt)
    return receipt
