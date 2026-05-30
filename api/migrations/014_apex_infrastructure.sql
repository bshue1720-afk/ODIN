-- Migration 014: APEX Infrastructure — Decision Queue + Revenue Events + KPI Targets
-- Spoke-agnostic. No RE dependency. Powers morning briefing, decisions command, revenue command.

-- ─── DECISION QUEUE ──────────────────────────────────────────────────────────
-- ODIN surfaces stalled items proactively in 1-3-1 format.
-- Founder only approves or declines — never invents solutions.
CREATE TABLE IF NOT EXISTS decision_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spoke           TEXT NOT NULL DEFAULT 'general',
    issue_summary   TEXT NOT NULL,
    option_1        TEXT,
    option_2        TEXT,
    option_3        TEXT,
    recommended     TEXT,      -- which option ODIN recommends
    reason          TEXT,      -- why ODIN recommends it
    status          TEXT NOT NULL DEFAULT 'pending',
    -- status values: pending | approved | declined | auto_executed | dismissed
    auto_execute_at TIMESTAMP, -- if still pending at this time, auto-execute recommended
    resolved_at     TIMESTAMP,
    created_by      TEXT DEFAULT 'odin',
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dq_status     ON decision_queue(status);
CREATE INDEX IF NOT EXISTS idx_dq_spoke      ON decision_queue(spoke);
CREATE INDEX IF NOT EXISTS idx_dq_auto_exec  ON decision_queue(auto_execute_at);

-- ─── REVENUE EVENTS ──────────────────────────────────────────────────────────
-- Track income and expenses per spoke. Powers /revenue command + morning briefing.
CREATE TABLE IF NOT EXISTS revenue_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spoke       TEXT NOT NULL DEFAULT 'general',
    amount      NUMERIC(12,2) NOT NULL,   -- positive = income, negative = expense
    type        TEXT NOT NULL DEFAULT 'income',  -- income | expense | refund
    description TEXT,
    event_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rev_spoke ON revenue_events(spoke);
CREATE INDEX IF NOT EXISTS idx_rev_date  ON revenue_events(event_date);

-- ─── KPI TARGETS ─────────────────────────────────────────────────────────────
-- Each metric has a target, current value, DRI, and traffic light status.
-- ODIN auto-updates current_value and status on heartbeat jobs.
CREATE TABLE IF NOT EXISTS kpi_targets (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    spoke         TEXT NOT NULL DEFAULT 'general',
    metric_name   TEXT NOT NULL,
    target_value  NUMERIC(12,2),
    current_value NUMERIC(12,2) DEFAULT 0,
    unit          TEXT DEFAULT '',        -- '$', '%', 'count', 'days'
    dri           TEXT DEFAULT 'Brock',   -- directly responsible individual
    status        TEXT DEFAULT 'green',   -- green | yellow | red
    last_updated  TIMESTAMP DEFAULT NOW(),
    UNIQUE(spoke, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_kpi_spoke  ON kpi_targets(spoke);
CREATE INDEX IF NOT EXISTS idx_kpi_status ON kpi_targets(status);

-- Seed ODIN's core KPIs
INSERT INTO kpi_targets (spoke, metric_name, target_value, unit, dri) VALUES
    ('general',      'Decisions Resolved This Week',   5,     'count', 'ODIN'),
    ('general',      'Follow-ups Fired On Time',       90,    '%',     'ODIN'),
    ('general',      'Tasks Completed This Week',      10,    'count', 'ODIN'),
    ('general',      'System Uptime',                  99,    '%',     'ODIN'),
    ('real_estate',  'Deals Under Contract',            2,    'count', 'Brock'),
    ('real_estate',  'Monthly Revenue Target',      20000,    '$',     'Brock'),
    ('real_estate',  'Active Pipeline Leads',           15,   'count', 'ODIN'),
    ('real_estate',  'Follow-up Response Rate',         30,   '%',     'ODIN'),
    ('valdr_ops',    'Active Clients',                  10,   'count', 'Brock'),
    ('valdr_ops',    'Monthly Recurring Revenue',     1500,   '$',     'Brock')
ON CONFLICT (spoke, metric_name) DO NOTHING;
