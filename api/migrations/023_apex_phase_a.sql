-- Migration 023: APEX Phase A — Opt-Out Tracking + Decision Outcomes
-- Adds opt-out audit columns to leads and outcome tracking to decision_queue.

-- ── LEADS: Opt-Out Tracking ───────────────────────────────────────────────────
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS opted_out_at   TIMESTAMP,
    ADD COLUMN IF NOT EXISTS opt_out_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_leads_opted_out ON leads(opted_out_at)
    WHERE opted_out_at IS NOT NULL;

-- ── DECISION QUEUE: Outcome Tracking ─────────────────────────────────────────
ALTER TABLE decision_queue
    ADD COLUMN IF NOT EXISTS outcome_notes        TEXT,
    ADD COLUMN IF NOT EXISTS outcome_recorded_at  TIMESTAMP;
