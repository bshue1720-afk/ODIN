-- Migration 017: Buyer Onboarding
-- Adds onboarding_stage, xleads_contact_id, and onboarding_started_at to buyers table.
-- Enables heartbeat to auto-send welcome SMS and track buyer onboarding pipeline.

ALTER TABLE buyers
  ADD COLUMN IF NOT EXISTS onboarding_stage      TEXT DEFAULT 'new',
  ADD COLUMN IF NOT EXISTS onboarding_started_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS xleads_contact_id     TEXT;

-- Onboarding stages: new → welcomed → qualified → active → inactive
-- 'new' = in DB but no outreach yet
-- 'welcomed' = welcome SMS sent
-- 'qualified' = responded, buy box confirmed
-- 'active' = actively receiving deals
-- 'inactive' = ghosted or opted out

CREATE INDEX IF NOT EXISTS idx_buyers_onboarding_stage
  ON buyers (onboarding_stage)
  WHERE is_dnc = FALSE;

CREATE INDEX IF NOT EXISTS idx_buyers_xleads_contact_id
  ON buyers (xleads_contact_id)
  WHERE xleads_contact_id IS NOT NULL;
