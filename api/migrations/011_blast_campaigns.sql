-- Migration 011: blast_campaigns table for SMS deliverability monitoring
-- Tracks every blast: sent count, opt-outs, replies, and health status

CREATE TABLE IF NOT EXISTS blast_campaigns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     TEXT NOT NULL,
    workflow_name   TEXT,
    tags            TEXT,
    sent_count      INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    opt_out_count   INTEGER DEFAULT 0,
    reply_count     INTEGER DEFAULT 0,
    health_status   TEXT DEFAULT 'ok',  -- ok | warning | flagged
    alert_sent      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blast_campaigns_created ON blast_campaigns(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_blast_campaigns_workflow ON blast_campaigns(workflow_id);
