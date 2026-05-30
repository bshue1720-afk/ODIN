-- Migration 020: Revenue auto-sync dedup column
-- Allows heartbeat to auto-insert won XLeads opportunities without duplicates

ALTER TABLE revenue_events
  ADD COLUMN IF NOT EXISTS xleads_opp_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_revenue_events_opp_id
  ON revenue_events(xleads_opp_id)
  WHERE xleads_opp_id IS NOT NULL;
