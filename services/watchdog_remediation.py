"""services/watchdog_remediation.py -- Tier 1 self-healing: press the button first.

Michael's directive (2026-08-07): when a notification arrives that the system
can fix, the system should fix it. Most watchdog anomalies never needed
judgment -- they needed a known re-run executed. This module maps fingerprint
prefixes to SAFE, IDEMPOTENT in-process actions and gives each incident ONE
attempt before a human hears about it.

Semantics (the anti-flap design, learned from issue #5):
  - First detection of an auto-fixable anomaly: attempt the fix, log it to
    watchdog_remediations, and HOLD the anomaly out of active_anomalies for
    that sweep (grace). A self-healed incident = ZERO emails, full audit trail.
  - Still present next sweep (fix didn't clear it): the anomaly surfaces
    normally, with the attempt receipt appended to its human text -- the alert
    arrives as "tried X, still broken", which is a better email.
  - Cooldown (config auto_remediation.cooldown_hours) stops retry loops: one
    attempt per incident window, never a hammering bot.

HARD LINES: actions are in-process re-runs of engines that already run
unattended on this scheduler (tp-daily, growth-monitor, sonar, janitor, dmarc).
Nothing here touches money, outbound sends, auth, DNS, or content engines with
LLM spend (Slipstream re-fires cost real dollars and can double-publish; those
stay alert-with-runbook).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action registry: fingerprint prefix -> (action_name, thunk). Thunks import
# lazily and re-run the SAME code path the scheduler runs, so behavior is
# identical to a normal cron tick (tp/growth are idempotent per CT day).
# ---------------------------------------------------------------------------


def _fix_tp_daily() -> str:
    from services.tp_daily_engine import run_tp_daily
    return str(run_tp_daily(commit=True))[:300]


def _fix_growth_monitor() -> str:
    from services.growth_monitor_engine import run_growth_monitor
    return str(run_growth_monitor(commit=True))[:300]


def _fix_sonar() -> str:
    from services.sonar_inbox import run_sweep
    return str(run_sweep(commit=True))[:300]


def _fix_janitor() -> str:
    from services.inbox_janitor import run_sweep
    return str(run_sweep(commit=True))[:300]


def _fix_dmarc() -> str:
    from services.wd_dmarc_monitor import run_weekly
    return str(run_weekly())[:300]


_ACTIONS: Tuple[Tuple[str, str, Callable[[], str]], ...] = (
    ("monitor-stale-tp_daily", "rerun-tp-daily", _fix_tp_daily),
    ("monitor-stale-growth_monitor", "rerun-growth-monitor", _fix_growth_monitor),
    ("service-heartbeat-stale-sonar_inbox", "rerun-sonar-sweep", _fix_sonar),
    ("service-heartbeat-missing-sonar_inbox", "rerun-sonar-sweep", _fix_sonar),
    ("service-heartbeat-stale-inbox_janitor", "rerun-inbox-janitor", _fix_janitor),
    ("service-heartbeat-missing-inbox_janitor", "rerun-inbox-janitor", _fix_janitor),
    ("service-heartbeat-stale-wd_dmarc", "rerun-dmarc-audit", _fix_dmarc),
    ("service-heartbeat-missing-wd_dmarc", "rerun-dmarc-audit", _fix_dmarc),
)


def action_for(fingerprint: str) -> Optional[Tuple[str, Callable[[], str]]]:
    for prefix, name, thunk in _ACTIONS:
        if fingerprint.startswith(prefix):
            return name, thunk
    return None


# ---------------------------------------------------------------------------
# Attempt log (Postgres): the audit trail + the cooldown source of truth.
# ---------------------------------------------------------------------------

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS watchdog_remediations (
    id           SERIAL PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    action       TEXT NOT NULL,
    ok           BOOLEAN NOT NULL,
    detail       TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _last_attempt(fingerprint: str) -> Optional[datetime]:
    from services.database import fetch_all
    rows = fetch_all(
        "SELECT attempted_at FROM watchdog_remediations "
        "WHERE fingerprint = %s ORDER BY attempted_at DESC LIMIT 1",
        (fingerprint,))
    return rows[0][0] if rows else None


def _log_attempt(fingerprint: str, action: str, ok: bool, detail: str) -> None:
    from services.database import execute_query
    execute_query(_TABLE_SQL)
    execute_query(
        "INSERT INTO watchdog_remediations (fingerprint, action, ok, detail) "
        "VALUES (%s, %s, %s, %s)",
        (fingerprint, action, ok, detail[:500]))


# ---------------------------------------------------------------------------
# The sweep hook
# ---------------------------------------------------------------------------


def maybe_remediate(anomalies: List, cfg: dict, now: datetime) -> Tuple[Set[str], Dict[str, str]]:
    """Called by watchdog.run_once with the freshly-detected anomaly list.

    Returns (held, amended):
      held    -- fingerprints to EXCLUDE from this sweep's active set (fix was
                 just attempted; give it until next sweep to clear silently)
      amended -- fingerprint -> suffix to append to the human text (fix was
                 attempted earlier in the cooldown window and did NOT clear)
    Fail-safe throughout: any DB/action error degrades to normal alerting.
    """
    ar = cfg.get("auto_remediation") or {}
    if not ar.get("enabled"):
        return set(), {}
    cooldown_h = float(ar.get("cooldown_hours") or 20)
    held: Set[str] = set()
    amended: Dict[str, str] = {}
    for a in anomalies:
        match = action_for(a.fingerprint)
        if not match:
            continue
        name, thunk = match
        try:
            last = _last_attempt(a.fingerprint)
        except Exception as e:
            logger.warning("[remediation] attempt-log read failed for %s: %s",
                           a.fingerprint, e)
            continue  # can't prove one attempt only -> alert normally
        if last is not None:
            age_h = (now - last).total_seconds() / 3600
            if age_h < cooldown_h:
                amended[a.fingerprint] = (
                    f" [auto-fix '{name}' attempted {int(age_h)}h ago; did not clear]")
                continue
        # Fresh incident window: one attempt, held from this sweep's alert set.
        try:
            detail = thunk()
            ok = True
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            ok = False
        try:
            _log_attempt(a.fingerprint, name, ok, detail)
        except Exception as e:
            logger.warning("[remediation] attempt-log write failed for %s: %s",
                           a.fingerprint, e)
        if ok:
            held.add(a.fingerprint)
            logger.info("[remediation] %s: ran '%s', holding one sweep (%s)",
                        a.fingerprint, name, detail[:120])
        else:
            amended[a.fingerprint] = f" [auto-fix '{name}' FAILED just now: {detail[:120]}]"
            logger.warning("[remediation] %s: '%s' failed: %s", a.fingerprint, name, detail)
    return held, amended


__all__ = ["maybe_remediate", "action_for"]
