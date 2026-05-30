-- ═══════════════════════════════════════════════════════════════
-- ODIN Migration 008: Expand buyers table + pipeline_stages table
-- Adds investor data fields from XLeads CSV export.
-- ═══════════════════════════════════════════════════════════════

-- Expand buyers table with investor intelligence fields
ALTER TABLE buyers
  ADD COLUMN IF NOT EXISTS city                 VARCHAR(100),
  ADD COLUMN IF NOT EXISTS state                VARCHAR(10),
  ADD COLUMN IF NOT EXISTS zip                  VARCHAR(20),
  ADD COLUMN IF NOT EXISTS is_flipper           BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_landlord          BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_note_holder       BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_lender            BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_distressed        BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS portfolio_owned      INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS portfolio_value      NUMERIC DEFAULT 0,
  ADD COLUMN IF NOT EXISTS portfolio_buy_avg    NUMERIC DEFAULT 0,
  ADD COLUMN IF NOT EXISTS flipped_count        INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS flipped_avg_profit   NUMERIC DEFAULT 0,
  ADD COLUMN IF NOT EXISTS source               VARCHAR(50) DEFAULT 'manual',
  ADD COLUMN IF NOT EXISTS xleads_detail_url    TEXT,
  ADD COLUMN IF NOT EXISTS phone2               VARCHAR(50),
  ADD COLUMN IF NOT EXISTS email2               VARCHAR(255),
  ADD COLUMN IF NOT EXISTS is_dnc               BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_litigator         BOOLEAN DEFAULT FALSE;

-- Unique constraint on phone to allow dedup
CREATE UNIQUE INDEX IF NOT EXISTS idx_buyers_phone_unique
  ON buyers(phone) WHERE phone IS NOT NULL AND phone != '';

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_buyers_state       ON buyers(state);
CREATE INDEX IF NOT EXISTS idx_buyers_zip         ON buyers(zip);
CREATE INDEX IF NOT EXISTS idx_buyers_is_flipper  ON buyers(is_flipper);
CREATE INDEX IF NOT EXISTS idx_buyers_is_landlord ON buyers(is_landlord);
CREATE INDEX IF NOT EXISTS idx_buyers_source      ON buyers(source);

-- Pipeline stages reference table
CREATE TABLE IF NOT EXISTS pipeline_stages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pipeline    VARCHAR(100) NOT NULL,
    stage_name  VARCHAR(200) NOT NULL,
    stage_order INTEGER DEFAULT 0,
    notes       TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_stages_unique
  ON pipeline_stages(pipeline, stage_name);

-- Seed known pipelines from XLeads screenshots
INSERT INTO pipeline_stages (pipeline, stage_name, stage_order, notes) VALUES
-- Wholesaling (7 stages)
('Wholesaling', 'New Lead',                    1, 'Fresh lead entered pipeline'),
('Wholesaling', 'Contacted',                   2, 'Initial contact made'),
('Wholesaling', 'Appointment Set',             3, 'Walkthrough/call scheduled'),
('Wholesaling', 'Offer Made',                  4, 'Verbal or written offer sent'),
('Wholesaling', 'Negotiating',                 5, 'Back and forth on price'),
('Wholesaling', 'Send Contract to Seller',     6, 'Triggers acq-esign-to-seller workflow'),
('Wholesaling', 'Under Contract',              7, 'Signed — ready to dispo'),
-- Disps (8 stages)
('Disps', 'New Deal',                          1, 'Contract received, dispo starts'),
('Disps', 'Blast Buyers',                      2, 'Triggers disps-deal-blaster workflow'),
('Disps', 'Buyer Interest',                    3, 'Buyers responding'),
('Disps', 'Showing Scheduled',                 4, 'Walkthrough with buyer set'),
('Disps', 'Offer Accepted',                    5, 'Buyer accepted price'),
('Disps', 'Send Assignment',                   6, 'Triggers disps-esign-assignment workflow'),
('Disps', 'Assignment Signed',                 7, 'Assignment executed'),
('Disps', 'Closed',                            8, 'Deal funded, fee collected'),
-- JV (5 stages from screenshots)
('JV', 'Approved JV Lead (Needs Culled)',      1, 'Raw JV leads to review'),
('JV', 'Send JV Agreement (E-Sign)',           2, 'Triggers jv-esign workflow'),
('JV', 'Walkthrough / Inspection Appointment',3, 'Physical inspection scheduled'),
('JV', 'Send Assignment (to Cash Buyer)',      4, 'Triggers jv-send-assignment workflow'),
('JV', 'Failed Underwriting - Deal Not Viable',5, 'Dead deal — triggers jv-failed-underwriting'),
-- PPL (11 stages)
('PPL', 'New PPL Lead',                        1, 'Inbound PPL lead'),
('PPL', 'Attempting Contact',                  2, 'Triggers ppl-no-answer sequences'),
('PPL', 'Contacted',                           3, 'Live conversation initiated'),
('PPL', 'Appointment Set',                     4, 'Call/walk scheduled'),
('PPL', 'Offer Made',                          5, 'Price given'),
('PPL', 'Negotiating',                         6, 'Working the deal'),
('PPL', 'Send Contract',                       7, 'Triggers ppl-send-contract workflow'),
('PPL', 'Under Contract',                      8, 'Signed'),
('PPL', 'Send Assignment',                     9, 'Triggers ppl-send-assignment workflow'),
('PPL', 'Closed',                             10, 'Funded'),
('PPL', 'Refund Stage',                       11, 'Lead did not produce — triggers ppl-refund-stage'),
-- On-Market (estimated)
('On-Market', 'Identified',                    1, 'Listed deal found'),
('On-Market', 'LOI Sent',                      2, 'Triggers om-loi-blaster-sub2'),
('On-Market', 'Offer Accepted',                3, 'Price agreed'),
('On-Market', 'Under Contract',                4, 'Executed contract'),
('On-Market', 'Send Assignment',               5, 'Triggers om-esign workflow'),
('On-Market', 'Closed',                        6, 'Deal done')
ON CONFLICT (pipeline, stage_name) DO NOTHING;
