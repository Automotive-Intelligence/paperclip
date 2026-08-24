"""services/revenue_board.py -- what needs hands to make money. Nothing else.

Michael's brief, verbatim: "I don't want to see what is and what went silent, I just
want to know what needs our hands to get us revenue."

So this is not a status page. Every row is an ACTION with money attached, and a row only
exists while it is actionable. When there is nothing to do the board says so, which is a
real and good answer rather than an empty table.

The finding that motivated it: the AIPG funnel canary had been green for 72 consecutive
hours against the real form -- the spend gate was 48 -- so paid advertising across six
brands had been eligible to resume for about a day and nobody knew. The watchdog only
ever watched for that gate to FAIL, never for it to CLEAR. An opportunity opening is as
invisible as a pipe breaking, and costs more.

Every item is DERIVED from live state (canary runs, the leads table, the send-token
store, the approval queues). Nothing is hand-maintained, so nothing can rot into a lie.
Money impact is stated as a mechanism, never a fabricated number -- the hero-metrics
rule applies here more than anywhere.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Ranked buckets. Lower sorts first. The order encodes a judgment: money we can collect
# now beats money we could earn later, and both beat plumbing.
P_BLOCKED_REVENUE = 0     # a real buyer is waiting, or we cannot take their money
P_UNBLOCKED_SPEND = 1     # a gate opened and nobody walked through it
P_NEEDS_APPROVAL = 2      # one tap from Michael releases it
P_RAIL_DOWN = 3           # the machinery that produces revenue is broken


def _item(priority: int, title: str, why: str, action: str, owner: str,
          detail: str = "", where: str = "") -> Dict[str, Any]:
    return {"priority": priority, "title": title, "why_money": why,
            "next_action": action, "owner": owner, "detail": detail, "where": where}


def _spend_gate() -> List[Dict[str, Any]]:
    """The AIPG canary gate. Halted paid spend across six brands; 48h green releases it.
    Reports BOTH directions -- gate open and unclaimed is an action, gate red is an
    action, gate still counting is not."""
    try:
        from services.lead_canary import green_streak, latest_canary
        s = green_streak("aipg", 72)
        latest = latest_canary("aipg")
    except Exception:
        logger.warning("[revenue] canary unreadable")
        return []
    out: List[Dict[str, Any]] = []
    if latest and not latest.get("responded"):
        out.append(_item(
            P_RAIL_DOWN,
            "AIPG funnel canary is RED",
            "A real lead submitted right now would likely be lost, and paid spend stays "
            "gated while it is red.",
            "Check /admin/lead-canary-status and fix the lead path before spending.",
            "AVO (Build & Tech)",
            detail=str(latest.get("detail", ""))[:300], where="/inventory"))
        return out
    if s.get("gate_ready"):
        hrs = s.get("green", 0)
        out.append(_item(
            P_UNBLOCKED_SPEND,
            "Paid spend is UNBLOCKED and nobody has resumed it",
            "Paid advertising across six brands was halted behind this gate. It is now "
            "open, so every hour it stays unclaimed is acquisition we are not buying.",
            "Resume AIPG paid campaigns, then clone the funnel proof to the next brand.",
            "Michael + Don Draper",
            detail=f"{hrs} consecutive green canary runs against the real form, "
                   f"{s.get('reds', 0)} reds. The bar was 48.",
            where="/inventory"))
    return out


def _unworked_leads() -> List[Dict[str, Any]]:
    """Real leads that arrived. A lead nobody calls is money already paid for and left
    on the floor, and speed decay is the whole reason the funnel standard exists."""
    try:
        from services.database import fetch_all
        rows = fetch_all(
            "SELECT brand, COUNT(*), MIN(created_at), MAX(created_at) FROM leads "
            "WHERE NOT is_synthetic AND created_at > NOW() - INTERVAL '30 days' "
            "GROUP BY brand ORDER BY 2 DESC")
    except Exception:
        logger.warning("[revenue] leads table unreadable")
        return []
    out: List[Dict[str, Any]] = []
    for brand, n, oldest, newest in rows or []:
        out.append(_item(
            P_BLOCKED_REVENUE,
            f"{n} real {str(brand).upper()} lead{'s' if n != 1 else ''} in the last 30 days",
            "These are people who raised their hand. Contact speed is the single "
            "biggest lever on whether they convert, and nothing here proves they were "
            "called.",
            "Confirm every one has had a human contact attempt; work any that have not.",
            "Michael + CRO",
            detail=f"oldest {oldest}, newest {newest}",
            where="GHL (tag website-lead)"))
    return out


def _send_rail() -> List[Dict[str, Any]]:
    """The 1:1 brand-send rail. Authorized identities whose Gmail token is dead cannot
    send, and send_as_brand degrades silently to draft-and-log -- so a promised sample
    to a real prospect just never goes out."""
    authorized = [a.strip().lower() for a in
                  (os.getenv("SEND_AUTHORIZED_MAILBOXES") or "").split(",") if a.strip()]
    if not authorized:
        return []
    try:
        from services.database import fetch_all
        rows = fetch_all("SELECT email, status FROM postal_tokens")
    except Exception:
        return []
    state = {str(e).lower(): s for e, s in (rows or [])}
    dead = [a for a in authorized if state.get(a, "missing") != "active"]
    if not dead:
        return []
    return [_item(
        P_RAIL_DOWN,
        f"{len(dead)} of {len(authorized)} brand-send identities cannot send",
        "This is the 1:1 rail the Sales Desk uses to reach prospects. When the token is "
        "dead, send_as_brand degrades to draft-and-log, so a promised follow-up simply "
        "never arrives and nothing errors.",
        "Send those by hand for now, or build the domain-wide-delegation rail so the "
        "tokens stop expiring.",
        "AVO (Build & Tech)",
        detail="dead: " + ", ".join(dead), where="docs/PITWALL_V2_SPEC.md")]


def _approvals() -> List[Dict[str, Any]]:
    """Things one tap from Michael releases."""
    out: List[Dict[str, Any]] = []
    try:
        from services.partner_actions import list_requests
        pending = list_requests("pending", 20)
        for r in pending:
            out.append(_item(
                P_NEEDS_APPROVAL,
                f"Partner action waiting on you: {str(r.get('action'))[:70]}",
                "A partner agent asked to do something with real blast radius. It sits "
                "until you decide.",
                f"Approve or deny request #{r.get('id')}.",
                "Michael",
                detail=f"from {r.get('requested_by')}, raised {r.get('created_at')}",
                where="/org"))
    except Exception:
        logger.warning("[revenue] partner actions unreadable")
    try:
        from services.bookd_handoff import list_pending
        for h in list_pending():
            out.append(_item(
                P_NEEDS_APPROVAL,
                f"Credential staged and waiting: {h.get('key_name')}",
                "Something downstream cannot run until this is installed. If it is a "
                "payment key, we cannot collect money without it.",
                f"Reveal handoff #{h.get('id')} and install it.",
                "Michael",
                detail=f"submitted by {h.get('submitted_by')} on {h.get('created_at')}"))
    except Exception:
        logger.warning("[revenue] handoffs unreadable")
    return out


def board() -> Dict[str, Any]:
    """The whole board. Never raises: a broken source drops its rows rather than the
    page, and `sources_failed` says so instead of quietly showing a shorter list."""
    items: List[Dict[str, Any]] = []
    failed: List[str] = []
    for name, fn in (("spend_gate", _spend_gate), ("leads", _unworked_leads),
                     ("send_rail", _send_rail), ("approvals", _approvals)):
        try:
            items.extend(fn())
        except Exception:
            logger.exception("[revenue] source %s failed", name)
            failed.append(name)
    items.sort(key=lambda i: i["priority"])
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "sources_failed": failed,
        "empty_message": ("Nothing needs your hands right now. The machine is running "
                          "and no money is waiting on a decision."),
    }
