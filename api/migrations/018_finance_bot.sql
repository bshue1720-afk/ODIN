-- Migration 018: Finance Bot
-- Tables: bank_accounts, transactions, subscriptions, finance_snapshots
-- Spoke: financial_health
-- Run: python run_migrations.py (from ODIN/)

-- ── BANK ACCOUNTS ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bank_accounts (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    teller_account_id   TEXT        UNIQUE NOT NULL,
    institution_name    TEXT,
    account_name        TEXT,
    account_type        TEXT,       -- depository | credit
    subtype             TEXT,       -- checking | savings | credit_card | money_market
    last_balance        NUMERIC(12,2),
    available_balance   NUMERIC(12,2),
    currency            TEXT        DEFAULT 'USD',
    is_biz_card         BOOLEAN     DEFAULT FALSE,
    credit_limit        NUMERIC(12,2),
    last_synced         TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ── TRANSACTIONS ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    teller_tx_id        TEXT        UNIQUE NOT NULL,
    teller_account_id   TEXT        NOT NULL,
    amount              NUMERIC(12,2) NOT NULL,  -- positive=expense, negative=income/refund
    tx_date             DATE        NOT NULL,
    description         TEXT,
    merchant_name       TEXT,
    category            TEXT,
    subcategory         TEXT,
    tx_type             TEXT,       -- card_payment | ach | transfer | wire | etc.
    status              TEXT        DEFAULT 'posted',  -- posted | pending
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_account   ON transactions(teller_account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date      ON transactions(tx_date DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant  ON transactions(merchant_name);
CREATE INDEX IF NOT EXISTS idx_transactions_category  ON transactions(category);

-- ── SUBSCRIPTIONS ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_name       TEXT        NOT NULL,
    teller_account_id   TEXT,
    amount              NUMERIC(12,2) NOT NULL,
    frequency           TEXT,       -- monthly | weekly | annual | irregular
    last_charged        DATE,
    next_expected       DATE,
    charge_count        INT         DEFAULT 1,
    total_spent         NUMERIC(12,2) DEFAULT 0,
    category            TEXT,
    status              TEXT        DEFAULT 'active',  -- active | cancelled | uncertain
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(merchant_name, teller_account_id, amount)
);

-- ── FINANCE SNAPSHOTS ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS finance_snapshots (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date           DATE        UNIQUE NOT NULL DEFAULT CURRENT_DATE,
    total_deposits          NUMERIC(12,2) DEFAULT 0,
    total_credit_used       NUMERIC(12,2) DEFAULT 0,
    total_credit_available  NUMERIC(12,2) DEFAULT 0,
    biz_card_balance        NUMERIC(12,2) DEFAULT 0,
    biz_card_available      NUMERIC(12,2) DEFAULT 0,
    net_liquid              NUMERIC(12,2) DEFAULT 0,
    mtd_spend               NUMERIC(12,2) DEFAULT 0,
    snapshot_data           JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ── ADD financial_health SPOKE ────────────────────────────────────────────────
INSERT INTO spokes (name, slug, owner_user_id, active)
VALUES ('financial_health', 'financial_health', 'a4ede2f8-b3b1-4158-816f-e7503dab81a4', TRUE)
ON CONFLICT (slug) DO NOTHING;

-- ── KPI TARGETS: financial_health ────────────────────────────────────────────
INSERT INTO kpi_targets (spoke, metric_name, target_value, unit, dri) VALUES
    ('financial_health', 'Monthly Spend Budget',       3000, 'dollars', 'brock'),
    ('financial_health', 'Active Subscriptions',         12, 'count',   'brock'),
    ('financial_health', 'Biz Card Available Balance', 2000, 'dollars', 'brock'),
    ('financial_health', 'Monthly Business Revenue',   5000, 'dollars', 'brock')
ON CONFLICT (spoke, metric_name) DO NOTHING;
