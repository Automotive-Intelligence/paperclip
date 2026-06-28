-- migrations/2026_06_26_kpi_snapshots.sql
-- Persona Cron Loop Phase B — KPI snapshot table.
--
-- Each row is one (persona, kpi_name, [brand]) reading at a point in time.
-- Personas read the LATEST row per (persona, kpi_name, brand) when they wake;
-- never query vendor APIs directly. Clean separation.
--
-- Status taxonomy:
--   ok               — fresh reading, value populated
--   stale            — connector returned cache older than expected
--   no_data          — connector succeeded but had nothing to report
--   connector_down   — connector raised exception (network, auth, etc)
--   rate_limited    — vendor rate-limit hit; retry on next cadence
--   schema_drift    — vendor response shape changed; parser failed
--   timeout         — connector exceeded 30s budget

CREATE TABLE IF NOT EXISTS kpi_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona         TEXT NOT NULL,
    kpi_name        TEXT NOT NULL,
    brand           TEXT,
    value_numeric   NUMERIC,
    value_text      TEXT,
    unit            TEXT,
    ts_collected    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL,
    status          TEXT NOT NULL,
    staleness_sec   INTEGER,
    error_detail    TEXT,
    raw_payload     JSONB
);

-- Lookup: "give me the most recent reading for this KPI" (Phase C hot path).
CREATE INDEX IF NOT EXISTS ix_kpi_snapshots_latest
    ON kpi_snapshots (persona, kpi_name, brand, ts_collected DESC);

-- Partial: surface failing connectors quickly (B&T monitoring lane).
CREATE INDEX IF NOT EXISTS ix_kpi_snapshots_failing
    ON kpi_snapshots (status, ts_collected DESC) WHERE status != 'ok';

-- Run-level grouping: every collector cycle gets a run_id so the brief can
-- say "12/30 connectors green this morning, 3 stale, 1 down."
ALTER TABLE kpi_snapshots ADD COLUMN IF NOT EXISTS run_id UUID;
CREATE INDEX IF NOT EXISTS ix_kpi_snapshots_run ON kpi_snapshots (run_id);
