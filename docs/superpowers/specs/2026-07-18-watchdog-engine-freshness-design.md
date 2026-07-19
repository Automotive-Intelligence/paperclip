# Watchdog: engine-output freshness + rail-based alerting

**Seat:** Build & Tech #2 (Hardening Crew)
**Date:** 2026-07-18
**Status:** Approved design (TP green-lit 2026-07-18). Refined after live probing.
**Pillars:** 2 (watchdog catches silent failure), 3 (verification), with a Pillar-4 truth-check folded in.

## Problem

The single worst failure mode is "ran, exited 0, shipped nothing" or the subtler variant seen today: "ran, exited 0, shipped SOMETHING but cannot tell us what." On 2026-07-18 the blog engine published 07-18 posts live to AvI, AIPG, and BAE (verified HTTP 200) but died before writing its success receipt and exited 0. The receipt said failure, the live sites said success, nobody reconciled them, and no human was paged.

Two watchdogs exist. Neither watches engine OUTPUT:
- `services/watchdog.py` (Railway, hourly): checks 6 brand-site HTTP statuses + avo-telemetry commit freshness. Alerts to Slack. Does NOT check whether any content actually published.
- `~/.local/bin/avo-watchdog.sh` (laptop): watches 1 job; alert path off.

TP directive: alerting must be **on a rail, not Slack**.

## Goals

1. The Railway watchdog detects when a brand's blog has gone stale on the LIVE site (no fresh post within its expected cadence), and when the newest advertised post 404s.
2. It detects when `emails_sent` is stuck at 0 over the revenue window (the standing silent failure).
3. It covers all 7 brand surfaces (adds theaiphoneguy.com, currently absent).
4. Alerts route through a rail (GitHub issue -> email Michael), reusing the proven `paperclip_uptime.yml` pattern. No Slack.
5. Everything brand/threshold-specific lives in config, not hardcoded (productization north star: turning "runs our business" into "runs any business" is a config change, not a rewrite).

## Non-goals (YAGNI)

- Not migrating the engines themselves (next increment; this makes that migration observable first).
- Not touching the RevOps jobs, the scheduler, or any send path.
- Not building multi-tenant config. One `config/watchdog.yaml` for our brands.
- Not scheduling `telemetry_truthpass.py` as its own rail. Its one valuable automated check (a surface claiming something a live probe contradicts) is folded into the watchdog; the script stays a manual human-audit tool.

## Design

### Detection (extends `services/watchdog.py`, same `Anomaly` shape)

**`_check_blog_freshness(cfg)`** — for each configured brand:
1. GET the brand's `sitemap_url`; parse `<url>` entries whose `<loc>` contains `/blog/<slug>` (a real post, not the index).
2. Take the newest `<lastmod>` date. If none parseable, emit a `blog-freshness-unknown-<brand>` warn anomaly (we cannot see the signal; that itself is worth knowing, never a silent pass).
3. If newest post age > `max_age_hours`, emit `blog-stale-<brand>` (severity from config; default warn).
4. Verify the newest post `<loc>` returns 200. If not, emit `blog-404-<brand>` critical (sitemap advertises a post the site does not serve).

Verified feasible 2026-07-18 against AvI (16 posts, newest 07-18), AIPG (18, 07-18), BAE (07-18), all 200.

**`_check_emails_sent(cfg)`** — call the existing revenue KPI path (`_team_revenue_kpis` / the `/revenue` aggregate) for the configured window; if `emails_sent == 0` while `prospects_created > cfg.min_prospects_for_alert`, emit `emails-sent-zero` warn. Guarded so an empty pipeline does not alarm.

**Truth-check fold-in (small):** `_check_env_truth()` reads the service's own `/health` and flags if `environment != "production"` on the prod deploy (`env-mislabelled`). One check, cheap, closes the Pillar-4 gap that truthpass would have flagged.

All new checks register in `_all_anomalies()` and are wrapped so one raising does not sink the others (existing pattern).

### Alerting (rail, not Slack)

- **Remove** `_post_to_slack` and its call in `run_once`. Detection no longer delivers; it records state.
- **Keep** the Postgres `watchdog_state` table: it becomes the store the GET endpoint reads (now justified, not just dedup).
- **Add** `GET /health/watchdog` (public, read-only, cheap): returns the current active anomalies from `watchdog_state` plus `checked_at` and coverage. No fresh sweep on GET (avoids abuse); the hourly job populates state.
- **Keep** `POST /admin/run-watchdog` (authed) for manual fresh runs / debugging.
- **Add** `avo-telemetry/.github/workflows/watchdog.yml`: clone of `paperclip_uptime.yml`. Every ~20 min it GETs `/health/watchdog`; if `active_anomalies` is non-empty it opens/updates a single GitHub issue "AVO Watchdog: anomalies"; when empty it comments RECOVERED and closes. GitHub emails Michael on issue creation. Alert lifecycle (dedup, recovery, audit trail) lives in the issue, which is why the hourly job no longer needs Slack.

### Config: `config/watchdog.yaml`

Follows the `config/pricing.yaml` + `config/pricing.py` precedent (yaml + a cached loader). Shape:

```yaml
brands:
  automotive_intelligence:
    sitemap_url: https://automotiveintelligence.io/sitemap.xml
    blog_max_age_hours: 96      # cadence ~ every 2 days + headroom
    severity: warn
  ai_phone_guy:
    sitemap_url: https://theaiphoneguy.com/sitemap.xml
    blog_max_age_hours: 96
    severity: warn
  # ... worship_digital, agent_empire, bookd, paper_and_purpose (per-brand; some HELD -> longer/none)
site_urls:                       # the 7 HTTP-health surfaces
  - https://automotiveintelligence.io
  - https://theaiphoneguy.com
  - ...
emails_sent:
  window_days: 7
  min_prospects_for_alert: 25
```

Brands whose blog is intentionally held (WD today) get a long/omitted threshold in config, not a code special-case. That choice is surfaced to the TP/Studio, not decided here.

## Data flow

hourly scheduler -> `run_once()` -> `_all_anomalies()` (sites + telemetry + blog-freshness + emails-sent + env-truth) -> upsert `watchdog_state`. Separately, GitHub Action (every ~20m) -> `GET /health/watchdog` -> reads `watchdog_state` -> opens/closes one GitHub issue -> email to Michael.

## Error handling

- Any single check raising is caught in `_all_anomalies()`; the sweep continues (existing behavior).
- Sitemap fetch failure -> `blog-freshness-unknown-<brand>` warn (never a silent pass).
- DB unavailable on GET -> endpoint returns `{"ok": false, "error": "..."}` with 200 so the Action can distinguish "watchdog itself is broken" (which the uptime watcher already covers via /health/ready).

## Testing

TDD, `tests/test_watchdog_freshness.py`:
1. `_check_blog_freshness` on a fixture sitemap with a fresh per-post lastmod -> no anomaly (mock the 200 verify).
2. Fixture with newest lastmod older than threshold -> `blog-stale-<brand>`.
3. Fixture where newest post URL returns 404 -> `blog-404-<brand>` critical.
4. Sitemap with no `/blog/<slug>` entries -> `blog-freshness-unknown-<brand>`.
5. `_check_emails_sent`: emails_sent=0 with prospects above floor -> anomaly; below floor -> none.
6. `run_once` no longer calls Slack (assert `_post_to_slack` gone / not invoked).
7. Config loader parses `config/watchdog.yaml`.

All network/DB calls mocked in unit tests. Real-network verification is the end-to-end proof below, not a unit test.

## Definition of done (the proof, per the brief: built is not done, fired is done)

1. Unit tests pass.
2. On a deploy of this branch (or a local run against live sitemaps), temporarily set one brand's `blog_max_age_hours` to 1, confirm `GET /health/watchdog` returns a `blog-stale-<brand>` anomaly, confirm the GitHub Action opens an issue and the email lands, then restore the threshold and confirm the next run comments RECOVERED and closes the issue.
3. Record the receipt (the issue URL + endpoint output) in the audit folder.
