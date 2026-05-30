-- Migration 012: custom_agents table
-- Stores Claude-generated agent code created via the `add agent` Slack command.
-- Each agent has a trigger keyword, a description, and a Python code body.

CREATE TABLE IF NOT EXISTS custom_agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,       -- short slug e.g. "etsy_monitor"
    trigger_keyword TEXT NOT NULL UNIQUE,       -- what user types in Slack to invoke it
    description     TEXT NOT NULL,             -- what this agent does (plain English)
    code            TEXT NOT NULL,             -- full Python module as a string
    created_by      TEXT,                      -- 'brock' or 'katelyn'
    status          TEXT DEFAULT 'active',     -- active | disabled
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_custom_agents_trigger ON custom_agents(trigger_keyword);
CREATE INDEX IF NOT EXISTS idx_custom_agents_status  ON custom_agents(status);
