-- Migration 026: A/B blast variant tracking, webhook dead letter queue, lead source attribution
-- Run: node run_migration.js 026_ab_testing_dlq_source.sql

-- A/B variant on blast_campaigns
ALTER TABLE blast_campaigns
  ADD COLUMN IF NOT EXISTS variant       TEXT DEFAULT 'A',
  ADD COLUMN IF NOT EXISTS variant_label TEXT;

-- A/B variant on leads (which variant they received)
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS blast_variant TEXT;

-- Webhook dead letter queue
CREATE TABLE IF NOT EXISTS webhook_dead_letters (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source       TEXT NOT NULL DEFAULT 'xleads',
    endpoint     TEXT,
    payload      JSONB,
    error_msg    TEXT,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    next_retry   TIMESTAMPTZ,
    resolved     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON webhook_dead_letters (resolved, next_retry)
    WHERE resolved = FALSE;

-- Lead source attribution column
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS lead_source TEXT;

-- Backfill lead_source from existing xleads tags where possible
-- (left as NULL for now — populated on new inbound webhooks going forward)
