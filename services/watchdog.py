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
  3b. Slipstream queue depth: per brand, how many unchecked '- [ ]' topics remain
     in the queue file? A drained queue makes the engine HOLD SILENTLY (produce
     nothing) -- the 2026-07-31 BAE miss. Reads the queue directly (locations from
     config/slipstream_brands.yaml); config-gated (slipstream_queues.min_unchecked).
  4. Weekly-social freshness: is the newest committed Studio weekly SOCIAL batch
     (marketing_deliverables/*studio_weekly_<date>*) within cadence? The batch has
     no live URL, so the committed folder is the truth signal. Disabled until the
     social-engine cloud cutover (the laptop engine does not commit its folder).
  5. Media-worker health: the cloud (Railway) off-laptop video render service is
     trigger-driven, so reachability of its health endpoint IS the signal -- down
     means no video can be produced off the laptop (critical). Config-gated on
     media_worker.health_url.
  6. emails_sent stuck at 0 while the pipeline fills = the agent send rail is dead.
  7. env-truth: the live service should call itself 'production' in production.
  8. Postal auth-state: per inbox account (avi, wd, aipg, agentempire, bookd,
     salesdroid), is the Google OAuth token still 'active'? A revoked token
     (status != 'active') takes that inbox DARK -- no inbound sweep, no CMO
     override reply -- silently. Nothing watched this when all 6 went dark for a
     MONTH (tokens revoked 2026-07-01); reads the same state /postal/status
     exposes. Config-gated (postal_auth.enabled).

Anomaly deduplication:
  Fingerprint-based in watchdog_state. current_state_json() reads this table for
  the poller; the GitHub issue is the alert-level dedup.
"""
from __future__ import annotations

import logging
import base64
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

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


# The Slipstream blog engine reads the next unchecked '- [ ]' topic from each
# brand's queue file (locations are the SoT in config/slipstream_brands.yaml). A
# drained queue makes the brand HOLD SILENTLY -- produce nothing every run -- so
# this check reads queue DEPTH on the same GitHub-Contents-API rail the telemetry
# check uses, and alerts BEFORE the queue empties. Config-gated in watchdog.yaml
# (slipstream_queues.min_unchecked; 0 disables).
_SLIPSTREAM_BRANDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "slipstream_brands.yaml")


def _slipstream_brand_queues() -> List[Tuple[str, str, str]]:
    """(brand_key, queue_repo, queue_path) for every ENABLED Slipstream brand, read
    from config/slipstream_brands.yaml (the single source of truth for queue
    locations -- no duplicated paths in watchdog.yaml). Empty list if unreadable."""
    import yaml
    try:
        with open(_SLIPSTREAM_BRANDS_PATH, "r", encoding="utf-8") as fh:
            full = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:  # pragma: no cover - config always present
        logger.warning("[watchdog] cannot read slipstream_brands.yaml: %s", e)
        return []
    out: List[Tuple[str, str, str]] = []
    for key, b in (full.get("brands") or {}).items():
        if not b.get("enabled"):
            continue
        repo = b.get("queue_repo") or b.get("repo")
        path = b.get("queue_path")
        if repo and path:
            out.append((key, repo, path))
    return out


def _fetch_queue_unchecked_count(repo: str, path: str) -> Optional[int]:
    """Count '- [ ]' unchecked topics in repo/path via the GitHub Contents API,
    the same rail as _check_telemetry_freshness. None if the file cannot be read
    (network / auth / 404): an unreadable queue is logged + skipped, never a false
    alarm. The regex mirrors the engine's _next_topic so the count is exactly what
    the engine would see."""
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}",
                         headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning("[watchdog] slipstream queue fetch failed for %s/%s: %s", repo, path, e)
        return None
    if not r.ok:
        logger.warning("[watchdog] slipstream queue HTTP %s for %s/%s", r.status_code, repo, path)
        return None
    try:
        text = base64.b64decode(r.json().get("content", "")).decode("utf-8")
    except (ValueError, KeyError):
        return None
    return len(re.findall(r"^- \[ \]", text, re.M))


def _check_slipstream_queues(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Alert when a Slipstream brand's topic queue is drained (or nearly). An
    exhausted queue is the SILENT hold behind the 2026-07-31 BAE miss: the engine
    had no unchecked topic, produced nothing, and no rail saw it. This is the
    OUTSIDE check -- it reads the queue file directly, independent of the engine's
    own run receipt -- matching the blog-freshness philosophy. Config-gated:
    slipstream_queues.min_unchecked (0 disables); alerts at <= that many left."""
    if cfg is None:
        cfg = load_watchdog_config()
    sq = cfg.get("slipstream_queues") or {}
    min_unchecked = int(sq.get("min_unchecked") or 0)
    if min_unchecked <= 0:
        return []  # disabled
    sev = sq.get("severity", "warn")
    out: List[Anomaly] = []
    for brand, repo, path in _slipstream_brand_queues():
        count = _fetch_queue_unchecked_count(repo, path)
        if count is None:
            continue  # unreadable -> logged + skipped, never a false alarm
        if count == 0:
            out.append(Anomaly(
                f"slipstream-queue-exhausted-{brand}",
                f"{brand} Slipstream topic queue is EXHAUSTED (0 unchecked topics in "
                f"{repo}/{path}) -- the blog engine will produce NOTHING for this brand "
                f"until it is refilled.", sev))
        elif count <= min_unchecked:
            out.append(Anomaly(
                f"slipstream-queue-low-{brand}",
                f"{brand} Slipstream topic queue is running low ({count} unchecked "
                f"topic(s) left in {repo}/{path}, <= {min_unchecked}); refill soon.", sev))
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


# The two Railway-ported laptop monitors write a dated block into an avo-telemetry
# state file each run; the newest block's date is the freshness signal. Disabled
# (max_age_hours 0) until each job's cloud cutover, then ~30h catches a missed daily.
#
# Each entry carries the cron's (hour, minute) in America/Chicago. The block header
# stamps only a DATE; ageing it from midnight UTC made a HEALTHY daily cadence cross
# the 30h threshold every day (stale ~06:00 UTC, "recovered" when the next block
# landed) -- 3 flap emails/day on issue #5, 2026-07-30..08-05. Anchoring the date at
# the cadence time it was actually written makes max healthy age 24h, so 30h fires
# only on a genuinely missed run.
_CT = ZoneInfo("America/Chicago")
_MONITOR_BLOCKS = {
    "tp_daily": ("team_principal_state.md",
                 re.compile(r"##\s*🏁\s*TP daily\s*[-—]+\s*(\d{4}-\d{2}-\d{2})"),
                 (7, 15)),
    "growth_monitor": ("growth_analytics_state.md",
                       re.compile(r"##\s*📈\s*Outbound monitor\s*[-—]+\s*(\d{4}-\d{2}-\d{2})"),
                       (18, 0)),
}


def _latest_dated_block(path: str, pattern: "re.Pattern") -> Optional[str]:
    """Newest YYYY-MM-DD stamped in `path`'s matching block headers, via the GitHub
    Contents API (SLIPSTREAM_GH_TOKEN reads the private repo; GITHUB/GH_TOKEN are
    unset on Railway). None if unreadable or no block found."""
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(f"https://api.github.com/repos/{TELEMETRY_REPO}/contents/{path}",
                         headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.warning("[watchdog] monitor freshness fetch failed for %s: %s", path, e)
        return None
    if not r.ok:
        logger.warning("[watchdog] monitor freshness HTTP %s for %s", r.status_code, path)
        return None
    try:
        content = base64.b64decode(r.json().get("content", "")).decode("utf-8")
    except (ValueError, KeyError):
        return None
    dates = pattern.findall(content)
    return max(dates) if dates else None


def _check_monitor_freshness(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Did each Railway-ported daily monitor land a fresh block? Config-driven per
    monitor (monitors.<key>_max_age_hours; 0 disables until that job's cutover)."""
    if cfg is None:
        cfg = load_watchdog_config()
    mon = cfg.get("monitors") or {}
    out: List[Anomaly] = []
    for key, (path, pattern, (run_h, run_m)) in _MONITOR_BLOCKS.items():
        max_h = int(mon.get(f"{key}_max_age_hours") or 0)
        if max_h <= 0:
            continue  # disabled until this monitor's cloud cutover
        newest = _latest_dated_block(path, pattern)
        if not newest:
            out.append(Anomaly(f"monitor-freshness-unknown-{key}",
                               f"No dated {key} block found in {path}; cannot verify it ran.", "warn"))
            continue
        try:
            dt = datetime.fromisoformat(newest).replace(hour=run_h, minute=run_m, tzinfo=_CT)
        except ValueError:
            out.append(Anomaly(f"monitor-freshness-unknown-{key}",
                               f"Unparseable {key} date {newest!r}.", "warn"))
            continue
        age_h = (_now_utc() - dt).total_seconds() / 3600
        if age_h > max_h:
            out.append(Anomaly(f"monitor-stale-{key}",
                               f"{key} newest entry is {newest} ({int(age_h)}h old, > {max_h}h) "
                               f"-- the Railway cron may have missed a run.", "warn"))
    return out


def _check_media_worker_health(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Is the cloud media worker (the off-laptop video render service on Railway)
    reachable? It is trigger-driven -- there is no render cadence to age-check --
    so the meaningful signal is simply whether its health endpoint answers 200.
    If it is down, no one can produce a video off the laptop (the whole point of
    the service), so the default severity is critical. Config-driven: runs only
    when media_worker.health_url is set; an absent URL means not-yet-deployed and
    the check skips, matching the disabled-by-default idiom of the other checks.
    """
    if cfg is None:
        cfg = load_watchdog_config()
    mw = cfg.get("media_worker") or {}
    url = (mw.get("health_url") or "").strip()
    if not url:
        return []  # not configured / not deployed yet
    sev = mw.get("severity", "critical")
    try:
        code = _http_status(url)
    except requests.RequestException as e:
        return [Anomaly(
            "media-worker-down",
            f"Cloud media worker health check failed ({type(e).__name__}) at {url} "
            f"-- off-laptop video rendering is unavailable.", sev)]
    if code != 200:
        return [Anomaly(
            f"media-worker-http-{code}",
            f"Cloud media worker returned HTTP {code} at {url} "
            f"-- off-laptop video rendering may be down.", sev)]
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


def _postal_auth_accounts() -> List[dict]:
    """The Postal auth state, read from the SAME internal function the
    /postal/status endpoint uses -- a direct in-process call, never an HTTP hop
    to our own service. Isolated as a seam so tests can mock the status source
    (mirrors _revenue_summary / _current_environment)."""
    from services.postal_oauth import list_connected_accounts
    return list_connected_accounts()


def _check_postal_auth(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Any Postal inbox account whose OAuth status != 'active' (e.g. needs_reauth)
    means inbound Gmail/Postal sweep AND the CMO override reply path are DARK for
    that account. This is the check that was MISSING when all 6 inboxes (avi, wd,
    aipg, agentempire, bookd, salesdroid) silently went dark for a MONTH -- tokens
    revoked 2026-07-01, undetected -- because nothing watched auth-state. Reads the
    same source /postal/status exposes (services.postal_oauth.list_connected_accounts)
    directly in-process. FAIL-SAFE: if that source raises/unreachable, return []
    (a true service-down is covered by paperclip_uptime.yml); never emit a false
    "needs re-auth." Config-gated: postal_auth.enabled (false no-ops the check)."""
    if cfg is None:
        cfg = load_watchdog_config()
    pa = cfg.get("postal_auth") or {}
    if not pa.get("enabled", False):
        return []  # disabled
    sev = pa.get("severity", "warn")
    try:
        accounts = _postal_auth_accounts()
    except Exception as e:  # unreachable DB / import / query failure
        logger.warning("[watchdog] postal auth state unreachable, skipping: %s", e)
        return []  # fail-safe: a real outage is covered by uptime, never a false re-auth
    out: List[Anomaly] = []
    for acct in (accounts or []):
        if (acct.get("status") or "").strip() == "active":
            continue
        label = acct.get("account_label") or "?"
        email = acct.get("email") or "?"
        out.append(Anomaly(
            f"postal-inbox-reauth-{label}",
            f"Postal inbox {label} ({email}) needs re-auth -- inbound mail + CMO "
            f"override dark for that account", sev))
    return out


# ---------------------------------------------------------------------------
# Meta-monitoring: the watchdog-of-watchdogs layer
# ---------------------------------------------------------------------------
#
# Every check above watches an ENGINE's output. Nothing watched the WATCHERS:
# a dead APScheduler serves stale watchdog_state with a fresh checked_at, a
# GitHub-disabled alert workflow stops delivering, and an hourly job that raises
# every run (sonar) is swallowed by the scheduler's error handler. In all three
# cases silence reads as health. The pieces below close that loop:
#
#   - monitor_heartbeats: jobs with no committed output record a row per
#     completed sweep; _check_service_heartbeats ages those rows. run_once()
#     records its own 'watchdog_sweep' heartbeat, which /health/watchdog exposes
#     as swept_at so the ALERT RAIL can detect a dead sweeper (the rail-side
#     synthetic anomaly) -- the GitHub leg watches the Railway leg.
#   - _check_alert_rail: the Railway leg watches the GitHub leg (has each rail
#     workflow SUCCEEDED recently?). A dead rail cannot deliver its own
#     obituary, so run_hourly() sends that one anomaly by direct Resend email.


_HEARTBEAT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS monitor_heartbeats (
    name     TEXT PRIMARY KEY,
    last_run TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def record_heartbeat(name: str) -> None:
    """Upsert `name`'s last-completed-run timestamp. Callers wrap in try/except:
    a heartbeat write must never sink the job it certifies."""
    from services.database import execute_query
    execute_query(_HEARTBEAT_TABLE_SQL)
    execute_query(
        "INSERT INTO monitor_heartbeats (name, last_run) VALUES (%s, NOW()) "
        "ON CONFLICT (name) DO UPDATE SET last_run = NOW()",
        (name,),
    )


def _heartbeat_ts(name: str) -> Optional[datetime]:
    from services.database import fetch_all
    rows = fetch_all("SELECT last_run FROM monitor_heartbeats WHERE name = %s", (name,))
    return rows[0][0] if rows else None


def _check_service_heartbeats(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Age the heartbeat of every service configured under heartbeats.<name>_max_age_hours.
    Catches the failure output checks cannot see: a scheduled job that dies or raises
    every run while producing nothing (sonar's cheap no-new-items exit writes nothing,
    so only its heartbeat proves it swept). 0/absent disables a service."""
    if cfg is None:
        cfg = load_watchdog_config()
    hb = cfg.get("heartbeats") or {}
    out: List[Anomaly] = []
    for key, raw in hb.items():
        if not key.endswith("_max_age_hours"):
            continue
        name = key[: -len("_max_age_hours")]
        max_h = int(raw or 0)
        if max_h <= 0:
            continue
        try:
            ts = _heartbeat_ts(name)
        except Exception as e:  # DB down is covered by the uptime rail; never false-alarm
            logger.warning("[watchdog] heartbeat read failed for %s, skipping: %s", name, e)
            continue
        if ts is None:
            out.append(Anomaly(
                f"service-heartbeat-missing-{name}",
                f"{name} has never recorded a completed run -- its scheduled job may "
                f"never have started.", "warn"))
            continue
        age_h = (_now_utc() - ts).total_seconds() / 3600
        if age_h > max_h:
            out.append(Anomaly(
                f"service-heartbeat-stale-{name}",
                f"{name} last completed a run {int(age_h)}h ago (> {max_h}h) -- its "
                f"scheduled job may be dead or erroring every run.", "warn"))
    return out


def _check_alert_rail(cfg: Optional[dict] = None) -> List[Anomaly]:
    """Has each GitHub Actions rail workflow (the only path from 'anomaly recorded'
    to 'Michael knows') SUCCEEDED recently? GitHub auto-disables schedules after 60
    days of repo inactivity and throttles them under load; either way silence looks
    like health. Threshold must clear observed scheduling drift (the 20-min cron
    really fires every ~2.5h under throttle). API unreachable -> log + skip, never
    a false alarm."""
    if cfg is None:
        cfg = load_watchdog_config()
    ar = cfg.get("alert_rail") or {}
    workflows = ar.get("workflows") or []
    max_h = int(ar.get("max_age_hours") or 0)
    if not workflows or max_h <= 0:
        return []
    repo = ar.get("repo") or TELEMETRY_REPO
    sev = ar.get("severity", "critical")
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
             or os.environ.get("SLIPSTREAM_GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out: List[Anomaly] = []
    for wf in workflows:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs",
                params={"status": "success", "per_page": 1}, headers=headers, timeout=15)
        except requests.RequestException as e:
            logger.warning("[watchdog] alert-rail fetch failed for %s: %s", wf, e)
            continue
        if not r.ok:
            logger.warning("[watchdog] alert-rail HTTP %s for %s", r.status_code, wf)
            continue
        runs = (r.json() or {}).get("workflow_runs") or []
        if not runs:
            out.append(Anomaly(
                f"alert-rail-stale-{wf}",
                f"Alert rail workflow {wf} in {repo} has NO successful runs on record "
                f"-- anomaly delivery may be down.", sev))
            continue
        ts_str = runs[0].get("created_at") or ""
        try:
            run_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            logger.warning("[watchdog] unparseable rail run ts for %s: %r", wf, ts_str)
            continue
        age_h = (_now_utc() - run_ts).total_seconds() / 3600
        if age_h > max_h:
            out.append(Anomaly(
                f"alert-rail-stale-{wf}",
                f"Alert rail workflow {wf} last succeeded {int(age_h)}h ago (> {max_h}h) "
                f"-- anomaly delivery is likely DOWN (disabled/broken workflow or dead "
                f"schedule). This notice was sent by direct email because the rail "
                f"cannot deliver its own obituary.", sev))
    return out


def _email_alert_direct(anomalies: List[Anomaly]) -> bool:
    """Direct Resend email for anomalies the GitHub rail cannot deliver (because the
    anomaly IS the rail being down). Same proven Resend path as sonar escalations.
    Called only for NEW rail anomalies, so one incident = one email."""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key or not anomalies:
        return False
    cfg = load_watchdog_config()
    to = ((cfg.get("alert_rail") or {}).get("notify_email")
          or "michael@automotiveintelligence.io")
    rows = "".join(
        f"<li><code>{a.fingerprint}</code>: {a.human}<br><b>Fix:</b> {_runbook(a.fingerprint)}</li>"
        for a in anomalies)
    try:
        r = requests.post(
            "https://api.resend.com/emails", timeout=15,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"from": os.environ.get("LEAD_ALERT_FROM",
                                         "AVO Watchdog <cmo@mail.automotiveintelligence.io>"),
                  "to": [to],
                  "subject": "[Watchdog] the alert rail itself is DOWN",
                  "html": ("<p>The GitHub-issue alert rail is not delivering, so this "
                           "came direct from the Railway watchdog:</p>"
                           f"<ul>{rows}</ul>")})
        return r.ok
    except requests.RequestException:
        logger.exception("[watchdog] direct rail-down email failed")
        return False


# Solutions attached to every alert: fingerprint-prefix -> concrete remediation.
# First match wins (order matters for overlapping prefixes). The point is that an
# alert lands as problem + proposed fix, not a raw symptom Michael has to triage.
_RUNBOOKS: Tuple[Tuple[str, str], ...] = (
    ("alert-rail-stale", "Open the Actions tab in salesdroid/avo-telemetry: re-enable "
     "the workflow if GitHub auto-disabled it (60-day inactivity rule), else read the "
     "failed run log. Until it is green, anomaly delivery is down."),
    ("service-heartbeat-", "That scheduled job stopped completing runs. For "
     "sonar_inbox: check ZERNIO_API_KEY and Railway logs, then POST "
     "/admin/run-sonar-inbox to reproduce. If MANY heartbeats/monitors are stale at "
     "once, the paperclip APScheduler is dead: restart the paperclip service."),
    ("site-network-", "Probe the URL manually. If the host is down, check its "
     "platform (Vercel: AvI/BAE, GHL: crm.worshipdigital.co, Shopify: P&P). DNS "
     "changes are manual-only. A transient blip self-clears next sweep."),
    ("site-http-", "5xx: redeploy or roll back on the hosting platform. 4xx: the "
     "latest deploy broke a route. 429 is usually transient throttling and "
     "self-clears."),
    ("telemetry-stale", "The daily writers (tp-daily 07:15 CT, growth-monitor 18:00 "
     "CT) stopped committing. Check paperclip logs; backfill with POST "
     "/admin/run-tp-daily and /admin/run-growth-monitor {\"commit\":true}."),
    ("blog-404-", "The sitemap advertises a post that does not serve 200 -- usually "
     "a bad deploy or deleted post. Redeploy the site or fix the sitemap."),
    ("blog-freshness-unknown-", "Sitemap unreadable or carries no /blog/ entries. "
     "Check the site deploy and sitemap generation."),
    ("blog-stale-", "Slipstream MWF engine (14:15 CT). Check Railway logs and "
     "OPENROUTER_API_KEY/fal keys -- a stage-fail HOLDs silently. Reproduce with "
     "POST /admin/run-slipstream (dry). WD ships by human merge-to-main only."),
    ("slipstream-queue-", "Refill the brand's topic queue: add '- [ ]' topics to the "
     "queue file (path in config/slipstream_brands.yaml). An exhausted queue makes "
     "the engine hold silently."),
    ("weekly-social-", "Studio social engine (Sun 16:50 CT). Check Railway logs; "
     "reproduce with POST /admin/run-studio-social (dry)."),
    ("monitor-stale-", "tp-daily (07:15 CT) / growth-monitor (18:00 CT) missed a "
     "run. Check paperclip logs; re-run POST /admin/run-tp-daily or "
     "/admin/run-growth-monitor {\"commit\":true}."),
    ("monitor-freshness-unknown-", "The state file has no dated block or an "
     "unparseable date -- check the file in avo-telemetry and the engine's writer."),
    ("media-worker-", "Restart the media-worker service on Railway and check its "
     "deploy logs. Off-laptop video rendering is down until then."),
    ("emails-sent-zero", "If the SDR desk is LIVE, the send rail is dead: check the "
     "Instantly API key and campaign status. While the desk is in shadow mode this "
     "is expected -- acknowledge it in config/watchdog.yaml (acknowledged:) instead "
     "of muting the rail."),
    ("env-mislabelled", "Set the service env: railway variables --set "
     "APP_ENV=production on paperclip, then redeploy."),
    ("postal-inbox-reauth-", "Re-run the Google OAuth connect flow for that account "
     "(/postal connect). Inbound mail + CMO override stay dark for it until then."),
)


def _runbook(fingerprint: str) -> str:
    for prefix, text in _RUNBOOKS:
        if fingerprint.startswith(prefix):
            return text
    return ""


def _ack_map(cfg: Optional[dict] = None) -> Dict[str, Dict[str, str]]:
    """Still-valid acknowledgements from config: fingerprint -> {until, reason}.
    An ack is an executive decision on record -- 'known condition, no action until
    <date>' -- that keeps the anomaly OFF the email rail without deleting the
    check. Past its until date it resurfaces by itself."""
    if cfg is None:
        cfg = load_watchdog_config()
    today = _now_utc().date().isoformat()
    out: Dict[str, Dict[str, str]] = {}
    for entry in (cfg.get("acknowledged") or []):
        fp = str(entry.get("fingerprint") or "").strip()
        until = str(entry.get("until") or "").strip()
        if fp and until >= today:
            out[fp] = {"until": until, "reason": str(entry.get("reason") or "").strip()}
    return out


# Registry of every check the watchdog runs. Extend here as coverage grows.
_CHECKS = (
    _check_brand_sites,
    _check_telemetry_freshness,
    _check_blog_freshness,
    _check_slipstream_queues,
    _check_weekly_social_freshness,
    _check_monitor_freshness,
    _check_media_worker_health,
    _check_emails_sent,
    _check_env_truth,
    _check_postal_auth,
    _check_service_heartbeats,
    _check_alert_rail,
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
    try:
        # The sweep's own heartbeat: /health/watchdog serves it as swept_at so the
        # alert rail can tell a live sweeper from a dead scheduler serving stale
        # state (checked_at alone is stamped at READ time and proves nothing).
        record_heartbeat("watchdog_sweep")
    except Exception as e:
        logger.warning("[watchdog] sweep heartbeat write failed: %s", e)
    return anomalies, new


def run_hourly() -> None:
    """Scheduler entry point."""
    anomalies, new = run_once()
    # A dead alert rail cannot deliver its own obituary -- that one class of NEW
    # anomaly goes out by direct email. Everything else stays on the rail.
    rail_down = [a for a in new if a.fingerprint.startswith("alert-rail-stale")]
    if rail_down:
        _email_alert_direct(rail_down)
    logger.info(
        "[watchdog] cycle done: %d active, %d new anomalies",
        len(anomalies), len(new),
    )


def _split_acked(rows: List[dict]) -> Tuple[List[dict], List[dict]]:
    """Partition anomaly dicts into (active, acknowledged) per config acks, and
    attach each anomaly's runbook so alerts always carry a proposed fix."""
    acks = _ack_map()
    active: List[dict] = []
    acked: List[dict] = []
    for a in rows:
        a = dict(a)
        a["runbook"] = _runbook(a.get("fingerprint") or "")
        ack = acks.get(a.get("fingerprint") or "")
        if ack:
            a.update(ack)
            acked.append(a)
        else:
            active.append(a)
    return active, acked


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
    swept_at: Optional[str] = None
    try:
        ts = _heartbeat_ts("watchdog_sweep")
        swept_at = ts.isoformat() if ts else None
    except Exception as e:
        logger.warning("[watchdog] swept_at read failed: %s", e)
    active, acked = _split_acked(
        [{"fingerprint": r[0], "human": r[1], "severity": r[2]} for r in rows])
    return {
        "ok": True,
        "checked_at": _now_utc().isoformat(),
        "swept_at": swept_at,
        "active_anomalies": active,
        "acknowledged": acked,
    }


def run_now_json() -> dict:
    """Manual-trigger admin endpoint response."""
    anomalies, new = run_once()
    active, acked = _split_acked(
        [{"fingerprint": a.fingerprint, "human": a.human, "severity": a.severity}
         for a in anomalies])
    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "active_anomalies": active,
        "acknowledged": acked,
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


__all__ = ["Anomaly", "record_heartbeat", "run_once", "run_hourly", "run_now_json",
           "current_state_json"]
