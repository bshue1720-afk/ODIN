-- Migration 030: Sentiment Persistence + Tone Profile Keys
-- 1. Adds reply_sentiment column to leads table (persist Haiku classification)
-- 2. Adds tone profile support to memories via new key constraint (no schema change needed)

-- ── LEADS: Persist reply sentiment ────────────────────────────────────────────
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS reply_sentiment  TEXT,
    ADD COLUMN IF NOT EXISTS last_reply_body  TEXT,
    ADD COLUMN IF NOT EXISTS last_reply_at    TIMESTAMP;

-- Index for filtering/routing by sentiment
CREATE INDEX IF NOT EXISTS idx_leads_sentiment ON leads(reply_sentiment)
    WHERE reply_sentiment IS NOT NULL;

-- ── MEMORIES: No schema change needed — new keys handled by application layer ──
-- Tone profile keys added to MEMORY_KEYS in memory.py:
--   tone_email   — how Brock writes cold/follow-up emails
--   tone_sms     — how Brock writes SMS follow-ups
--   tone_slack   — how Brock communicates in Slack
-- These are upserted by the `tone update` command and loaded by drafters.
