"""
ODIN — Outbound Voice Briefing (Twilio REST)
Places an outbound phone call to Brock and reads a message aloud via TwiML.
This is the "Apex outbound voice" parity piece — ODIN can call YOU with the
morning briefing or any alert, not just post to Slack/Discord.

Pure `requests` — no twilio SDK dependency. Env-gated: if creds are missing,
every function silently returns False so deploys never break.

Setup (Railway env vars):
  TWILIO_ACCOUNT_SID    — from twilio.com/console
  TWILIO_AUTH_TOKEN     — from twilio.com/console
  TWILIO_FROM_NUMBER    — a Twilio number you own, e.g. +19015551234
  BROCK_PHONE_NUMBER    — your cell, e.g. +19015559876
  ODIN_BASE_URL         — public URL of this API (for the TwiML callback),
                          e.g. https://odin-api-production-e85d.up.railway.app

Flow:
  call_briefing(text) → Twilio places a call to BROCK_PHONE_NUMBER →
  Twilio fetches {ODIN_BASE_URL}/api/voice/briefing?msg=... → that route
  returns TwiML <Say> with the briefing text → Brock hears it.

Cost: ~$0.013/min outbound US + number rental ~$1.15/mo. Pennies per briefing.
"""

import os
import requests

# Last briefing text staged for the TwiML callback. ODIN runs --workers 1,
# and the call is placed from the same process that serves /api/voice/briefing,
# so a module global is a safe, dependency-free hand-off.
_LAST_BRIEFING = ''


def is_available() -> bool:
    """True only if all Twilio creds + numbers are configured."""
    return all(os.environ.get(k) for k in (
        'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN',
        'TWILIO_FROM_NUMBER', 'BROCK_PHONE_NUMBER',
    ))


def call_briefing(text: str, to_number: str = '') -> bool:
    """
    Place an outbound call that reads `text` aloud.
    Returns True if Twilio accepted the call request, False otherwise.
    `text` is passed to the /api/voice/briefing TwiML route via query param.
    """
    if not is_available():
        return False  # not configured — silently skip

    global _LAST_BRIEFING
    _LAST_BRIEFING = text or ''

    sid    = os.environ['TWILIO_ACCOUNT_SID']
    token  = os.environ['TWILIO_AUTH_TOKEN']
    from_n = os.environ['TWILIO_FROM_NUMBER']
    to_n   = to_number or os.environ['BROCK_PHONE_NUMBER']
    base   = os.environ.get(
        'ODIN_BASE_URL',
        'https://odin-api-production-e85d.up.railway.app',
    ).rstrip('/')

    # Twilio fetches this URL when the call connects; it returns TwiML.
    twiml_url = f'{base}/api/voice/briefing'

    try:
        resp = requests.post(
            f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json',
            auth=(sid, token),
            data={
                'To':   to_n,
                'From': from_n,
                'Url':  twiml_url,
                # Stash the message in a status callback param Twilio echoes back
                'StatusCallbackEvent': 'completed',
            },
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except Exception:
        return False


def build_twiml(message: str = '') -> str:
    """
    Build a TwiML response that reads `message` aloud, then hangs up.
    Used by the /api/voice/briefing Flask route. If `message` is empty,
    falls back to the last briefing staged by call_briefing().
    """
    import re
    from xml.sax.saxutils import escape

    message = message or _LAST_BRIEFING

    # Strip Slack/markdown so the voice reads cleanly
    clean = re.sub(r'\*+', '', message or '')
    clean = re.sub(r'_([^_]+)_', r'\1', clean)
    clean = re.sub(r'`[^`]+`', '', clean)
    clean = re.sub(r'[•◦▪]', '', clean)
    clean = re.sub(r'\n{2,}', '. ', clean)
    clean = re.sub(r'\n', '. ', clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()
    if not clean:
        clean = 'Good morning. No briefing items today.'
    # Twilio <Say> handles ~4000 chars; keep it sane
    clean = clean[:1500]

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Say voice="Polly.Matthew">{escape(clean)}</Say>'
        '<Pause length="1"/>'
        '<Say voice="Polly.Matthew">That is your briefing. Goodbye.</Say>'
        '</Response>'
    )
