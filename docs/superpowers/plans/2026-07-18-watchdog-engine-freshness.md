# Watchdog Engine-Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extend the Railway watchdog to catch engine-output silent failures (stale/404 blog, emails_sent stuck at 0, env mislabel), route alerts through a GitHub-issue rail instead of Slack, and drive it all from config.

**Architecture:** Add config-driven checks to `services/watchdog.py` (same `Anomaly` shape), read brand/threshold config from `config/watchdog.yaml` via a cached loader, expose current anomalies at a public `GET /health/watchdog` that reads the `watchdog_state` table, and add a GitHub Actions workflow in avo-telemetry that polls that endpoint and manages a single GitHub issue.

**Tech Stack:** Python 3, FastAPI, requests, PyYAML, Postgres (existing `services.database` helpers), GitHub Actions.

## Global Constraints

- No em-dashes in any authored text (code comments, yaml, workflow, docs).
- Config-driven, never hardcoded to one brand or Michael's accounts (productization north star).
- Keep every existing watchdog behavior; one raising check must not sink the sweep.
- `curl`/`requests`, never `urllib` (macOS SSL). Unit tests mock all network + DB.
- Verify by reading back; built is not done, fired is done.
- Revenue source: `from tools.revenue_tracker import get_revenue_summary` -> dict with keys `emails_sent`, `pipeline_value`, `deals_closed`, `closed_revenue` (+ a prospect count read defensively).
- DB helpers: `from services.database import fetch_all, execute_query`.

---

### Task 1: watchdog config + loader

**Files:**
- Create: `config/watchdog.yaml`
- Create: `config/watchdog_config.py`
- Test: `tests/test_watchdog_config.py`

**Interfaces:**
- Produces: `load_watchdog_config() -> dict` with keys `brands` (dict brand->{sitemap_url, blog_max_age_hours, severity}), `site_urls` (list[str]), `emails_sent` ({window_days, min_prospects_for_alert}), `telemetry` ({repo, max_stale_hours}).

- [ ] **Step 1: Write `config/watchdog.yaml`** (complete, all 7 brands; WD held -> long threshold)

```yaml
# Watchdog coverage config. Brand/threshold specifics live here, not in code,
# so extending to a new business is a config edit, not a rewrite.
brands:
  automotive_intelligence:
    sitemap_url: https://automotiveintelligence.io/sitemap.xml
    blog_max_age_hours: 96
    severity: warn
  ai_phone_guy:
    sitemap_url: https://theaiphoneguy.com/sitemap.xml
    blog_max_age_hours: 96
    severity: warn
  agent_empire:
    sitemap_url: https://buildagentempire.com/sitemap.xml
    blog_max_age_hours: 168
    severity: warn
  worship_digital:
    sitemap_url: https://worshipdigital.co/sitemap.xml
    blog_max_age_hours: 0        # 0 = blog-freshness check disabled (content HELD; Studio owns cadence)
    severity: warn
  paper_and_purpose:
    sitemap_url: https://paperandpurpose.co/sitemap.xml
    blog_max_age_hours: 0        # no blog engine on P&P (GAP per rails ledger)
    severity: warn
  bookd:
    sitemap_url: https://bookd.cx/sitemap.xml
    blog_max_age_hours: 0        # staged, holds for Ryan
    severity: warn
site_urls:
  - https://automotiveintelligence.io
  - https://theaiphoneguy.com
  - https://worshipdigital.co
  - https://crm.worshipdigital.co
  - https://buildagentempire.com
  - https://bookd.cx
  - https://paperandpurpose.co
emails_sent:
  window_days: 7
  min_prospects_for_alert: 25
telemetry:
  repo: salesdroid/avo-telemetry
  max_stale_hours: 48
```

- [ ] **Step 2: Write the failing test** `tests/test_watchdog_config.py`

```python
from config.watchdog_config import load_watchdog_config

def test_config_has_seven_sites_and_brands():
    cfg = load_watchdog_config()
    assert len(cfg["site_urls"]) == 7
    assert "https://theaiphoneguy.com" in cfg["site_urls"]
    assert cfg["brands"]["automotive_intelligence"]["blog_max_age_hours"] == 96
    # held/gap brands disabled via 0
    assert cfg["brands"]["worship_digital"]["blog_max_age_hours"] == 0
    assert cfg["emails_sent"]["min_prospects_for_alert"] == 25
```

- [ ] **Step 3: Run test, expect fail** — `pytest tests/test_watchdog_config.py -v` -> ImportError.

- [ ] **Step 4: Implement `config/watchdog_config.py`**

```python
"""Loader for config/watchdog.yaml. One cached accessor, mirrors config/pricing.py."""
from __future__ import annotations
import os
from functools import lru_cache
import yaml

_PATH = os.path.join(os.path.dirname(__file__), "watchdog.yaml")

@lru_cache(maxsize=1)
def load_watchdog_config() -> dict:
    with open(_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
```

- [ ] **Step 5: Run test, expect pass.** Commit: `git add config/watchdog.yaml config/watchdog_config.py tests/test_watchdog_config.py && git commit -m "feat(watchdog): config + loader for coverage"`

---

### Task 2: blog-freshness check

**Files:**
- Modify: `services/watchdog.py`
- Test: `tests/test_watchdog_freshness.py`

**Interfaces:**
- Consumes: `load_watchdog_config`, `Anomaly`.
- Produces: `_check_blog_freshness(cfg=None) -> List[Anomaly]`; helper `_newest_blog_post(sitemap_xml: str) -> Optional[Tuple[str, str]]` returning `(iso_date, url)` for the newest `/blog/<slug>` entry.

- [ ] **Step 1: Write failing tests** `tests/test_watchdog_freshness.py`

```python
from unittest import mock
from services import watchdog

SITEMAP = """<urlset>
<url><loc>https://x.co/blog</loc><lastmod>2026-07-01</lastmod></url>
<url><loc>https://x.co/blog/fresh-post</loc><lastmod>2026-07-18</lastmod></url>
<url><loc>https://x.co/blog/old-post</loc><lastmod>2026-07-10</lastmod></url>
</urlset>"""

def test_newest_blog_post_picks_latest_slug():
    assert watchdog._newest_blog_post(SITEMAP) == ("2026-07-18", "https://x.co/blog/fresh-post")

def test_no_slug_entries_returns_none():
    assert watchdog._newest_blog_post("<urlset></urlset>") is None

def _cfg(hours):
    return {"brands": {"b": {"sitemap_url": "https://x.co/sitemap.xml",
            "blog_max_age_hours": hours, "severity": "warn"}}}

def test_fresh_post_within_threshold_no_anomaly():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026,7,18,20,tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=200):
        assert watchdog._check_blog_freshness(_cfg(96)) == []

def test_stale_post_flags():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026,7,25,tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=200):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-stale-b" for a in out)

def test_newest_post_404_flags_critical():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP), \
         mock.patch.object(watchdog, "_now_utc",
             return_value=watchdog.datetime(2026,7,18,20,tzinfo=watchdog.timezone.utc)), \
         mock.patch.object(watchdog, "_http_status", return_value=404):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-404-b" and a.severity == "critical" for a in out)

def test_disabled_when_hours_zero():
    with mock.patch.object(watchdog, "_fetch_text", return_value=SITEMAP):
        assert watchdog._check_blog_freshness(_cfg(0)) == []

def test_unparseable_sitemap_flags_unknown():
    with mock.patch.object(watchdog, "_fetch_text", return_value="<urlset></urlset>"):
        out = watchdog._check_blog_freshness(_cfg(96))
        assert any(a.fingerprint == "blog-freshness-unknown-b" for a in out)
```

- [ ] **Step 2: Run, expect fail** — `pytest tests/test_watchdog_freshness.py -v`.

- [ ] **Step 3: Implement in `services/watchdog.py`** (add near the checks; introduce tiny seams `_fetch_text`, `_http_status`, `_now_utc` so tests mock cleanly)

```python
import re

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _fetch_text(url: str, timeout: int = 15) -> str:
    r = requests.get(url, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text

def _http_status(url: str, timeout: int = 15) -> int:
    return requests.get(url, timeout=timeout, allow_redirects=True).status_code

def _newest_blog_post(sitemap_xml: str) -> Optional[Tuple[str, str]]:
    best = None
    for entry in re.findall(r"<url>(.*?)</url>", sitemap_xml, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", entry)
        lm = re.search(r"<lastmod>(.*?)</lastmod>", entry)
        if not loc or "/blog/" not in loc.group(1) or not lm:
            continue
        d = lm.group(1).strip()[:10]
        if best is None or d > best[0]:
            best = (d, loc.group(1).strip())
    return best

def _check_blog_freshness(cfg=None) -> List[Anomaly]:
    from config.watchdog_config import load_watchdog_config
    cfg = cfg or load_watchdog_config()
    out: List[Anomaly] = []
    for brand, b in (cfg.get("brands") or {}).items():
        max_h = int(b.get("blog_max_age_hours") or 0)
        if max_h <= 0:
            continue  # disabled (held/gap brand)
        sev = b.get("severity", "warn")
        try:
            xml = _fetch_text(b["sitemap_url"])
        except requests.RequestException as e:
            out.append(Anomaly(f"blog-freshness-unknown-{brand}",
                f"Cannot fetch sitemap for {brand}: {type(e).__name__}", sev))
            continue
        newest = _newest_blog_post(xml)
        if not newest:
            out.append(Anomaly(f"blog-freshness-unknown-{brand}",
                f"No per-post /blog/ entries in {brand} sitemap; cannot verify freshness.", sev))
            continue
        d, url = newest
        try:
            post_dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            out.append(Anomaly(f"blog-freshness-unknown-{brand}",
                f"Unparseable newest post date for {brand}: {d!r}", sev))
            continue
        age_h = (_now_utc() - post_dt).total_seconds() / 3600
        if age_h > max_h:
            out.append(Anomaly(f"blog-stale-{brand}",
                f"{brand} newest blog post is {int(age_h)}h old (> {max_h}h): {url}", sev))
        if _http_status(url) != 200:
            out.append(Anomaly(f"blog-404-{brand}",
                f"{brand} sitemap advertises a post that does not serve 200: {url}", "critical"))
    return out
```

- [ ] **Step 4: Run, expect pass.**

- [ ] **Step 5: Commit** `git add services/watchdog.py tests/test_watchdog_freshness.py && git commit -m "feat(watchdog): live blog-freshness check (stale + 404 + unknown)"`

---

### Task 3: emails-sent-stuck check

**Files:** Modify `services/watchdog.py`; Test `tests/test_watchdog_emails.py`.

**Interfaces:** Produces `_check_emails_sent(cfg=None) -> List[Anomaly]`.

- [ ] **Step 1: Failing test**

```python
from unittest import mock
from services import watchdog

def _cfg(): return {"emails_sent": {"window_days": 7, "min_prospects_for_alert": 25}}

def test_zero_emails_with_pipeline_flags():
    summ = {"emails_sent": 0, "prospects_created": 40}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        out = watchdog._check_emails_sent(_cfg())
        assert any(a.fingerprint == "emails-sent-zero" for a in out)

def test_zero_emails_but_empty_pipeline_no_flag():
    summ = {"emails_sent": 0, "prospects_created": 3}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        assert watchdog._check_emails_sent(_cfg()) == []

def test_emails_flowing_no_flag():
    summ = {"emails_sent": 12, "prospects_created": 40}
    with mock.patch.object(watchdog, "_revenue_summary", return_value=summ):
        assert watchdog._check_emails_sent(_cfg()) == []
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
def _revenue_summary(days: int) -> dict:
    from tools.revenue_tracker import get_revenue_summary
    return get_revenue_summary(days=days) or {}

def _check_emails_sent(cfg=None) -> List[Anomaly]:
    from config.watchdog_config import load_watchdog_config
    cfg = cfg or load_watchdog_config()
    es = cfg.get("emails_sent") or {}
    days = int(es.get("window_days", 7))
    floor = int(es.get("min_prospects_for_alert", 25))
    summ = _revenue_summary(days)
    if summ.get("error"):
        return []
    prospects = int(summ.get("prospects_created") or summ.get("total_prospects") or 0)
    if int(summ.get("emails_sent") or 0) == 0 and prospects > floor:
        return [Anomaly("emails-sent-zero",
            f"emails_sent is 0 over {days}d while {prospects} prospects were created "
            f"-- the agent send rail may be dead.", "warn")]
    return []
```

- [ ] **Step 4: Run, expect pass.** **Step 5: Commit.**

---

### Task 4: wire checks in, remove Slack, add env-truth, config-drive site URLs

**Files:** Modify `services/watchdog.py`; Test `tests/test_watchdog_wiring.py`.

- [ ] **Step 1: Failing tests**

```python
from unittest import mock
from services import watchdog

def test_slack_function_removed():
    assert not hasattr(watchdog, "_post_to_slack")

def test_run_once_does_not_post_slack(monkeypatch):
    monkeypatch.setattr(watchdog, "_all_anomalies", lambda: [watchdog.Anomaly("x","y","warn")])
    monkeypatch.setattr(watchdog, "_active_fingerprints", lambda: set())
    monkeypatch.setattr(watchdog, "_record_active", lambda a: None)
    # requests.post must never be called now
    monkeypatch.setattr(watchdog.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("slack called")))
    watchdog.run_once()

def test_all_anomalies_includes_new_checks():
    names = {c.__name__ for c in watchdog._CHECKS}
    assert "_check_blog_freshness" in names
    assert "_check_emails_sent" in names
    assert "_check_env_truth" in names
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**
  - Delete `_post_to_slack` and the `SLACK_CHANNEL`/`SLACK_API` constants and the `if new: _post_to_slack(new)` line in `run_once`.
  - Replace `BRAND_URLS` usage in `_check_brand_sites` to read `load_watchdog_config()["site_urls"]`.
  - Add `_check_env_truth`:

```python
def _check_env_truth() -> List[Anomaly]:
    try:
        r = requests.get("http://127.0.0.1:8080/health", timeout=8)
        env = (r.json() or {}).get("environment")
    except Exception:
        return []
    if env and env != "production":
        return [Anomaly("env-mislabelled",
            f"Live service reports environment={env!r} (expected 'production').", "warn")]
    return []
```
  (Note: resolve the in-process health read without a self HTTP call if a helper exists; otherwise read `SETTINGS.environment` directly. Confirm during implementation.)
  - Introduce `_CHECKS = (_check_brand_sites, _check_telemetry_freshness, _check_blog_freshness, _check_emails_sent, _check_env_truth)` and have `_all_anomalies` iterate `_CHECKS`.

- [ ] **Step 4: Run, expect pass.** **Step 5: Commit.**

---

### Task 5: GET /health/watchdog endpoint

**Files:** Modify `services/watchdog.py` (add `current_state_json`); Modify `app.py` (add route); Test `tests/test_watchdog_endpoint.py`.

**Interfaces:** Produces `current_state_json() -> dict` reading `watchdog_state` (no fresh sweep).

- [ ] **Step 1: Failing test**

```python
from unittest import mock
from services import watchdog

def test_current_state_json_reads_table():
    rows = [("blog-stale-b", "b stale", "warn", None, None)]
    with mock.patch("services.database.fetch_all", return_value=rows):
        js = watchdog.current_state_json()
        assert js["ok"] is True
        assert js["active_anomalies"][0]["fingerprint"] == "blog-stale-b"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement in `services/watchdog.py`**

```python
def current_state_json() -> dict:
    """Read-only snapshot of active anomalies from watchdog_state (no fresh sweep)."""
    try:
        from services.database import fetch_all
        rows = fetch_all(
            "SELECT fingerprint, human, severity FROM watchdog_state ORDER BY severity DESC, fingerprint")
    except Exception as e:
        return {"ok": False, "error": str(e), "active_anomalies": []}
    return {
        "ok": True,
        "checked_at": _now_utc().isoformat(),
        "active_anomalies": [
            {"fingerprint": r[0], "human": r[1], "severity": r[2]} for r in rows
        ],
    }
```

- [ ] **Step 4: Add route in `app.py`** (near other `/health*` routes)

```python
@app.get("/health/watchdog")
async def health_watchdog():
    from services.watchdog import current_state_json
    return JSONResponse(await asyncio.to_thread(current_state_json))
```

- [ ] **Step 5: Run tests + import-check app.** **Step 6: Commit.**

---

### Task 6: GitHub Actions alert rail (avo-telemetry repo)

**Files:** Create `~/avo-telemetry/.github/workflows/watchdog.yml` (separate repo; committed there, not in this PR).

- [ ] **Step 1:** Write `watchdog.yml` modeled exactly on `paperclip_uptime.yml`: schedule `*/20 * * * *` + `workflow_dispatch`; `permissions: issues: write, contents: read`; probe `GET https://paperclip-production-ba14.up.railway.app/health/watchdog`; parse `active_anomalies`; if non-empty open/update issue titled `AVO Watchdog: anomalies` with the anomaly list in the body; if empty and an issue is open, comment RECOVERED and close.

- [ ] **Step 2:** Commit in avo-telemetry: `git add .github/workflows/watchdog.yml && git commit -m "watchdog: GitHub-issue alert rail polling /health/watchdog"`; push.

- [ ] **Step 3:** Trigger via `gh workflow run watchdog.yml` and confirm a run appears.

---

### Task 7: End-to-end verification (the proof)

- [ ] Run the full watchdog against live sitemaps locally with Doppler env: confirm no false anomalies today (AvI/AIPG/BAE fresh, WD/P&P/Book'd disabled).
- [ ] Temporarily set `automotive_intelligence.blog_max_age_hours: 1`, run, confirm `blog-stale-automotive_intelligence` appears in `current_state_json()`; restore.
- [ ] After deploy: `curl .../health/watchdog` returns the snapshot; `gh workflow run watchdog.yml` opens a GitHub issue (email lands); restore threshold; next run closes it with RECOVERED.
- [ ] Record receipt (endpoint output + issue URL) in `marketing_deliverables/130_operating_machines/`.

## Self-Review

- Spec coverage: blog-freshness (T2), 404 (T2), unknown (T2), emails_sent (T3), env-truth (T4), 7 sites + Slack removal + config-driven (T1,T4), GET endpoint (T5), GH rail (T6), config (T1), proof (T7). All covered.
- Placeholder scan: env-truth in-process read flagged to confirm during impl (acceptable: two concrete options given). No TODO/TBD.
- Type consistency: `Anomaly(fingerprint, human, severity)` used consistently; `_check_*` return `List[Anomaly]`; `_CHECKS` tuple; `current_state_json`/`run_now_json` distinct (snapshot vs fresh).
