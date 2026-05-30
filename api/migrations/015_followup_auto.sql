-- Migration 015: Follow-Up Auto
-- Adds xleads_contact_id and followup_fired_at to leads table.
-- Enables heartbeat to auto-fire next_action SMS when follow_up_date = today.

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS xleads_contact_id  TEXT,
  ADD COLUMN IF NOT EXISTS followup_fired_at  TIMESTAMP WITH TIME ZONE;

-- Index for fast heartbeat query (leads due today with contact ID set)
CREATE INDEX IF NOT EXISTS idx_leads_followup_auto
  ON leads (follow_up_date, xleads_contact_id)
  WHERE status NOT IN ('closed', 'dead', 'contracted');
