"""services/prospect_feed.py -- the names behind "19 prospects created today".

The Pit Wall reported a COUNT and stopped there, which is the least useful form of that
fact. A count cannot be checked. Michael asked to click through to the actual prospects
and on into the CRM record, and he is right to: these rows are created by autonomous
sales agents, and at least one of them (Tyler) has a documented history of fabricated
enrichment. A number you cannot open is a number you have to trust.

`crm_push_logs` records agent, CRM provider, prospect company name, status, timestamp.
It does NOT record the CRM record id, so we can name every prospect and hand you a
search link into the right CRM, but we cannot deep-link the exact record yet. That is a
capture gap, not a rendering gap -- stated plainly here rather than papered over with a
link that might land on the wrong record.

`duplicate_skipped` rows are surfaced too. A run that skips 15 duplicates and creates 3
is a different story from one that creates 18, and only one of those is prospecting.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Where a human goes to find the record. Search links, not record links, because we do
# not store the record id (see module docstring).
_CRM_SEARCH = {
    "ghl": "https://app.gohighlevel.com/",
    "twenty": "https://crm.automotiveintelligence.io/",
}

_AGENT_BRAND = {
    "tyler": "The AI Phone Guy",
    "marcus": "Worship Digital",
    "ryan_data": "Automotive Intelligence",
}


def _crm_link(provider: str, name: str) -> str:
    base = _CRM_SEARCH.get((provider or "").lower(), "")
    if not base:
        return ""
    # A search query is honest about what it is: it puts you in the right CRM looking
    # for the right company, without pretending to know the record id.
    return f"{base}?search={quote(name)}" if name else base


def recent(days: int = 1, limit: int = 200) -> Dict[str, Any]:
    """Prospects touched in the window, newest first, with per-agent totals."""
    try:
        from services.database import fetch_all
        rows = fetch_all(
            "SELECT agent_name, crm_provider, business_name, status, created_at "
            "FROM crm_push_logs WHERE created_at >= CURRENT_DATE - make_interval(days => %s) "
            "ORDER BY created_at DESC LIMIT %s", (max(0, days - 1), limit))
    except Exception as e:
        logger.exception("[prospects] read failed")
        return {"ok": False, "error": str(e)[:160], "prospects": [], "totals": {}}

    prospects: List[Dict[str, Any]] = []
    by_agent: Dict[str, Dict[str, int]] = {}
    for agent, provider, name, status, when in rows or []:
        agent = str(agent or "")
        status = str(status or "")
        by_agent.setdefault(agent, {"created": 0, "duplicate_skipped": 0, "failed": 0})
        by_agent[agent][status] = by_agent[agent].get(status, 0) + 1
        prospects.append({
            "agent": agent,
            "brand": _AGENT_BRAND.get(agent, ""),
            "crm": str(provider or ""),
            "name": str(name or ""),
            "status": status,
            "when": str(when),
            "crm_link": _crm_link(str(provider or ""), str(name or "")),
        })

    created = sum(v.get("created", 0) for v in by_agent.values())
    dupes = sum(v.get("duplicate_skipped", 0) for v in by_agent.values())
    failed = sum(v.get("failed", 0) for v in by_agent.values())
    return {
        "ok": True,
        "days": days,
        "totals": {"created": created, "duplicate_skipped": dupes, "failed": failed,
                   "rows": len(prospects)},
        "by_agent": by_agent,
        "prospects": prospects,
        "note": ("Links are CRM searches, not record links: crm_push_logs does not store "
                 "the record id. Capture it on the push path to enable deep links."),
    }
