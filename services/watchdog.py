"""services/watchdog.py -- AVO Watchdog, Railway edition.

Runs in the paperclip service (not the laptop, which can't catch a 3am outage
while it sleeps). The hourly scheduler DETECTS anomalies and records them to the
watchdog_state table. It does NOT deliver alerts. Delivery is a rail: the
avo-telemetry GitHub Actions workflow polls GET /health/watchdog and opens/closes
a GitHub issue -> emails Michael. Keeping the alert lifecycle in the issue (dedup,
recovery, audit trail) is why there is no Slack here.

Checks (config-driven via config/watchdog.yaml; registered in _CHECKS):

  1. Brand-site health: HTTP status on each configured site URL (non-2xx/3xx = anomaly).
  2. avo-telemetry freshness: last commit older than N hours = coordination layer stalled.
  3. Blog freshness: per brand, is the newest LIVE blog post within its cadence, and
     does it serve 200? Catches "engine exited 0 but shipped nothing / a 404" -- the
     silent failure a receipt-only check misses (verified 2026-07-18).
  4. Weekly-social freshness: is the newest committed Studio weekly SOCIAL batch
     (marketing_deliverables/*studio_weekly_<date>*) within cadence? The batch has
     no live URL, so the committed folder is the truth signal. Disabled until the
     social-engine cloud cutover (the laptop engine does not commit its folder).
  5. emails_sent stuck at 0 while the pipeline fills = the agent send rail is dead.
  6. env-truth: the live service should call itself 'production' in production.

Anomaly deduplication:
  Fingerprint-based in watchdog_state. current_state_json() reads this table for
  the poller; the GitHub issue is the alert-level dedup.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

import requests

from config.watchdog_config import load_watchdog_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Network / time seams (patched in tests; real callers hit the wire)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_text(url: str, timeout: int = 15) -> str:
    r = requests.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def _http_status(url: str, timeout: int = 15) -> int:
    return requests.get(url, timeout=timeout, allow_redirects=True).status_code


BRAND_URLS: List[str] = [
    "https://automotiveintelligence.io",
    "https://worshipdigital.co",
    "https://crm.worshipdigital.co",
    "https://buildagentempire.com",
    "https://bookd.cx",
    "https://paperandpurpose.co",
]

TELEMETRY_REPO = "salesdroid/avo-telemetry"
TELEMETRY_MAX_STALE_HOURS = 48

# The Studio weekly social batch is staged as a deliverable folder whose name
# embeds the target Monday, e.g. `141_studio_weekly_2026-07-20` (or the older
# `..._2026-07-06_to_2026-07-12` variant -- we take the FIRST date, the Monday).
WEEKLY_BATCH_DIR = "marketing_deliverables"
_WEEKLY_BATCH_RE = re.compile(r"studio_weekly_(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------------------
# Anomaly datatype
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anomaly:
    """One anomaly. `fingerprint` is a stable dedup key; `human` is the
    message that ends up in Slack.
    """
    fingerprint: str
    human: str
    severity: str = "warn"  # "warn" | "critical"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_brand_sites() -> List[Anomaly]:
    """HEAD each brand URL; any non-2xx/3xx is an anomaly.
    Coverage list is config-driven (config/watchdog.yaml site_urls)."""
    out: List[Anomaly] = []
    urls = (load_watchdog_config().get("site_urls") or BRAND_URLS)
    for url in urls:
        try:
            r = requests.get(url, timeout=15, allow_redirects=True)
            code = r.status_code
        except requests.RequestException as e:
            out.append(Anomaly(
                fingerprint=f"site-network-{url}",
                human=f"Site DOWN/network error: {url} -- {type(e).__name__}",
                severity="critical",
            ))
            continue
        if not (200 <= code < 400):
            out.append(Anomaly(
                fingerprint=f"site-http-{url}-{code}",
                human=f"Site returned HTTP {code}: {url}",
                severity="critical" if code >= 500 else "warn",
            ))
    return out


def _check_telemetry_freshness() -> List[Anomaly]:
    """avo-telemetry not-committed-in-N-hours = coordination layer stalled.
    Uses GitHub REST rather than a local git clone so this survives running
    inside a stateless container.
    """
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(
            f"https://api.github.com/repos/{TELEMETRY_REPO}/commits",
            params={"per_page": 1}, headers=headers, timeout=15,
        )
    except requests.RequestException as e:
        # Network failure to GitHub isn't a Watchdog anomaly per se; log + skip.
        logger.warning("[watchdog] telemetry commit fetch failed: %s", e)
        return []
    if not r.ok:
        logger.warning("[watchdog] telemetry commit fetch HTTP %s: %s", r.status_code, r.text[:120])
        return []
    commits = r.json() or []
    if not commits:
        return []
    ts_str = (commits[0].get("commit") or {}).get("committer", {}).get("date") or ""
    try:
        commit_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        logger.warning("[watchdog] unparseable telemetry commit ts: %r", ts_str)
        return []
    stale_h = (datetime.now(timezone.utc) - commit_ts).total_seconds() / 3600
    if stale_h > TELEMETRY_MAX_STALE_HOURS:
        return [Anomaly(
            fingerprint="telemetry-stale",
            human=(
                f"avo-telemetry has no commits in {int(stale_h)}h "
                f"(>{TELEMETRY_MAX_STALE_HOURS}h) -- coordination layer may be stalled."
            ),
            severity="warn",
        )]
    return []


def _newest_blog_post(sitemap_xml: str) -> Optional[Tuple[str, str]]:
    """Return (iso_date, url) for the newest <loc> that is a real blog post
    (contains '/blog/<slug>', not the '/blog' index). None if none found.
    Sitemap lastmod is the truth signal here: verified 2026-07-18 that AvI,
    AIPG, and BAE all carry per-post /blog/<slug> lastmod dates.
    """
    best: Optional[Tuple[str, str]] = None
    for entry in re.findall(r"<url>(.*?)</url>", sitemap_xml, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", entry)
        lm = re.search(r"<lastmod>(.*?)</lastmod>", entry)
        if not loc or "/blog/" not in loc.group(1) or not lm:
            continue
        d = lm.group(1).strip()[:10]
        if best is None or d > best[0]:
            best = (d, loc.group(1).strip())
    return best


def _check_blog_freshness(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Per brand: is the newest LIVE blog post within its expected cadence, and
    does it actually serve 200? A receipt-only check would have false-alarmed on
    2026-07-18 (posts shipped, receipt did not); the live site is the truth.
    """
    if cfg is None:
        cfg = load_watchdog_config()
    out: List[Anomaly] = []
    for brand, b in (cfg.get("brands") or {}).items():
        max_h = int(b.get("blog_max_age_hours") or 0)
        if max_h <= 0:
            continue  # disabled: content HELD or no blog engine (GAP)
        sev = b.get("severity", "warn")
        try:
            xml = _fetch_text(b["sitemap_url"])
        except requests.RequestException as e:
            out.append(Anomaly(
                f"blog-freshness-unknown-{brand}",
                f"Cannot fetch sitemap for {brand}: {type(e).__name__}", sev))
            continue
        newest = _newest_blog_post(xml)
        if not newest:
            out.append(Anomaly(
                f"blog-freshness-unknown-{brand}",
                f"No per-post /blog/ entries in {brand} sitemap; cannot verify freshness.",
                sev))
            continue
        d, url = newest
        try:
            post_dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            out.append(Anomaly(
                f"blog-freshness-unknown-{brand}",
                f"Unparseable newest post date for {brand}: {d!r}", sev))
            continue
        age_h = (_now_utc() - post_dt).total_seconds() / 3600
        if age_h > max_h:
            out.append(Anomaly(
                f"blog-stale-{brand}",
                f"{brand} newest blog post is {int(age_h)}h old (> {max_h}h): {url}", sev))
        try:
            if _http_status(url) != 200:
                out.append(Anomaly(
                    f"blog-404-{brand}",
                    f"{brand} sitemap advertises a post that does not serve 200: {url}",
                    "critical"))
        except requests.RequestException as e:
            out.append(Anomaly(
                f"blog-404-{brand}",
                f"{brand} newest post URL failed to load ({type(e).__name__}): {url}",
                "critical"))
    return out


def _latest_weekly_batch_monday() -> Optional[str]:
    """ISO date (YYYY-MM-DD) of the week covered by the newest committed
    `marketing_deliverables/*studio_weekly_<date>*` folder in avo-telemetry, or
    None if none can be read. The Studio weekly engine stages one gated batch
    folder per run whose name embeds the target Monday; the cloud engine commits
    it every run (being stateless, it must). Uses the GitHub Contents API so this
    works from a stateless container, the same rail as _check_telemetry_freshness.
    """
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(
            f"https://api.github.com/repos/{TELEMETRY_REPO}/contents/{WEEKLY_BATCH_DIR}",
            headers=headers, timeout=15,
        )
    except requests.RequestException as e:
        logger.warning("[watchdog] weekly-batch listing failed: %s", e)
        return None
    if not r.ok:
        logger.warning("[watchdog] weekly-batch listing HTTP %s: %s", r.status_code, r.text[:120])
        return None
    best: Optional[str] = None
    for entry in (r.json() or []):
        if entry.get("type") != "dir":
            continue  # a stray file named studio_weekly must not count as a batch
        m = _WEEKLY_BATCH_RE.search(entry.get("name") or "")
        if not m:
            continue
        d = m.group(1)
        if best is None or d > best:
            best = d
    return best


def _check_weekly_social_freshness(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Is the newest committed Studio weekly SOCIAL batch within cadence?

    The batch has no live URL (posts schedule into Zernio, not a public page),
    so the truth signal is the committed deliverable folder, not the engine's
    own on-disk receipt (which a stateless watcher cannot see). Disabled while
    max_age_hours is 0 -- the current laptop engine does not reliably commit its
    folder, so leaving it 0 avoids a false alarm; ENABLE at the cloud cutover,
    when the stateless cloud engine commits every run. This is the outside check
    that catches the exit-0-shipped-nothing case (the 2026-07-11 failure mode).
    """
    if cfg is None:
        cfg = load_watchdog_config()
    ws = cfg.get("weekly_social") or {}
    max_h = int(ws.get("max_age_hours") or 0)
    if max_h <= 0:
        return []  # disabled until the social-engine cloud cutover
    sev = ws.get("severity", "warn")
    monday = _latest_weekly_batch_monday()
    if not monday:
        return [Anomaly(
            "weekly-social-freshness-unknown",
            "No marketing_deliverables/*studio_weekly_<date>* folder found in "
            "avo-telemetry; cannot verify the weekly social batch shipped.", sev)]
    try:
        batch_dt = datetime.fromisoformat(monday).replace(tzinfo=timezone.utc)
    except ValueError:
        return [Anomaly(
            "weekly-social-freshness-unknown",
            f"Unparseable weekly-batch date {monday!r}; cannot verify freshness.", sev)]
    # The folder date is the batch's TARGET Monday. A batch for the upcoming week
    # is future-dated (negative age) and healthy; a missed run lets the newest
    # Monday recede into the past until it crosses the threshold.
    age_h = (_now_utc() - batch_dt).total_seconds() / 3600
    if age_h > max_h:
        return [Anomaly(
            "weekly-social-stale",
            f"Newest Studio weekly social batch covers week of {monday} "
            f"({int(age_h)}h old, > {max_h}h) -- the weekly engine may have "
            f"silently missed a run.", sev)]
    return []


def _revenue_summary(days: int) -> dict:
    from tools.revenue_tracker import get_revenue_summary
    return get_revenue_summary(days=days) or {}


def _check_emails_sent(cfg: Optional[dict] = None) -> List[Anomaly]:
    """The standing silent failure: the agent send rail shows emails_sent=0 for
    30 days while the pipeline fills. Alert only when a real pipeline exists, so
    an empty book never cries wolf.
    """
    if cfg is None:
        cfg = load_watchdog_config()
    es = cfg.get("emails_sent") or {}
    days = int(es.get("window_days", 7))
    floor = int(es.get("min_prospects_for_alert", 25))
    summ = _revenue_summary(days)
    if summ.get("error"):
        return []
    prospects = int(summ.get("prospects_created") or summ.get("total_prospects") or 0)
    if int(summ.get("emails_sent") or 0) == 0 and prospects > floor:
        return [Anomaly(
            "emails-sent-zero",
            f"emails_sent is 0 over {days}d while {prospects} prospects were created "
            f"-- the agent send rail may be dead.", "warn")]
    return []


def _current_environment() -> Optional[str]:
    """The service's own declared environment (config.runtime SETTINGS)."""
    try:
        from config.runtime import get_settings
        return get_settings().environment
    except Exception:
        return None


def _check_env_truth() -> List[Anomaly]:
    """Pillar-4 fold-in: the live service should call itself 'production' in
    production. A surface that disagrees with reality is exactly what the
    (unscheduled) truth-pass used to catch by hand.
    """
    env = _current_environment()
    if env and env != "production":
        return [Anomaly(
            "env-mislabelled",
            f"Live service reports environment={env!r} (expected 'production').",
            "warn")]
    return []


# Registry of every check the watchdog runs. Extend here as coverage grows.
_CHECKS = (
    _check_brand_sites,
    _check_telemetry_freshness,
    _check_blog_freshness,
    _check_weekly_social_freshness,
    _check_emails_sent,
    _check_env_truth,
)


def _all_anomalies() -> List[Anomaly]:
    """Composite check. One check raising never sinks the others."""
    out: List[Anomaly] = []
    for check in _CHECKS:
        try:
            out.extend(check())
        except Exception as e:
            logger.exception("[watchdog] check %s raised: %s", check.__name__, e)
    return out


# ---------------------------------------------------------------------------
# Dedup via Postgres
# ---------------------------------------------------------------------------


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS watchdog_state (
    fingerprint  TEXT PRIMARY KEY,
    human        TEXT NOT NULL,
    severity     TEXT NOT NULL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _active_fingerprints() -> Set[str]:
    from services.database import fetch_all
    rows = fetch_all("SELECT fingerprint FROM watchdog_state")
    return {r[0] for r in rows}


def _record_active(anomalies: List[Anomaly]) -> None:
    """Upsert each current anomaly; delete any fingerprint not present now
    (self-clearing anomalies)."""
    from services.database import execute_query
    execute_query(_CREATE_TABLE_SQL)
    if anomalies:
        for a in anomalies:
            execute_query(
                """
                INSERT INTO watchdog_state (fingerprint, human, severity)
                VALUES (%s, %s, %s)
                ON CONFLICT (fingerprint) DO UPDATE
                    SET last_seen = NOW(), human = EXCLUDED.human, severity = EXCLUDED.severity
                """,
                (a.fingerprint, a.human, a.severity),
            )
    # Prune fingerprints not in the current set (auto-clear resolved).
    current = tuple(a.fingerprint for a in anomalies)
    if current:
        execute_query(
            "DELETE FROM watchdog_state WHERE fingerprint <> ALL(%s)",
            (list(current),),
        )
    else:
        execute_query("DELETE FROM watchdog_state")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
#
# Alerting is NOT done here. Detection records anomalies to watchdog_state; the
# GitHub Actions workflow (avo-telemetry/.github/workflows/watchdog.yml) polls
# GET /health/watchdog and opens/closes a GitHub issue -> emails Michael. That
# keeps the alert lifecycle (dedup, recovery, audit trail) on a rail, not Slack.


def run_once() -> Tuple[List[Anomaly], List[Anomaly]]:
    """Execute one Watchdog cycle. Returns (all_anomalies, new_anomalies).
    Idempotent + safe to call from HTTP endpoint or scheduler.
    """
    anomalies = _all_anomalies()
    try:
        active = _active_fingerprints()
    except Exception as e:
        logger.warning("[watchdog] state fetch failed (assuming fresh): %s", e)
        active = set()
    new = [a for a in anomalies if a.fingerprint not in active]
    try:
        _record_active(anomalies)
    except Exception as e:
        logger.warning("[watchdog] state persist failed: %s", e)
    return anomalies, new


def run_hourly() -> None:
    """Scheduler entry point."""
    anomalies, new = run_once()
    logger.info(
        "[watchdog] cycle done: %d active, %d new anomalies",
        len(anomalies), len(new),
    )


def current_state_json() -> dict:
    """Read-only snapshot of currently-active anomalies from watchdog_state.
    No fresh sweep: cheap and safe for a public GET the GitHub Action polls.
    The hourly scheduler populates the table.
    """
    try:
        from services.database import fetch_all
        rows = fetch_all(
            "SELECT fingerprint, human, severity FROM watchdog_state "
            "ORDER BY severity DESC, fingerprint"
        )
    except Exception as e:
        # Watchdog's own store being down is itself surfaced by the uptime
        # watcher (/health/ready); here we fail soft so the poller can tell
        # "no anomalies" from "watchdog broken".
        return {"ok": False, "error": str(e), "active_anomalies": []}
    return {
        "ok": True,
        "checked_at": _now_utc().isoformat(),
        "active_anomalies": [
            {"fingerprint": r[0], "human": r[1], "severity": r[2]} for r in rows
        ],
    }


def run_now_json() -> dict:
    """Manual-trigger admin endpoint response."""
    anomalies, new = run_once()
    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "active_anomalies": [
            {"fingerprint": a.fingerprint, "human": a.human, "severity": a.severity}
            for a in anomalies
        ],
        "new_this_cycle": [
            {"fingerprint": a.fingerprint, "human": a.human, "severity": a.severity}
            for a in new
        ],
        "coverage": {
            "brand_urls": BRAND_URLS,
            "telemetry_repo": TELEMETRY_REPO,
            "telemetry_max_stale_hours": TELEMETRY_MAX_STALE_HOURS,
        },
    }


__all__ = ["Anomaly", "run_once", "run_hourly", "run_now_json", "current_state_json"]
