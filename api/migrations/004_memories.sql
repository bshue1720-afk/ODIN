-- ═══════════════════════════════════════════════════════════════
-- ODIN Layer 2 — Migration 004: Memory Layer + Slack UID on users
-- Stores persistent facts about users across conversations.
-- No external services — uses existing Railway PostgreSQL.
-- ═══════════════════════════════════════════════════════════════

-- Add slack_uid to users table so memory lookup works by Slack identity
ALTER TABLE users ADD COLUMN IF NOT EXISTS slack_uid VARCHAR(100);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_slack_uid ON users(slack_uid) WHERE slack_uid IS NOT NULL;

-- Seed known Slack UIDs
UPDATE users SET slack_uid = 'U0B6MSYBY72' WHERE email = 'katelynrxo@gmail.com';
UPDATE users SET slack_uid = 'U0B5C32BJ6B' WHERE email = 'shueboxllc@gmail.com';


CREATE TABLE IF NOT EXISTS memories (
    id          UUID    PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    key         VARCHAR(255) NOT NULL,   -- e.g. "current_business", "income_goal", "preferred_style"
    value       TEXT    NOT NULL,        -- the fact itself
    source      VARCHAR(50) DEFAULT 'chat',  -- 'chat', 'advisor', 'manual'
    importance  INTEGER DEFAULT 2 CHECK (importance BETWEEN 1 AND 3),
                                         -- 1=low, 2=medium, 3=high

    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- One key per user (upsert by key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_key ON memories(user_id, key);

CREATE INDEX IF NOT EXISTS idx_memories_user_id    ON memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);

CREATE OR REPLACE TRIGGER trg_memories_updated_at
    BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
