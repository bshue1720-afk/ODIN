-- Migration 021: Offer tracking + blast reply linking
-- Supports Offer Generation (scorecard → draft offer letter)
-- and SMS Outbound (link inbound replies to originating blast campaign)

-- Offer tracking on leads
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS offer_drafted_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS offer_amount      DECIMAL(12,2),
  ADD COLUMN IF NOT EXISTS offer_status      TEXT DEFAULT 'none';
  -- offer_status: none | drafted | sent | countered | accepted | dead

-- Link inbound replies to originating blast campaign
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS blast_campaign_id UUID REFERENCES blast_campaigns(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_leads_blast_campaign ON leads(blast_campaign_id);
CREATE INDEX IF NOT EXISTS idx_leads_offer_status   ON leads(offer_status) WHERE offer_status != 'none';
