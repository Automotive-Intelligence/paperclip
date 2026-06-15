-- migrations/2026_06_14_ape_tables.sql
-- Extends agent_handoffs and adds APE-specific tables.

-- 1. New columns on agent_handoffs for APE lifecycle tracking.
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_session_id TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_status TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_impact_tier TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_reversibility TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_undo_command TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_audit_envelope JSONB;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_started_at TIMESTAMPTZ;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS ape_completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_agent_handoffs_ape_status ON agent_handoffs(ape_status);

-- 2. Pre/post metric snapshots for ship health correlation.
CREATE TABLE IF NOT EXISTS autonomous_ship_telemetry (
    id BIGSERIAL PRIMARY KEY,
    handoff_id BIGINT NOT NULL REFERENCES agent_handoffs(id),
    ship_id TEXT NOT NULL,
    persona TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    pre_value DOUBLE PRECISION,
    post_value DOUBLE PRECISION,
    pre_taken_at TIMESTAMPTZ,
    post_taken_at TIMESTAMPTZ,
    delta_pct DOUBLE PRECISION,
    flagged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ship_telemetry_persona_created ON autonomous_ship_telemetry(persona, created_at);
CREATE INDEX IF NOT EXISTS ix_ship_telemetry_flagged ON autonomous_ship_telemetry(flagged) WHERE flagged = TRUE;

-- 3. Pause flags (per-persona 24h pause, global pause).
CREATE TABLE IF NOT EXISTS persona_executor_pause (
    persona TEXT PRIMARY KEY,        -- "INFRASTRUCTURE", "BUILD_TECH", or "*" for global
    paused_until TIMESTAMPTZ NOT NULL,
    reason TEXT,
    triggered_by_ship_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Adversarial reviewer transcripts.
CREATE TABLE IF NOT EXISTS reviewer_transcripts (
    id BIGSERIAL PRIMARY KEY,
    handoff_id BIGINT NOT NULL REFERENCES agent_handoffs(id),
    ship_id TEXT NOT NULL,
    cycle INT NOT NULL,
    verdict TEXT NOT NULL,           -- APPROVE | REVISE | HALT
    concerns TEXT,
    reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_reviewer_transcripts_handoff ON reviewer_transcripts(handoff_id);

-- 5. Reply telemetry (for REVERT/PAUSE/ASK frequency tracking).
CREATE TABLE IF NOT EXISTS ape_reply_telemetry (
    id BIGSERIAL PRIMARY KEY,
    ship_id TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    reply_action TEXT NOT NULL,      -- REVERT | PAUSE | PAUSE_ALL | ASK | NOTES | HELP | UNKNOWN
    processed BOOLEAN DEFAULT FALSE,
    received_at TIMESTAMPTZ DEFAULT NOW()
);
