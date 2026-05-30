-- ═══════════════════════════════════════════════════════════════
-- ODIN Migration 010: Spoke System
-- Adds multi-business spoke awareness. Additive only — no existing
-- data is deleted or modified. All defaults preserve current behavior.
-- ═══════════════════════════════════════════════════════════════

-- ─── SPOKES TABLE ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spokes (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          VARCHAR(100) NOT NULL,
    slug          VARCHAR(50)  NOT NULL UNIQUE,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    active        BOOLEAN DEFAULT true,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_spokes_slug   ON spokes(slug);
CREATE INDEX IF NOT EXISTS idx_spokes_active ON spokes(active);

-- ─── ADD SPOKE COLUMN TO EXISTING TABLES ────────────────────────
-- Default 'real_estate' preserves all existing lead/task/action data
ALTER TABLE leads         ADD COLUMN IF NOT EXISTS spoke VARCHAR(50) DEFAULT 'real_estate';
ALTER TABLE tasks         ADD COLUMN IF NOT EXISTS spoke VARCHAR(50) DEFAULT 'real_estate';
ALTER TABLE agent_actions ADD COLUMN IF NOT EXISTS spoke VARCHAR(50) DEFAULT 'real_estate';
-- Businesses belong to Katelyn's spoke by default (existing records)
ALTER TABLE businesses    ADD COLUMN IF NOT EXISTS spoke VARCHAR(50) DEFAULT 'katelyn_business';

-- ─── SEED SPOKES ────────────────────────────────────────────────
INSERT INTO spokes (name, slug, active)
VALUES
    ('Real Estate',      'real_estate',      true),
    ('Katelyn Business', 'katelyn_business', true)
ON CONFLICT (slug) DO NOTHING;

-- Set owner references (safe even on re-run — only updates NULL owners)
UPDATE spokes
SET owner_user_id = (SELECT id FROM users WHERE email = 'shueboxllc@gmail.com' LIMIT 1)
WHERE slug = 'real_estate' AND owner_user_id IS NULL;

UPDATE spokes
SET owner_user_id = (SELECT id FROM users WHERE email = 'katelynrxo@gmail.com' LIMIT 1)
WHERE slug = 'katelyn_business' AND owner_user_id IS NULL;
