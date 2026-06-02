"""
ODIN Skill — Email Drafter
Drafts context-aware emails for Brock: seller follow-ups, buyer outreach, partner comms.
Uses Sonnet for quality.
"""

import os
import anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        _client = anthropic.Anthropic(api_key=key)
    return _client


SYSTEM_PROMPT = """You are ODIN's email writer for Shue Box LLC (Memphis TN real estate wholesaling).
Write emails in Brock's voice: direct, warm, honest, brief. No corporate speak. Real person writing to real people.

Email types you handle:
- seller_followup: Following up with a motivated seller after a call
- buyer_outreach:  Reaching out to cash buyers with a new deal
- buyer_followup:  Following up with a buyer who showed interest
- partner_outreach: Reaching out to a potential JV or wholesale partner
- general:         Any other context-based email

Always:
- Keep it short (3-5 sentences max for most emails)
- Include a clear single ask or next step
- Sound like a real person, not a template

Output EXACTLY:
Subject: [subject line]
---
[email body]"""


def draft(recipient_type: str, context: str,
          recipient_name: str = '', tone: str = 'warm',
          tone_profile: str = '') -> dict:
    """
    Draft an email.

    Args:
        recipient_type: seller_followup | buyer_outreach | buyer_followup |
                        partner_outreach | general
        context:        What the email is about — deal details, last conversation, etc.
        recipient_name: Their first name (for personalization)
        tone:           warm | urgent | professional

    Returns dict with subject and body.
    """
    client = _get_client()

    type_context = {
        'seller_followup':  'Follow up with a motivated seller after initial call.',
        'buyer_outreach':   'Reach out to a cash buyer with a new deal opportunity.',
        'buyer_followup':   'Follow up with a buyer who expressed interest in a deal.',
        'partner_outreach': 'Reach out to a potential wholesale or JV partner.',
        'general':          'Write a context-appropriate email.',
    }.get(recipient_type, 'Write a context-appropriate email.')

    name_line  = f'Recipient first name: {recipient_name}\n' if recipient_name else ''
    tone_line  = f'Tone profile (Brock\'s style): {tone_profile}\n' if tone_profile else ''

    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': (
                f'Email type: {type_context}\n'
                f'{name_line}'
                f'{tone_line}'
                f'Tone: {tone}\n\n'
                f'Context:\n{context}'
            )
        }]
    )

    raw = msg.content[0].text.strip()
    subject = ''
    body = ''

    lines = raw.splitlines()
    if lines and lines[0].startswith('Subject:'):
        subject = lines[0].replace('Subject:', '').strip()
        # Skip the --- separator if present
        start = 2 if len(lines) > 1 and lines[1].strip() == '---' else 1
        body = '\n'.join(lines[start:]).strip()
    else:
        body = raw

    return {
        'recipient_type': recipient_type,
        'recipient_name': recipient_name,
        'subject':        subject,
        'body':           body,
        'raw':            raw,
    }


def format_slack(result: dict) -> str:
    lines = ['*📧 Email Draft*']
    if result['recipient_name']:
        lines.append(f'To: {result["recipient_name"]}')
    lines.append(f'Subject: *{result["subject"]}*')
    lines.append('---')
    lines.append(result['body'])
    return '\n'.join(lines)
