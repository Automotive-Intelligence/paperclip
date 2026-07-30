-- migrations/2026_06_28_persona_wake_tables.sql
-- Persona Cron Loop Phase C1 — wake events + Owner's Brief queue.
--
-- persona_wake_events: audit trail. One row per persona wake cycle. Records
--   what the persona evaluated + decided. Never mutated after write.
-- owner_brief_queue:   per-persona contributions to the daily brief. Composer
--   reads unshipped rows into the 7:30am/6pm email, then stamps ts_sent.
--
-- Both tables are append-only. Rollback = ignore them (nothing depends on
-- their absence). Ledger stays clean.

CREATE TABLE IF NOT EXISTS persona_wake_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona         TEXT NOT NULL,
    wake_ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cadence         TEXT NOT NULL,
    kpis_evaluated  INTEGER NOT NULL DEFAULT 0,
    green_count     INTEGER NOT NULL DEFAULT 0,
    yellow_count    INTEGER NOT NULL DEFAULT 0,
    red_count       INTEGER NOT NULL DEFAULT 0,
    actions_taken   JSONB,
    duration_ms     INTEGER,
    error_detail    TEXT
);
CREATE INDEX IF NOT EXISTS ix_persona_wake_events_persona_ts
    ON persona_wake_events (persona, wake_ts DESC);

CREATE TABLE IF NOT EXISTS owner_brief_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    persona         TEXT NOT NULL,
    brief_window    TEXT NOT NULL,   -- 'morning' | 'evening'
    contribution    TEXT NOT NULL,   -- the persona's paragraph
    red_items       JSONB,           -- structured RED items for top-of-fold
    shipped_items   JSONB,           -- what GREEN-shipped this window
    kpi_movers      JSONB,           -- top 3 deltas vs last cycle
    ts_added        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ts_sent         TIMESTAMPTZ,
    UNIQUE (persona, brief_window, ((ts_added AT TIME ZONE 'UTC')::date))
);
CREATE INDEX IF NOT EXISTS ix_owner_brief_queue_unsent
    ON owner_brief_queue (brief_window, ts_added DESC)
    WHERE ts_sent IS NULL;
