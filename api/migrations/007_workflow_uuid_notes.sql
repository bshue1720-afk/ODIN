-- ═══════════════════════════════════════════════════════════════
-- ODIN Migration 007: Update workflow_registry with step details
-- and partial UUIDs extracted from XLeads screenshots 2026-05-24.
-- All UUIDs marked partial — verify before using in blast commands.
-- ═══════════════════════════════════════════════════════════════

-- ── SMS FOLDER ────────────────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Custom Tag (is any bot). Steps: SMS → Wait → Conversation AI → branch Contact Reply/Time Out 4min → END. Published.'
WHERE name = 'sms-ai-bot';

UPDATE workflow_registry SET
  notes = 'Trigger: Custom Tag (is any landlord). Steps: SMS → Wait → Conversation AI → branch Contact Reply/Time Out 4min → END. Published.'
WHERE name = 'sms-ai-bot-landlords';

UPDATE workflow_registry SET
  notes = 'Trigger: Call Details (Direction=Outgoing, Status contains no-answer). Steps: SMS (1 message) → END. Simple auto-drop on missed outbound call. 176 enrolled.'
WHERE name = 'sms-drop-no-answer';

UPDATE workflow_registry SET
  notes = '*** PRIMARY BLAST WORKFLOW *** Published, 176 enrolled. UUID not captured in screenshots — click into it in XLeads and copy from URL. Complex tag-based routing with 8am-5pm time window and daily send limit.'
WHERE name = 'sms-blast-bulk';

UPDATE workflow_registry SET
  notes = 'Trigger: Number Validation. Steps: Enable/Disable DND → END. Validates Twilio numbers on contact creation. Published.'
WHERE name = 'twilio-validation';

-- ── ACQUISITIONS FOLDER ───────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Contact Tag (folder=acquisition). Steps: Create or Update Opportunity → END. One-step: AI-qualified tag → creates opp on Deal Board. Published.'
WHERE name = 'acq-ai-qualified-seller';

UPDATE workflow_registry SET
  notes = 'Trigger: Missed incoming call. Steps: Multi-step SMS sequence. Auto-fires when seller calls and you miss it. Published.'
WHERE name = 'acq-missed-call-textback';

UPDATE workflow_registry SET
  notes = 'Trigger: Pipeline Stage Changed (Wholesaling pipeline → Send Contract to Seller stage). Steps: Send Documents & Contracts → END. Auto-sends seller contract on stage change. Published.'
WHERE name = 'acq-esign-to-seller';

UPDATE workflow_registry SET
  notes = 'Trigger: Scheduled/weekly cadence. Steps: Long multi-step drip. Weekly follow-up for acquisition pipeline leads. Published.'
WHERE name = 'acq-followup-weekly';

UPDATE workflow_registry SET
  notes = 'Pre-built XLeads recipe. Trigger: Appointment. Steps: Long sequence — confirmation + day-before reminder. Draft only.'
WHERE name = 'acq-appt-confirmation';

-- ── COLD CALLING FOLDER ───────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Custom Tag (folder=acquisition). Steps: Add to Power Dialer → Wait → Call → Wait → Call (multi-attempt). Published.'
WHERE name = 'cc-ai-followup-outbound';

UPDATE workflow_registry SET
  notes = 'Trigger: Scheduled/weekly. Steps: Long drip sequence with AI-assisted follow-up touches. Published.'
WHERE name = 'cc-ai-followup-weekly';

UPDATE workflow_registry SET
  notes = 'Trigger: Manual/tag. Steps: SMS → Wait → SMS (2 touches only). Simple double-tap to increase contact rates before calling. Published.'
WHERE name = 'cc-double-tap';

UPDATE workflow_registry SET
  notes = 'Trigger: Custom Tag + Call Dispositions. Steps: Add to Power Dialer → Contact List → END. MAIN DIALING ENGINE — 9513 total enrolled, 800 active. Published.'
WHERE name = 'cc-power-dialer';

UPDATE workflow_registry SET
  notes = 'Trigger: Manual/tag. Steps: Long triple-tap outreach sequence (many SMS/call steps). ROS = Reach Out System. Published.'
WHERE name = 'cc-ros-quick-tap';

UPDATE workflow_registry SET
  notes = 'Trigger: Call Details (Direction=Outgoing, Status contains completed). Steps: Remove Tag → END. Auto-removes contact from dialer when call completes. 276 enrolled. Published.'
WHERE name = 'cc-remove-dialer';

-- ── DISPOSITIONS FOLDER ───────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Manual/deal stage. Steps: Long multi-step blast to buyer list. Use when deal is under contract to notify warm buyers. Published.'
WHERE name = 'disps-deal-blaster';

UPDATE workflow_registry SET
  notes = 'Trigger: Pipeline Stage Changed (Wholesaling → Send Assignment stage). Steps: Send Documents & Contracts → END. Auto-sends assignment contract to buyer. Published.'
WHERE name = 'disps-esign-assignment';

UPDATE workflow_registry SET
  notes = 'Trigger: Manual/tag. Steps: Long multi-touch drip to cold buyers. Used to build and expand buyer list. Published.'
WHERE name = 'disps-cold-buyer-outreach';

-- ── EMAIL FOLDER ──────────────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Contact Tag + conditions. Steps: Email sends + waits (multi-step). Draft only — not yet published. Email equivalent of SMS blaster.'
WHERE name = 'email-blaster';

-- ── JV FOLDER ─────────────────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Pipeline Stage Changed. Steps: Wait → Send → Add Tag → Wait → END. JV agreement e-sign flow. Published.'
WHERE name = 'jv-esign';

UPDATE workflow_registry SET
  notes = 'Trigger: Manual/deal stage. Steps: Complex multi-step underwriting analysis. Routes deal through JV underwriting review. Published.'
WHERE name = 'jv-deal-underwriter';

UPDATE workflow_registry SET
  notes = 'Trigger: Underwriting result (fail). Steps: Multi-step fallback sequence. Fires when deal fails JV underwriting. Published.'
WHERE name = 'jv-new-lead-text';

-- Fix jv-new-lead-text (was incorrectly used above) — reset and update properly
UPDATE workflow_registry SET
  notes = 'Trigger: Pipeline Stage Changed (JV pipeline → Approved stage). Steps: SMS → END. Simple one-touch SMS when JV lead is approved. Published.'
WHERE name = 'jv-new-lead-text';

-- ── PPL FOLDER ────────────────────────────────────────────────
UPDATE workflow_registry SET
  notes = 'Trigger: Pipeline Stage Changed (Send Assignment stage). Steps: Send Documents & Contracts → END. Auto assignment send for JV/PPL deals. Published.'
WHERE name = 'ppl-send-assignment';

UPDATE workflow_registry SET
  notes = 'Trigger: Scheduled/daily. Steps: Long daily touchpoint drip sequence. Daily follow-up for PPL leads. Published.'
WHERE name = 'ppl-followup-daily';

UPDATE workflow_registry SET
  notes = 'Trigger: Scheduled/monthly. Steps: Long monthly cadence sequence. Long-term monthly nurture for unconverted PPL leads. Published.'
WHERE name = 'ppl-followup-monthly';

UPDATE workflow_registry SET
  notes = 'Trigger: Scheduled/weekly. Steps: Long weekly cadence sequence. Weekly follow-up for PPL leads. Published.'
WHERE name = 'ppl-followup-weekly';

UPDATE workflow_registry SET
  notes = 'Trigger: Missed call / no-answer on cold call. Steps: Multi-step SMS + follow-up sequence. Fires when cold call PPL lead does not answer. Published.'
WHERE name = 'ppl-no-answer-cold';

UPDATE workflow_registry SET
  notes = 'Trigger: Missed call / no-answer on warm/inbound lead. Steps: Multi-step sequence with different tone than cold version. Published.'
WHERE name = 'ppl-no-answer-non-cold';

-- ── NEW: COLD CALL INTAKE FORM (not in original seed) ─────────
INSERT INTO workflow_registry (name, xleads_id, purpose, trigger, status, notes)
VALUES (
  'cc-intake-form',
  NULL,
  'Intake workflow for new cold call leads. Fires on form submission or tag.',
  'form_submitted',
  'active',
  'Found in screenshots but not in folder listings — may live at root level. UUID partial: fb801445-205d-4315-b140-2c2a6d1a81a (verify). Published.'
)
ON CONFLICT (name) DO NOTHING;
