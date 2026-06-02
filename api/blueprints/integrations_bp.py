"""ODIN Blueprint — External integration routes: Slack events, Telegram webhook, Voice briefing."""
import os

import psycopg2.extras
from flask import Blueprint, request, jsonify, Response

from core.db      import get_db, log_action
from core.helpers import _slack_post, _slack_verify, SLACK_SIGNING_SECRET
from utils.slack_commands import parse_command
from utils.slack_templates import CHANNELS
import utils.business_advisor as advisor
import utils.memory           as memory
import utils.telegram_notify  as telegram_notify
import utils.twilio_voice     as twilio_voice
import utils.discord_notify   as discord_notify
import utils.agent_builder    as agent_builder
import utils.xleads           as xleads
from commands.executor import _execute_slack_command

bp = Blueprint('integrations', __name__)


@bp.route('/api/slack/events', methods=['POST'])
def slack_events():
    """
    Receives all Slack events (DMs + @mentions).
    1. Verifies signature  2. Handles URL challenge  3. Parses command and executes
    """
    if SLACK_SIGNING_SECRET and not _slack_verify(request):
        return jsonify({'error': 'Invalid signature'}), 403

    data       = request.get_json(silent=True) or {}
    event_type = data.get('type')

    if request.headers.get('X-Slack-Retry-Num'):
        return jsonify({'ok': True})

    if event_type == 'url_verification':
        return jsonify({'challenge': data.get('challenge')})

    if event_type == 'event_callback':
        event   = data.get('event', {})
        subtype = event.get('subtype')

        if event.get('bot_id') or subtype in ('bot_message', 'message_changed', 'message_deleted'):
            return jsonify({'ok': True})

        msg_type  = event.get('type')
        text      = (event.get('text') or '').strip()
        channel   = event.get('channel', '')
        slack_uid = event.get('user', '')

        if msg_type in ('app_mention', 'message') and text:
            katelyn_uid = os.environ.get('KATELYN_SLACK_UID', '')
            is_katelyn  = bool(katelyn_uid and slack_uid == katelyn_uid)
            cmd         = parse_command(text)
            is_command  = cmd.get('action') != 'unknown'

            if is_command:
                log_action(None, 'slack_command', data={'action': cmd.get('action'), 'raw': text[:200]})
                _execute_slack_command(cmd, reply_channel=channel, sender_uid=slack_uid)
            else:
                first_word   = text.strip().lower().split()[0] if text.strip() else ''
                rest_of_text = text.strip()[len(first_word):].strip()
                try:
                    custom_result = agent_builder.load_and_run(
                        trigger_keyword=first_word,
                        params=rest_of_text,
                        get_db=get_db,
                        xleads_mod=xleads,
                    )
                    if 'No active agent found' not in custom_result.get('slack_text', ''):
                        log_action(None, 'custom_agent_run',
                                   data={'trigger': first_word, 'user': slack_uid})
                        _slack_post(channel, custom_result['slack_text'])
                        if custom_result.get('discord_text'):
                            discord_notify.post(custom_result['discord_text'])
                        return jsonify({'ok': True})
                except Exception:
                    pass

                log_action(None, 'odin_chat', data={'user': slack_uid, 'msg': text[:200]})
                try:
                    db_user_id = None
                    try:
                        conn = get_db()
                        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                        cur.execute("SELECT id FROM users WHERE slack_uid = %s", (slack_uid,))
                        row = cur.fetchone()
                        conn.close()
                        if row:
                            db_user_id = str(row['id'])
                    except Exception:
                        pass

                    mem_rows    = memory.load(db_user_id, get_db) if db_user_id else []
                    mem_context = memory.format_for_prompt(mem_rows)

                    spoke = 'katelyn_business' if is_katelyn else 'real_estate'
                    reply = advisor.chat(text, memory_context=mem_context, spoke=spoke)
                    _slack_post(channel, reply)

                    if db_user_id:
                        try:
                            memory.extract_and_save(db_user_id, text, reply, get_db)
                        except Exception:
                            pass

                except RuntimeError:
                    _slack_post(channel,
                        "⚠️ ODIN's business advisor needs an Anthropic API key. "
                        "Ask Brock to add ANTHROPIC_API_KEY to Railway.")
                except Exception as e:
                    _slack_post(channel, f'⚠️ Advisor error: {e}')

    return jsonify({'ok': True})


@bp.route('/api/voice/briefing', methods=['GET', 'POST'])
def voice_briefing():
    """Twilio fetches this when an ODIN outbound call connects."""
    msg = request.values.get('msg', '')
    return Response(twilio_voice.build_twiml(msg), mimetype='text/xml')


@bp.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Receives Telegram bot updates. Only Brock's chat id may run commands."""
    update = request.get_json(silent=True) or {}
    parsed = telegram_notify.parse_update(update)
    if not parsed:
        return jsonify({'ok': True})

    chat_id = parsed['chat_id']
    text    = parsed['text']
    from_id = parsed['from_id']

    allowed = os.environ.get('TELEGRAM_BROCK_CHAT_ID', '')
    if allowed and str(chat_id) != str(allowed) and from_id != str(allowed):
        telegram_notify.send(chat_id, '🔒 This ODIN bot is private.')
        return jsonify({'ok': True})

    try:
        cmd = parse_command(text)
        if cmd.get('action') in (None, 'unknown'):
            telegram_notify.send(chat_id, "🤔 Unknown command. Send 'help' for the list.")
            return jsonify({'ok': True})
        log_action(None, 'telegram_command', data={'action': cmd.get('action'), 'raw': text[:200]})
        _execute_slack_command(cmd, reply_channel=f'tg:{chat_id}',
                               sender_uid=os.environ.get('BROCK_SLACK_UID', 'U0B5C32BJ6B'))
    except Exception as e:
        telegram_notify.send(chat_id, f'❌ Error: {type(e).__name__}: {e}')

    return jsonify({'ok': True})
