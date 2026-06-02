"""
ODIN Core — Shared helpers: Slack posting, Haiku AI, agent runner, Slack verify.
No Flask routes. No circular imports.
"""
import os
import time
import hmac
import hashlib
import traceback as tb

import requests

from .db import get_db
from .discord_state import _discord_queues

SLACK_SIGNING_SECRET = os.environ.get('SLACK_SIGNING_SECRET', '')

MAYA_PROMPT = (
    "You're Maya with Shue Box LLC. Your whole job is to make selling a home feel easy and "
    "stress-free for people. Be warm, genuinely curious, and patient — a lot of folks you "
    "talk to are going through something tough and just need someone who actually listens. "
    "Keep your sentences short and natural, like a real conversation. Gently find out three "
    "things: what's motivating them to sell, when they need to move, and what price would "
    "work for them. If they're dealing with something hard — a divorce, financial stress, "
    "losing a family member — acknowledge that first before anything else. Never pressure "
    "anyone. If the timing isn't right, offer to follow up when it is. Never mention you're "
    "AI. You're Maya, and you genuinely care about helping people move on to their next chapter."
)


def _slack_verify(req) -> bool:
    """Verify that an incoming request is genuinely from Slack."""
    ts  = req.headers.get('X-Slack-Request-Timestamp', '')
    sig = req.headers.get('X-Slack-Signature', '')
    if not ts or not sig:
        return False
    if abs(time.time() - int(ts)) > 300:   # replay-attack guard
        return False
    base     = f'v0:{ts}:{req.get_data(as_text=True)}'
    expected = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def _slack_post(channel: str, text: str):
    """Fire a message to a Slack channel (or virtual discord:/tg: channel)."""
    if channel.startswith('discord:'):
        if channel in _discord_queues:
            _discord_queues[channel].append(text)
        return
    if channel.startswith('tg:'):
        import utils.telegram_notify as telegram_notify
        telegram_notify.send(channel[3:], text)
        return
    requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={
            'Authorization': f'Bearer {os.environ.get("SLACK_BOT_TOKEN", "")}',
            'Content-Type':  'application/json',
        },
        json={'channel': channel, 'text': text},
        timeout=5,
    )


def _haiku(prompt: str, max_tokens: int = 400) -> str:
    """Call Claude Haiku for short generative text."""
    try:
        import anthropic as _anthropic
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            return '(AI unavailable)'
        client = _anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f'(AI error: {e})'


def _run_agent(name: str, fn, input_summary: str = '',
               triggered_by: str = 'brock', **kwargs):
    """
    Call any agent function, log the result (success or error) to agent_logs.
    Returns the result dict on success. Re-raises exceptions so existing
    error handling in callers still works.
    """
    import utils.agent_log as agent_log
    start = time.monotonic()
    try:
        result = fn(**kwargs)
        duration = int((time.monotonic() - start) * 1000)
        output = ''
        if isinstance(result, dict):
            output = result.get('slack_text') or result.get('text') or str(result)
        agent_log.write(
            agent_name    = name,
            action        = input_summary[:200] if input_summary else name,
            status        = 'ok',
            input_summary = input_summary,
            output_summary= str(output)[:500],
            duration_ms   = duration,
            triggered_by  = triggered_by,
            get_db        = get_db,
        )
        return result
    except Exception as exc:
        duration = int((time.monotonic() - start) * 1000)
        agent_log.write(
            agent_name    = name,
            action        = input_summary[:200] if input_summary else name,
            status        = 'error',
            input_summary = input_summary,
            error_detail  = tb.format_exc(),
            duration_ms   = duration,
            triggered_by  = triggered_by,
            get_db        = get_db,
        )
        raise
