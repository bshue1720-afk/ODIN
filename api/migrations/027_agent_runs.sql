-- Migration 027 — agent_runs table for dynamic sub-agent spawn tracking

CREATE TABLE IF NOT EXISTS agent_runs (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    task         TEXT        NOT NULL,
    triggered_by TEXT        NOT NULL DEFAULT 'api',
    status       TEXT        NOT NULL DEFAULT 'running',  -- running | done | error
    steps        JSONB,                                    -- JSON array of step results
    result       TEXT,                                     -- final synthesized output
    duration_ms  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_created   ON agent_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_triggered ON agent_runs (triggered_by);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status    ON agent_runs (status);
