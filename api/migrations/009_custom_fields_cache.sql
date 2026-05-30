-- Migration 009: Custom fields cache + property lookup cache
-- Run: node scripts/run_migration.js 009

-- Custom fields from XLeads — cached locally for fast reference
CREATE TABLE IF NOT EXISTS custom_fields (
    id          SERIAL PRIMARY KEY,
    xleads_id   VARCHAR(100) UNIQUE,
    name        VARCHAR(255) NOT NULL,
    field_key   VARCHAR(255),
    data_type   VARCHAR(50),
    placeholder TEXT,
    synced_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Property lookup cache — Shelby County Assessor data (30-day TTL)
CREATE TABLE IF NOT EXISTS property_lookup_cache (
    id              SERIAL PRIMARY KEY,
    address         TEXT NOT NULL,
    address_norm    TEXT UNIQUE,
    owner_name      TEXT,
    mailing_address TEXT,
    assessed_value  NUMERIC,
    market_value    NUMERIC,
    tax_status      TEXT,
    year_built      INTEGER,
    sqft            INTEGER,
    bedrooms        INTEGER,
    parcel_id       VARCHAR(100),
    raw_data        JSONB,
    fetched_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Seed the known custom fields from screenshots (Contract Info folder)
INSERT INTO custom_fields (name, field_key, data_type) VALUES
  ('BUS OFFER',                     'bus_offer',                   'MONETARY'),
  ('Legal Description',             'legal_description',           'TEXT'),
  ('Closing Date Due for Cash Buyer','closing_date_cash_buyer',    'DATE'),
  ('Low Ball Offer (TBV)',          'low_ball_offer_tbv',          'MONETARY'),
  ('AR Opportunity Value',          'ar_opportunity_value',        'MONETARY'),
  ('Original Purchase Price',       'original_purchase_price',     'MONETARY'),
  ('Cash Buyer Name',               'cash_buyer_name',             'TEXT'),
  ('Assignment Fee from Cash Buyer','assignment_fee_cash_buyer',   'MONETARY'),
  ('Alpha Sequence Score',          'alpha_sequence_score',        'NUMERICAL'),
  ('AI Opportunity Value',          'ai_opportunity_value',        'MONETARY'),
  ('ARV',                           'arv',                         'MONETARY'),
  ('Realtor Dollar Gain from Cash Buyer','realtor_dollar_gain',    'MONETARY'),
  ('Cash Buyer EMD',                'cash_buyer_emd',              'MONETARY'),
  ('MLG_Carl_UnitPrice',            'mlg_carl_unit_price',         'MONETARY')
ON CONFLICT (xleads_id) DO NOTHING;
