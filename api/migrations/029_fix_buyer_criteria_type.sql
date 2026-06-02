-- Migration 029: Fix buyer_criteria.buyer_id type mismatch
-- buyers.id is UUID (migration 001) but buyer_criteria.buyer_id was INTEGER (migration 025).
-- The JOIN (bc.buyer_id = b.id) silently failed on every inbound reply — buyer
-- qualification capture was completely non-functional.
--
-- The table is empty in production (all inserts failed due to type error),
-- so we drop and recreate it cleanly with the correct UUID type.

DROP TABLE IF EXISTS buyer_criteria;

CREATE TABLE buyer_criteria (
    id           SERIAL PRIMARY KEY,
    buyer_id     UUID REFERENCES buyers(id) ON DELETE CASCADE,
    min_price    NUMERIC,
    max_price    NUMERIC,
    zip_codes    TEXT[],
    deal_types   TEXT[],
    max_rehab    NUMERIC,
    notes        TEXT,
    raw_reply    TEXT,
    captured_at  TIMESTAMP DEFAULT NOW(),
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_criteria_buyer_id ON buyer_criteria(buyer_id);
CREATE INDEX  IF NOT EXISTS idx_buyer_criteria_zips ON buyer_criteria USING GIN(zip_codes);
