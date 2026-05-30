-- Migration 022: Outreach Tracking
-- Tracks all cold outreach (email, SMS, call, etc.) across any channel.
-- One contact row per prospect; one touch row per send/reply.

CREATE TABLE IF NOT EXISTS outreach_contacts (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER DEFAULT 1,
    name                TEXT,
    business_name       TEXT NOT NULL,
    niche               TEXT,       -- plumbing, roofing, dental, law, auto_repair, etc.
    email               TEXT,
    phone               TEXT,
    website             TEXT,
    tier                INTEGER DEFAULT 0,
    source              TEXT,       -- leads file or agent that found them
    spoke               TEXT DEFAULT 'ai_audit',
    status              TEXT DEFAULT 'cold',
    -- cold | contacted | replied | call_booked | audit_sold | build_sold | retainer | not_interested | dead
    status_updated_at   TIMESTAMP,
    notes               TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_contacts_email
    ON outreach_contacts(LOWER(email)) WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_outreach_contacts_niche  ON outreach_contacts(niche);
CREATE INDEX IF NOT EXISTS idx_outreach_contacts_status ON outreach_contacts(status);
CREATE INDEX IF NOT EXISTS idx_outreach_contacts_spoke  ON outreach_contacts(spoke);

CREATE TABLE IF NOT EXISTS outreach_touches (
    id               SERIAL PRIMARY KEY,
    contact_id       INTEGER REFERENCES outreach_contacts(id) ON DELETE CASCADE,
    user_id          INTEGER DEFAULT 1,
    channel          TEXT NOT NULL DEFAULT 'email',  -- email | sms | call | linkedin
    direction        TEXT DEFAULT 'outbound',         -- outbound | inbound
    subject          TEXT,
    body             TEXT,
    hook_type        TEXT,  -- missed_calls | labor_cost | intake_speed | estimate_followup | storm_surge | etc.
    status           TEXT DEFAULT 'pending',
    -- pending | sent | replied | bounced | failed
    gmail_message_id TEXT,
    gmail_thread_id  TEXT,
    sent_at          TIMESTAMP,
    replied_at       TIMESTAMP,
    reply_body       TEXT,
    sequence_step    INTEGER DEFAULT 1,  -- 1=initial, 2=day3_followup, 3=day7_final
    created_at       TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outreach_touches_contact   ON outreach_touches(contact_id);
CREATE INDEX IF NOT EXISTS idx_outreach_touches_status    ON outreach_touches(status);
CREATE INDEX IF NOT EXISTS idx_outreach_touches_channel   ON outreach_touches(channel);
CREATE INDEX IF NOT EXISTS idx_outreach_touches_gmail     ON outreach_touches(gmail_message_id)
    WHERE gmail_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outreach_touches_sent_at   ON outreach_touches(sent_at)
    WHERE sent_at IS NOT NULL;
