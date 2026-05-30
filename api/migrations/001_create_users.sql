-- ═══════════════════════════════════════════════════════════════
-- ODIN Phase 3 — Migration 001: Users, Permissions, Approval Queue
-- Component 1: Multi-role user system
-- ═══════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── USERS ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(255) NOT NULL,
    email               VARCHAR(255) UNIQUE NOT NULL,
    phone               VARCHAR(50),
    namespace           VARCHAR(100) UNIQUE NOT NULL,
    password_hash       VARCHAR(255),

    -- Role system (Component 1)
    role                VARCHAR(50) NOT NULL CHECK (role IN (
                            'super_admin', 're_partner', 'acquisition_manager',
                            'caller', 'disposition_agent', 'business_standard',
                            'virtual_assistant'
                        )),
    spend_limit         NUMERIC,
    can_approve_deals   BOOLEAN DEFAULT false,
    max_approvable_fee  NUMERIC,
    created_by          UUID,  -- FK added after table exists (self-ref)
    invited_at          TIMESTAMP WITH TIME ZONE,
    onboarded           BOOLEAN DEFAULT false,

    -- State
    active              BOOLEAN DEFAULT true,
    last_active         TIMESTAMP WITH TIME ZONE,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

ALTER TABLE users
    ADD CONSTRAINT fk_users_created_by
    FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- ─── INVITE TOKENS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invite_tokens (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       VARCHAR(255) UNIQUE NOT NULL,
    used        BOOLEAN DEFAULT false,
    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── AGENT ACTIONS LOG ──────────────────────────────────────────
-- Every agent action must log here with user_id (architecture rule)
CREATE TABLE IF NOT EXISTS agent_actions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    action_type VARCHAR(100) NOT NULL,
    resource    VARCHAR(100),
    resource_id UUID,
    data        JSONB,
    result      VARCHAR(50),
    ip_address  VARCHAR(45),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── LEADS ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS leads (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES users(id),   -- who owns/logged this lead
    assigned_to     UUID REFERENCES users(id),   -- caller assigned
    address         VARCHAR(500),
    city            VARCHAR(100) DEFAULT 'Memphis',
    state           VARCHAR(10) DEFAULT 'TN',
    zip             VARCHAR(20),
    beds            INTEGER,
    baths           NUMERIC,
    sqft            INTEGER,
    condition       VARCHAR(50),  -- Light/Medium/Heavy

    -- MCTP fields
    motivation      TEXT,
    motivation_score INTEGER DEFAULT 0,  -- 0-3
    condition_score  INTEGER DEFAULT 0,  -- 0-2
    timeline        TEXT,
    timeline_score  INTEGER DEFAULT 0,  -- 0-3
    asking_price    NUMERIC,
    price_score     INTEGER DEFAULT 0,  -- 0-2
    mctp_total      INTEGER GENERATED ALWAYS AS (
                        motivation_score + condition_score +
                        timeline_score + price_score
                    ) STORED,

    -- Deal math (populated when analyzed)
    arv_mid         NUMERIC,
    rehab_mid       NUMERIC,
    lao             NUMERIC,
    walkup          NUMERIC,
    buyer_max       NUMERIC,
    assign_price    NUMERIC,
    fee_at_lao      NUMERIC,
    fee_at_walkup   NUMERIC,
    rec_max_contract NUMERIC,

    -- Status
    status          VARCHAR(50) DEFAULT 'new' CHECK (status IN (
                        'new', 'contacted', 'warm', 'hot', 'contracted',
                        'assigned', 'closed', 'dead'
                    )),
    caller_notes    TEXT,
    next_action     TEXT,
    follow_up_date  DATE,

    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── APPROVAL QUEUE ─────────────────────────────────────────────
-- Component 3: Approval routing engine output lands here
CREATE TABLE IF NOT EXISTS approval_queue (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action_type      VARCHAR(100) NOT NULL,  -- hot_lead_review / deal_approval / add_user / etc.
    initiated_by     UUID REFERENCES users(id),
    assigned_to      UUID REFERENCES users(id),
    approver_role    VARCHAR(50),
    priority         INTEGER DEFAULT 1,      -- 1=normal, 2=high, 3=urgent
    resource_id      UUID,                   -- lead_id or deal_id
    data             JSONB,
    status           VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
                         'pending', 'approved', 'rejected', 'escalated'
                     )),
    message_template TEXT,
    approver_notes   TEXT,
    offer_script     TEXT,                   -- sent back to caller after approval
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at      TIMESTAMP WITH TIME ZONE
);

-- ─── BUYERS ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS buyers (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID REFERENCES users(id),  -- who added this buyer
    name        VARCHAR(255) NOT NULL,
    company     VARCHAR(255),
    email       VARCHAR(255),
    phone       VARCHAR(50),
    market      VARCHAR(100) DEFAULT 'Memphis TN',
    buy_box     TEXT,
    max_price   NUMERIC,
    preferred_condition  VARCHAR(100),
    active      BOOLEAN DEFAULT true,
    notes       TEXT,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── INDEXES ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_role         ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_namespace    ON users(namespace);
CREATE INDEX IF NOT EXISTS idx_users_active       ON users(active);
CREATE INDEX IF NOT EXISTS idx_leads_user_id      ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_assigned_to  ON leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_leads_status       ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_mctp_total   ON leads(mctp_total);
CREATE INDEX IF NOT EXISTS idx_agent_actions_uid  ON agent_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_actions_type ON agent_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_approval_assigned  ON approval_queue(assigned_to);
CREATE INDEX IF NOT EXISTS idx_approval_status    ON approval_queue(status);
CREATE INDEX IF NOT EXISTS idx_approval_resource  ON approval_queue(resource_id);

-- ─── UPDATED_AT TRIGGER ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE OR REPLACE TRIGGER trg_leads_updated_at
    BEFORE UPDATE ON leads
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
