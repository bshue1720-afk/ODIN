"""
ODIN — Discord Webhook Notifier
Mirrors key Slack alerts to Discord so Eddie (and future team members)
can get notified in Discord without needing Slack access.

Setup:
  1. In Discord: Server Settings → Integrations → Webhooks → New Webhook
  2. Set DISCORD_WEBHOOK_BROCK and/or DISCORD_WEBHOOK_TEAM in Railway env vars
  3. Done — no OAuth, no bot registration needed

Env vars (set in Railway):
  DISCORD_WEBHOOK_BROCK   — #odin-alerts or your private channel
  DISCORD_WEBHOOK_EDDIE   — Eddie's notification channel (for hot lead alerts)
  DISCORD_WEBHOOK_TEAM    — shared team channel (optional)

Usage:
  discord_notify.post(message)              — posts to Brock's channel
  discord_notify.post(message, target='eddie')   — posts to Eddie's channel
  discord_notify.post(message, target='team')    — posts to team channel
"""

import os
import requests

_WEBHOOKS = {
    'brock': lambda: os.environ.get('DISCORD_WEBHOOK_BROCK', ''),
    'eddie': lambda: os.environ.get('DISCORD_WEBHOOK_EDDIE', ''),
    'team':  lambda: os.environ.get('DISCORD_WEBHOOK_TEAM', ''),
}


def post(message: str, target: str = 'brock', tts: bool = False) -> bool:
    """
    Post a message to a Discord channel via webhook.
    Returns True on success, False if webhook not configured or failed.
    Slack markdown is auto-converted to Discord markdown.
    tts=True: Discord reads the message aloud (text-to-speech) in the channel.
    """
    url = _WEBHOOKS.get(target, _WEBHOOKS['brock'])()
    if not url:
        return False  # Webhook not configured — silently skip

    content = _slack_to_discord(message)
    # Discord max message length is 2000 chars
    if len(content) > 1900:
        content = content[:1897] + '…'

    try:
        resp = requests.post(
            url,
            json={'content': content, 'username': 'ODIN', 'tts': tts},
            timeout=5,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def post_tts(message: str, target: str = 'brock') -> bool:
    """
    Post a TTS (text-to-speech) message to Discord.
    Discord reads this aloud in the channel when you have the app open with audio on.
    Uses a condensed version of the message — strips markdown formatting for clean audio.
    """
    import re
    # Strip markdown for cleaner TTS audio
    clean = re.sub(r'\*+', '', message)           # remove bold/italic asterisks
    clean = re.sub(r'_([^_]+)_', r'\1', clean)   # remove italic underscores
    clean = re.sub(r'`[^`]+`', '', clean)         # remove inline code
    clean = re.sub(r'\n{3,}', '\n\n', clean)      # collapse extra blank lines
    clean = clean.strip()
    if len(clean) > 1900:
        clean = clean[:1897] + '…'
    return post(clean, target=target, tts=True)


def post_embed(title: str, description: str, color: int = 0x9B59B6,
               target: str = 'brock') -> bool:
    """
    Post a rich embed to Discord (purple by default — ODIN brand color).
    color: hex int e.g. 0xFF4444 = red, 0x00CC44 = green, 0xFFAA00 = orange
    """
    url = _WEBHOOKS.get(target, _WEBHOOKS['brock'])()
    if not url:
        return False

    try:
        resp = requests.post(
            url,
            json={
                'username': 'ODIN',
                'embeds': [{
                    'title':       title[:256],
                    'description': description[:4096],
                    'color':       color,
                }]
            },
            timeout=5,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def hot_lead(sender_name: str, phone: str, body: str, score: dict,
             contact_id: str, target: str = 'brock') -> bool:
    """Formatted hot/warm lead Discord alert."""
    total = score.get('total', 0)
    tier  = score.get('tier', 'Unknown')
    emoji = '🔥' if tier == 'Hot' else ('⚡' if tier == 'Warm' else '🧊')
    color = 0xFF4444 if tier == 'Hot' else (0xFFAA00 if tier == 'Warm' else 0x888888)

    desc = (
        f'**Message:** {body[:300]}\n\n'
        f'**Score:** M:{score.get("motivation",0)}  '
        f'C:{score.get("condition",0)}  '
        f'T:{score.get("timeline",0)}  '
        f'P:{score.get("price",0)}  = **{total}/10**\n\n'
        f'**Phone:** {phone}\n'
        f'**Contact ID:** `{contact_id}`'
    )
    return post_embed(
        title=f'{emoji} {tier} Lead — {sender_name}',
        description=desc,
        color=color,
        target=target,
    )


def _slack_to_discord(text: str) -> str:
    """
    Convert Slack markdown to Discord markdown.
    Slack uses *bold*, _italic_, `code`, ```code block```
    Discord uses **bold**, *italic*, `code`, ```code block```
    """
    import re
    # Slack *bold* → Discord **bold** (but skip ***triple***)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'**\1**', text)
    # Slack _italic_ → Discord *italic*
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'*\1*', text)
    # Slack :emoji: stays as-is (Discord supports Unicode emoji)
    # Strip Slack channel/user links <#C123|channel> → #channel
    text = re.sub(r'<#\w+\|([^>]+)>', r'#\1', text)
    text = re.sub(r'<@\w+>', '@user', text)
    # Slack URLs <url|text> → [text](url)
    text = re.sub(r'<(https?://[^|>]+)\|([^>]+)>', r'[\2](\1)', text)
    text = re.sub(r'<(https?://[^>]+)>', r'\1', text)
    return text
