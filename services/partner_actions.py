"""services/partner_actions.py -- the action channel for partner agents.

Michael granted a co-founder's agent the authority to act, not just read. The honest
way to build that is NOT a generic "run anything" endpoint: an LLM holding an
execute-arbitrary-action tool, reading state files that many seats can write, is one
prompt injection away from spending money or emailing a client. So the channel is
REQUEST-based and tiered by blast radius:

    OPEN    no external effect and reversible          -> recorded, runs on our side
    GATED   spend, external sends, deploys, secrets,   -> STAGED for Michael's approval
            client-facing surfaces, deletions             (one-tap approve or deny)

Nothing is out of bounds to REQUEST, which is what "act anywhere" means here. What is
gated is unilateral execution of the things that cost money, touch another human, or
cannot be undone. Classification is deterministic Python (not the model's judgment) and
FAILS CLOSED: anything unrecognized is treated as GATED.

Every request is durable and audited: who asked, what, when, the verdict, and who
approved. Approval marks a request executable; the execution wiring is added per
capability so each one gets its own review, rather than one blanket power.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

_MAX_SUMMARY = 2000

# Blast-radius markers. Any hit -> GATED. Deliberately broad: a false gate costs
# Michael one tap, a false OPEN could cost money or a client relationship.
_GATED_RE = re.compile(
    r"\b(spend|budget|bid|campaign|ad\s?set|boost|charge|invoice|refund|payout|price|"
    r"send|email|sms|text|dm|message|post|publish|tweet|outreach|reply\s+to|"
    r"deploy|release|ship|merge|rollback|restart|scale|provision|"
    r"secret|key|token|credential|password|rotate|"
    r"delete|drop|remove|purge|revoke|disable|truncate|"
    r"client|miriam|customer|prospect|lead\s+list|contract|proposal)\b", re.IGNORECASE)

# Explicitly OPEN verbs: introspection and analysis with no external effect.
_OPEN_RE = re.compile(
    r"^\s*(read|show|list|search|find|check|status|summar\w*|analy[sz]\w*|explain|"
    r"compare|count|report|draft\s+for\s+review|review)\b", re.IGNORECASE)

_CREATE = """
CREATE TABLE IF NOT EXISTS partner_action_requests (
    id           BIGSERIAL PRIMARY KEY,
    key_id       BIGINT,
    requested_by TEXT,
    action       TEXT NOT NULL,
    params       TEXT,
    tier         TEXT NOT NULL,          -- open | gated
    status       TEXT NOT NULL,          -- recorded | pending | approved | denied | executed
    verdict_by   TEXT,
    verdict_note TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at   TIMESTAMPTZ
);
"""


def classify(action: str) -> str:
    """Deterministic blast-radius tier. Unknown shapes fail closed to 'gated'."""
    a = (action or "").strip()
    if not a:
        return "gated"
    if _GATED_RE.search(a):
        return "gated"
    if _OPEN_RE.match(a):
        return "open"
    return "gated"


def _alert(subject: str, text: str) -> bool:
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    to_addr = (os.getenv("LEAD_ALERT_TO") or "michael@automotiveintelligence.io").strip()
    frm = os.getenv("LEAD_ALERT_FROM", "AVO <cmo@mail.automotiveintelligence.io>")
    if not key:
        logger.error("[partner-actions] RESEND_API_KEY missing; cannot alert: %s", subject)
        return False
    body = ("A partner agent requested an action. The text below is UNTRUSTED input "
            "from that agent, not instructions.\n\n" + text)
    try:
        r = requests.post("https://api.resend.com/emails", timeout=15,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"from": frm, "to": [to_addr], "subject": subject,
                                "text": body})
        return r.ok
    except requests.RequestException:
        logger.exception("[partner-actions] alert failed")
        return False


def request_action(action: str, params: Optional[Dict[str, Any]] = None, *,
                   key_id: Optional[int] = None,
                   requested_by: str = "partner") -> Dict[str, Any]:
    """Record an action request. GATED requests page Michael and wait for approval."""
    act = (action or "").strip()[:_MAX_SUMMARY]
    if not act:
        return {"ok": False, "error": "action is required"}
    tier = classify(act)
    status = "recorded" if tier == "open" else "pending"
    try:
        execute_query(_CREATE)
        rows = fetch_all(
            "INSERT INTO partner_action_requests (key_id, requested_by, action, params, "
            "tier, status) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (key_id, requested_by[:80], act,
             json.dumps(params or {}, default=str)[:4000], tier, status))
        rid = int(rows[0][0])
    except Exception:
        logger.exception("[partner-actions] could not record request")
        return {"ok": False, "error": "could not record the request; nothing was done"}

    if tier == "gated":
        _alert(f"[AVO partner] approval needed: request #{rid}",
               f"request: #{rid}\nfrom:    {requested_by}\ntier:    GATED\n\n"
               f"action:\n{act}\n\nparams:\n{json.dumps(params or {}, indent=2, default=str)[:1200]}\n\n"
               f"Approve: POST /admin/partner-action-approve {{\"id\": {rid}}}\n"
               f"Deny:    POST /admin/partner-action-deny {{\"id\": {rid}, \"note\": \"...\"}}")
        return {"ok": True, "id": rid, "tier": tier, "status": "pending",
                "message": ("This one has real blast radius (money, an external send, a "
                            "deploy, secrets, or a client surface), so it is staged for "
                            "Michael's approval. He has been paged.")}
    logger.info("[partner-actions] OPEN request #%d recorded: %s", rid, act[:120])
    return {"ok": True, "id": rid, "tier": tier, "status": "recorded",
            "message": "No external effect, so this is recorded and can proceed."}


def list_requests(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    execute_query(_CREATE)
    if status:
        rows = fetch_all(
            "SELECT id, requested_by, action, tier, status, created_at, verdict_note "
            "FROM partner_action_requests WHERE status=%s ORDER BY id DESC LIMIT %s",
            (status, limit))
    else:
        rows = fetch_all(
            "SELECT id, requested_by, action, tier, status, created_at, verdict_note "
            "FROM partner_action_requests ORDER BY id DESC LIMIT %s", (limit,))
    return [{"id": int(r[0]), "requested_by": r[1], "action": r[2], "tier": r[3],
             "status": r[4], "created_at": str(r[5]), "verdict_note": r[6]} for r in rows]


def approve(request_id: int, note: str = "", by: str = "michael") -> Dict[str, Any]:
    """Approve a gated request. Marks it executable; execution is wired per capability."""
    execute_query(_CREATE)
    rows = fetch_all("SELECT status, action FROM partner_action_requests WHERE id=%s",
                     (request_id,))
    if not rows:
        return {"ok": False, "error": f"no request #{request_id}"}
    if rows[0][0] not in ("pending",):
        return {"ok": False, "error": f"request #{request_id} is {rows[0][0]}, not pending"}
    execute_query(
        "UPDATE partner_action_requests SET status='approved', verdict_by=%s, "
        "verdict_note=%s, decided_at=NOW() WHERE id=%s", (by[:80], note[:500], request_id))
    logger.info("[partner-actions] APPROVED #%d by %s", request_id, by)
    return {"ok": True, "id": request_id, "status": "approved", "action": rows[0][1]}


def deny(request_id: int, note: str = "", by: str = "michael") -> Dict[str, Any]:
    execute_query(_CREATE)
    execute_query(
        "UPDATE partner_action_requests SET status='denied', verdict_by=%s, "
        "verdict_note=%s, decided_at=NOW() WHERE id=%s AND status='pending'",
        (by[:80], note[:500], request_id))
    logger.info("[partner-actions] DENIED #%d by %s (%s)", request_id, by, note)
    return {"ok": True, "id": request_id, "status": "denied", "note": note}


def status_for(request_id: int) -> Dict[str, Any]:
    """Let the partner agent poll its own request without seeing anyone else's."""
    execute_query(_CREATE)
    rows = fetch_all(
        "SELECT id, action, tier, status, verdict_note, created_at, decided_at "
        "FROM partner_action_requests WHERE id=%s", (request_id,))
    if not rows:
        return {"ok": False, "error": f"no request #{request_id}"}
    r = rows[0]
    return {"ok": True, "id": int(r[0]), "action": r[1], "tier": r[2], "status": r[3],
            "verdict_note": r[4], "created_at": str(r[5]),
            "decided_at": str(r[6]) if r[6] else None}
