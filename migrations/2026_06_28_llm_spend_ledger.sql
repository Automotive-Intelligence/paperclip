-- migrations/2026_06_28_llm_spend_ledger.sql
-- LLM spend ledger: one row per Anthropic API call, with token counts and
-- computed USD cost, tagged by persona / surface / brand / client so spend can
-- be sliced any way (per-persona, per-brand, per-client) — dimensions the
-- Anthropic Console can't give you. The daily spend email reads from here.
--
-- services/llm_ledger.py also lazy-creates this table on first insert (same
-- pattern as ape_routine_digest_queue), so the ledger works even where no
-- migration runner is wired. This file is the formal record.

CREATE TABLE IF NOT EXISTS llm_spend_ledger (
    id                    BIGSERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    persona               TEXT,                 -- e.g. 'CMO', 'Infrastructure'; null if not a persona call
    surface               TEXT,                 -- 'executor' | 'executor:revision' | 'reviewer' | ...
    brand                 TEXT,                 -- optional brand tag
    client                TEXT,                 -- optional client tag (for per-client attribution)
    model                 TEXT NOT NULL,        -- model id as sent to the API
    input_tokens          BIGINT NOT NULL DEFAULT 0,
    output_tokens         BIGINT NOT NULL DEFAULT 0,
    cache_creation_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens     BIGINT NOT NULL DEFAULT 0,
    cost_usd              NUMERIC(12, 6) NOT NULL DEFAULT 0,  -- computed at insert from a pricing map
    request_id            TEXT                  -- Anthropic response _request_id, for reconciliation
);

CREATE INDEX IF NOT EXISTS ix_llm_spend_ts        ON llm_spend_ledger(ts);
CREATE INDEX IF NOT EXISTS ix_llm_spend_persona   ON llm_spend_ledger(persona, ts);
CREATE INDEX IF NOT EXISTS ix_llm_spend_client    ON llm_spend_ledger(client, ts) WHERE client IS NOT NULL;
