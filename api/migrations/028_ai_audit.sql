-- Migration 028: AI Audit prospect pipeline
-- Tracks prospects from cold email → booked → paid → delivered

CREATE TABLE IF NOT EXISTS audit_prospects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id),
    name            TEXT NOT NULL,
    business        TEXT,
    niche           TEXT,          -- roofing, dental, auto, law, plumbing
    email           TEXT,
    phone           TEXT,
    city            TEXT,
    stage           TEXT NOT NULL DEFAULT 'cold',
                    -- cold → emailed → replied → booked → paid → delivered → converted
    amount_paid     NUMERIC(10,2),
    tier            TEXT,          -- audit, quick_win, full_build, retainer
    notes           TEXT,
    last_contact    TIMESTAMP,
    follow_up_date  DATE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_prospects_stage    ON audit_prospects(stage);
CREATE INDEX IF NOT EXISTS idx_audit_prospects_niche    ON audit_prospects(niche);
CREATE INDEX IF NOT EXISTS idx_audit_prospects_followup ON audit_prospects(follow_up_date);

-- Seed the ai_audit spoke if not already present
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spokes WHERE name='ai_audit') THEN
    INSERT INTO spokes (name, slug, owner_user_id, active)
    SELECT 'ai_audit', 'ai_audit', id, true FROM users WHERE role='super_admin' LIMIT 1;
  END IF;
END $$;

-- KPI targets for ai_audit spoke
INSERT INTO kpi_targets (spoke, metric_name, target_value, unit, dri, status)
VALUES
    ('ai_audit', 'Audits Sold',     5,    'count',   'brock', 'green'),
    ('ai_audit', 'Monthly Revenue', 2485, 'dollars', 'brock', 'green'),
    ('ai_audit', 'Emails Sent',     36,   'count',   'brock', 'green'),
    ('ai_audit', 'Reply Rate',      15,   'percent', 'brock', 'green'),
    ('ai_audit', 'Booked Calls',    5,    'count',   'brock', 'green')
ON CONFLICT (spoke, metric_name) DO NOTHING;
