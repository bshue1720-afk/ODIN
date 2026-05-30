-- Migration 013: agent_logs table
-- Every agent run (built-in and custom) writes one row here.
-- IT agent queries this for diagnosis. Brock/Katelyn can review via `logs` command.

CREATE TABLE IF NOT EXISTS agent_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      TEXT NOT NULL,              -- e.g. 'scout', 'income_calculator', 'etsy_monitor'
    action          TEXT,                       -- brief description of what was asked
    status          TEXT NOT NULL DEFAULT 'ok', -- ok | error | warning
    input_summary   TEXT,                       -- truncated input (first 500 chars)
    output_summary  TEXT,                       -- truncated output (first 500 chars)
    error_detail    TEXT,                       -- full error message + traceback if status=error
    duration_ms     INTEGER,                    -- how long the run took
    triggered_by    TEXT DEFAULT 'brock',       -- brock | katelyn | heartbeat | webhook | system
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name  ON agent_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_logs_status      ON agent_logs(status);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created     ON agent_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_errors      ON agent_logs(created_at DESC) WHERE status = 'error';
