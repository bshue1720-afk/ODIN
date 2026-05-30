"""
ODIN Skill — Content Engine
Turns deal summaries or voice transcripts into LinkedIn posts + email blurbs.
Uses Sonnet for quality content.
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


SYSTEM_PROMPT = """You are ODIN's content writer for Shue Box LLC.
Brock is a real estate wholesaler in Memphis TN working toward his first deal.
He is building ODIN, an autonomous AI system to run his business.

BROCK'S VOICE: Direct, honest, grounded. Not hype-y. Shares real numbers and real struggles.
Talks like a normal person, not a "guru". Mentions Memphis specifically. Authentic.

When given a deal, transcript, or experience — write:
1. THREE LinkedIn posts (vary the angle — story, lesson, tactical)
2. ONE email blurb (for buyer list — concise, deal-focused)

FORMAT:
--- POST 1 ---
[post content]

--- POST 2 ---
[post content]

--- POST 3 ---
[post content]

--- EMAIL BLURB ---
Subject: [subject line]
[email body — 3-5 sentences max]"""


def generate(source: str, source_type: str = 'deal_summary') -> dict:
    """
    Generate content from a deal summary, transcript, or experience note.

    Args:
        source:      Raw text — deal summary, call transcript, or experience
        source_type: 'deal_summary' | 'transcript' | 'lesson' | 'milestone'

    Returns dict with posts list and email blurb.
    """
    client = _get_client()

    type_context = {
        'deal_summary': 'This is a real estate deal summary.',
        'transcript':   'This is a call transcript with a motivated seller.',
        'lesson':       'This is a lesson or insight Brock wants to share.',
        'milestone':    'This is a business milestone or achievement.',
    }.get(source_type, 'This is content from Brock\'s wholesaling business.')

    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1200,
        system=SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': f'{type_context}\n\nCreate content from this:\n\n{source}'
        }]
    )

    raw = msg.content[0].text.strip()

    # Parse sections
    posts = []
    email_subj = ''
    email_body = ''

    current_section = None
    current_lines = []

    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith('--- POST') and stripped.endswith('---'):
            if current_section and current_lines:
                if current_section.startswith('post'):
                    posts.append('\n'.join(current_lines).strip())
                elif current_section == 'email':
                    email_body = '\n'.join(current_lines).strip()
            current_section = f'post{len(posts)+1}'
            current_lines = []
        elif stripped == '--- EMAIL BLURB ---':
            if current_section and current_lines:
                posts.append('\n'.join(current_lines).strip())
            current_section = 'email'
            current_lines = []
        else:
            if current_section == 'email' and stripped.startswith('Subject:'):
                email_subj = stripped.replace('Subject:', '').strip()
            else:
                current_lines.append(line)

    # Flush last section
    if current_section == 'email' and current_lines:
        email_body = '\n'.join(current_lines).strip()
    elif current_section and current_lines:
        posts.append('\n'.join(current_lines).strip())

    return {
        'posts':         posts,
        'email_subject': email_subj,
        'email_body':    email_body,
        'raw':           raw,
    }


def format_slack(result: dict) -> str:
    lines = ['*✍️ Content Generated*', '']
    for i, post in enumerate(result['posts'], 1):
        lines.append(f'*LinkedIn Post {i}:*')
        lines.append(post)
        lines.append('')
    if result['email_subject']:
        lines.append(f'*Email — Subject:* {result["email_subject"]}')
        lines.append(result['email_body'])
    return '\n'.join(lines)
