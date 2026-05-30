-- ═══════════════════════════════════════════════════════════════
-- ODIN Phase 4 — Migration 003: Katelyn's Business Automation Engine
-- Tables: businesses, tasks, products
-- ═══════════════════════════════════════════════════════════════

-- ─── BUSINESSES ─────────────────────────────────────────────────
-- Tracks each business idea or venture Katelyn is building.
-- Populated by the Business Advisor agent after she picks a direction.
CREATE TABLE IF NOT EXISTS businesses (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    name                  VARCHAR(255) NOT NULL,
    type                  VARCHAR(100) NOT NULL,     -- etsy, shopify, gumroad, email_outreach, freelance, etc.
    description           TEXT,

    -- From advisor output
    status                VARCHAR(50) DEFAULT 'idea' CHECK (status IN (
                              'idea', 'active', 'paused', 'closed'
                          )),
    income_potential_low  NUMERIC,                   -- $/month low end
    income_potential_high NUMERIC,                   -- $/month high end
    automation_level      INTEGER DEFAULT 0,         -- 0-100 percent ODIN can automate
    weekly_hours          NUMERIC,                   -- hours/week Katelyn does herself
    time_to_start_hours   NUMERIC,                   -- estimated setup hours

    -- Accounts and first steps from advisor (stored as JSON arrays)
    accounts_needed       JSONB,                     -- ["Etsy", "Canva", "Printify"]
    first_steps           JSONB,                     -- ["Step 1...", "Step 2...", "Step 3..."]

    notes                 TEXT,
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── TASKS ──────────────────────────────────────────────────────
-- Action items tied to a business (or general).
-- ODIN assigns tasks; Katelyn marks them done via dashboard or Slack.
CREATE TABLE IF NOT EXISTS tasks (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id   UUID REFERENCES businesses(id) ON DELETE SET NULL,
    assigned_by   UUID REFERENCES users(id) ON DELETE SET NULL,  -- Brock or ODIN

    title         VARCHAR(500) NOT NULL,
    description   TEXT,

    status        VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
                      'pending', 'in_progress', 'completed', 'blocked'
                  )),
    priority      INTEGER DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),  -- 1=low 2=medium 3=high

    due_date      DATE,
    completed_at  TIMESTAMP WITH TIME ZONE,

    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── PRODUCTS ───────────────────────────────────────────────────
-- Individual products or offerings tied to a business.
-- Tracks what's for sale, where, and basic revenue data.
CREATE TABLE IF NOT EXISTS products (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id   UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,

    name          VARCHAR(255) NOT NULL,
    description   TEXT,
    price         NUMERIC,

    platform      VARCHAR(100),   -- etsy, shopify, gumroad, amazon, etc.
    status        VARCHAR(50) DEFAULT 'draft' CHECK (status IN (
                      'draft', 'active', 'archived'
                  )),
    url           TEXT,

    sales_count   INTEGER DEFAULT 0,
    revenue_total NUMERIC DEFAULT 0,

    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── INDEXES ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_businesses_user_id    ON businesses(user_id);
CREATE INDEX IF NOT EXISTS idx_businesses_status     ON businesses(status);
CREATE INDEX IF NOT EXISTS idx_tasks_user_id         ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_business_id     ON tasks(business_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status          ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date        ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_products_user_id      ON products(user_id);
CREATE INDEX IF NOT EXISTS idx_products_business_id  ON products(business_id);
CREATE INDEX IF NOT EXISTS idx_products_status       ON products(status);

-- ─── UPDATED_AT TRIGGERS ────────────────────────────────────────
CREATE OR REPLACE TRIGGER trg_businesses_updated_at
    BEFORE UPDATE ON businesses
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
