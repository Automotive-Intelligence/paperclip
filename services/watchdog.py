"""services/watchdog.py -- AVO Watchdog, Railway edition.

Port of ~/.local/bin/avo-watchdog.sh to run in the paperclip service instead
of on the local Mac via launchd. Local can't catch a 3am outage while the
laptop sleeps; this runs on Railway's uptime and posts anomalies to
#build-tech via the existing SLACK_BOT_TOKEN.

Three families of checks (dropped: launchd-job health, since Railway has
no launchd — paperclip's own scheduler health surfaces via /health):

  1. Brand-site health: HTTP status on each brand URL. Anything other than
     2xx/3xx = anomaly.
  2. avo-telemetry freshness: last commit older than N hours = anomaly.
     Coordination layer stalling is a leading indicator of a persona chat
     going dark.
  3. Extension slot: add more checks over time (Vercel deploys, Doppler
     token expiry, etc.) inside `_all_anomalies`.

Anomaly deduplication:
  Fingerprint-based, stored in a small watchdog_state table so a persistent
  anomaly doesn't spam Slack every hour. Only NEW fingerprints alert.
  When the underlying anomaly resolves, its fingerprint drops from the
  active set on the next run.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

import requests

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

SLACK_CHANNEL = "build-tech"
SLACK_API = "https://slack.com/api/chat.postMessage"


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
    """HEAD each brand URL; any non-2xx/3xx is an anomaly."""
    out: List[Anomaly] = []
    for url in BRAND_URLS:
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
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
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
        from config.watchdog_config import load_watchdog_config
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


def _revenue_summary(days: int) -> dict:
    from tools.revenue_tracker import get_revenue_summary
    return get_revenue_summary(days=days) or {}


def _check_emails_sent(cfg: Optional[dict] = None) -> List[Anomaly]:
    """The standing silent failure: the agent send rail shows emails_sent=0 for
    30 days while the pipeline fills. Alert only when a real pipeline exists, so
    an empty book never cries wolf.
    """
    if cfg is None:
        from config.watchdog_config import load_watchdog_config
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


def _all_anomalies() -> List[Anomaly]:
    """Composite check. Add more callables here as coverage expands."""
    out: List[Anomaly] = []
    for check in (_check_brand_sites, _check_telemetry_freshness, _check_blog_freshness):
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
# Slack alert
# ---------------------------------------------------------------------------


def _post_to_slack(new_anomalies: List[Anomaly]) -> Optional[str]:
    """Post ONE consolidated message per run summarizing new anomalies."""
    if not new_anomalies:
        return None
    token = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not token:
        logger.error("[watchdog] SLACK_BOT_TOKEN missing -- alert skipped")
        return None
    lines = ["🩺 *AVO Watchdog*: new infrastructure anomaly detected"]
    for a in new_anomalies:
        icon = "🚨" if a.severity == "critical" else "⚠️"
        lines.append(f"{icon} {a.human}")
    text = "\n".join(lines)
    try:
        r = requests.post(
            SLACK_API,
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": SLACK_CHANNEL, "text": text, "unfurl_links": False},
            timeout=15,
        )
    except requests.RequestException as e:
        logger.warning("[watchdog] slack post failed: %s", e)
        return None
    if not r.ok or not r.json().get("ok"):
        logger.warning("[watchdog] slack error: %s %s", r.status_code, r.text[:200])
        return None
    return r.json().get("ts")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


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
    if new:
        _post_to_slack(new)
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


__all__ = ["Anomaly", "run_once", "run_hourly", "run_now_json"]
