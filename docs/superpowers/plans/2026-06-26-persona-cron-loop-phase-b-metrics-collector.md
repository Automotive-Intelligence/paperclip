# Persona Cron Loop — Phase B: Metrics Collector

**Initiative owner:** Build & Tech
**Phase:** B (Phase A KPI scorecards = PR #70, awaiting target lock)
**Date:** 2026-06-26
**Status:** PLAN DRAFT — needs greenlight before connector code lands
**Standing practice (locked 2026-06-25):** writing-plans → subagent-driven-development → verification-before-completion

## Goal

Build the connector layer that auto-populates every Phase A KPI's current value from its source-of-truth system. When a persona wakes on cron, it reads a snapshot, not the raw vendor APIs. Snapshot is freshness-tagged so a stale connector doesn't silently feed a stale scorecard to a decision-making executive.

## What this phase produces

1. `services/metrics_collector.py` — runner that iterates all scorecard YAMLs, calls per-connector functions, writes `kpi_snapshots` rows to Postgres
2. `services/metric_connectors/` — one module per named source. Pure functions: `def fetch(kpi_spec: dict) -> KPIReading`. No side effects beyond the named API call.
3. `migrations/2026_06_26_kpi_snapshots.sql` — `kpi_snapshots` table (persona, kpi_name, value, unit, ts_collected, source, status, error_detail)
4. APScheduler job entries — one per cadence (hourly / every_4h / twice_daily / daily / weekly), pulls only KPIs at that cadence
5. Backfill of any KPIs with available history (Postgres logs go back, GA4 has 14mo, GSC has 16mo)

Snapshot table is the contract: Phase C personas read from it, never directly from vendor APIs. Clean separation.

## What this phase does NOT do

- Does not wake any persona on cron (Phase C)
- Does not write the adversarial reviewer (Phase C)
- Does not generate the Owner's Brief (Phase C)
- Does not change Phase A scorecard targets (those are Michael's call)

Pure data layer. Ships dark — collector runs in background, results visible only by querying `kpi_snapshots`. Personas don't read it until Phase C.

## Connector inventory (~30 distinct sources across all 9 scorecards)

Grouping by lift, recommend shipping in **4 sub-phases**:

### B1 — Already-wired connectors (~1-2 days)

These either exist or use existing tools. Ship first to prove the collector pattern with zero new vendor integrations.

| Connector | Source | Already-have? | Used by |
|---|---|---|---|
| `postgres_agent_logs` | Internal Postgres `agent_logs` table | ✅ | B&T (agent_error_rate, agent_run_completion_rate) |
| `agent_handoffs` | Internal Postgres `agent_handoffs` table | ✅ | B&T (time_to_resolution), Pit Wall |
| `cto_daily_sweep` | `infrastructure_state.md` parser | ✅ shipped | B&T (domain_ssl_green_rate, api_uptime) |
| `ga4_per_property` | `~/cd-ops/pull_ga4.py` already wired | ✅ | Internal Marketing, CMG, B2B Ops |
| `twenty_opportunity_log` | `tools/twenty.py` we just shipped | ✅ shipped | CRO (pipeline, opportunities, velocity) |
| `twenty_pipeline_join` | `tools/twenty.py` aggregated | ✅ | CRO (pipeline_coverage_ratio) |
| `twenty_client_status` | `tools/twenty.py` filter | ✅ | B2B Ops (churn) |
| `github_actions` | gh CLI / GitHub API | ✅ | B&T (deploy_frequency) |
| `doppler_audit` | doppler CLI | ✅ | B&T (secrets_rotation_compliance) |
| `vercel_inventory` | existing daily sweep | ✅ | B&T (api_uptime contribution) |
| `red_tier_queue` | Internal Postgres view | ✅ trivial | Pit Wall |
| `pit_wall_decision_log` | Internal Postgres table | ✅ trivial | Pit Wall |

**B1 ships:** ~12 connectors, ~25 KPIs covered. Strong foundation.

### B2 — Vendor APIs we already have keys for (~2 days)

| Connector | Source | Key in Doppler? | Used by |
|---|---|---|---|
| `gsc_per_brand` | Google Search Console API | per memory yes (CMO sweep tooling) | CMO, Internal Marketing |
| `stripe_arr` + `stripe` | Stripe API | ✅ | CRO (avo_mrr), B2B Ops (payments) |
| `loops` + `loops_per_brand` | Loops API | ✅ | CMO (newsletter), Internal Marketing |
| `instantly_per_campaign` | Instantly API (`tools/instantly.py`) | ✅ existing tool | CRO (cold_email_reply_rate) |
| `instantly_mailbox_health` | Instantly API mailbox endpoint | ✅ same client | CRO |
| `anthropic_usage_api` | Anthropic Console API | ✅ | B&T (token_budget_burn_rate) |
| `zernio_per_platform_per_brand` | Zernio MCP per `reference_zernio` | ✅ MCP wired | CMO, Internal Marketing |
| `higgsfield_log` | `tools/higgsfield.py` log table | ✅ | CMO, Internal Marketing |
| `buffer_higgsfield_zernio_join` | join over the 3 above | ✅ | CMO (content_velocity) |

**B2 ships:** ~9 connectors, ~15 more KPIs covered.

### B3 — Net-new connectors (~2-3 days)

| Connector | Source | Status | Used by |
|---|---|---|---|
| `skool_api` | Skool API (or scrape) | needs auth | Agent Empire |
| `youtube_data_api` | YouTube Data API v3 | needs OAuth/key | Agent Empire |
| `gmb_reviews_per_brand` | Google My Business API | per-brand auth | Internal Marketing, B2B Ops |
| `platform_reviews` | per-platform (Yelp, Trustpilot, etc) | TBD scope | Internal Marketing |
| `wordpress_ghost_per_brand` | WP REST API + Ghost API per brand site | per-site | Internal Marketing (blog_publish) |
| `shopify_pp` | Shopify Admin API for P&P | ✅ key exists | CMG (presell_orders, conversion) |
| `riverside_log` | Riverside doesn't have API → manual log | manual entry | Agent Empire |
| `ga4_property_audit` | GA4 admin / property health | extends existing GA4 tool | B2B Ops |
| `imessage_log` | osascript or local SQLite read | local only | CMG (client_response_time) |

**B3 ships:** ~9 connectors, ~12 more KPIs covered. Some (Skool, YouTube, GMB) require Michael authenticating once.

### B4 — Internal aggregators + WEND (~1-2 days)

These compose over other connectors or need Michael alignment first.

| Connector | Status | Used by |
|---|---|---|
| `scorecard_aggregator` | Composes over all per-persona snapshots → portfolio index | Pit Wall (portfolio_health_index) |
| `client_health_aggregator` | Composes Twenty status + Stripe + comms sentiment | B2B Ops (client_health) |
| `comms_sentiment_aggregator` | LLM-judge over email/iMessage logs | CMG (client_satisfaction), B2B Ops |
| `content_review_logs` + `content_review_logs_pp` | New Postgres table — every agent-generated outbound content reviewed before publish | CMO + Internal Marketing + CMG (brand_voice_compliance) |
| `telemetry_strategic_calls` | Parser over `strategic_calls.md` in avo-telemetry | Pit Wall |
| `agent_handoffs_cross_persona` | View over `agent_handoffs` filtered cross-persona | Pit Wall |
| `nova_conversation_logs` | WEND-side | **needs Michael walk-through** (Customer Advocate scorecard) |
| `aata_audit_log` | WEND-side | **needs Michael walk-through** |
| `post_chat_survey` | WEND-side, may not exist yet | **needs Michael walk-through** |
| `dealer_integration_status` | WEND-side, pre-launch | **deferred to WEND go-live** |

**B4 ships:** ~10 connectors. WEND-side deferred to dedicated session.

## Snapshot schema (`kpi_snapshots`)

```sql
CREATE TABLE kpi_snapshots (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  persona         TEXT NOT NULL,           -- e.g. 'cmo', 'cro'
  kpi_name        TEXT NOT NULL,           -- e.g. 'weekly_qualified_leads_organic'
  brand           TEXT,                    -- nullable; for per_brand KPIs
  value_numeric   NUMERIC,                 -- when KPI is numeric
  value_text      TEXT,                    -- when KPI is symbolic / categorical
  unit            TEXT,                    -- 'leads/week', '%', etc.
  ts_collected    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source          TEXT NOT NULL,           -- the connector name
  status          TEXT NOT NULL,           -- 'ok', 'stale', 'connector_down', 'no_data'
  staleness_sec   INTEGER,                 -- how old is the underlying data
  error_detail    TEXT,                    -- when status != 'ok'
  raw_payload     JSONB                    -- optional — what the connector saw
);

CREATE INDEX ON kpi_snapshots (persona, kpi_name, ts_collected DESC);
CREATE INDEX ON kpi_snapshots (status) WHERE status != 'ok';
```

Phase C reads this via a `latest_snapshot(persona, kpi_name)` function — always returns most recent, regardless of staleness. Persona's prompt is fed `(value, staleness_sec, status)` so it can act on stale-data with appropriate caution.

## Cadence / scheduling

APScheduler entries (one per cadence bucket, not per KPI):

| Cadence | Schedule (CDT) | KPIs included |
|---|---|---|
| hourly | every hour at :15 | CRO hourly KPIs, B&T token burn, agent_error_rate |
| every_4h | 06:15, 10:15, 14:15, 18:15, 22:15 | B&T uptime, error/completion |
| daily | 06:00 | most KPIs |
| weekly | Mon 05:30 | trend / aggregate KPIs |
| monthly | 1st of month 05:00 | MRR, podcast episodes, churn |

Collector runs offset 15min before persona wake — Phase C personas wake at :30/:00 so the snapshot is fresh when they read.

## Failure modes + handling

- **Connector down**: snapshot row written with `status='connector_down'` + error_detail. Persona sees stale value with status flag, can decide to act with caution or post a flag to B&T.
- **Vendor API rate-limit**: backoff + retry with jitter; on persistent fail, write `status='rate_limited'`.
- **Schema drift** (vendor changes response shape): connector function raises typed exception, logger captures, snapshot row written with `status='schema_drift'`.
- **Slow connector** (>30s): timeout, write `status='timeout'`. Don't block the collector run.
- **No data** (e.g. new property with no traffic yet): `status='no_data'`, value NULL.

Persona reads (status, value, ts_collected) tuple. Adversarial reviewer in Phase C will explicitly question decisions made on `status != 'ok'` snapshots.

## Verification gates between sub-phases

Per verification-before-completion standing practice:

- **B1 ship**: query `kpi_snapshots` and confirm B&T's 8 KPIs all populating cleanly for 24 hours. Compare against manually-computed values from `agent_logs` to verify accuracy.
- **B2 ship**: same — 24h clean populate. Cross-check Loops open-rate from snapshot vs Loops dashboard.
- **B3 ship**: 24h clean. Michael spot-checks 2-3 (Skool member count, YouTube subs) against his actual dashboards.
- **B4 ship**: aggregator results match hand-computed for 1 sample (e.g. portfolio_health_index computed via SQL matches the connector's output).

Only after B1-B4 all green does Phase C start.

## Risk / tradeoffs

1. **Vendor API churn**: 9 brands × ~6 vendors = lots of surface. Each vendor changing their API breaks a connector. Mitigation: schema_drift status flag + connector_drift_agent (spawns on persistent drift, posts flag to B&T).
2. **Token cost from anthropic_usage_api polling**: cheap (admin endpoint, not generation). Negligible.
3. **GMB / Google Business Profile API approval**: takes 1-3 days for Google to approve; if blocked, that KPI runs on a manual-update path until approved.
4. **iMessage log**: only readable from Michael's local Mac. Either runs as a local agent that publishes to the collector, or we drop that specific KPI source.
5. **Snapshot table volume**: ~52 KPIs × cadences avg every 6h × 6 brand-fans = ~500 rows/day. Trivial.

## Open questions for Michael (Phase B gate)

1. **WEND/Customer Advocate connectors** — defer entirely until we sit together on what the live metric surface is, or skip the scorecard for Customer Advocate in Phase C v1 and add it in v2?
2. **iMessage connector** — accept that this requires a local-Mac agent (more infra) or drop iMessage-source KPIs (cmg.client_response_time degrades)?
3. **content_review_logs table** — new table, Phase B writes. Confirm there's an existing process or agent doing pre-publish content review that we hook into vs building net-new?
4. **GMB API approval** — want me to file the Google Business Profile API access request now (1-3 day approval) so it's ready by the time B3 ships?
5. **Connector budget guard** — global daily ceiling on vendor API calls (avoid surprise overage) — pick a number, or set after first week of data?

## What ships in this PR

Just this plan. Zero code. Zero runtime impact. Pure design artifact for your review.

After greenlight, B1 lands as its own PR (~1-2 days), then B2, B3, B4 in sequence with verification gates between.

## Rollback

Plan-only PR. Revert = revert.
