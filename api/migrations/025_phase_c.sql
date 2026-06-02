-- Migration 025: Phase C — Multi-Step Follow-Up + Buyer Qualification

-- ── LEADS: Multi-step follow-up sequence ─────────────────────────────────────
ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS followup_step       INT DEFAULT 1,
    ADD COLUMN IF NOT EXISTS followup_max_steps  INT DEFAULT 3;

-- ── BUYER CRITERIA: Qualification data captured from buyer SMS replies ────────
CREATE TABLE IF NOT EXISTS buyer_criteria (
    id           SERIAL PRIMARY KEY,
    buyer_id     INTEGER,
    min_price    NUMERIC,
    max_price    NUMERIC,
    zip_codes    TEXT[],      -- e.g. ARRAY['38109','38111']
    deal_types   TEXT[],      -- e.g. ARRAY['flip','rental']
    max_rehab    NUMERIC,
    notes        TEXT,
    raw_reply    TEXT,        -- original SMS reply for reference
    captured_at  TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_criteria_buyer_id ON buyer_criteria(buyer_id);
CREATE INDEX  IF NOT EXISTS idx_buyer_criteria_zips ON buyer_criteria USING GIN(zip_codes);
