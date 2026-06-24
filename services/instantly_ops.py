"""services/instantly_ops.py — Instantly v2 ops module.

F1 race-pace telemetry foundation for the Instantly outbound stack.

This module is the source of truth for any Instantly read. Callers:
  - Future HTTP endpoints under /admin/instantly/* (status, senders, replies)
  - Future CLI binary (paperclip-cli instantly ...)
  - Morning briefing (rivers/shared/morning_briefing.py) — will refactor to use
    this once the read side is proven; today it duplicates a subset.

Design rules:
  - Stateless. No DB writes. No external state changes.
  - Every call to Instantly goes through a wrapper here, not raw requests at
    callsites. One place to fix when the Instantly API changes.
  - Per-campaign credentials are passed in by the caller — the registry
    KNOWN_CAMPAIGNS resolves env-var names to live secrets, but the wrappers
    accept api_key + campaign_id directly so unit-testing stays cheap.

Validated against production Instantly 2026-05-06; see PR #26 history for the
endpoint discovery that informed which routes are real (POST /campaigns/analytics
returns 404; the GETs work).

Smoke test:
    cd ~/paperclip && railway run --service paperclip python3 -m services.instantly_ops
"""

import os
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────────────

INSTANTLY_BASE = "https://api.instantly.ai/api/v2"
DEFAULT_TIMEOUT = 20

CAMPAIGN_STATUS_LABEL = {
    0: "Draft",
    1: "Active",
    2: "Paused",
    3: "Completed",
    4: "Running Subseq",
}

LEAD_STATUS_NAME = {
    1: "Active",
    2: "Paused",
    3: "Completed",
    -1: "Bounced",
    -2: "Unsubscribed",
    -3: "Skipped",
}

# Registry: human label -> (api_key_env_var, campaign_id_env_var)
# Tyler runs under a per-campaign key; Ryan_data runs under the workspace key.
KNOWN_CAMPAIGNS: List[Tuple[str, str, str]] = [
    ("Tyler — AI Phone Guy", "INSTANTLY_API_KEY_TYLER", "INSTANTLY_CAMPAIGN_TYLER"),
    ("Ryan Data — Automotive Intelligence", "INSTANTLY_API_KEY", "INSTANTLY_CAMPAIGN_RYAN_DATA"),
]

# Health thresholds — tune in one place
DELIVERABILITY_DEAD_MIN_SENT = 50      # need this much volume to call it
DELIVERABILITY_DEAD_OPEN_RATE = 0.0    # zero opens with the volume above
BOUNCE_RATE_WARN = 5.0                 # %
BOUNCE_RATE_CRITICAL = 10.0            # %
WARMUP_SCORE_HEALTHY = 90              # Instantly's 0-100 warmup_score
LEAD_POOL_RUNWAY_CRITICAL_DAYS = 3     # fewer active leads / daily send pace


# ── Low-level wrappers ──────────────────────────────────────────────────────

def _hdr(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def get_campaign(api_key: str, campaign_id: str) -> Dict[str, Any]:
    """GET /campaigns/{id} — config: name, status, senders, sequence, schedule."""
    if not api_key or not campaign_id:
        return {"error": "not_configured"}
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/{campaign_id}",
            headers=_hdr(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json() or {}
        return {"error": "http", "status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        logger.warning(f"[instantly_ops] get_campaign failed: {e}")
        return {"error": "exception", "exception": str(e)}


def get_campaign_analytics_lifetime(api_key: str, campaign_id: str) -> Dict[str, Any]:
    """GET /campaigns/analytics?campaign_id=... — lifetime totals.

    Returns the first element of the response list (Instantly wraps a single
    campaign's result in a 1-element list).
    """
    if not api_key or not campaign_id:
        return {"error": "not_configured"}
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/analytics",
            headers=_hdr(api_key),
            params={"campaign_id": campaign_id},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code != 200:
            return {"error": "http", "status": r.status_code, "body": r.text[:300]}
        data = r.json()
        if isinstance(data, list) and data:
            return data[0]
        return data or {}
    except Exception as e:
        logger.warning(f"[instantly_ops] lifetime analytics failed: {e}")
        return {"error": "exception", "exception": str(e)}


def get_campaign_analytics_daily(
    api_key: str, campaign_id: str, days: int = 14
) -> List[Dict[str, Any]]:
    """GET /campaigns/analytics/daily — per-day rows, most-recent `days` entries."""
    if not api_key or not campaign_id:
        return []
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/analytics/daily",
            headers=_hdr(api_key),
            params={"campaign_id": campaign_id},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        rows = r.json() or []
        return rows[-days:] if isinstance(rows, list) else []
    except Exception as e:
        logger.warning(f"[instantly_ops] daily analytics failed: {e}")
        return []


def get_campaign_analytics_steps(api_key: str, campaign_id: str) -> List[Dict[str, Any]]:
    """GET /campaigns/analytics/steps — per-sequence-step rows."""
    if not api_key or not campaign_id:
        return []
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/campaigns/analytics/steps",
            headers=_hdr(api_key),
            params={"campaign_id": campaign_id},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json() or []
        return []
    except Exception as e:
        logger.warning(f"[instantly_ops] steps analytics failed: {e}")
        return []


def list_leads_page(
    api_key: str, campaign_id: str, limit: int = 100, cursor: Optional[str] = None
) -> Dict[str, Any]:
    """POST /leads/list — single page. Returns full response dict (items + next_starting_after)."""
    if not api_key or not campaign_id:
        return {"items": []}
    body = {"campaign": campaign_id, "limit": limit}
    if cursor:
        body["starting_after"] = cursor
    try:
        r = requests.post(
            f"{INSTANTLY_BASE}/leads/list",
            headers=_hdr(api_key),
            json=body,
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json() or {"items": []}
        return {"items": [], "error": "http", "status": r.status_code}
    except Exception as e:
        logger.warning(f"[instantly_ops] list_leads_page failed: {e}")
        return {"items": [], "error": "exception"}


def list_leads_all(
    api_key: str, campaign_id: str, max_pages: int = 20
) -> List[Dict[str, Any]]:
    """Paginate through all leads on a campaign."""
    items: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(max_pages):
        page = list_leads_page(api_key, campaign_id, limit=100, cursor=cursor)
        items.extend(page.get("items", []))
        cursor = page.get("next_starting_after")
        if not cursor:
            break
    return items


def list_sender_accounts(api_key: str, limit: int = 50) -> List[Dict[str, Any]]:
    """GET /accounts?limit=N — list sender accounts on this workspace."""
    if not api_key:
        return []
    try:
        r = requests.get(
            f"{INSTANTLY_BASE}/accounts",
            headers=_hdr(api_key),
            params={"limit": limit},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        d = r.json()
        if isinstance(d, dict):
            return d.get("items", []) or []
        return d or []
    except Exception as e:
        logger.warning(f"[instantly_ops] list_sender_accounts failed: {e}")
        return []


# ── Composite reports — the F1 telemetry views ─────────────────────────────

def _extract_schedule_summary(campaign: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the active sending window out of campaign.campaign_schedule.

    Schedule shape (validated 2026-05-06):
      campaign_schedule.schedules[].timing.{from,to}
      campaign_schedule.schedules[].days     -- dict keyed "0".."6"
      campaign_schedule.schedules[].timezone
    """
    sched = campaign.get("campaign_schedule") or {}
    schedules = sched.get("schedules") or []
    if not schedules:
        return {"configured": False}
    s = schedules[0]
    timing = s.get("timing") or {}
    days = s.get("days") or {}
    active_days = [int(k) for k, v in days.items() if v]
    return {
        "configured": True,
        "name": s.get("name"),
        "from": timing.get("from"),
        "to": timing.get("to"),
        "timezone": s.get("timezone"),
        "active_days": sorted(active_days),  # 0=Sun, 6=Sat in Instantly's convention
    }


def _lead_status_breakdown(leads: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count leads by readable status name."""
    counter: Counter = Counter()
    for lead in leads:
        st = lead.get("status", 0)
        counter[LEAD_STATUS_NAME.get(st, f"unknown({st})")] += 1
    return dict(counter)


def _compute_rates(lifetime: Dict[str, Any]) -> Dict[str, Optional[float]]:
    sent = lifetime.get("emails_sent_count", 0) or 0
    if sent <= 0:
        return {"open_rate_pct": None, "reply_rate_pct": None, "bounce_rate_pct": None}
    return {
        "open_rate_pct": round(100 * (lifetime.get("open_count", 0) or 0) / sent, 1),
        "reply_rate_pct": round(100 * (lifetime.get("reply_count", 0) or 0) / sent, 1),
        "bounce_rate_pct": round(100 * (lifetime.get("bounced_count", 0) or 0) / sent, 1),
    }


def _health_flags(lifetime: Dict[str, Any], rates: Dict[str, Optional[float]]) -> List[str]:
    """Surface-level red/yellow flags for the campaign row in the pit board."""
    flags: List[str] = []
    sent = lifetime.get("emails_sent_count", 0) or 0
    opens = lifetime.get("open_count", 0) or 0
    if sent >= DELIVERABILITY_DEAD_MIN_SENT and opens == 0:
        flags.append("DELIVERABILITY_DEAD")
    br = rates.get("bounce_rate_pct")
    if br is not None and br >= BOUNCE_RATE_CRITICAL:
        flags.append("BOUNCE_CRITICAL")
    elif br is not None and br >= BOUNCE_RATE_WARN:
        flags.append("BOUNCE_WARN")
    return flags


def _lead_pool_runway_days(
    active_leads: int, recent_daily_sends: List[int]
) -> Optional[float]:
    """Days of leads remaining at the average recent daily send pace.

    Returns None when there's no recent pace (campaign hasn't sent yet).
    """
    if not recent_daily_sends:
        return None
    pace = sum(recent_daily_sends) / max(1, len(recent_daily_sends))
    if pace <= 0:
        return None
    return round(active_leads / pace, 1)


def campaign_status_report(
    api_key: str, campaign_id: str, *, label: Optional[str] = None
) -> Dict[str, Any]:
    """The pit-board row for one campaign — everything the driver needs at a glance."""
    report: Dict[str, Any] = {
        "label": label,
        "campaign_id": campaign_id,
    }
    if not api_key or not campaign_id:
        report["error"] = "not_configured"
        return report

    cfg = get_campaign(api_key, campaign_id)
    if "error" in cfg:
        report["error"] = cfg
        return report

    lifetime = get_campaign_analytics_lifetime(api_key, campaign_id)
    daily = get_campaign_analytics_daily(api_key, campaign_id, days=14)
    leads = list_leads_all(api_key, campaign_id, max_pages=20)

    rates = _compute_rates(lifetime if isinstance(lifetime, dict) else {})
    flags = _health_flags(lifetime if isinstance(lifetime, dict) else {}, rates)
    breakdown = _lead_status_breakdown(leads)

    # Yesterday's row from daily (UTC date - 24h)
    yesterday_str = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
    yrow = next(
        (x for x in daily if str(x.get("date", "")).startswith(yesterday_str)),
        daily[-1] if daily else {},
    )

    recent_sends = [int(r.get("sent", r.get("emails_sent_count", 0)) or 0) for r in daily[-7:]]
    active_leads = breakdown.get("Active", 0)
    runway = _lead_pool_runway_days(active_leads, recent_sends)
    if runway is not None and runway <= LEAD_POOL_RUNWAY_CRITICAL_DAYS:
        flags.append("LEAD_POOL_EXHAUSTED")

    report.update({
        "name": cfg.get("name"),
        "status": CAMPAIGN_STATUS_LABEL.get(cfg.get("status"), cfg.get("status")),
        "senders": cfg.get("email_list", []),
        "daily_limit": cfg.get("daily_limit"),
        "stop_on_reply": cfg.get("stop_on_reply"),
        "sequence_steps": len(((cfg.get("sequences") or [{}])[0]).get("steps", [])),
        "schedule": _extract_schedule_summary(cfg),
        "lifetime": {
            "sent": lifetime.get("emails_sent_count", 0) if isinstance(lifetime, dict) else 0,
            "opens": lifetime.get("open_count", 0) if isinstance(lifetime, dict) else 0,
            "replies": lifetime.get("reply_count", 0) if isinstance(lifetime, dict) else 0,
            "clicks": lifetime.get("click_count", 0) if isinstance(lifetime, dict) else 0,
            "bounced": lifetime.get("bounced_count", 0) if isinstance(lifetime, dict) else 0,
            "leads": lifetime.get("leads_count", 0) if isinstance(lifetime, dict) else 0,
            "completed": lifetime.get("completed_count", 0) if isinstance(lifetime, dict) else 0,
        },
        "rates": rates,
        "yesterday": {
            "sent": int(yrow.get("sent", yrow.get("emails_sent_count", 0)) or 0),
            "opens": int(yrow.get("opened", yrow.get("open_count", 0)) or 0),
            "replies": int(yrow.get("replies", yrow.get("reply_count", 0)) or 0),
            "clicks": int(yrow.get("clicks", yrow.get("click_count", 0)) or 0),
        },
        "leads": {
            "total": len(leads),
            "breakdown": breakdown,
            "runway_days_at_7d_pace": runway,
        },
        "flags": flags,
    })
    return report


def sender_health_report(api_key: str, *, label: Optional[str] = None) -> Dict[str, Any]:
    """Per-sender health view — warmup status, connection status, send capacity."""
    report: Dict[str, Any] = {"label": label}
    if not api_key:
        report["error"] = "not_configured"
        return report

    accounts = list_sender_accounts(api_key, limit=50)
    senders: List[Dict[str, Any]] = []
    counters = {"connected": 0, "paused": 0, "errored": 0}
    warmup_buckets: Counter = Counter()

    for a in accounts:
        status = a.get("status")
        warmup_status = a.get("warmup_status")
        warmup_score = a.get("stat_warmup_score") or 0
        setup_pending = bool(a.get("setup_pending"))

        if status == 1:
            counters["connected"] += 1
        elif status == 2:
            counters["paused"] += 1
        if setup_pending or warmup_status == 0:
            counters["errored"] += 1
        warmup_buckets[warmup_status] += 1

        flags = []
        if setup_pending:
            flags.append("SETUP_PENDING")
        if warmup_status == 0:
            flags.append("WARMUP_DISABLED")
        if warmup_score < WARMUP_SCORE_HEALTHY:
            flags.append("WARMUP_LOW")

        senders.append({
            "email": a.get("email"),
            "status": status,
            "warmup_status": warmup_status,
            "warmup_score": warmup_score,
            "is_managed": a.get("is_managed_account"),
            "provider_code": a.get("provider_code"),
            "flags": flags,
        })

    report.update({
        "total_accounts": len(accounts),
        "counters": counters,
        "warmup_buckets": dict(warmup_buckets),
        "senders": senders,
    })
    return report


# ── Aggregators — fleet view across all KNOWN_CAMPAIGNS ────────────────────

def _resolve_campaign(entry: Tuple[str, str, str]) -> Tuple[str, str, str]:
    label, key_var, cid_var = entry
    return label, (os.getenv(key_var, "") or "").strip(), (os.getenv(cid_var, "") or "").strip()


def fleet_status() -> List[Dict[str, Any]]:
    """campaign_status_report for every campaign in KNOWN_CAMPAIGNS."""
    out: List[Dict[str, Any]] = []
    for entry in KNOWN_CAMPAIGNS:
        label, api_key, cid = _resolve_campaign(entry)
        out.append(campaign_status_report(api_key, cid, label=label))
    return out


def fleet_sender_health() -> List[Dict[str, Any]]:
    """sender_health_report per workspace (dedupe by api_key — same key = same workspace)."""
    seen_keys: set = set()
    out: List[Dict[str, Any]] = []
    for entry in KNOWN_CAMPAIGNS:
        label, api_key, _ = _resolve_campaign(entry)
        if not api_key or api_key in seen_keys:
            continue
        seen_keys.add(api_key)
        out.append(sender_health_report(api_key, label=f"{label} workspace"))
    return out


# ── Smoke test entry point ──────────────────────────────────────────────────

def _print_status_summary(report: Dict[str, Any]) -> None:
    print(f"\n{'='*70}\n{report.get('label')}\n{'='*70}")
    if report.get("error"):
        print(f"  ERROR: {report['error']}")
        return
    print(f"  status:        {report['status']}")
    print(f"  senders:       {report['senders']}")
    print(f"  daily_limit:   {report['daily_limit']}")
    sched = report["schedule"]
    if sched.get("configured"):
        print(f"  schedule:      {sched['from']}–{sched['to']} {sched['timezone']} days={sched['active_days']}")
    else:
        print(f"  schedule:      NOT CONFIGURED")
    lt = report["lifetime"]
    r = report["rates"]
    print(f"  lifetime:      {lt['sent']} sent / {lt['opens']} opens ({r['open_rate_pct']}%) / "
          f"{lt['replies']} replies ({r['reply_rate_pct']}%) / {lt['bounced']} bounced ({r['bounce_rate_pct']}%)")
    y = report["yesterday"]
    print(f"  yesterday:     {y['sent']} sent / {y['opens']} opens / {y['replies']} replies / {y['clicks']} clicks")
    leads = report["leads"]
    print(f"  leads:         {leads['total']} total — {leads['breakdown']}")
    print(f"  runway:        {leads['runway_days_at_7d_pace']} days at 7d pace")
    if report["flags"]:
        print(f"  🚨 FLAGS:      {', '.join(report['flags'])}")


def _print_sender_summary(report: Dict[str, Any]) -> None:
    print(f"\n{'='*70}\nSENDERS — {report.get('label')}\n{'='*70}")
    if report.get("error"):
        print(f"  ERROR: {report['error']}")
        return
    c = report["counters"]
    print(f"  total: {report['total_accounts']} | connected={c['connected']} paused={c['paused']} errored={c['errored']}")
    print(f"  warmup_buckets: {report['warmup_buckets']}")
    for s in report["senders"]:
        flag_str = f" 🚨 {','.join(s['flags'])}" if s["flags"] else ""
        print(f"    - {s['email']}: status={s['status']} warmup={s['warmup_status']} score={s['warmup_score']}{flag_str}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("\n### FLEET STATUS ###")
    for r in fleet_status():
        _print_status_summary(r)
    print("\n### SENDER HEALTH ###")
    for r in fleet_sender_health():
        _print_sender_summary(r)
