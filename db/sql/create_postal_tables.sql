-- Postal Agent state tables — multi-account Gmail OAuth + sync state
-- See ~/cd-ops/plans/paperclip_postal_agent_2026-06-22.md
--
-- Run once at deploy: psql $DATABASE_URL -f db/sql/create_postal_tables.sql

-- ---------------------------------------------------------------
-- postal_tokens: per-account OAuth refresh tokens (encrypted at rest)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postal_tokens (
    account_label    text PRIMARY KEY,            -- 'avi', 'wd', 'salesdroid', 'aipg', 'agentempire', 'bookd'
    email            text NOT NULL,                -- michael@automotiveintelligence.io
    refresh_token    bytea NOT NULL,               -- Fernet-encrypted with key derived from APP_SECRET
    scopes           text NOT NULL,                -- space-delimited granted scopes
    status           text NOT NULL DEFAULT 'active', -- active | needs_reauth | disabled
    last_reauth_at   timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_postal_tokens_email ON postal_tokens(email);
CREATE INDEX IF NOT EXISTS idx_postal_tokens_status ON postal_tokens(status);

-- ---------------------------------------------------------------
-- postal_state: per-account sync watermarks
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postal_state (
    account_label    text PRIMARY KEY REFERENCES postal_tokens(account_label) ON DELETE CASCADE,
    last_history_id  bigint,                       -- Gmail history API cursor (preferred over time-based for v2)
    last_synced_at   timestamptz,                  -- fallback for v1 polling
    last_error       text,
    last_error_at    timestamptz,
    sync_count       bigint NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------
-- postal_processed: idempotency log of message_ids we've handled
-- (TTL 90d via periodic prune — see services/postal_agent)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postal_processed (
    msg_id           text NOT NULL,
    account_label    text NOT NULL REFERENCES postal_tokens(account_label) ON DELETE CASCADE,
    thread_id        text,
    classified_as    text,                         -- 'intent_reply' | 'lead_response' | 'billing' | 'newsletter' | etc
    routed_to        text,                         -- 'twenty_avi' | 'avo_chat:internal-marketing' | 'pit_wall' | 'label_only' | etc
    confidence       real,                         -- 0.0-1.0 classifier confidence
    processed_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_label, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_postal_processed_processed_at ON postal_processed(processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_postal_processed_classified ON postal_processed(classified_as);

-- ---------------------------------------------------------------
-- postal_oauth_state: short-lived CSRF tokens for OAuth round-trip
-- (TTL 10min via periodic prune)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postal_oauth_state (
    state_token      text PRIMARY KEY,
    account_label    text NOT NULL,                -- where the token will land after callback
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_postal_oauth_state_created ON postal_oauth_state(created_at);
