"""
ODIN — Telegram Channel (second command channel, Apex multi-channel parity)
Lets Brock run ODIN commands from Telegram and receive alerts there — a true
second control surface next to Slack. Telegram is chosen over WhatsApp because
it needs zero business verification: create a bot with @BotFather, drop two env
vars, done. (WhatsApp requires Meta Business API approval — slower.)

Pure `requests` — no SDK. Env-gated: missing token → every call returns False.

Setup:
  1. In Telegram, message @BotFather → /newbot → get a bot token.
  2. Railway env: TELEGRAM_BOT_TOKEN = 123456:ABC...
  3. Message your new bot once, then visit
     https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat id.
  4. Railway env: TELEGRAM_BROCK_CHAT_ID = 123456789
  5. Register the webhook (one time):
       curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<ODIN_BASE_URL>/api/telegram/webhook"
  6. Done — text commands to the bot; they route through the same parser as Slack.

Usage:
  telegram_notify.post("message")        — push an alert to Brock's chat
  telegram_notify.send(chat_id, "text")  — reply to a specific chat
"""

import os
import re
import requests


def _token() -> str:
    return os.environ.get('TELEGRAM_BOT_TOKEN', '')


def is_available() -> bool:
    return bool(_token())


def _api(method: str) -> str:
    return f'https://api.telegram.org/bot{_token()}/{method}'


def _slack_to_telegram(text: str) -> str:
    """Convert Slack markdown to plain text Telegram is happy with."""
    text = re.sub(r'\*([^*]+)\*', r'\1', text)   # bold
    text = re.sub(r'_([^_]+)_', r'\1', text)     # italic
    text = re.sub(r'`([^`]+)`', r'\1', text)     # inline code
    text = re.sub(r'<@\w+>', '', text)           # slack mentions
    return text.strip()


def send(chat_id, text: str) -> bool:
    """Send a message to a specific Telegram chat id. Returns True on success."""
    if not is_available() or not chat_id:
        return False
    body = _slack_to_telegram(text)
    # Telegram max message length is 4096
    if len(body) > 4000:
        body = body[:3997] + '…'
    try:
        resp = requests.post(
            _api('sendMessage'),
            json={'chat_id': chat_id, 'text': body, 'disable_web_page_preview': True},
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


def post(text: str) -> bool:
    """Push an alert to Brock's configured Telegram chat."""
    return send(os.environ.get('TELEGRAM_BROCK_CHAT_ID', ''), text)


def parse_update(update: dict) -> dict:
    """
    Pull the relevant bits out of a Telegram webhook update.
    Returns { 'chat_id': ..., 'text': ..., 'from_id': ... } or {} if not a message.
    """
    msg = update.get('message') or update.get('edited_message') or {}
    chat = msg.get('chat', {})
    text = (msg.get('text') or '').strip()
    if not text or not chat.get('id'):
        return {}
    return {
        'chat_id': chat.get('id'),
        'text':    text,
        'from_id': str(msg.get('from', {}).get('id', '')),
    }
