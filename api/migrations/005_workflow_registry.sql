-- ═══════════════════════════════════════════════════════════════
-- ODIN Migration 005: Workflow Registry
-- Tracks XLeads workflows created/managed through ODIN.
-- GHL API cannot create workflows — this registry lets you
-- register, name, and reference any workflow by purpose.
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS workflow_registry (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(100) NOT NULL,
    xleads_id   VARCHAR(200),               -- GHL workflow UUID from URL
    purpose     TEXT,                       -- what this workflow does
    trigger     VARCHAR(100) DEFAULT 'manual',  -- e.g. 'customer_replied', 'blast', 'mctp_hot'
    status      VARCHAR(20)  DEFAULT 'active',  -- active, paused, archived
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    notes       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_registry_name ON workflow_registry(name);
CREATE INDEX IF NOT EXISTS idx_workflow_registry_status ON workflow_registry(status);

-- Seed: Customer Replied webhook workflow (confirmed 2026-05-24)
-- Trigger: Contact replies → fires ODIN inbound webhook
INSERT INTO workflow_registry (name, xleads_id, purpose, trigger, status, notes)
VALUES (
    'customer-replied',
    '9184a227-6bdc-4894-8a3d-d26ed44ec555',
    'Fires when a contact replies to any SMS. Posts payload to ODIN /api/xleads/inbound webhook for AI triage.',
    'customer_replied',
    'active',
    'Confirmed live 2026-05-23. XLeads name: 1779585848078. Allow re-entry ON, Allow multiple opps ON.'
)
ON CONFLICT (name) DO NOTHING;
