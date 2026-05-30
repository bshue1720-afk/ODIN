"""
ODIN — Autonomous Email Triage (Apex "email" parity)
Reads the inbox, classifies each thread with Claude Haiku, and drafts replies.
DRAFT-BY-DEFAULT: ODIN never auto-sends unless EMAIL_AUTO_SEND=true AND the
thread is classified low-risk. Everything else is staged for Brock to approve.

Built on gmail_client.py (already wired to shueboxllc@gmail.com via OAuth).
No new dependencies — uses the existing Gmail service + the heartbeat _haiku().

Env vars:
  EMAIL_AUTO_SEND        — 'true' to allow auto-sending low-risk replies
                           (default OFF — draft only, always safe)
  EMAIL_TRIAGE_MAX       — max threads to scan per run (default 10)

Returns a Slack-ready summary string from run_triage().
"""

import os
import json

try:
    from . import gmail_client
except ImportError:
    import gmail_client


# Categories ODIN sorts inbound email into
_CATEGORIES = ('seller_lead', 'buyer_lead', 'vendor', 'personal', 'spam', 'other')


def _classify_and_draft(thread: dict, haiku_fn) -> dict:
    """
    Ask Haiku to classify one thread and (if it warrants a reply) draft one.
    Returns { category, urgency, needs_reply, draft, risk }.
    """
    prompt = (
        'You are ODIN, an email triage assistant for a real estate wholesaling + '
        'business operations company (Shue Box LLC, owner Brock).\n\n'
        f'Email:\nFrom: {thread.get("from","")}\n'
        f'Subject: {thread.get("subject","")}\n'
        f'Snippet: {thread.get("snippet","")}\n\n'
        'Return ONLY a JSON object with these keys:\n'
        '  category: one of seller_lead, buyer_lead, vendor, personal, spam, other\n'
        '  urgency: high, medium, or low\n'
        '  needs_reply: true or false\n'
        '  risk: low, medium, or high (high = anything involving money, contracts, '
        'legal, or commitments — never auto-send these)\n'
        '  draft: a short professional reply (2-4 sentences, warm and relatable tone) '
        'if needs_reply is true, else empty string\n'
        'No prose, JSON only.'
    )
    raw = haiku_fn(prompt, max_tokens=400)
    try:
        start = raw.find('{')
        end   = raw.rfind('}')
        data  = json.loads(raw[start:end + 1]) if start >= 0 else {}
    except Exception:
        data = {}
    return {
        'category':    data.get('category', 'other'),
        'urgency':     data.get('urgency', 'low'),
        'needs_reply': bool(data.get('needs_reply', False)),
        'risk':        data.get('risk', 'high'),
        'draft':       (data.get('draft') or '').strip(),
    }


def run_triage(haiku_fn, max_threads: int = None) -> str:
    """
    Scan recent inbox threads, classify + draft, optionally auto-send low-risk.
    `haiku_fn(prompt, max_tokens)` is passed in from app/heartbeat (the existing
    Haiku helper) so this module has no anthropic dependency of its own.
    Returns a Slack-ready summary.
    """
    if not gmail_client.is_available():
        return '📧 Email triage skipped — Gmail not authorized (run gmail_client.py --setup).'

    max_threads = max_threads or int(os.environ.get('EMAIL_TRIAGE_MAX', 10))
    auto_send   = os.environ.get('EMAIL_AUTO_SEND', '').lower() == 'true'

    try:
        threads = gmail_client.get_recent_threads(max_results=max_threads, query='in:inbox is:unread')
    except Exception as e:
        return f'📧 Email triage error reading inbox: {type(e).__name__}: {e}'

    if not threads:
        return '📧 Email triage: inbox clear — no unread threads.'

    sent, drafted, skipped = [], [], []
    for t in threads:
        result = _classify_and_draft(t, haiku_fn)
        subj   = (t.get('subject') or '(no subject)')[:50]

        if not result['needs_reply']:
            skipped.append(f'• _{result["category"]}_ — {subj}')
            continue

        can_auto = auto_send and result['risk'] == 'low' and result['urgency'] != 'high'
        if can_auto and result['draft']:
            from_addr = _extract_email(t.get('from', ''))
            try:
                gmail_client.send(from_addr, f'Re: {t.get("subject","")}', result['draft'])
                sent.append(f'• ✅ {result["category"]} — {subj}')
            except Exception:
                drafted.append(f'• ⚠️ send failed, draft ready — {subj}\n  ›_{result["draft"][:120]}_')
        else:
            tag = '🔒 high-risk' if result['risk'] == 'high' else '📝 draft'
            drafted.append(
                f'• {tag} [{result["category"]}/{result["urgency"]}] — {subj}\n'
                f'  ›_{result["draft"][:140]}_'
            )

    lines = [f'📧 *Email Triage — {len(threads)} unread*']
    if sent:
        lines.append(f'\n*Auto-sent ({len(sent)}):*\n' + '\n'.join(sent))
    if drafted:
        lines.append(f'\n*Drafts for your approval ({len(drafted)}):*\n' + '\n'.join(drafted))
    if skipped:
        lines.append(f'\n*No reply needed ({len(skipped)}):*\n' + '\n'.join(skipped[:8]))
    if not auto_send:
        lines.append('\n_Auto-send is OFF. Set EMAIL_AUTO_SEND=true to let ODIN send low-risk replies._')
    return '\n'.join(lines)


def _extract_email(from_header: str) -> str:
    """Pull the bare email out of a 'Name <addr@x.com>' header."""
    import re
    m = re.search(r'<([^>]+)>', from_header)
    if m:
        return m.group(1)
    m = re.search(r'[\w.\-+]+@[\w.\-]+', from_header)
    return m.group(0) if m else from_header
