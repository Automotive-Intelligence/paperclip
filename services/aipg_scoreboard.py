"""
services/aipg_scoreboard.py — AIPG funnel scoreboard data layer.

Surfaces the metrics Pit Wall asked for in flag #5 (2026-06-15):
  • Tyler outreach volume        (last 7d + 30d push counts via crm_push_logs)
  • Zoe nurture activity          (zoe agent_run_costs runs as a proxy for
                                   nurture content shipped; we don't yet track
                                   reply % — surfaced as TODO in the response)
  • Demo-booked count             (GHL appointments API, last 7d + 30d)
  • Close rate                    (GHL deal_stage transitions to won)
  • Founder-offer take-rate       ($187 Founder vs $482 Standard; from
                                   crm_push_logs metadata when present,
                                   surfaced as TODO with raw counts if the
                                   monetary metadata isn't being persisted yet)

Consumed by:
  • GET /admin/scoreboard/aipg  — JSON for cockpit panel + ad-hoc curl
  • Pit Wall daily brief        — future integration; the JSON shape is
                                  stable enough to embed verbatim

Pulls live; no caching layer. The endpoint is internal and called at most
hourly so latency on GHL roundtrips is fine.

Per Pit Wall flag #5 (2026-06-15).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from services.database import fetch_all

logger = logging.getLogger(__name__)


GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"
_REQUEST_TIMEOUT = 12


# ── GHL helpers (read-only) ────────────────────────────────────────────────


def _ghl_headers() -> Optional[Dict[str, str]]:
    token = (os.getenv("GHL_API_KEY") or "").strip()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Version": GHL_VERSION,
        "Content-Type": "application/json",
    }


def _ghl_location_id() -> str:
    return (os.getenv("GHL_LOCATION_ID") or "").strip()


def _ghl_pipeline_id() -> str:
    return (os.getenv("GHL_PIPELINE_ID") or "").strip()


def _ghl_count_appointments(days: int) -> tuple[Optional[int], Optional[str]]:
    """Count GHL appointments across ALL location calendars in the trailing
    N days.

    GHL's /calendars/events endpoint requires a calendarId/userId/groupId
    per request (not just locationId), so we first list the location's
    calendars and then sum events per calendar. startTime/endTime must be
    ISO-string milliseconds; `limit` is not a valid parameter.

    Returns (count, blocker_reason). count is None when we can't read.
    """
    headers = _ghl_headers()
    loc = _ghl_location_id()
    if not headers or not loc:
        return None, "GHL_API_KEY or GHL_LOCATION_ID missing"
    import datetime as _dt
    end = _dt.datetime.now(_dt.timezone.utc)
    start = end - _dt.timedelta(days=days)
    start_ms_str = str(int(start.timestamp() * 1000))
    end_ms_str = str(int(end.timestamp() * 1000))
    try:
        cal_r = requests.get(
            f"{GHL_BASE}/calendars/",
            headers=headers, params={"locationId": loc},
            timeout=_REQUEST_TIMEOUT,
        )
        if cal_r.status_code == 401:
            return None, "GHL_API_KEY lacks calendars.readonly scope"
        if not cal_r.ok:
            return None, f"/calendars/ http={cal_r.status_code}"
        calendars = cal_r.json().get("calendars") or []
        if not calendars:
            return 0, None

        total = 0
        for cal in calendars:
            cal_id = cal.get("id")
            if not cal_id:
                continue
            r = requests.get(
                f"{GHL_BASE}/calendars/events",
                headers=headers,
                params={
                    "locationId": loc,
                    "calendarId": cal_id,
                    "startTime": start_ms_str,
                    "endTime": end_ms_str,
                },
                timeout=_REQUEST_TIMEOUT,
            )
            if r.status_code == 401:
                return None, "GHL_API_KEY lacks calendars/events.readonly scope"
            if not r.ok:
                # Skip individual calendar failures; keep counting the rest.
                logger.warning(
                    "[aipg_scoreboard] /calendars/events cal=%s http=%s body=%s",
                    cal_id, r.status_code, r.text[:120],
                )
                continue
            events = r.json().get("events") or []
            total += len(events)
        return total, None
    except Exception as e:
        return None, f"/calendars/events raised {type(e).__name__}: {e}"


def _ghl_count_tyler_prospects(days: int) -> tuple[Optional[int], Optional[str]]:
    """Count GHL contacts tagged 'tyler-prospect' added in the trailing N days.

    Uses the /contacts/ endpoint that our token CAN read (verified — returns
    175 tyler-prospect contacts at scope check). This is the closest live
    proxy for "AIPG funnel new-entry volume" while calendars scope is missing.
    """
    headers = _ghl_headers()
    loc = _ghl_location_id()
    if not headers or not loc:
        return None, "GHL_API_KEY or GHL_LOCATION_ID missing"
    import datetime as _dt
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    cutoff_iso = cutoff.isoformat()
    try:
        # Paginate up to 5 pages = 1000 contacts max (well above weekly volume).
        seen = 0
        start_after = None
        start_after_id = None
        for _ in range(5):
            params = {"locationId": loc, "limit": 200}
            if start_after and start_after_id:
                params["startAfter"] = start_after
                params["startAfterId"] = start_after_id
            r = requests.get(
                f"{GHL_BASE}/contacts/",
                headers=headers,
                params=params,
                timeout=_REQUEST_TIMEOUT,
            )
            if not r.ok:
                return None, f"/contacts/ http={r.status_code}"
            body = r.json()
            contacts = body.get("contacts") or []
            if not contacts:
                break
            for c in contacts:
                tags = c.get("tags") or []
                if not any(t.startswith("tyler-prospect") for t in tags):
                    continue
                created = c.get("dateAdded") or ""
                if created and created >= cutoff_iso:
                    seen += 1
            meta = body.get("meta") or {}
            start_after = meta.get("startAfter")
            start_after_id = meta.get("startAfterId")
            if not start_after or not start_after_id:
                break
        return seen, None
    except Exception as e:
        return None, f"/contacts/ raised {type(e).__name__}: {e}"


def _ghl_count_deals_won(days: int) -> Optional[int]:
    """Count GHL opportunities that moved to status=won in trailing N days."""
    headers = _ghl_headers()
    loc = _ghl_location_id()
    pipeline = _ghl_pipeline_id()
    if not headers or not loc or not pipeline:
        return None
    try:
        import datetime as _dt
        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
        r = requests.get(
            f"{GHL_BASE}/opportunities/search",
            headers=headers,
            params={
                "location_id": loc,
                "pipeline_id": pipeline,
                "status": "won",
                "limit": 100,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if not r.ok:
            return None
        opps = r.json().get("opportunities") or []
        return sum(
            1 for o in opps
            if (o.get("updatedAt") or o.get("dateAdded") or "") >= cutoff.isoformat()
        )
    except Exception as e:
        logger.warning("[aipg-scoreboard] GHL won-count raised: %s", e)
        return None


# ── Postgres helpers ──────────────────────────────────────────────────────


def _agent_runs(agent: str, days: int) -> int:
    rows = fetch_all(
        """
        SELECT COUNT(*) FROM agent_run_costs
        WHERE agent_name=%s AND created_at >= NOW() - (%s::int * INTERVAL '1 day')
        """,
        (agent, days),
    )
    return int(rows[0][0] or 0) if rows else 0


def _push_counts(agent: str, days: int) -> Dict[str, int]:
    rows = fetch_all(
        """
        SELECT status, COUNT(*) FROM crm_push_logs
        WHERE agent_name=%s AND created_at >= NOW() - (%s::int * INTERVAL '1 day')
        GROUP BY status
        """,
        (agent, days),
    )
    return {r[0]: int(r[1] or 0) for r in rows}


# ── Public scoreboard builder ─────────────────────────────────────────────


@dataclass
class AipgScoreboard:
    window_days: int
    tyler_runs: int
    tyler_pushes_created: int
    tyler_pushes_duplicate_skipped: int
    tyler_pushes_failed: int
    zoe_runs: int
    tyler_prospects_in_ghl: Optional[int]   # contacts tagged tyler-prospect, last N days
    appointments_count: Optional[int]
    deals_won_count: Optional[int]
    close_rate_pct: Optional[float]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_days": self.window_days,
            "tyler": {
                "runs": self.tyler_runs,
                "pushes_created": self.tyler_pushes_created,
                "pushes_duplicate_skipped": self.tyler_pushes_duplicate_skipped,
                "pushes_failed": self.tyler_pushes_failed,
                "prospects_in_ghl": self.tyler_prospects_in_ghl,
            },
            "zoe": {
                "runs": self.zoe_runs,
            },
            "demo_booked_count": self.appointments_count,
            "deals_won_count": self.deals_won_count,
            "close_rate_pct": self.close_rate_pct,
            "notes": self.notes,
        }


def build_aipg_scoreboard(days: int = 7) -> AipgScoreboard:
    """Build the live AIPG funnel scoreboard for the trailing `days` window."""
    tyler_runs = _agent_runs("tyler", days)
    tyler_pushes = _push_counts("tyler", days)
    zoe_runs = _agent_runs("zoe", days)

    appointments, appt_reason = _ghl_count_appointments(days)
    prospects_in_ghl, prospects_reason = _ghl_count_tyler_prospects(days)
    deals_won = _ghl_count_deals_won(days)

    close_rate: Optional[float] = None
    if appointments and appointments > 0 and deals_won is not None:
        close_rate = round((deals_won / appointments) * 100, 1)

    notes: List[str] = []
    if appointments is None:
        notes.append(
            f"demo_booked_count unavailable — {appt_reason or 'unknown reason'}. "
            f"In the meantime tyler.prospects_in_ghl is the closest live proxy."
        )
    if prospects_in_ghl is None:
        notes.append(
            f"tyler.prospects_in_ghl unavailable — {prospects_reason or 'unknown reason'}."
        )
    if deals_won is None:
        notes.append(
            "deals_won_count unavailable — GHL_PIPELINE_ID missing or "
            "/opportunities/search non-OK."
        )
    notes.append(
        "Zoe nurture conversion % not yet computed — wire after we track "
        "outbound message ids → reply association in a future PR."
    )
    notes.append(
        "Founder-offer take-rate ($187 vs $482) not yet computed — needs "
        "monetary_value flag on each prospect_created event in track_event."
    )

    return AipgScoreboard(
        window_days=days,
        tyler_runs=tyler_runs,
        tyler_pushes_created=tyler_pushes.get("created", 0),
        tyler_pushes_duplicate_skipped=tyler_pushes.get("duplicate_skipped", 0),
        tyler_pushes_failed=tyler_pushes.get("failed", 0),
        zoe_runs=zoe_runs,
        tyler_prospects_in_ghl=prospects_in_ghl,
        appointments_count=appointments,
        deals_won_count=deals_won,
        close_rate_pct=close_rate,
        notes=notes,
    )
