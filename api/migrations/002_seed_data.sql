-- ═══════════════════════════════════════════════════════════════
-- ODIN Phase 3 — Migration 002: Seed Data
-- Component 6: Brock (super_admin) + Wife (business_standard)
-- ═══════════════════════════════════════════════════════════════
-- NOTE: password_hash is NULL — users set passwords via invite link.
-- Brock's account is pre-onboarded (no invite needed).
-- ═══════════════════════════════════════════════════════════════

INSERT INTO users (
    name, email, phone, namespace, role,
    can_approve_deals, max_approvable_fee,
    active, onboarded, spend_limit
) VALUES (
    'Brock',
    'brock@shuebox.com',
    NULL,
    'brock',
    'super_admin',
    true,
    NULL,       -- unlimited
    true,
    true,
    NULL        -- unlimited spend
) ON CONFLICT (namespace) DO UPDATE SET
    role               = EXCLUDED.role,
    can_approve_deals  = EXCLUDED.can_approve_deals,
    max_approvable_fee = EXCLUDED.max_approvable_fee,
    active             = EXCLUDED.active,
    onboarded          = EXCLUDED.onboarded;

INSERT INTO users (
    name, email, phone, namespace, role,
    can_approve_deals, max_approvable_fee,
    active, onboarded, spend_limit
) VALUES (
    'Wife',
    'wife@shuebox.com',
    NULL,
    'wife',
    'business_standard',
    false,
    NULL,
    true,
    false,
    NULL
) ON CONFLICT (namespace) DO UPDATE SET
    role    = EXCLUDED.role,
    active  = EXCLUDED.active;

-- Log seed action
INSERT INTO agent_actions (user_id, action_type, resource, data)
SELECT id, 'system_seed', 'users',
       '{"note": "Initial seed: super_admin + business_standard"}'::jsonb
FROM users WHERE namespace = 'brock'
ON CONFLICT DO NOTHING;
