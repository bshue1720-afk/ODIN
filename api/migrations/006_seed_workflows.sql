-- ═══════════════════════════════════════════════════════════════
-- ODIN Migration 006: Seed workflow_registry from XLeads inventory
-- Cataloged 2026-05-24 from XLeads Automations screenshots.
-- xleads_id = NULL where UUID was not visible (list view only).
-- Fill UUIDs by clicking into each workflow and copying from URL.
-- ═══════════════════════════════════════════════════════════════

-- ── ACQUISITIONS ──────────────────────────────────────────────
INSERT INTO workflow_registry (name, xleads_id, purpose, trigger, status, notes)
VALUES
('acq-ai-qualified-seller',     NULL, 'AI qualifies inbound seller → moves to Deal Board automatically', 'contact_replied', 'active', 'Folder: Acquisitions | Published'),
('acq-missed-call-textback',    NULL, 'Sends automated SMS when a call is missed from a seller', 'missed_call', 'active', 'Folder: Acquisitions | Published'),
('acq-esign-to-seller',         NULL, 'Sends e-sign contract to seller after deal agreed', 'manual', 'active', 'Folder: Acquisitions | Published'),
('acq-followup-weekly',         NULL, 'Weekly pipeline follow-up sequence for acquisition leads', 'scheduled', 'active', 'Folder: Acquisitions | Published'),
('acq-appt-confirmation',       NULL, 'Appointment confirmation + reminder drip', 'scheduled', 'draft', 'Folder: Acquisitions | Draft'),

-- ── COLD CALLING ──────────────────────────────────────────────
('cc-ai-followup-outbound',     NULL, 'AI-powered follow-up after outbound cold calls', 'contact_replied', 'active', 'Folder: Cold-Calling | Published'),
('cc-ai-followup-weekly',       NULL, 'Weekly AI follow-up for cold call leads', 'scheduled', 'active', 'Folder: Cold-Calling | Published'),
('cc-double-tap',               NULL, 'Two-touch sequence to increase contact rates on cold leads', 'manual', 'active', 'Folder: Cold-Calling | Published'),
('cc-power-dialer',             NULL, 'Power dialer enrollment — 9513 total enrolled, 800 active', 'manual', 'active', 'Folder: Cold-Calling | Published — HIGH VOLUME'),
('cc-ros-quick-tap',            NULL, 'ROS Quick Tap follow-up sequence', 'manual', 'active', 'Folder: Cold-Calling | Published'),
('cc-remove-dialer',            NULL, 'Removes contact from power dialer when they reply or opt out', 'contact_replied', 'active', 'Folder: Cold-Calling | Published | 276 enrolled'),

-- ── DISPOSITIONS ──────────────────────────────────────────────
('disps-deal-blaster',          NULL, 'Blasts contracted deal to warm buyer list', 'manual', 'active', 'Folder: Disps | Published'),
('disps-esign-assignment',      NULL, 'Sends assignment contract e-sign to cash buyer', 'manual', 'active', 'Folder: Disps | Published'),
('disps-cold-buyer-outreach',   NULL, 'Cold outreach sequence to build/expand buyer list', 'manual', 'active', 'Folder: Disps | Published'),

-- ── EMAIL ─────────────────────────────────────────────────────
('email-blaster',               NULL, 'Bulk email blast workflow', 'manual', 'draft', 'Folder: Email | Draft — not yet published'),

-- ── JV ────────────────────────────────────────────────────────
('jv-esign',                    NULL, 'Sends JV agreement e-sign to partner', 'manual', 'active', 'Folder: JV | Published'),
('jv-send-assignment',          NULL, 'Sends assignment contract to JV cash buyer', 'manual', 'active', 'Folder: JV | Published'),
('jv-deal-underwriter',         NULL, 'Routes deal to JV underwriting review', 'manual', 'active', 'Folder: JV | Published'),
('jv-new-lead-text',            NULL, 'SMS notification when a new JV lead comes in', 'contact_created', 'active', 'Folder: JV | Published'),

-- ── ON-MARKET ─────────────────────────────────────────────────
('om-dual-agency-lowball',      NULL, 'Low ball offer sequence for on-market dual agency deals', 'manual', 'active', 'Folder: On-Market | Published'),
('om-dual-agency-blaster',      NULL, 'Tags + blasts dual agency leads', 'manual', 'active', 'Folder: On-Market | Published'),
('om-followup-graveyard',       NULL, 'Long-term follow-up for stale on-market leads', 'scheduled', 'active', 'Folder: On-Market | Published'),
('om-loi-blaster-sub2',         NULL, 'LOI blast for Subject-To on-market leads', 'manual', 'active', 'Folder: On-Market | Published'),
('om-lowball-blaster-tag',      NULL, 'Tags + blasts low ball on-market leads', 'manual', 'active', 'Folder: On-Market | Published'),
('om-lowball-high-equity',      NULL, 'Low ball blast targeting high-equity on-market properties', 'manual', 'active', 'Folder: On-Market | Published'),
('om-esign',                    NULL, 'On-market offer e-sign workflow', 'manual', 'active', 'Folder: On-Market | Published'),

-- ── PPL (Pay Per Lead) ────────────────────────────────────────
('ppl-send-assignment',         NULL, 'Sends assignment contract to buyer for PPL deal', 'manual', 'active', 'Folder: PPL | Published'),
('ppl-followup-daily',          NULL, 'Daily follow-up sequence for PPL leads', 'scheduled', 'active', 'Folder: PPL | Published'),
('ppl-followup-monthly',        NULL, 'Monthly follow-up for long-term PPL nurture', 'scheduled', 'active', 'Folder: PPL | Published'),
('ppl-followup-weekly',         NULL, 'Weekly follow-up for PPL leads', 'scheduled', 'active', 'Folder: PPL | Published'),
('ppl-no-answer-cold',          NULL, 'No-answer sequence for cold call PPL leads', 'missed_call', 'active', 'Folder: PPL | Published'),
('ppl-no-answer-non-cold',      NULL, 'No-answer sequence for non-cold-call PPL leads', 'missed_call', 'active', 'Folder: PPL | Published'),
('ppl-send-contract',           NULL, 'Automated contract send for PPL deals', 'manual', 'active', 'Folder: PPL | Published'),
('ppl-refund-stage',            NULL, 'Refund stage workflow for PPL leads that do not close', 'manual', 'active', 'Folder: PPL | Published'),

-- ── SMS ───────────────────────────────────────────────────────
('sms-ai-bot',                  NULL, 'AI SMS bot — responds to inbound SMS using conversation AI', 'contact_replied', 'active', 'Folder: SMS | Published — KEY: AI auto-responder'),
('sms-ai-bot-landlords',        NULL, 'AI SMS bot variant tuned for landlord leads', 'contact_replied', 'active', 'Folder: SMS | Published'),
('sms-drop-no-answer',          NULL, 'SMS drop when call goes unanswered', 'missed_call', 'active', 'Folder: SMS | Published | 176 enrolled'),
('sms-blast-bulk',              NULL, 'PRIMARY BLAST WORKFLOW — bulk outbound SMS to cold leads. USE THIS for he+tax-delinquent blast.', 'manual', 'active', 'Folder: SMS | Published | 176 enrolled | NEED UUID from XLeads URL'),
('sms-blast-bulk-hard-mode',    NULL, 'Hard mode variant of bulk SMS blast', 'manual', 'draft', 'Folder: SMS | Draft'),
('twilio-validation',           NULL, 'Twilio number validation workflow', 'manual', 'active', 'Folder: SMS | Published')

ON CONFLICT (name) DO NOTHING;
