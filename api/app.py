"""
ODIN Phase 3 — Flask API
Railway deployment: Procfile → gunicorn app:app
Local dev:         python app.py (requires DATABASE_URL in .env)
"""
import os, uuid, secrets, json, hmac, hashlib, time
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import jwt
import psycopg2
import psycopg2.extras
import psycopg2.sql
import requests
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, g, Response

from utils.permissions import check_permission_role
from utils.approval_router import route_approval, build_offer_script
import utils.xleads as xleads
from utils.slack_commands import parse_command, is_negative_reply, HELP_TEXT
import utils.business_advisor as advisor
import utils.business_scout as scout_agent
import utils.income_calculator as income_agent
import utils.business_builder as builder_agent
import utils.automation_auditor as auditor_agent
import utils.it_agent as it_agent
import utils.business_plan_pdf as biz_pdf
import utils.agent_builder as agent_builder
import utils.agent_log as agent_log
from utils.slack_templates import (
    send_slack_notification,
    hot_lead_notification,
    warm_lead_notification,
    approval_response_notification,
    new_user_invite_notification,
    CHANNELS,
)
# RE Skill Modules (Layer 4)
import utils.lao_calculator    as lao_calc
import utils.arv_analyzer      as arv_analyzer
import utils.offer_calculator  as offer_calc
import utils.lead_scorer       as lead_scorer
import utils.script_generator  as script_gen
import utils.buyer_matcher     as buyer_matcher
import utils.content_engine    as content_engine
import utils.email_drafter     as email_drafter
# Heartbeat (Layer 5)
import utils.heartbeat as heartbeat
# Finance Bot (Layer 5 — financial_health spoke)
import utils.finance_bot as finance_bot
# Memory (Layer 2)
import utils.memory as memory
# Property Lookup (Shelby County Assessor)
import utils.property_lookup as property_lookup
# Comps scraper (Redfin public data)
import utils.comps_scraper as comps_scraper
# Discord webhook notifier
import utils.discord_notify as discord_notify
# Google Calendar + Gmail (graceful — no-op if not yet authorized)
import utils.google_calendar as google_calendar
import utils.gmail_client as gmail_client
# Apex-parity channels (all env-gated — no-op if creds absent)
import utils.telegram_notify as telegram_notify
import utils.twilio_voice as twilio_voice
import utils.email_triage as email_triage
import utils.outreach_tracker as outreach_tracker
import utils.email_sender as email_sender
import utils.lead_sniper as lead_sniper

load_dotenv()

app = Flask(__name__, static_folder='../dashboard', static_url_path='')

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


def _slack_verify(req):
    """Verify that an incoming request is genuinely from Slack."""
    ts  = req.headers.get('X-Slack-Request-Timestamp', '')
    sig = req.headers.get('X-Slack-Signature', '')
    if not ts or not sig:
        return False
    if abs(time.time() - int(ts)) > 300:   # replay-attack guard
        return False
    base = f'v0:{ts}:{req.get_data(as_text=True)}'
    expected = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# Discord response capture: maps virtual channel IDs to collected message lists
# Allows _execute_slack_command to work unchanged for Discord callers.
_discord_queues: dict = {}


def _handle_discord_command(cmd: dict, discord_user: str = '') -> str:
    """
    Execute a parsed command for a Discord caller.
    Intercepts _slack_post calls via a virtual channel and returns the
    collected text as a single string instead of posting to Slack.
    """
    import uuid
    vch = f'discord:{uuid.uuid4().hex}'
    _discord_queues[vch] = []
    _execute_slack_command(cmd, reply_channel=vch, sender_uid=discord_user)
    messages = _discord_queues.pop(vch, [])
    return '\n\n'.join(messages) if messages else '✅ Done.'


def _slack_post(channel: str, text: str):
    """Fire a message to a Slack channel via the bot token.
    If channel is a discord: virtual channel, collect the message instead.
    If channel is a tg:<chat_id> virtual channel, route to Telegram."""
    if channel.startswith('discord:'):
        if channel in _discord_queues:
            _discord_queues[channel].append(text)
        return
    if channel.startswith('tg:'):
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
    """Call Claude Haiku for short generative text in Slack command handlers."""
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


def _execute_slack_command(cmd: dict, reply_channel: str, sender_uid: str = ''):
    """
    Execute a parsed Slack command and post the result back to reply_channel.
    sender_uid: Slack user ID of the person who sent the command.
    All XLeads calls happen here.
    """
    action = cmd.get('action')

    # ── HELP ──────────────────────────────────────────────────────────────────
    if action == 'help':
        _slack_post(reply_channel, HELP_TEXT)
        return

    # ── DEBUG BLAST ───────────────────────────────────────────────────────────
    if action == 'debug_blast':
        import json as _json
        tags = cmd.get('tags', ['he', 'tax-delinquent'])
        _slack_post(reply_channel, f'🔍 Diagnosing blast for tags: `{tags}`...')
        try:
            def _norm(t):
                return (t['name'] if isinstance(t, dict) else t).lower().strip().replace('-', ' ')
            norm_tags    = [_norm(t) for t in tags]
            compound_tag = ', '.join(norm_tags)

            # Sample GET /contacts/ — check tags field
            raw   = xleads._get('/contacts/', params={'locationId': xleads.LOCATION_ID, 'limit': 3})
            first = (raw.get('contacts') or [{}])[0]
            meta  = raw.get('meta', {})

            # Quick scan: first 100 contacts — count matching strategies
            sep_match = 0
            compound_match = 0
            total_tagged = 0
            all_tags_seen = set()
            scan = xleads._get('/contacts/', params={'locationId': xleads.LOCATION_ID, 'limit': 100})
            for c in scan.get('contacts', []):
                ctags = {_norm(t) for t in (c.get('tags') or [])}
                if ctags: total_tagged += 1
                all_tags_seen.update(ctags)
                if all(nt in ctags for nt in norm_tags): sep_match += 1
                if compound_tag in ctags: compound_match += 1

            tag_preview = ', '.join(f'[{t}]' for t in list(all_tags_seen)[:15])
            msg = (
                f'*Blast Debug — tags: {norm_tags}*\n'
                f'*Compound form:* `{compound_tag}`\n'
                f'*Total contacts:* {meta.get("total","?")}\n'
                f'───── First 100 contacts ─────\n'
                f'• With any tag: {total_tagged}\n'
                f'• Individual tag match: {sep_match}\n'
                f'• Compound tag match: {compound_match}\n'
                f'*Tags seen (sample):* {tag_preview}\n'
            )
            _slack_post(reply_channel, msg)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Debug failed: {e}')
        return

    # ── BLAST ─────────────────────────────────────────────────────────────────
    if action == 'blast':
        wf_id = cmd.get('workflow_id')
        tags  = cmd.get('tags')
        query = cmd.get('query')
        limit = cmd.get('limit', 100)
        if not wf_id:
            _slack_post(reply_channel,
                '⚠️ Workflow ID required. Run `workflows` to see your workflow IDs, '
                'then: `blast tags:high-equity,tax-delinquent workflow:<id> limit:100`')
            return
        if not tags and not query:
            _slack_post(reply_channel,
                '⚠️ Need tags or query to target contacts. '
                'Example: `blast tags:high-equity,tax-delinquent workflow:<id>`')
            return
        _slack_post(reply_channel,
            f'⏳ Blasting up to {limit} contacts... (tags: {tags or "—"}, query: {query or "—"})\n'
            f'_Running in background — results will post here when done._')

        def _run_blast(wf_id, tags, query, limit, reply_channel):
            try:
                result = xleads.bulk_trigger_workflow(wf_id, tags=tags, query=query, limit=limit)
                failed_count = len(result['failed']) if isinstance(result['failed'], list) else int(result.get('failed', 0))
                sent_count   = int(result.get('triggered', 0))
                try:
                    import psycopg2 as _pg2
                    _db_url = os.environ.get('DATABASE_URL', '')
                    if _db_url:
                        _conn = _pg2.connect(_db_url, sslmode='require')
                    else:
                        _pgpass = os.environ.get('PGPASSWORD')
                        if not _pgpass:
                            raise RuntimeError('Set DATABASE_URL or PGPASSWORD env var for DB access.')
                        _conn = _pg2.connect(
                            host='kodama.proxy.rlwy.net', port=55551,
                            user='postgres', password=_pgpass,
                            dbname='railway', sslmode='require'
                        )
                    _cur = _conn.cursor()
                    _cur.execute('SELECT name FROM workflow_registry WHERE xleads_id = %s', (wf_id,))
                    _row = _cur.fetchone()
                    wf_name = _row[0] if _row else wf_id
                    _cur.execute("""
                        INSERT INTO blast_campaigns
                          (workflow_id, workflow_name, tags, sent_count, failed_count)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (wf_id, wf_name,
                          ','.join(tags) if isinstance(tags, list) else (tags or ''),
                          sent_count, failed_count))
                    _conn.commit()
                    _conn.close()
                except Exception as db_err:
                    print(f'[blast] Campaign log failed: {db_err}')
                _slack_post(reply_channel,
                    f'✅ *Blast complete*\n'
                    f'• Enrolled: {sent_count}\n'
                    f'• Failed:   {failed_count}\n'
                    f'• Total matched: {result.get("total", sent_count)}\n'
                    f'_Reply `blast stats` to see results. ODIN monitors opt-out rate hourly._')
            except Exception as e:
                _slack_post(reply_channel, f'❌ Blast failed: {e}')

        import threading
        threading.Thread(target=_run_blast, args=(wf_id, tags, query, limit, reply_channel), daemon=True).start()
        return

    # ── TEXTBLAST (direct SMS, no workflow/time gate) ────────────────────────
    if action == 'textblast':
        tags    = cmd.get('tags')
        limit   = cmd.get('limit', 100)
        message = cmd.get('message')
        if not message:
            _slack_post(reply_channel,
                '⚠️ Usage: `textblast tags:he,tax-delinquent limit:100 msg:"Your message here"`')
            return
        if not tags:
            _slack_post(reply_channel, '⚠️ Need tags to target contacts. Example: `textblast tags:he,tax-delinquent msg:"..."`')
            return
        _slack_post(reply_channel,
            f'⏳ Sending SMS directly to up to {limit} contacts (tags: {tags})...\n'
            f'_Running in background — results will post here when done._')

        def _run_textblast(tags, limit, message, reply_channel):
            try:
                contacts = xleads.search_contacts(tags=tags, limit=limit)
                sent, failed = 0, 0
                for c in contacts:
                    cid = c.get('id')
                    try:
                        xleads.send_sms(cid, message)
                        sent += 1
                    except Exception:
                        failed += 1
                _slack_post(reply_channel,
                    f'✅ *Direct SMS blast complete*\n'
                    f'• Sent:   {sent}\n'
                    f'• Failed: {failed}\n'
                    f'• Total matched: {len(contacts)}')
            except Exception as e:
                _slack_post(reply_channel, f'❌ Textblast failed: {e}')

        import threading
        threading.Thread(target=_run_textblast, args=(tags, limit, message, reply_channel), daemon=True).start()
        return

    # ── SMS ───────────────────────────────────────────────────────────────────
    if action == 'sms':
        contact_id = cmd.get('contact_id')
        message    = cmd.get('message')
        if not contact_id or not message:
            _slack_post(reply_channel, '⚠️ Usage: `text <contact_id> <message>`')
            return
        try:
            xleads.send_sms(contact_id, message)
            _slack_post(reply_channel, f'✅ SMS sent to `{contact_id}`')
        except Exception as e:
            _slack_post(reply_channel, f'❌ SMS failed: {e}')
        return

    # ── EMAIL ─────────────────────────────────────────────────────────────────
    if action == 'email':
        contact_id = cmd.get('contact_id')
        subject    = cmd.get('subject', 'Message from Shue Box LLC')
        body       = cmd.get('body', '')
        if not contact_id or not body:
            _slack_post(reply_channel,
                '⚠️ Usage: `email <contact_id> subject:Your subject body:Your message`')
            return
        try:
            xleads.send_email(contact_id, subject, body)
            _slack_post(reply_channel, f'✅ Email sent to `{contact_id}`')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Email failed: {e}')
        return

    # ── LEADS ─────────────────────────────────────────────────────────────────
    if action == 'leads':
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT address, mctp_total, status, caller_notes
                       FROM leads
                       WHERE mctp_total >= 5
                       ORDER BY mctp_total DESC NULLS LAST
                       LIMIT 10"""
                )
                rows = cur.fetchall()
            conn.close()
            if not rows:
                _slack_post(reply_channel, '📭 No hot/warm leads right now.')
                return
            lines = ['*🔥 Hot & Warm Leads (Top 10)*']
            for r in rows:
                tier = '🔴 HOT' if r['mctp_total'] >= 8 else '🟡 WARM'
                lines.append(
                    f'{tier} *{r["address"]}* — MCTP {r["mctp_total"]}/10'
                    + (f'\n   _{r["caller_notes"]}_' if r.get('caller_notes') else '')
                )
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Error fetching leads: {e}')
        return

    # ── APPROVALS ─────────────────────────────────────────────────────────────
    if action == 'approvals':
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT aq.id, aq.action_type, l.address, u.name as caller
                       FROM approval_queue aq
                       LEFT JOIN leads l ON l.id = aq.resource_id
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.status = 'pending'
                       ORDER BY aq.priority DESC, aq.created_at ASC
                       LIMIT 10"""
                )
                rows = cur.fetchall()
            conn.close()
            if not rows:
                _slack_post(reply_channel, '✅ No pending approvals.')
                return
            lines = ['*📋 Pending Approvals*']
            for r in rows:
                lines.append(
                    f'• `{r["id"]}` — {r["address"] or "unknown"} (caller: {r["caller"] or "—"})'
                    f'\n  `approve {r["id"]}` or `reject {r["id"]} <reason>`'
                )
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Error fetching approvals: {e}')
        return

    # ── APPROVE ───────────────────────────────────────────────────────────────
    if action == 'approve':
        approval_id = cmd.get('approval_id')
        if not approval_id:
            _slack_post(reply_channel, '⚠️ Usage: `approve <approval_id>`')
            return
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT aq.*, l.address, l.lao, u.namespace as caller_ns
                       FROM approval_queue aq
                       LEFT JOIN leads l ON l.id = aq.resource_id
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.id = %s AND aq.status = 'pending'""",
                    (approval_id,)
                )
                item = cur.fetchone()
                if not item:
                    conn.close()
                    _slack_post(reply_channel, f'⚠️ Approval `{approval_id}` not found or already resolved.')
                    return
                offer_script = build_offer_script(item['address'], item['lao']) if item.get('lao') else None
                cur.execute(
                    """UPDATE approval_queue SET status='approved',
                       offer_script=%s, resolved_at=NOW() WHERE id=%s""",
                    (offer_script, approval_id)
                )
                if item['resource_id']:
                    cur.execute("UPDATE leads SET status='hot' WHERE id=%s", (item['resource_id'],))
                conn.commit()
            conn.close()
            msg = f'✅ *Approved* — {item["address"]}'
            if offer_script:
                msg += f'\n\n*Offer Script:*\n_{offer_script}_'
            _slack_post(reply_channel, msg)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Approve failed: {e}')
        return

    # ── REJECT ────────────────────────────────────────────────────────────────
    if action == 'reject':
        approval_id = cmd.get('approval_id')
        notes       = cmd.get('notes', 'Rejected via Slack')
        if not approval_id:
            _slack_post(reply_channel, '⚠️ Usage: `reject <approval_id> <reason>`')
            return
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT l.address FROM approval_queue aq
                       LEFT JOIN leads l ON l.id = aq.resource_id
                       WHERE aq.id = %s AND aq.status = 'pending'""",
                    (approval_id,)
                )
                item = cur.fetchone()
                if not item:
                    conn.close()
                    _slack_post(reply_channel, f'⚠️ Approval `{approval_id}` not found.')
                    return
                cur.execute(
                    "UPDATE approval_queue SET status='rejected', approver_notes=%s, resolved_at=NOW() WHERE id=%s",
                    (notes, approval_id)
                )
                conn.commit()
            conn.close()
            _slack_post(reply_channel, f'❌ *Rejected* — {item["address"]}\nReason: {notes}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Reject failed: {e}')
        return

    # ── IGNORE (Do-Not-Contact) ────────────────────────────────────────────────
    if action == 'ignore':
        contact_id = cmd.get('contact_id')
        if not contact_id:
            _slack_post(reply_channel, '⚠️ Usage: `ignore <contact_id>`')
            return
        try:
            xleads.add_contact_tags(contact_id, ['Do-Not-Contact', 'ODIN-Ignored'])
            # Remove from all active workflows by triggering removal
            workflows = xleads.list_workflows()
            removed = 0
            for wf in workflows:
                try:
                    xleads.remove_from_workflow(contact_id, wf['id'])
                    removed += 1
                except Exception:
                    pass
            _slack_post(reply_channel,
                f'🚫 Contact `{contact_id}` tagged *Do-Not-Contact* and removed from {removed} workflow(s).')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Ignore failed: {e}')
        return

    # ── WORKFLOW ADD ──────────────────────────────────────────────────────────
    if action == 'workflow_add':
        name = cmd.get('name')
        if not name:
            _slack_post(reply_channel, '❌ `name:` is required. Example: `workflow add name:blast-38111 id:<xleads_id> purpose:Outbound cold text sequence`')
            return
        try:
            db   = get_db()
            cur  = db.cursor()
            cur.execute("""
                INSERT INTO workflow_registry (name, xleads_id, purpose, trigger, status, created_by, notes)
                VALUES (%s, %s, %s, %s, 'active', %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    xleads_id = EXCLUDED.xleads_id,
                    purpose   = COALESCE(EXCLUDED.purpose, workflow_registry.purpose),
                    trigger   = COALESCE(EXCLUDED.trigger, workflow_registry.trigger),
                    notes     = COALESCE(EXCLUDED.notes, workflow_registry.notes)
                RETURNING id, name, xleads_id, purpose, trigger
            """, (
                name,
                cmd.get('xleads_id'),
                cmd.get('purpose'),
                cmd.get('trigger', 'manual'),
                g.user['user_id'] if hasattr(g, 'user') and g.user else None,
                cmd.get('notes'),
            ))
            db.commit()
            row = cur.fetchone()
            xid_display = f'`{row[2]}`' if row[2] else '_no XLeads ID yet_'
            _slack_post(reply_channel,
                f'✅ *Workflow registered:* `{row[1]}`\n'
                f'XLeads ID: {xid_display}\n'
                f'Trigger: `{row[4]}` | Purpose: {row[3] or "—"}\n'
                f'Use this ID in blast: `blast tags:... workflow:{row[2] or "<id>"} limit:100`')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Error registering workflow: {type(e).__name__}: {e}')
        return

    # ── WORKFLOWS ─────────────────────────────────────────────────────────────
    if action == 'workflows':
        try:
            lines = ['*⚙️ ODIN Workflow Registry*']
            db  = get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT name, xleads_id, purpose, trigger, status
                FROM workflow_registry ORDER BY created_at DESC
            """)
            rows = cur.fetchall()
            if rows:
                for r in rows:
                    xid  = f'`{r[1]}`' if r[1] else '_no ID_'
                    stat = '🟢' if r[4] == 'active' else '⏸'
                    lines.append(f'{stat} *{r[0]}* [{r[3]}]\n  ID: {xid}\n  {r[2] or "—"}')
            else:
                lines.append('_No workflows registered yet. Use `workflow add name:... id:... purpose:...`_')

            lines.append('\n*⚙️ XLeads Live Workflows*')
            xl_wf = xleads.list_workflows()
            if xl_wf:
                for wf in xl_wf[:15]:
                    lines.append(f'• `{wf["id"]}` — {wf["name"]} ({wf.get("status","—")})')
            else:
                lines.append('_None returned from XLeads API_')

            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Error: {e}')
        return

    # ── BUYERS ────────────────────────────────────────────────────────────────
    if action == 'buyers':
        query = (cmd.get('query') or '').lower().strip()
        try:
            db   = get_db()
            cur  = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Stats summary always shown
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE active AND NOT is_dnc) AS total_active,
                  COUNT(*) FILTER (WHERE state IN ('TN','MS') AND active) AS memphis_metro,
                  COUNT(*) FILTER (WHERE is_flipper AND active) AS flippers,
                  COUNT(*) FILTER (WHERE is_landlord AND active) AS landlords
                FROM buyers
            """)
            stats = cur.fetchone()

            # Filter buyers
            if 'flipper' in query:
                cur.execute("""
                    SELECT name, city, state, phone, email, flipped_count, flipped_avg_profit, notes
                    FROM buyers WHERE is_flipper=true AND active=true AND is_dnc=false
                    ORDER BY flipped_count DESC NULLS LAST LIMIT 15
                """)
            elif 'memphis' in query or not query:
                cur.execute("""
                    SELECT name, city, state, phone, email, is_flipper, is_landlord,
                           flipped_count, portfolio_owned, notes
                    FROM buyers WHERE state IN ('TN','MS') AND active=true AND is_dnc=false
                    ORDER BY CASE WHEN is_flipper THEN 0 ELSE 1 END,
                             flipped_count DESC NULLS LAST LIMIT 15
                """)
            else:
                cur.execute("""
                    SELECT name, city, state, phone, email, is_flipper, is_landlord,
                           flipped_count, notes
                    FROM buyers
                    WHERE active=true AND is_dnc=false
                      AND (LOWER(name) LIKE %s OR LOWER(city) LIKE %s
                           OR LOWER(COALESCE(company,'')) LIKE %s)
                    ORDER BY flipped_count DESC NULLS LAST LIMIT 15
                """, (f'%{query}%', f'%{query}%', f'%{query}%'))

            rows = cur.fetchall()

            lines = [
                f'*🏘️ Buyer Database*',
                f'Total active: *{stats["total_active"]}* | Memphis/TN/MS: *{stats["memphis_metro"]}* | '
                f'Flippers: *{stats["flippers"]}* | Landlords: *{stats["landlords"]}*',
                ''
            ]
            if rows:
                filter_label = query or 'Memphis metro'
                lines.append(f'*Top buyers ({filter_label}):*')
                for r in rows:
                    btypes = []
                    if r.get('is_flipper'):   btypes.append('Flipper')
                    if r.get('is_landlord'):  btypes.append('Landlord')
                    btype = '/'.join(btypes) or 'Buyer'
                    flips = f' | {r["flipped_count"]} flips' if r.get('flipped_count') else ''
                    loc   = f'{r.get("city","")}, {r.get("state","")}'.strip(', ')
                    phone = r.get('phone','—')
                    lines.append(f'• *{r["name"]}* ({btype}) — {loc} — {phone}{flips}')
            else:
                lines.append('_No buyers found for that filter._')

            lines.append(f'\nUse `match <address> arv:<amount> assign:<price>` to rank buyers for a deal.')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Buyers error: {e}')
        return

    # ── CONTACTS SEARCH ───────────────────────────────────────────────────────
    if action == 'contacts':
        query = cmd.get('query', '')
        try:
            contacts = xleads.search_contacts(query=query, limit=10)
            if not contacts:
                _slack_post(reply_channel, f'📭 No contacts found for "{query}".')
                return
            lines = [f'*🔍 Contacts matching "{query}"*']
            for c in contacts:
                name  = f'{c.get("firstName", "")} {c.get("lastName", "")}'.strip() or 'Unknown'
                phone = c.get('phone', '—')
                lines.append(f'• `{c["id"]}` — {name} | {phone}')
            lines.append('\n_Use `contact <id>` for full detail, `text <id> <msg>` to SMS_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Search failed: {e}')
        return

    # ── CONTACT DETAIL ────────────────────────────────────────────────────────
    if action == 'contact':
        contact_id = cmd.get('contact_id')
        if not contact_id:
            _slack_post(reply_channel, '⚠️ Usage: `contact <contact_id>`')
            return
        try:
            c = xleads.get_contact(contact_id)
            name    = f'{c.get("firstName", "")} {c.get("lastName", "")}'.strip()
            phone   = c.get('phone', '—')
            email   = c.get('email', '—')
            address = c.get('address1', '—')
            tags    = ', '.join(c.get('tags', [])) or '—'
            _slack_post(reply_channel,
                f'*👤 {name}*\n'
                f'• ID: `{contact_id}`\n'
                f'• Phone: {phone}\n'
                f'• Email: {email}\n'
                f'• Address: {address}\n'
                f'• Tags: {tags}\n\n'
                f'_`text {contact_id} <msg>` | `ignore {contact_id}` | `contract {contact_id} template:<id>`_')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Contact lookup failed: {e}')
        return

    # ── CONTRACT ──────────────────────────────────────────────────────────────
    if action == 'contract':
        contact_id  = cmd.get('contact_id')
        template_id = cmd.get('template_id')
        if not contact_id:
            _slack_post(reply_channel,
                '⚠️ Usage: `contract <contact_id> template:<template_id>`\n'
                'Run `contract templates` to see available templates.')
            return
        if not template_id:
            try:
                templates = xleads.list_contract_templates()
                lines = ['*📄 Contract Templates* — specify one with `template:<id>`']
                for t in (templates or [])[:10]:
                    lines.append(f'• `{t.get("_id") or t.get("id")}` — {t.get("name", "Unnamed")}')
                _slack_post(reply_channel, '\n'.join(lines))
            except Exception as e:
                _slack_post(reply_channel, f'❌ Could not list templates: {e}')
            return
        try:
            # sent_by uses Brock's GHL user ID — auto-pulled from JWT
            sent_by = user_id if user_id else 'system'
            xleads.send_contract_from_template(template_id, contact_id, sent_by_user_id=sent_by)
            _slack_post(reply_channel,
                f'✅ Contract sent to `{contact_id}` from template `{template_id}`')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Contract send failed: {e}')
        return

    # ── PHONE NUMBERS ─────────────────────────────────────────────────────────
    if action == 'numbers':
        try:
            numbers = xleads.list_phone_numbers()
            if not numbers:
                _slack_post(reply_channel, '📭 No phone numbers found.')
                return
            lines = ['*📞 Your Phone Numbers*']
            for n in numbers:
                num   = n.get('phoneNumber') or n.get('value') or '—'
                name  = n.get('friendlyName') or n.get('title') or '—'
                lines.append(f'• {num} — {name}')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Error: {e}')
        return

    if action in ('buy_number', 'confirm_buy'):
        _slack_post(reply_channel,
            '⚠️ Number search/purchase is not available via the GHL API.\n'
            'Buy numbers in XLeads → Settings → Phone Numbers.')
        return

    # ── MAYA STATUS ───────────────────────────────────────────────────────────
    if action == 'maya_status':
        _slack_post(reply_channel, '🔍 Checking Maya\'s current config in XLeads...')
        try:
            agents = xleads.list_voice_agents()
            if not agents:
                _slack_post(reply_channel,
                    '⚠️ No Voice AI agents found in XLeads.\n'
                    'The Voice AI API endpoint may differ for your account.\n'
                    'Try: XLeads → Settings → Voice AI to confirm it\'s enabled.\n'
                    'Then `maya update` to push the official prompt.')
                return
            lines = ['*🤖 Voice AI Agents in XLeads:*\n']
            for a in agents:
                name    = a.get('name', 'Unnamed')
                agent_id = a.get('id', '—')
                prompt  = (a.get('prompt') or a.get('instructions') or '—')[:120]
                lines.append(
                    f'• *{name}* — `{agent_id}`\n'
                    f'  Current prompt: _{prompt}..._'
                )
            lines.append('\n_`maya update` to push the official Maya prompt to XLeads_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel,
                f'❌ Voice AI API error: `{e}`\n\n'
                f'This likely means the Voice AI endpoint path needs adjustment for '
                f'your XLeads account. Use `debug Voice AI API returning error {e}` '
                f'for a full diagnosis.')
        return

    # ── MAYA UPDATE ───────────────────────────────────────────────────────────
    if action == 'maya_update':
        _slack_post(reply_channel, '⏳ Fetching Voice AI agents to find Maya...')
        try:
            agents = xleads.list_voice_agents()
            if not agents:
                _slack_post(reply_channel,
                    '⚠️ No Voice AI agents returned from XLeads API.\n'
                    'You may need to set the prompt manually:\n'
                    'XLeads → Settings → Voice AI → paste the prompt below:\n\n'
                    f'```{MAYA_PROMPT}```')
                return

            # Find Maya — match by name, fallback to first agent
            maya = next(
                (a for a in agents if 'maya' in (a.get('name') or '').lower()),
                agents[0]
            )
            agent_id   = maya.get('id')
            agent_name = maya.get('name', 'Agent')

            # Push the prompt
            result = xleads.update_voice_agent(
                agent_id,
                prompt=MAYA_PROMPT,
                name='Maya',
            )
            log_action(None, 'maya_prompt_updated', 'xleads', agent_id)
            _slack_post(reply_channel,
                f'✅ *Maya prompt updated in XLeads*\n'
                f'Agent: *{agent_name}* (`{agent_id}`)\n\n'
                f'*New prompt:*\n_{MAYA_PROMPT}_\n\n'
                f'Maya is ready. Test her by calling your XLeads inbound number.')

        except Exception as e:
            _slack_post(reply_channel,
                f'❌ Could not update Maya automatically: `{e}`\n\n'
                f'Paste this manually in XLeads → Settings → Voice AI:\n\n'
                f'```{MAYA_PROMPT}```')
        return

    # ── IT SUPPORT / DEBUG ────────────────────────────────────────────────────
    if action == 'debug':
        problem = cmd.get('problem', '').strip()
        if not problem:
            _slack_post(reply_channel,
                '⚠️ Describe the problem: `debug <what\'s happening>`\n'
                'Examples:\n'
                '  `debug ODIN returns 502 when I submit a lead`\n'
                '  `debug my Etsy shop got suspended`\n'
                '  `debug XLeads SMS not sending after workflow trigger`')
            return
        who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel, f'🖥️ Diagnosing: _{problem}_...')
        try:
            result = _run_agent('it_agent', it_agent.run,
                                input_summary=problem, triggered_by=who,
                                problem=problem, get_db=get_db)
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ IT agent error: {e}')
        return

    # ── AGENT LOGS ────────────────────────────────────────────────────────────
    if action == 'logs':
        filter_type = cmd.get('filter')   # 'errors' or None
        agent_name  = cmd.get('agent')    # specific agent name or None
        try:
            rows = agent_log.get_recent(
                get_db,
                limit      = 25,
                agent_name = agent_name,
                status     = 'error' if filter_type == 'errors' else None,
            )
            if filter_type == 'errors':
                title = 'Recent Agent Errors'
            elif agent_name:
                title = f'Logs — {agent_name}'
            else:
                title = 'Recent Agent Logs'
            _slack_post(reply_channel, agent_log.format_slack(rows, title=title))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Could not fetch logs: {e}')
        return

    # ── ADD AGENT ────────────────────────────────────────────────────────────
    if action == 'add_agent':
        description = cmd.get('description', '').strip()
        if not description:
            _slack_post(reply_channel,
                '⚠️ Describe what you want the agent to do:\n'
                '  `add agent that monitors my Etsy shop and alerts me on new orders`\n'
                '  `add agent that checks weather in Memphis every morning`\n'
                '  `add bot that pulls my latest Shopify revenue`')
            return
        _slack_post(reply_channel,
            f'🤖 Building your agent...\n_Description: {description}_\n\n'
            f'_(Claude is writing the code — this takes 10-20 seconds)_')
        try:
            who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID', '') else 'brock'
            spec = agent_builder.generate(description, created_by=who)
            saved = agent_builder.save(spec, get_db)
            _slack_post(reply_channel,
                f'✅ *Agent created: `{saved["name"]}`*\n\n'
                f'📋 *What it does:* {spec["description"]}\n'
                f'⌨️ *How to use:* `{saved["trigger_keyword"]} <optional params>`\n\n'
                f'_Agent is live immediately. Type `{saved["trigger_keyword"]}` to run it._\n'
                f'_To see all agents: `agents` | To remove: `disable agent {saved["name"]}`_')
        except ValueError as e:
            _slack_post(reply_channel, f'⚠️ {e}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Agent creation failed: {e}')
        return

    # ── AGENTS LIST ───────────────────────────────────────────────────────────
    if action == 'agents_list':
        try:
            agents_list = agent_builder.list_agents(get_db)
            if not agents_list:
                _slack_post(reply_channel,
                    '🤖 *No custom agents yet.*\n'
                    'Create one: `add agent <description of what you want it to do>`')
                return
            lines = [f'🤖 *Custom Agents ({len(agents_list)})*\n']
            for a in agents_list:
                who = a.get('created_by', '?')
                lines.append(
                    f'• *`{a["trigger_keyword"]}`* — {a["description"]}\n'
                    f'  _Created by {who}_'
                )
            lines.append('\n_Run any agent by typing its trigger keyword._')
            lines.append('_Remove: `disable agent <name>`_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Could not list agents: {e}')
        return

    # ── DISABLE AGENT ─────────────────────────────────────────────────────────
    if action == 'agent_disable':
        name = cmd.get('name', '').strip()
        if not name:
            _slack_post(reply_channel, '⚠️ Usage: `disable agent <name>`')
            return
        try:
            found = agent_builder.disable_agent(name, get_db)
            if found:
                _slack_post(reply_channel, f'✅ Agent `{name}` disabled.')
            else:
                _slack_post(reply_channel,
                    f'⚠️ No active agent named `{name}` found. Run `agents` to see the list.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Could not disable agent: {e}')
        return

    # ── BUSINESS SCOUT ────────────────────────────────────────────────────────
    if action == 'scout':
        keywords  = cmd.get('keywords', '')
        who       = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel, f'🔍 Scouting business ideas for: _{keywords or "general"}_...')
        try:
            result = _run_agent('scout', scout_agent.run,
                                input_summary=keywords, triggered_by=who,
                                keywords=keywords,
                                budget_usd=cmd.get('budget', 500),
                                hours_per_week=cmd.get('hours', 10))
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ Scout failed: {e}')
        return

    # ── INCOME CALCULATOR ─────────────────────────────────────────────────────
    if action == 'income':
        business = cmd.get('business', '')
        if not business:
            _slack_post(reply_channel, '⚠️ Usage: `income <business name> hours:10 budget:200`')
            return
        who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel, f'💰 Modeling revenue for: _{business}_...')
        try:
            result = _run_agent('income_calculator', income_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business,
                                hours_per_week=cmd.get('hours', 10),
                                starting_budget=cmd.get('budget', 200))
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ Income model failed: {e}')
        return

    # ── BUSINESS BUILDER ──────────────────────────────────────────────────────
    if action == 'build' and cmd.get('contact_id') is None:
        business = cmd.get('business') or cmd.get('raw', '')
        if not business:
            _slack_post(reply_channel, '⚠️ Usage: `build <business name>`')
            return
        who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel, f'🏗️ Building task breakdown for: _{business}_...')
        try:
            result = _run_agent('business_builder', builder_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business)
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ Builder failed: {e}')
        return

    # ── AUTOMATION AUDITOR ────────────────────────────────────────────────────
    if action == 'audit':
        business = cmd.get('business', '')
        if not business:
            _slack_post(reply_channel, '⚠️ Usage: `audit <business name>`')
            return
        who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel, f'🤖 Auditing automation potential for: _{business}_...')
        try:
            result = _run_agent('automation_auditor', auditor_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business)
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit failed: {e}')
        return

    # ── IDEAS (5 highly automatable business ideas for Brock or Katelyn) ────────
    if action == 'ideas':
        is_katelyn = sender_uid == os.environ.get('KATELYN_SLACK_UID', '')
        who        = 'katelyn' if is_katelyn else 'brock'
        keywords   = cmd.get('keywords', '')
        variation  = cmd.get('variation', False)

        # Katelyn → conversational advisor (uses her full aesthetic system prompt)
        if is_katelyn:
            prompt = (
                'Give me 5 business ideas that fit my aesthetic.'
                + (f' I prefer: {keywords}.' if keywords else '')
                + (' Make them different from what you suggested before.' if variation else '')
            )
            try:
                from utils import memory
                db = get_db()
                mem_rows = memory.load(db, user_id=None, limit=10)
                mem_context = memory.format_for_prompt(mem_rows)
                reply = advisor.chat(prompt, memory_context=mem_context, spoke='katelyn_business')
                _slack_post(reply_channel, reply)
            except Exception as e:
                _slack_post(reply_channel, f'❌ Ideas failed: {e}')
            return

        # Brock → structured scout agent
        ctx = (
            'Brock needs businesses that run 90%+ automated via ODIN with minimal daily input. '
            'He already has ODIN, XLeads CRM, Claude API, Twilio, and a buyer/seller database. '
            'Focus exclusively on highly automatable, low startup cost, path to $1k/month in 90 days. '
            + (f'Additional preferences: {keywords}' if keywords else '')
            + (' Give 5 DIFFERENT ideas from any previous set — explore different categories.' if variation else '')
        )
        _slack_post(reply_channel,
            f'💡 Generating 5 highly automatable business ideas...'
            + (' _(different from last batch)_' if variation else ''))
        try:
            result = _run_agent('business_scout', scout_agent.run,
                                input_summary=ctx, triggered_by=who,
                                keywords=ctx, for_user=who)
            text_out = result['slack_text']
            text_out += (
                '\n\n_Like one? Say `plan <business name>` and ODIN will run full market research '
                'and generate a complete PDF business plan._'
            )
            _slack_post(reply_channel, text_out)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Ideas generation failed: {e}')
        return

    # ── FULL PLAN (chain all 4 agents) ────────────────────────────────────────
    if action == 'plan':
        business = cmd.get('business', '')
        if not business:
            _slack_post(reply_channel, '⚠️ Usage: `plan <business name>`')
            return
        who = 'katelyn' if sender_uid == os.environ.get('KATELYN_SLACK_UID','') else 'brock'
        _slack_post(reply_channel,
            f'📊 Running full business plan for *{business}*...\n'
            f'_(4 agents + PDF: scout → income → builder → auditor → plan)_')
        errors = []
        scout = income = build = audit = None
        try:
            _slack_post(reply_channel, '1/4 🔍 Scouting market...')
            scout  = _run_agent('scout', scout_agent.run,
                                input_summary=business, triggered_by=who,
                                keywords=business)
            _slack_post(reply_channel, scout['slack_text'])
        except Exception as e:
            errors.append(f'Scout: {e}')

        try:
            _slack_post(reply_channel, '2/4 💰 Modeling revenue...')
            income = _run_agent('income_calculator', income_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business)
            _slack_post(reply_channel, income['slack_text'])
        except Exception as e:
            errors.append(f'Income: {e}')

        try:
            _slack_post(reply_channel, '3/4 🏗️ Building task breakdown...')
            build  = _run_agent('business_builder', builder_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business)
            _slack_post(reply_channel, build['slack_text'])
        except Exception as e:
            errors.append(f'Builder: {e}')

        try:
            _slack_post(reply_channel, '4/4 🤖 Auditing automation...')
            audit  = _run_agent('automation_auditor', auditor_agent.run,
                                input_summary=business, triggered_by=who,
                                business_name=business)
            _slack_post(reply_channel, audit['slack_text'])
        except Exception as e:
            errors.append(f'Auditor: {e}')

        if errors:
            _slack_post(reply_channel, f'⚠️ Some agents had errors: {"; ".join(errors)}')

        # ── Generate PDF business plan ────────────────────────────────────────
        if not errors and all(v is not None for v in [scout, income, build, audit]):
            try:
                _slack_post(reply_channel, '📄 Generating PDF business plan...')
                pdf_path = biz_pdf.generate(
                    business_name=business,
                    scout_data=scout,
                    income_data=income,
                    build_data=build,
                    audit_data=audit,
                    for_user=who,
                )
                # Normalize path for display
                display_path = pdf_path.replace('C:/Users/Brock/OneDrive/Desktop/Master Folder/', '')
                _slack_post(reply_channel,
                    f'✅ *Business plan complete for {business}.*\n'
                    f'📂 PDF saved: `{display_path}`\n'
                    f'_Open from OneDrive or Master Folder/ODIN/business_plans/_')
            except Exception as e:
                _slack_post(reply_channel,
                    f'✅ *Analysis complete for {business}.*\n'
                    f'⚠️ PDF generation failed: {e}')
        elif not errors:
            _slack_post(reply_channel, f'✅ *Full analysis complete for {business}.*')
        return

    # ── ANALYZE (full deal analysis) ──────────────────────────────────────────
    if action == 'analyze':
        address = cmd.get('address', '')
        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `analyze 4314 Leatherwood Memphis beds:3 baths:2 sqft:1534 zip:38111 condition:medium`')
            return
        _slack_post(reply_channel, f'🔍 Analyzing {address}...')
        try:
            result = offer_calc.analyze_deal(
                address=address,
                beds=cmd.get('beds', 3),
                baths=cmd.get('baths', 2.0),
                sqft=cmd.get('sqft', 0),
                year_built=0,
                condition=cmd.get('condition', 'unknown'),
                zip_code=cmd.get('zip_code', ''),
                rehab_override=cmd.get('rehab'),
                target_fee=cmd.get('target_fee', 20000.0),
            )
            log_action(None, 'analyze_deal', data={'address': address})
            analysis_text = offer_calc.format_slack(result)
            # Append Redfin comps automatically
            try:
                comps = comps_scraper.get_comps(address, zip_code=cmd.get('zip_code', ''))
                analysis_text += comps_scraper.format_slack(comps)
            except Exception:
                pass
            _slack_post(reply_channel, analysis_text)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Analysis failed: {e}')
        return

    # ── SCORE (MCTP lead scoring) ──────────────────────────────────────────────
    if action == 'score':
        notes = cmd.get('notes', '')
        if not notes:
            _slack_post(reply_channel,
                '⚠️ Usage: `score Called seller, motivated divorce, needs out in 30 days, wants $80k`')
            return
        _slack_post(reply_channel, '📊 Scoring lead...')
        try:
            result = lead_scorer.score(
                notes=notes,
                address=cmd.get('address', ''),
                caller_name=cmd.get('name', ''),
            )
            log_action(None, 'lead_score', data={'address': cmd.get('address','')})
            _slack_post(reply_channel, lead_scorer.format_slack(result))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Scoring failed: {e}')
        return

    # ── SCRIPT (call script generator) ────────────────────────────────────────
    if action == 'script':
        _slack_post(reply_channel, '📝 Writing script...')
        try:
            script = script_gen.generate(
                seller_name=cmd.get('seller_name', ''),
                address=cmd.get('address', ''),
            )
            log_action(None, 'script_generate', data={'address': cmd.get('address','')})
            _slack_post(reply_channel, f'*📞 Call Script*\n\n{script}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Script generation failed: {e}')
        return

    # ── MATCH (buyer matcher) ──────────────────────────────────────────────────
    if action == 'match':
        address = cmd.get('address', '')
        _slack_post(reply_channel, f'🤝 Finding buyers for {address}...')
        try:
            deal = {
                'address':      address,
                'zip_code':     cmd.get('zip_code', ''),
                'arv_mid':      cmd.get('arv_mid', 0),
                'assign_price': cmd.get('assign_price', 0),
                'fee_at_lao':   cmd.get('fee_at_lao', 0),
                'beds': 3, 'baths': 2.0, 'sqft': 0, 'condition': 'unknown',
            }
            # Pull DB buyers — Memphis/TN/MS first, then all active
            try:
                conn = get_db()
                cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("""
                    SELECT name, company, email, phone, market, city, state, zip,
                           buy_box, max_price, notes, is_flipper, is_landlord,
                           is_note_holder, is_lender, portfolio_owned, portfolio_value,
                           portfolio_buy_avg, flipped_count, flipped_avg_profit
                    FROM buyers
                    WHERE active = true AND is_dnc = false AND is_litigator = false
                    ORDER BY
                      CASE WHEN state IN ('TN','MS') THEN 0 ELSE 1 END,
                      CASE WHEN is_flipper THEN 0 ELSE 1 END,
                      flipped_count DESC NULLS LAST
                    LIMIT 50
                """)
                db_buyers = [dict(r) for r in cur.fetchall()]
                conn.close()
            except Exception:
                db_buyers = []

            result = buyer_matcher.match(deal=deal, db_buyers=db_buyers)
            log_action(None, 'buyer_match', data={'address': address})
            _slack_post(reply_channel, buyer_matcher.format_slack(result))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Buyer match failed: {e}')
        return

    # ── DRAFT EMAIL ────────────────────────────────────────────────────────────
    if action == 'draft_email':
        _slack_post(reply_channel, '📧 Drafting email...')
        try:
            result = email_drafter.draft(
                recipient_type=cmd.get('recipient_type', 'general'),
                context=cmd.get('context', ''),
                recipient_name=cmd.get('recipient_name', ''),
            )
            log_action(None, 'email_draft', data={'type': cmd.get('recipient_type','')})
            _slack_post(reply_channel, email_drafter.format_slack(result))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Email draft failed: {e}')
        return

    # ── CONTENT ENGINE ─────────────────────────────────────────────────────────
    if action == 'content':
        source = cmd.get('source', '')
        if not source:
            _slack_post(reply_channel, '⚠️ Usage: `content <deal summary or transcript>`')
            return
        _slack_post(reply_channel, '✍️ Generating content...')
        try:
            result = content_engine.generate(source=source)
            log_action(None, 'content_generate', data={'chars': len(source)})
            _slack_post(reply_channel, content_engine.format_slack(result))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Content generation failed: {e}')
        return

    # ── SCHEDULE APPOINTMENT (Google Calendar) ───────────────────────────────
    if action == 'schedule':
        if not google_calendar.is_available():
            _slack_post(reply_channel,
                '📅 *Google Calendar not connected yet.*\n\n'
                'One-time setup (5 min):\n'
                '1. Go to console.cloud.google.com → New Project "ODIN"\n'
                '2. Enable *Google Calendar API*\n'
                '3. OAuth consent screen → External → add shueboxllc@gmail.com as test user\n'
                '4. Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON\n'
                '5. Save to `ODIN/api/credentials/google_oauth.json`\n'
                '6. Run locally: `python api/utils/google_calendar.py --setup`\n'
                '7. Upload `google_token.json` to Railway as an env var or file mount\n\n'
                '_Once done, `schedule John 4314 Leatherwood tomorrow 2pm` will create a calendar event._')
            return
        seller  = cmd.get('seller_name', '')
        address = cmd.get('address', '')
        time_str = cmd.get('time_str', 'tomorrow 9am')
        notes   = cmd.get('notes', '')
        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `schedule John 4314 Leatherwood Ave tomorrow 2pm`\n'
                'Add `notes:she wants $85k` for extra context.')
            return
        _slack_post(reply_channel, f'📅 Scheduling appointment with {seller}...')
        try:
            start_dt = google_calendar.parse_appointment_time(time_str)
            event    = google_calendar.schedule_appointment(
                seller_name=seller,
                address=address,
                start_dt=start_dt,
                notes=notes,
            )
            log_action(None, 'schedule_appointment',
                       data={'seller': seller, 'address': address, 'time': time_str})
            _slack_post(reply_channel,
                f'✅ *Appointment scheduled*\n'
                f'• Seller: {seller}\n'
                f'• Address: {address}\n'
                f'• Time: *{event["start"]}*\n'
                f'• <{event["link"]}|View in Google Calendar>')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Schedule failed: {e}')
        return

    # ── CALENDAR VIEW ─────────────────────────────────────────────────────────
    if action == 'calendar':
        if not google_calendar.is_available():
            _slack_post(reply_channel,
                '📅 Google Calendar not connected. '
                'Type `schedule` for setup instructions.')
            return
        view = cmd.get('view', 'today')
        try:
            events = (google_calendar.get_week_events()
                      if view == 'week'
                      else google_calendar.get_today_events())
            if not events:
                period = 'this week' if view == 'week' else 'today'
                _slack_post(reply_channel, f'📅 No appointments {period}.')
                return
            period = 'This Week' if view == 'week' else 'Today'
            lines = [f'*📅 Calendar — {period}*']
            for e in events:
                lines.append(google_calendar.format_event_slack(e))
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Calendar error: {e}')
        return

    # ── GMAIL SEND ────────────────────────────────────────────────────────────
    if action == 'gmail_send':
        if not gmail_client.is_available():
            _slack_post(reply_channel,
                '📧 *Gmail not connected yet.*\n\n'
                'One-time setup:\n'
                '1. Same Google project as Calendar — also enable *Gmail API*\n'
                '2. Run: `python api/utils/gmail_client.py --setup`\n'
                '3. Upload token to Railway\n\n'
                '_Once done, `gmail John 4314 Leatherwood` sends a follow-up from shueboxllc@gmail.com_')
            return
        mode = cmd.get('mode', 'seller')
        if mode == 'custom':
            to      = cmd.get('to', '')
            subject = cmd.get('subject', 'Message from Shue Box LLC')
            body    = cmd.get('body', '')
            if not to or not body:
                _slack_post(reply_channel,
                    '⚠️ Usage: `gmail custom to:email@example.com subject:Your Subject body:Your message`')
                return
            try:
                result = gmail_client.send(to, subject, body)
                _slack_post(reply_channel, f'✅ Email sent to `{to}` — subject: _{subject}_')
            except Exception as e:
                _slack_post(reply_channel, f'❌ Gmail send failed: {e}')
        else:
            # Seller follow-up — use email_drafter to generate then send
            seller  = cmd.get('seller', '')
            address = cmd.get('address', '')
            if not address:
                _slack_post(reply_channel, '⚠️ Usage: `gmail John 4314 Leatherwood Ave`')
                return
            _slack_post(reply_channel, f'📧 Drafting + sending seller follow-up for {seller}...')
            try:
                draft = email_drafter.draft(
                    recipient_type='seller_followup',
                    recipient_name=seller,
                    context=f'Address: {address}',
                )
                # gmail send requires a real email — look up contact first
                contacts = xleads.search_contacts(query=f'{seller} {address}', limit=1)
                to_email = ''
                if contacts:
                    to_email = contacts[0].get('email', '')
                if not to_email:
                    _slack_post(reply_channel,
                        f'📧 *Email drafted* but no email address found for {seller}.\n\n'
                        f'Subject: {draft.get("subject","")}\n\n{draft.get("body","")}\n\n'
                        f'_Use `gmail custom to:<email> subject:... body:...` to send manually._')
                    return
                result = gmail_client.send(
                    to_email=to_email,
                    subject=draft.get('subject', 'Following up on your property'),
                    body=draft.get('body', ''),
                    to_name=seller,
                )
                log_action(None, 'gmail_send', data={'to': to_email, 'seller': seller})
                _slack_post(reply_channel,
                    f'✅ *Email sent to {seller}* ({to_email})\n'
                    f'Subject: _{draft.get("subject","")}_')
            except Exception as e:
                _slack_post(reply_channel, f'❌ Gmail failed: {e}')
        return

    # ── GMAIL INBOX ───────────────────────────────────────────────────────────
    if action == 'gmail_inbox':
        if not gmail_client.is_available():
            _slack_post(reply_channel, '📧 Gmail not connected. Type `gmail` for setup instructions.')
            return
        query = cmd.get('query', '')
        try:
            threads = gmail_client.get_recent_threads(max_results=8, query=query)
            _slack_post(reply_channel, gmail_client.format_slack_threads(threads))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Inbox error: {e}')
        return

    # ── TAGS ──────────────────────────────────────────────────────────────────
    if action == 'tags':
        _slack_post(reply_channel, '⏳ Fetching tags from XLeads...')
        try:
            tags = xleads.list_tags()
            if not tags:
                _slack_post(reply_channel, '📭 No tags found in XLeads.')
                return
            tag_names = sorted([t.get('name', '—') for t in tags])
            lines = [f'*🏷️ XLeads Tags ({len(tag_names)} total)*']
            # Group in rows of 3 for readability
            for i in range(0, len(tag_names), 3):
                lines.append('  '.join(f'`{n}`' for n in tag_names[i:i+3]))
            lines.append('\n_Use tags in blast: `blast tags:<tag1>,<tag2> workflow:<id>`_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Tags fetch failed: {e}')
        return

    # ── CUSTOM FIELDS ─────────────────────────────────────────────────────────
    if action == 'fields':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT name, field_key, data_type
                FROM custom_fields ORDER BY name
            """)
            rows = cur.fetchall()
            db.close()
            if not rows:
                _slack_post(reply_channel,
                    '📭 No custom fields cached. Run `field sync` to pull from XLeads.')
                return
            lines = [f'*📋 XLeads Custom Fields ({len(rows)})*']
            for r in rows:
                key   = f'`{r["field_key"]}`' if r['field_key'] else '_no key_'
                dtype = r['data_type'] or '—'
                lines.append(f'• *{r["name"]}* — {key} ({dtype})')
            lines.append('\n_Run `field sync` to refresh from XLeads API_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Fields error: {e}')
        return

    # ── FIELD SYNC ────────────────────────────────────────────────────────────
    if action == 'field_sync':
        _slack_post(reply_channel, '⏳ Syncing custom fields from XLeads...')
        try:
            fields = xleads.list_custom_fields()
            if not fields:
                _slack_post(reply_channel, '⚠️ XLeads returned no custom fields.')
                return
            db  = get_db()
            cur = db.cursor()
            synced = 0
            for f in fields:
                fid   = f.get('id') or f.get('fieldKey', '')
                name  = f.get('name', '')
                key   = f.get('fieldKey') or f.get('key', '')
                dtype = f.get('dataType') or f.get('type', '')
                if not name:
                    continue
                cur.execute("""
                    INSERT INTO custom_fields (xleads_id, name, field_key, data_type, synced_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (xleads_id) DO UPDATE SET
                        name       = EXCLUDED.name,
                        field_key  = EXCLUDED.field_key,
                        data_type  = EXCLUDED.data_type,
                        synced_at  = NOW()
                """, (fid or name, name, key, dtype))
                synced += 1
            db.commit()
            db.close()
            _slack_post(reply_channel,
                f'✅ *Custom fields synced* — {synced} fields from XLeads\n'
                f'Run `fields` to view them all.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Field sync failed: {e}')
        return

    # ── PROPERTY LOOKUP (Shelby County Assessor) ──────────────────────────────
    if action == 'lookup':
        address = cmd.get('address', '').strip()
        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `lookup 4314 Leatherwood Ave Memphis`')
            return
        _slack_post(reply_channel, f'🔍 Looking up {address} in Shelby County Assessor...')
        try:
            result = property_lookup.lookup(address, get_db)
            log_action(None, 'property_lookup', data={'address': address})
            _slack_post(reply_channel, property_lookup.format_slack(result))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Lookup failed: {e}')
        return

    # ── MCTP LOG (Eddie / callers via Slack) ──────────────────────────────────
    if action == 'mctp_log':
        address = cmd.get('address', '').strip()
        seller  = cmd.get('seller_name', '').strip()
        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `mctp John 4314 Leatherwood Ave M:2 C:1 T:2 P:1 notes:motivated divorce wants 90k`\n'
                'M=Motivation(0-3)  C=Condition(0-2)  T=Timeline(0-3)  P=Price(0-2)')
            return

        mot   = max(0, min(3, cmd.get('motivation_score', 0)))
        cond  = max(0, min(2, cmd.get('condition_score', 0)))
        tl    = max(0, min(3, cmd.get('timeline_score', 0)))
        price = max(0, min(2, cmd.get('price_score', 0)))
        total = mot + cond + tl + price
        tier  = 'HOT' if total >= 8 else ('WARM' if total >= 5 else 'COLD')
        tier_emoji = '🔥' if tier == 'HOT' else ('⚡' if tier == 'WARM' else '🧊')
        notes = cmd.get('notes', '')
        caller_notes = f'Seller: {seller}. {notes}'.strip('. ') if seller else notes

        # Look up the calling user by their Slack UID
        caller_user_id = None
        caller_name    = 'Caller'
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if sender_uid:
                cur.execute("SELECT id, name FROM users WHERE slack_uid = %s", (sender_uid,))
            else:
                cur.execute("SELECT id, name FROM users WHERE role = 'caller' LIMIT 1")
            caller_row = cur.fetchone()
            if caller_row:
                caller_user_id = str(caller_row['id'])
                caller_name    = caller_row['name']
            db.close()
        except Exception:
            pass

        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Upsert lead
            cur.execute(
                'SELECT id FROM leads WHERE LOWER(address) = LOWER(%s)',
                (address,)
            )
            existing = cur.fetchone()

            if existing:
                lead_id = str(existing['id'])
                cur.execute("""
                    UPDATE leads SET
                        motivation_score = %s, condition_score = %s,
                        timeline_score = %s, price_score = %s,
                        status = %s, caller_notes = %s
                    WHERE id = %s
                """, (mot, cond, tl, price,
                      tier.lower(), caller_notes, lead_id))
            else:
                cur.execute("""
                    INSERT INTO leads
                        (user_id, assigned_to, address, city, state,
                         motivation_score, condition_score, timeline_score, price_score,
                         status, caller_notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    caller_user_id, caller_user_id,
                    address, 'Memphis', 'TN',
                    mot, cond, tl, price,
                    tier.lower(), caller_notes
                ))
                lead_id = str(cur.fetchone()['id'])

            db.commit()
            db.close()

            log_action(caller_user_id, 'mctp_log_slack', 'leads', lead_id,
                       {'address': address, 'total': total, 'tier': tier})

            # Confirm back to caller
            _slack_post(reply_channel,
                f'{tier_emoji} *MCTP Logged — {tier}*\n'
                f'• Seller: {seller or "—"} | Address: {address}\n'
                f'• M:{mot}  C:{cond}  T:{tl}  P:{price}  = *{total}/10*\n'
                f'• Notes: _{notes or "none"}_\n'
                f'• Lead ID: `{lead_id}`')

            # Alert Brock + Eddie if hot or warm
            if total >= 5:
                hot_alert = (
                    f'{tier_emoji} *{tier} Lead logged by {caller_name}*\n'
                    f'• Address: {address}\n'
                    f'• Seller: {seller or "—"}\n'
                    f'• Score: M:{mot} C:{cond} T:{tl} P:{price} = *{total}/10*\n'
                    f'• Notes: _{notes or "none"}_\n'
                    f'`score {caller_notes[:80]}` | `analyze {address}` | `match {address}`'
                )
                brock_ch = os.environ.get('SLACK_CHANNEL_BROCK', '')
                if brock_ch and brock_ch != reply_channel:
                    _slack_post(brock_ch, hot_alert)
                eddie_ch = os.environ.get('SLACK_CHANNEL_EDDIE', '')
                if eddie_ch and eddie_ch != reply_channel and eddie_ch != brock_ch:
                    _slack_post(eddie_ch, hot_alert)

        except Exception as e:
            _slack_post(reply_channel, f'❌ MCTP log failed: {e}')
        return

    # ── STATUS ────────────────────────────────────────────────────────────────
    if action == 'status':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE status NOT IN ('closed','dead')")
            active_leads = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE mctp_total >= 8 AND status NOT IN ('closed','dead')")
            hot = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE mctp_total BETWEEN 5 AND 7 AND status NOT IN ('closed','dead')")
            warm = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'")
            pending = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM users WHERE active = true")
            users = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM buyers")
            buyer_count = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM workflow_registry WHERE status = 'active'")
            workflows = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) as cnt FROM workflow_registry WHERE xleads_id IS NOT NULL AND status = 'active'")
            wf_linked = cur.fetchone()['cnt']

            cur.execute("""
                SELECT follow_up_date, address FROM leads
                WHERE follow_up_date < CURRENT_DATE AND status NOT IN ('closed','dead')
                ORDER BY follow_up_date ASC LIMIT 1
            """)
            oldest_overdue = cur.fetchone()

            db.close()

            google_cal_ok = google_calendar.is_available()
            gmail_ok      = gmail_client.is_available()

            now = datetime.now().strftime('%Y-%m-%d %I:%M %p CT')
            lines = [f'*🛰️ ODIN System Status — {now}*', '']
            lines.append(f'*Pipeline:* {active_leads} active leads | 🔥 {hot} hot | ⚡ {warm} warm | ⏳ {pending} pending approvals')
            lines.append(f'*Buyers DB:* {buyer_count:,} buyers indexed')
            lines.append(f'*Users:* {users} active')
            lines.append(f'*Workflows:* {wf_linked}/{workflows} registered with XLeads IDs')
            if oldest_overdue:
                lines.append(f'*⚠️ Oldest overdue follow-up:* {oldest_overdue["address"]} ({oldest_overdue["follow_up_date"]})')
            lines.append('')
            lines.append(f'*Integrations:*')
            lines.append(f'  XLeads API: ✅ connected')
            lines.append(f'  Slack:      ✅ connected')
            lines.append(f'  Google Cal: {"✅ authorized" if google_cal_ok else "❌ not set up — see setup instructions"}')
            lines.append(f'  Gmail:      {"✅ authorized" if gmail_ok else "❌ not set up — see setup instructions"}')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Status error: {type(e).__name__}: {e}')
        return

    # ── FOLLOW UP ─────────────────────────────────────────────────────────────
    if action == 'followup':
        address = cmd.get('address', '').strip()
        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `follow up 4314 Leatherwood Ave date:2026-06-01 action:Call back re: price`')
            return
        new_date      = cmd.get('date')
        new_action    = cmd.get('next_action')
        new_cid       = cmd.get('xleads_contact_id')
        if not new_date and not new_action and not new_cid:
            _slack_post(reply_channel,
                '⚠️ Provide at least `date:YYYY-MM-DD`, `action:<text>`, or `cid:<xleads_contact_id>`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT id, address, mctp_total FROM leads WHERE LOWER(address) LIKE LOWER(%s) LIMIT 1",
                        (f'%{address}%',))
            lead = cur.fetchone()
            if not lead:
                db.close()
                _slack_post(reply_channel, f'⚠️ No lead found matching `{address}`. Try `leads` to see addresses.')
                return
            updates, params = [], []
            if new_date:
                updates.append('follow_up_date = %s')
                params.append(new_date)
            if new_action:
                updates.append('next_action = %s')
                params.append(new_action)
            if new_cid:
                updates.append('xleads_contact_id = %s')
                params.append(new_cid)
            params.append(str(lead['id']))
            set_clause = ', '.join(updates)
            cur.execute("UPDATE leads SET " + set_clause + ", updated_at = NOW() WHERE id = %s", params)
            db.commit()
            db.close()
            lines = [f'✅ *Follow-up updated — {lead["address"]}*']
            if new_date:
                lines.append(f'📅 Date: {new_date}')
            if new_action:
                lines.append(f'📋 Action: {new_action}')
            if new_cid:
                lines.append(f'🔗 XLeads contact ID linked — auto-SMS will fire on date.')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Follow-up update failed: {type(e).__name__}: {e}')
        return

    # ── TASKS (delegation view) ───────────────────────────────────────────────
    if action == 'tasks':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            assignee_filter = cmd.get('assignee')
            if assignee_filter:
                cur.execute("""
                    SELECT id, title, status, priority, due_date, assignee, spoke
                    FROM tasks
                    WHERE assignee = %s AND status NOT IN ('completed')
                    ORDER BY priority DESC, due_date ASC NULLS LAST
                    LIMIT 20
                """, (assignee_filter,))
            else:
                cur.execute("""
                    SELECT id, title, status, priority, due_date, assignee, spoke
                    FROM tasks
                    WHERE status NOT IN ('completed')
                    ORDER BY priority DESC, due_date ASC NULLS LAST
                    LIMIT 20
                """)
            rows = cur.fetchall() or []
            db.close()
            if not rows:
                _slack_post(reply_channel, '✅ No pending tasks.')
                return
            pri_map = {3: '🔴', 2: '🟡', 1: '⚪'}
            label = f' ({assignee_filter})' if assignee_filter else ''
            lines = [f'*📋 Tasks{label} ({len(rows)})*']
            for t in rows:
                due = f' due {t["due_date"]}' if t['due_date'] else ''
                pri = pri_map.get(t['priority'], '⚪')
                assignee_tag = f'[{t["assignee"] or "?"}]'
                lines.append(f'{pri} {assignee_tag} *{t["title"]}*{due} _{t["spoke"] or "general"}_')
            lines.append('\n`task done <title>` | `assign task <title> to <eddie/odin/brock>`')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Tasks failed: {type(e).__name__}: {e}')
        return

    # ── TASK DONE ─────────────────────────────────────────────────────────────
    if action == 'task_done':
        query = cmd.get('query', '').strip()
        if not query:
            _slack_post(reply_channel, '⚠️ Usage: `task done <title keywords>`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, title FROM tasks
                WHERE LOWER(title) LIKE LOWER(%s)
                  AND status NOT IN ('completed')
                ORDER BY created_at DESC LIMIT 1
            """, (f'%{query}%',))
            t = cur.fetchone()
            if not t:
                db.close()
                _slack_post(reply_channel, f'⚠️ No active task matching `{query}`.')
                return
            cur.execute("""
                UPDATE tasks SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (str(t['id']),))
            db.commit()
            db.close()
            _slack_post(reply_channel, f'✅ *Task completed:* {t["title"]}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Task done failed: {type(e).__name__}: {e}')
        return

    # ── ASSIGN TASK ───────────────────────────────────────────────────────────
    if action == 'assign_task':
        query    = cmd.get('query', '').strip()
        assignee = cmd.get('assignee', '').strip()
        if not query or not assignee:
            _slack_post(reply_channel, '⚠️ Usage: `assign task <title> to <brock/eddie/odin>`')
            return
        if assignee not in ('brock', 'eddie', 'odin'):
            _slack_post(reply_channel, '⚠️ Assignee must be `brock`, `eddie`, or `odin`.')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, title FROM tasks
                WHERE LOWER(title) LIKE LOWER(%s)
                  AND status NOT IN ('completed')
                ORDER BY created_at DESC LIMIT 1
            """, (f'%{query}%',))
            t = cur.fetchone()
            if not t:
                db.close()
                _slack_post(reply_channel, f'⚠️ No active task matching `{query}`.')
                return
            cur.execute("""
                UPDATE tasks SET assignee = %s, updated_at = NOW() WHERE id = %s
            """, (assignee, str(t['id'])))
            db.commit()
            db.close()
            assignee_display = {'odin': '🤖 ODIN', 'eddie': '👤 Eddie', 'brock': '👤 Brock'}
            _slack_post(reply_channel,
                f'✅ *{t["title"]}* → assigned to {assignee_display.get(assignee, assignee)}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Assign task failed: {type(e).__name__}: {e}')
        return

    # ── SPOKES ────────────────────────────────────────────────────────────────
    if action == 'spokes':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT s.name, s.slug, s.active,
                       u.name AS owner_name,
                       (SELECT MAX(created_at) FROM agent_actions
                        WHERE spoke = s.slug) AS last_activity
                FROM spokes s
                LEFT JOIN users u ON u.id = s.owner_user_id
                WHERE s.active = true
                ORDER BY s.created_at ASC
            """)
            spokes = cur.fetchall()
            db.close()
            lines = ['*🔀 ODIN Spokes*']
            for sp in spokes:
                last = sp['last_activity'].strftime('%Y-%m-%d') if sp['last_activity'] else 'no activity'
                lines.append(f'• *{sp["name"]}* (`{sp["slug"]}`) | Owner: {sp["owner_name"] or "—"} | Last activity: {last}')
            if not spokes:
                lines.append('No spokes configured.')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Spokes error: {type(e).__name__}: {e}')
        return

    # ── DEAL STATUS (one-line lead lookup) ────────────────────────────────────
    if action == 'deal_status':
        address = cmd.get('address', '').strip()
        if not address:
            _slack_post(reply_channel, '⚠️ Usage: `status 4314 Leatherwood`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT l.id, l.address, l.city, l.mctp_total, l.status,
                       l.lao, l.assign_price, l.fee_at_lao,
                       l.next_action, l.follow_up_date, l.updated_at,
                       u.name AS assigned_name
                FROM leads l
                LEFT JOIN users u ON u.id = l.assigned_to
                WHERE LOWER(l.address) LIKE LOWER(%s)
                LIMIT 1
            """, (f'%{address}%',))
            lead = cur.fetchone()
            if not lead:
                db.close()
                _slack_post(reply_channel, f'⚠️ No lead found matching `{address}`.')
                return

            cur.execute("""
                SELECT COUNT(*) as cnt FROM buyers
                WHERE market ILIKE '%memphis%' AND active = true
            """)
            buyer_count = cur.fetchone()['cnt']

            cur.execute("""
                SELECT MAX(created_at) as last FROM agent_actions
                WHERE resource_id = %s
            """, (str(lead['id']),))
            last_action = cur.fetchone()['last']
            db.close()

            days_ago = (datetime.now() - last_action.replace(tzinfo=None)).days if last_action else '?'
            tier = 'Hot' if (lead['mctp_total'] or 0) >= 8 else ('Warm' if (lead['mctp_total'] or 0) >= 5 else 'Cold')
            lao_str = f'${lead["lao"]:,.0f}' if lead['lao'] else '—'
            fee_str = f'${lead["fee_at_lao"]:,.0f}' if lead['fee_at_lao'] else '—'

            # AI-generated next action
            next_action = _haiku(
                f'Memphis RE wholesaling. Lead: {lead["address"]}. Status: {lead["status"]}. '
                f'Score: {lead["mctp_total"]}/10. Days since last contact: {days_ago}. '
                f'Current next action: {lead["next_action"] or "none"}. '
                f'In ONE sentence (max 12 words), what should the wholesaler do next?'
            ) if not lead['next_action'] else lead['next_action']

            lines = [
                f'*📍 {lead["address"]}, {lead["city"]}*',
                f'Score: *{lead["mctp_total"]}/10* ({tier}) | Status: *{lead["status"]}* | Assigned: {lead["assigned_name"] or "Brock"}',
                f'LAO: {lao_str} | Fee @ LAO: {fee_str} | Last contact: {days_ago}d ago',
                f'Buyers matched: ~{buyer_count:,} in market',
                f'Next action: _{next_action}_',
            ]
            log_action(None, 'deal_status', 'leads', lead['id'])
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Deal status error: {type(e).__name__}: {e}')
        return

    # ── REVIEW (weekly CEO review on-demand) ──────────────────────────────────
    if action == 'review':
        _slack_post(reply_channel, '⏳ Running CEO review...')
        try:
            heartbeat.weekly_ceo_review()
            log_action(None, 'ceo_review', data={'triggered_by': 'slack'})
        except Exception as e:
            _slack_post(reply_channel, f'❌ Review failed: {type(e).__name__}: {e}')
        return

    # ── STATS (funnel stats) ──────────────────────────────────────────────────
    if action == 'stats':
        period = cmd.get('period', 'week')
        spoke  = cmd.get('spoke', 'real_estate')
        interval = '7 days' if period == 'week' else ('30 days' if period == 'month' else '1 day')
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cur.execute("""
                SELECT COUNT(*) as cnt FROM agent_actions
                WHERE action_type IN ('blast','sms_blast')
                  AND created_at >= NOW() - INTERVAL %s
                  AND spoke = %s
            """, (interval, spoke))
            contacts_reached = cur.fetchone()['cnt']

            cur.execute("""
                SELECT COUNT(*) as cnt FROM agent_actions
                WHERE action_type = 'mctp_log_slack'
                  AND created_at >= NOW() - INTERVAL %s
            """, (interval,))
            mctps = cur.fetchone()['cnt']

            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'warm')      AS warm,
                    COUNT(*) FILTER (WHERE status = 'hot')        AS hot,
                    COUNT(*) FILTER (WHERE status = 'contracted') AS contracted,
                    COUNT(*) FILTER (WHERE status = 'closed')     AS closed,
                    COALESCE(SUM(fee_at_lao) FILTER (WHERE status = 'closed'), 0) AS total_fees
                FROM leads WHERE spoke = %s
            """, (spoke,))
            funnel = cur.fetchone()

            # Brock vs Eddie split
            cur.execute("""
                SELECT u.name, u.role,
                       COUNT(l.id) AS lead_cnt,
                       COUNT(l.id) FILTER (WHERE l.status = 'closed') AS closed_cnt,
                       COALESCE(SUM(l.fee_at_lao) FILTER (WHERE l.status = 'closed'), 0) AS fees
                FROM users u
                LEFT JOIN leads l ON l.assigned_to = u.id AND l.spoke = %s
                WHERE u.role IN ('super_admin','caller','re_partner')
                  AND u.active = true
                GROUP BY u.id, u.name, u.role
                ORDER BY closed_cnt DESC
            """, (spoke,))
            by_user = cur.fetchall()

            # Best zip
            cur.execute("""
                SELECT zip, AVG(mctp_total) AS avg_score, COUNT(*) AS cnt
                FROM leads WHERE spoke = %s AND zip IS NOT NULL AND zip != ''
                GROUP BY zip ORDER BY avg_score DESC LIMIT 3
            """, (spoke,))
            best_zips = cur.fetchall()

            db.close()

            date_str = datetime.now().strftime('%Y-%m-%d')
            lines = [f'*📈 ODIN Funnel Stats — {period.title()} ending {date_str} | Spoke: {spoke}*', '']
            lines.append('*Deal Funnel:*')
            lines.append(f'  Contacts reached (blasts): *{contacts_reached}*')
            lines.append(f'  MCTPs logged:              *{mctps}*')
            lines.append(f'  Warm leads (pipeline):     *{funnel["warm"]}*')
            lines.append(f'  Hot leads (pipeline):      *{funnel["hot"]}*')
            lines.append(f'  Contracted:                *{funnel["contracted"]}*')
            lines.append(f'  Closed:                    *{funnel["closed"]}*')
            total_fees = funnel['total_fees'] or 0
            lines.append(f'  Total fees earned:         *${total_fees:,.0f}*')
            lines.append('')

            if by_user:
                lines.append('*By Operator:*')
                for u in by_user:
                    lines.append(f'  • {u["name"]} ({u["role"]}): {u["lead_cnt"]} leads | {u["closed_cnt"]} closed | ${u["fees"] or 0:,.0f} fees')
                lines.append('')

            if best_zips:
                lines.append('*Best Zips by Lead Quality:*')
                for z in best_zips:
                    lines.append(f'  • {z["zip"]}: avg score {z["avg_score"]:.1f}/10 ({z["cnt"]} leads)')

            log_action(None, 'stats', data={'period': period, 'spoke': spoke})
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Stats error: {type(e).__name__}: {e}')
        return

    # ── RESURRECT (dead lead re-engagement) ──────────────────────────────────
    if action == 'resurrect':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT l.id, l.address, l.city, l.mctp_total, l.status,
                       l.caller_notes, l.motivation_score, l.timeline_score,
                       l.updated_at, u.name AS assigned_name
                FROM leads l
                LEFT JOIN users u ON u.id = l.assigned_to
                WHERE l.spoke = 'real_estate'
                  AND (l.status IN ('cold','dead')
                       OR l.updated_at < NOW() - INTERVAL '30 days')
                  AND l.status NOT IN ('contracted','closed')
                ORDER BY l.mctp_total DESC, l.updated_at ASC
                LIMIT 10
            """)
            candidates = cur.fetchall()
            db.close()

            if not candidates:
                _slack_post(reply_channel, '✅ No resurrection candidates — all leads are active.')
                return

            lines = [f'*☠️ Resurrection Candidates ({len(candidates)} leads)*', '']
            for l in candidates:
                days_ago = (datetime.now() - l['updated_at'].replace(tzinfo=None)).days
                text = _haiku(
                    f'Memphis RE wholesaling re-engagement SMS. '
                    f'Lead: {l["address"]}. Last contact: {days_ago} days ago. '
                    f'Notes: {l["caller_notes"] or "none"}. Score: {l["mctp_total"]}/10. '
                    f'Write a SHORT, warm, natural re-engagement text (under 120 chars). '
                    f'Do not mention AI or scripts. Sound human.',
                    max_tokens=80,
                )
                lines.append(
                    f'*{l["address"]}* | {days_ago}d ago | Score: {l["mctp_total"]}/10 | {l["status"]}'
                )
                lines.append(f'  Assigned: {l["assigned_name"] or "Brock"}')
                lines.append(f'  📱 _{text}_')
                lines.append(f'  → `text <contact_id> {text[:50]}...`')
                lines.append('')

            log_action(None, 'resurrect', data={'count': len(candidates)})
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Resurrect error: {type(e).__name__}: {e}')
        return

    # ── SCORECARD (pre-offer due diligence) ──────────────────────────────────
    if action == 'scorecard':
        address   = cmd.get('address', '').strip()
        arv       = cmd.get('arv', 0)
        beds      = cmd.get('beds', 3)
        baths     = cmd.get('baths', 2.0)
        sqft      = cmd.get('sqft', 0)
        zip_code  = cmd.get('zip_code', '')
        condition = cmd.get('condition', 'unknown')
        year_built = cmd.get('year_built', 0)

        if not address:
            _slack_post(reply_channel,
                '⚠️ Usage: `scorecard 4314 Leatherwood arv:165000 beds:3 baths:2 sqft:1400 zip:38111 condition:medium`')
            return
        if not arv:
            _slack_post(reply_channel, '⚠️ `arv:` is required for scorecard math.')
            return

        _slack_post(reply_channel, f'⏳ Running scorecard for {address}...')
        try:
            # ── 1. ARV CONFIDENCE ──
            arv_low  = round(arv * 0.90)
            arv_high = round(arv * 1.10)
            arv_variance = arv_high - arv_low
            arv_variance_pct = round((arv_variance / arv) * 100)
            if arv_variance_pct <= 10:
                arv_confidence = 'High'
                arv_reason = 'Tight variance — comps are consistent'
            elif arv_variance_pct <= 20:
                arv_confidence = 'Medium'
                arv_reason = 'Moderate variance — verify with recent comps'
            else:
                arv_confidence = 'Low'
                arv_reason = 'High variance — limited comp data for this area'

            # ── 2. REHAB RISK ──
            pre_1950 = year_built > 0 and year_built < 1950
            if pre_1950:
                rehab_risk = 'High'
                rehab_note = f'Built {year_built} — likely knob/tube wiring, cast iron plumbing, no original HVAC. Budget $10–15k extra.'
            elif condition in ('heavy', 'poor', 'bad'):
                rehab_risk = 'High'
                rehab_note = 'Heavy condition — full gut likely. Use $45–65k rehab estimate.'
            elif condition in ('medium', 'fair', 'average'):
                rehab_risk = 'Medium'
                rehab_note = 'Medium condition — cosmetic + systems. Estimate $25–40k rehab.'
            else:
                rehab_risk = 'Low'
                rehab_note = 'Light condition — cosmetic only. Estimate $15–25k rehab.'

            # ── 3. BUYER DEMAND ──
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            zip_filter = f'%{zip_code}%' if zip_code else '%memphis%'
            cur.execute("""
                SELECT name, company, buy_box, max_price
                FROM buyers
                WHERE active = true
                  AND (market ILIKE %s OR buy_box ILIKE %s)
                ORDER BY CASE WHEN buy_box ILIKE %s THEN 0 ELSE 1 END,
                         max_price DESC NULLS LAST
                LIMIT 5
            """, (zip_filter, zip_filter, f'%{zip_code}%' if zip_code else '%38%'))
            matched_buyers = cur.fetchall()

            cur.execute("""
                SELECT COUNT(*) as cnt FROM buyers
                WHERE active = true AND market ILIKE '%memphis%'
            """)
            total_buyers = cur.fetchone()['cnt']
            db.close()

            if len(matched_buyers) >= 5:
                demand = 'Strong'
            elif len(matched_buyers) >= 2:
                demand = 'Moderate'
            else:
                demand = 'Weak'

            # ── 4. LAO MATH ──
            lao_math = lao_calc.calculate(arv)

            # ── 5. DEAL VERDICT ──
            weakest = []
            if arv_confidence == 'Low':
                weakest.append('low ARV confidence')
            if rehab_risk == 'High':
                weakest.append('high rehab risk')
            if demand == 'Weak':
                weakest.append('weak buyer demand')

            fee = lao_math['fee_at_lao']
            if fee >= 20000 and not weakest:
                verdict = 'GO'
            elif fee >= 10000 and len(weakest) <= 1:
                verdict = 'PROCEED WITH CAUTION'
            else:
                verdict = 'PASS'

            verdict_reason = _haiku(
                f'Memphis wholesaling deal: ARV ${arv:,}, fee at LAO ${fee:,}, '
                f'ARV confidence {arv_confidence}, rehab risk {rehab_risk}, '
                f'buyer demand {demand}. Verdict: {verdict}. '
                f'In ONE sentence (max 15 words), explain why.',
                max_tokens=50,
            )

            # ── FORMAT OUTPUT ──
            verdict_emoji = '✅' if verdict == 'GO' else ('⚠️' if verdict == 'PROCEED WITH CAUTION' else '❌')
            lines = [f'*📋 Scorecard — {address}*', '']

            lines.append('*1. ARV CONFIDENCE*')
            lines.append(f'  Range: ${arv_low:,} – ${arv_high:,} | Variance: {arv_variance_pct}%')
            lines.append(f'  Confidence: *{arv_confidence}* — {arv_reason}')
            lines.append('')

            lines.append('*2. REHAB RISK*')
            lines.append(f'  Risk: *{rehab_risk}*')
            lines.append(f'  {rehab_note}')
            lines.append('')

            lines.append('*3. BUYER DEMAND*')
            lines.append(f'  Demand: *{demand}* | {len(matched_buyers)} buyers matched (of {total_buyers:,} in DB)')
            if matched_buyers:
                for b in matched_buyers[:3]:
                    lines.append(f'    • {b["name"]}{" — " + b["company"] if b["company"] else ""}')
            lines.append('')

            lines.append('*4. LAO MATH*')
            lines.append(f'  LAO (open):    ${lao_math["lao"]:,}  (ARV × 0.42)')
            lines.append(f'  Walk-up ceil:  ${lao_math["walk_up_max"]:,}  (ARV × 0.55)')
            lines.append(f'  Assign price:  ${lao_math["assign_price"]:,}')
            lines.append(f'  Fee @ LAO:     *${lao_math["fee_at_lao"]:,}*')
            lines.append(f'  Fee @ walkup:  ${lao_math["fee_at_walkup"]:,}')
            lines.append('')

            lines.append(f'*5. DEAL VERDICT: {verdict_emoji} {verdict}*')
            lines.append(f'  _{verdict_reason}_')

            log_action(None, 'scorecard', 'leads', data={'address': address, 'arv': arv, 'verdict': verdict})

            # Save lead record with scorecard data
            try:
                _sdb = get_db()
                _sc  = _sdb.cursor()
                _sc.execute("""
                    INSERT INTO leads (user_id, address, arv_mid, lao, fee_at_lao, condition, spoke, status,
                                       beds, baths, sqft, rec_max_contract)
                    VALUES ((SELECT id FROM users WHERE role='super_admin' LIMIT 1),
                            %s, %s, %s, %s, %s, 'real_estate', 'new', %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (address, arv, lao_math['lao'], lao_math['fee_at_lao'], condition,
                      beds, baths, sqft, lao_math['lao']))
                _sdb.commit()
                _sc.close()
                _sdb.close()
            except Exception:
                pass

            if verdict in ('GO', 'PROCEED WITH CAUTION'):
                lines.append('')
                lines.append(f'_Run `draft offer {address}` to generate a seller offer letter._')

            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Scorecard error: {type(e).__name__}: {e}')
        return

    # ── DECISIONS (APEX Decision Queue) ──────────────────────────────────────
    if action == 'decisions':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, spoke, issue_summary, option_1, option_2, option_3,
                       recommended, reason, status,
                       EXTRACT(EPOCH FROM (NOW() - created_at))/3600 AS hrs_pending,
                       auto_execute_at
                FROM decision_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 10
            """)
            rows = cur.fetchall() or []
            db.close()

            if not rows:
                _slack_post(reply_channel, '✅ *Decision Queue is clear.* No pending decisions.')
                return

            lines = [f'*⚡ Decision Queue — {len(rows)} pending*', '']
            for d in rows:
                hrs = int(d['hrs_pending'])
                short_id = str(d['id'])[:8]
                auto_str = ''
                if d['auto_execute_at']:
                    hrs_left = max(0, int((d['auto_execute_at'] - datetime.now()).total_seconds() / 3600))
                    auto_str = f' | auto-executes in {hrs_left}h'
                lines.append(f'*[{hrs}h pending | {d["spoke"]}{auto_str}]*')
                lines.append(f'{d["issue_summary"]}')
                if d['option_1']: lines.append(f'  1. {d["option_1"]}')
                if d['option_2']: lines.append(f'  2. {d["option_2"]}')
                if d['option_3']: lines.append(f'  3. {d["option_3"]}')
                if d['recommended']:
                    lines.append(f'  → *ODIN recommends:* _{d["recommended"]}_')
                    if d['reason']: lines.append(f'    _{d["reason"]}_')
                lines.append(f'  `approve decision {short_id}` | `decline decision {short_id}`')
                lines.append('')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Decisions error: {type(e).__name__}: {e}')
        return

    if action == 'approve_decision':
        try:
            did = str(cmd.get('decision_id', '') or '').strip()
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Resolve position number (1/2/3) to actual ID
            if did.isdigit():
                pos = int(did)
                cur.execute("""
                    SELECT id FROM decision_queue WHERE status='pending'
                    ORDER BY created_at ASC LIMIT %s
                """, (pos,))
                rows = cur.fetchall()
                did = str(rows[-1]['id'])[:8] if rows else did
            cur.execute("""
                UPDATE decision_queue
                SET status = 'approved', resolved_at = NOW(), updated_at = NOW()
                WHERE id::text LIKE %s AND status = 'pending'
                RETURNING issue_summary, recommended
            """, (f'{did}%',))
            row = cur.fetchone()
            db.commit()
            db.close()
            if row:
                _slack_post(reply_channel, f'✅ *Decision approved.*\n_{row["issue_summary"]}_\nAction: {row["recommended"]}')
            else:
                _slack_post(reply_channel, f'⚠️ Decision `{did}` not found or already resolved.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Approve decision error: {e}')
        return

    if action == 'decline_decision':
        try:
            did = str(cmd.get('decision_id', '') or '').strip()
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if did.isdigit():
                pos = int(did)
                cur.execute("""
                    SELECT id FROM decision_queue WHERE status='pending'
                    ORDER BY created_at ASC LIMIT %s
                """, (pos,))
                rows = cur.fetchall()
                did = str(rows[-1]['id'])[:8] if rows else did
            cur.execute("""
                UPDATE decision_queue
                SET status = 'declined', resolved_at = NOW(), updated_at = NOW()
                WHERE id::text LIKE %s AND status = 'pending'
                RETURNING issue_summary
            """, (f'{did}%',))
            row = cur.fetchone()
            db.commit()
            db.close()
            if row:
                _slack_post(reply_channel, f'❌ *Decision declined.*\n_{row["issue_summary"]}_')
            else:
                _slack_post(reply_channel, f'⚠️ Decision `{did}` not found or already resolved.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Decline decision error: {e}')
        return

    # ── LOG DECISION ──────────────────────────────────────────────────────────
    if action == 'log_decision':
        try:
            from datetime import timedelta
            db  = get_db()
            cur = db.cursor()
            cur.execute("""
                INSERT INTO decision_queue
                  (spoke, issue_summary, option_1, option_2, option_3,
                   recommended, reason, status, auto_execute_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id
            """, (
                cmd.get('spoke', 'general'),
                cmd.get('issue', ''),
                cmd.get('option_1'),
                cmd.get('option_2'),
                cmd.get('option_3'),
                cmd.get('recommended'),
                cmd.get('reason'),
                datetime.utcnow() + timedelta(hours=48),
            ))
            new_id = str(cur.fetchone()[0])[:8]
            db.commit()
            db.close()
            _slack_post(reply_channel,
                f'✅ *Decision logged* `{new_id}` — _{cmd.get("issue", "")[:80]}_\n'
                f'Auto-executes in 48h if not resolved. Reply `decisions` to review.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Log decision error: {type(e).__name__}: {e}')
        return

    # ── REVENUE ───────────────────────────────────────────────────────────────
    if action == 'revenue':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT spoke,
                       SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END) AS income,
                       SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expenses,
                       COUNT(*) AS events
                FROM revenue_events
                WHERE DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY spoke
                ORDER BY income DESC
            """)
            rows = cur.fetchall() or []

            cur.execute("""
                SELECT spoke, amount, type, description, event_date
                FROM revenue_events
                ORDER BY event_date DESC, created_at DESC
                LIMIT 5
            """)
            recent = cur.fetchall() or []

            # Revenue targets
            cur.execute("""
                SELECT spoke, metric_name, target_value, current_value, unit
                FROM kpi_targets
                WHERE metric_name ILIKE '%revenue%' OR metric_name ILIKE '%mrr%'
            """)
            targets = {row['spoke']: row for row in (cur.fetchall() or [])}

            db.close()

            month_str = datetime.now().strftime('%B %Y')
            lines = [f'*💰 Revenue — {month_str}*', '']

            if rows:
                total_net = sum(r['income'] - r['expenses'] for r in rows)
                for r in rows:
                    net = r['income'] - r['expenses']
                    tgt = targets.get(r['spoke'])
                    tgt_str = f' / ${tgt["target_value"]:,.0f} target' if tgt else ''
                    pct = f' ({net/tgt["target_value"]*100:.0f}%)' if tgt and tgt['target_value'] else ''
                    lines.append(f'*{r["spoke"]}*: ${r["income"]:,.0f} in — ${r["expenses"]:,.0f} out = *${net:,.0f} net*{tgt_str}{pct}')
                lines.append(f'\n*Total net: ${total_net:,.0f}*')
            else:
                lines.append('No revenue logged this month yet.')
                lines.append('Log income: `revenue log spoke:real_estate amount:15000 type:income desc:Assignment fee 123 Main`')

            if recent:
                lines.append('\n*Recent events:*')
                for r in recent:
                    sign = '+' if r['type'] == 'income' else '-'
                    lines.append(f'• {r["event_date"]} | {r["spoke"]} | {sign}${abs(r["amount"]):,.0f} | {r["description"] or r["type"]}')

            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Revenue error: {type(e).__name__}: {e}')
        return

    if action == 'revenue_log':
        try:
            db  = get_db()
            cur = db.cursor()
            cur.execute("""
                INSERT INTO revenue_events (spoke, amount, type, description)
                VALUES (%s, %s, %s, %s)
            """, (cmd['spoke'], cmd['amount'], cmd['type'], cmd['description']))
            db.commit()
            db.close()
            sign = '+' if cmd['type'] == 'income' else '-'
            _slack_post(reply_channel,
                f'✅ Revenue logged: *{cmd["spoke"]}* | {sign}${abs(cmd["amount"]):,.0f} | _{cmd["description"]}_\n'
                f'Reply `revenue` for full month view.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Revenue log error: {e}')
        return

    # ── REVENUE SYNC (on-demand) ──────────────────────────────────────────────
    if action == 'revenue_sync':
        try:
            from utils import heartbeat
            _slack_post(reply_channel, '🔄 Scanning XLeads for won deals...')
            heartbeat.revenue_sync()
        except Exception as e:
            _slack_post(reply_channel, f'❌ Revenue sync error: {type(e).__name__}: {e}')
        return

    # ── BLAST STATS ───────────────────────────────────────────────────────────
    if action == 'blast_stats':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # Get recent campaigns (last 30 days), or specific one by ID prefix
            campaign_id = cmd.get('campaign_id')
            if campaign_id:
                cur.execute("""
                    SELECT id, workflow_name, tags, sent_count, failed_count,
                           opt_out_count, reply_count, health_status, created_at
                    FROM blast_campaigns
                    WHERE id::text LIKE %s
                    ORDER BY created_at DESC LIMIT 5
                """, (f'{campaign_id}%',))
            else:
                cur.execute("""
                    SELECT id, workflow_name, tags, sent_count, failed_count,
                           opt_out_count, reply_count, health_status, created_at
                    FROM blast_campaigns
                    WHERE created_at >= NOW() - INTERVAL '30 days'
                    ORDER BY created_at DESC LIMIT 10
                """)
            campaigns = cur.fetchall() or []

            if not campaigns:
                _slack_post(reply_channel, '📭 No blast campaigns found in the last 30 days.\nFire one: `blast tags:he,tax-delinquent workflow:<id> limit:100`')
                db.close()
                return

            lines = [f'*📊 Blast Campaign Stats*', '']

            for c in campaigns:
                sent     = c['sent_count'] or 0
                replies  = c['reply_count'] or 0
                opt_outs = c['opt_out_count'] or 0
                failed   = c['failed_count'] or 0
                reply_rate   = f'{replies/sent*100:.1f}%' if sent else '—'
                opt_out_rate = f'{opt_outs/sent*100:.1f}%' if sent else '—'
                short_id     = str(c['id'])[:8]
                date_str     = c['created_at'].strftime('%b %d %I:%M%p') if c['created_at'] else '—'
                health_emoji = {'ok': '✅', 'warning': '⚠️', 'flagged': '🚨'}.get(c['health_status'] or 'ok', '✅')

                lines.append(f'*{date_str}* — `{short_id}` {health_emoji}')
                lines.append(f'  Tags: `{c["tags"] or "—"}` | Workflow: {c["workflow_name"] or "—"}')
                lines.append(f'  Sent: {sent} | Failed: {failed} | Reply rate: *{reply_rate}* | Opt-out: {opt_out_rate}')

                # MCTP breakdown from linked leads
                cur.execute("""
                    SELECT
                      COUNT(*) FILTER (WHERE mctp_total >= 8) AS hot,
                      COUNT(*) FILTER (WHERE mctp_total >= 5 AND mctp_total < 8) AS warm,
                      COUNT(*) FILTER (WHERE mctp_total < 5 AND mctp_total IS NOT NULL) AS cold,
                      COUNT(*) AS total_leads
                    FROM leads
                    WHERE blast_campaign_id = %s
                """, (str(c['id']),))
                mctp = cur.fetchone()
                if mctp and mctp['total_leads'] > 0:
                    lines.append(f'  Leads scored: 🔥{mctp["hot"]} hot / ⚡{mctp["warm"]} warm / 🧊{mctp["cold"]} cold ({mctp["total_leads"]} total)')
                lines.append('')

            lines.append('`blast stats <id>` for specific campaign detail')
            db.close()
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Blast stats error: {type(e).__name__}: {e}')
        return

    # ── DRAFT OFFER ───────────────────────────────────────────────────────────
    if action == 'draft_offer':
        address = cmd.get('address', '').strip()
        if not address:
            _slack_post(reply_channel, '⚠️ Usage: `draft offer <address>`\nExample: `draft offer 4314 Leatherwood Memphis TN`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT * FROM leads
                WHERE address ILIKE %s
                ORDER BY updated_at DESC LIMIT 1
            """, (f'%{address}%',))
            lead = cur.fetchone()

            if not lead:
                _slack_post(reply_channel, f'⚠️ No lead found for `{address}`.\nRun `scorecard {address} arv:XXXXX` first to build the lead record.')
                db.close()
                return

            arv        = lead.get('arv_mid') or 0
            lao        = lead.get('lao') or 0
            fee        = lead.get('fee_at_lao') or 0
            condition  = lead.get('condition') or 'unknown'
            motivation = lead.get('motivation') or ''
            beds       = lead.get('beds') or 3
            baths      = lead.get('baths') or 2
            notes      = lead.get('caller_notes') or ''

            if not arv:
                _slack_post(reply_channel, f'⚠️ No ARV on record for `{address}`.\nRun `scorecard {address} arv:XXXXX` first.')
                db.close()
                return

            _slack_post(reply_channel, f'⏳ Drafting offer letter for {address}...')

            offer_prompt = (
                f'You are a real estate wholesaler. Draft a professional but warm seller offer letter '
                f'for the following property. Be direct and concise — max 200 words.\n\n'
                f'Property: {address}\n'
                f'Beds/Baths: {beds}bd/{baths}ba | Condition: {condition}\n'
                f'Our offer: ${lao:,.0f} (cash, as-is, fast close)\n'
                f'ARV: ${arv:,.0f}\n'
                f'Seller motivation notes: {motivation or notes or "Not yet determined"}\n\n'
                f'Include: cash offer amount, as-is purchase, flexible close date, '
                f'no repairs needed, no agent fees. '
                f'Warm tone — not pushy. End with a clear call to action.'
            )

            import anthropic as _anthropic
            _ac = _anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
            resp = _ac.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=500,
                messages=[{'role': 'user', 'content': offer_prompt}]
            )
            letter = resp.content[0].text.strip()

            # Save to lead
            cur.execute("""
                UPDATE leads
                SET offer_amount = %s, offer_status = 'drafted', offer_drafted_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
            """, (lao, str(lead['id'])))
            db.commit()
            db.close()

            _slack_post(reply_channel,
                f'*📝 Offer Letter — {address}*\n'
                f'Offer: *${lao:,.0f}* | ARV: ${arv:,.0f} | Fee: ${fee:,.0f}\n\n'
                f'{letter}\n\n'
                f'_Status set to "drafted". Reply `offer status {address}` to check. '
                f'Update: `follow up {address} action:Offer sent date:YYYY-MM-DD`_'
            )
        except Exception as e:
            _slack_post(reply_channel, f'❌ Draft offer error: {type(e).__name__}: {e}')
        return

    # ── OFFER STATUS ──────────────────────────────────────────────────────────
    if action == 'offer_status':
        address = cmd.get('address', '').strip()
        if not address:
            _slack_post(reply_channel, '⚠️ Usage: `offer status <address>`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT address, offer_amount, offer_status, offer_drafted_at,
                       arv_mid, lao, fee_at_lao, mctp_total, next_action, follow_up_date
                FROM leads WHERE address ILIKE %s
                ORDER BY updated_at DESC LIMIT 1
            """, (f'%{address}%',))
            lead = cur.fetchone()
            db.close()

            if not lead:
                _slack_post(reply_channel, f'⚠️ No lead found for `{address}`.')
                return

            status     = lead['offer_status'] or 'none'
            status_map = {'none': '⬜ No offer drafted', 'drafted': '📝 Drafted', 'sent': '📤 Sent',
                          'countered': '🔄 Countered', 'accepted': '✅ Accepted', 'dead': '❌ Dead'}
            status_str = status_map.get(status, status)
            drafted_str = lead['offer_drafted_at'].strftime('%b %d %I:%M%p') if lead.get('offer_drafted_at') else '—'

            lines = [f'*Offer Status — {lead["address"]}*', '']
            lines.append(f'Status: *{status_str}*')
            if lead.get('offer_amount'):
                lines.append(f'Offer amount: *${lead["offer_amount"]:,.0f}*')
            if lead.get('arv_mid'):
                lines.append(f'ARV: ${lead["arv_mid"]:,.0f} | LAO: ${lead["lao"]:,.0f} | Fee: ${lead["fee_at_lao"]:,.0f}')
            lines.append(f'MCTP score: {lead["mctp_total"] or "—"}/10')
            lines.append(f'Drafted: {drafted_str}')
            if lead.get('next_action'):
                lines.append(f'Next action: _{lead["next_action"]}_')
            if lead.get('follow_up_date'):
                lines.append(f'Follow-up: {lead["follow_up_date"]}')
            lines.append('')
            lines.append('`draft offer <address>` to regenerate | `follow up <address> action:... date:...` to update')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Offer status error: {type(e).__name__}: {e}')
        return

    # ── KPI SCORECARD ─────────────────────────────────────────────────────────
    if action == 'kpi_scorecard':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT spoke, metric_name, current_value, target_value, unit, dri, status
                FROM kpi_targets
                ORDER BY spoke, status DESC, metric_name
            """)
            rows = cur.fetchall() or []
            db.close()

            if not rows:
                _slack_post(reply_channel, 'No KPI targets set. Run migration 014 to seed defaults.')
                return

            STATUS_EMOJI = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
            lines = [f'*📊 ODIN KPI Scorecard*', '']

            current_spoke = None
            for r in rows:
                if r['spoke'] != current_spoke:
                    current_spoke = r['spoke']
                    lines.append(f'*{current_spoke.upper()}*')
                unit    = r['unit'] or ''
                cur_val = f'{unit}{r["current_value"] or 0:,.0f}' if unit == '$' else f'{r["current_value"] or 0:.0f}{unit}'
                tgt_val = f'{unit}{r["target_value"] or 0:,.0f}' if unit == '$' else f'{r["target_value"] or 0:.0f}{unit}'
                emoji   = STATUS_EMOJI.get(r['status'] or 'green', '⚪')
                lines.append(f'  {emoji} {r["metric_name"]}: {cur_val} / {tgt_val} — DRI: {r["dri"]}')

            lines.append('\n`revenue` for P&L | `decisions` for queue | `leads` for pipeline')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ KPI scorecard error: {type(e).__name__}: {e}')
        return

    # ── BUYERS ONBOARDING PIPELINE ────────────────────────────────────────────
    if action == 'buyers_onboarding':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT onboarding_stage, COUNT(*) AS cnt
                FROM buyers
                WHERE is_dnc = FALSE
                GROUP BY onboarding_stage
                ORDER BY cnt DESC
            """)
            stages = cur.fetchall() or []
            cur.execute("""
                SELECT name, phone, onboarding_stage, onboarding_started_at
                FROM buyers
                WHERE onboarding_stage = 'welcomed'
                  AND onboarding_started_at < NOW() - INTERVAL '3 days'
                  AND is_dnc = FALSE
                ORDER BY onboarding_started_at ASC
                LIMIT 8
            """)
            stuck = cur.fetchall() or []
            db.close()

            lines = ['*👥 Buyer Onboarding Pipeline*', '']
            stage_order = ['new', 'welcomed', 'qualified', 'active', 'inactive']
            stage_map   = {r['onboarding_stage']: r['cnt'] for r in stages}
            stage_emoji = {'new': '⚪', 'welcomed': '📩', 'qualified': '✅', 'active': '🟢', 'inactive': '💤'}
            for s in stage_order:
                cnt = stage_map.get(s, 0)
                lines.append(f'{stage_emoji.get(s,"•")} *{s.capitalize()}*: {cnt}')

            if stuck:
                lines.append(f'\n⚠️ *{len(stuck)} stuck in welcomed (3+ days no reply):*')
                for b in stuck:
                    days = (datetime.now() - b['onboarding_started_at'].replace(tzinfo=None)).days
                    lines.append(f'  • {b["name"] or b["phone"]} — {days}d since welcome')

            lines.append('\n`onboard <name> cid:<id>` to manually trigger welcome SMS')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Buyers onboarding error: {type(e).__name__}: {e}')
        return

    # ── ONBOARD BUYER (manual trigger) ────────────────────────────────────────
    if action == 'onboard_buyer':
        name = cmd.get('name', '').strip()
        cid  = cmd.get('xleads_contact_id', '').strip()
        if not name:
            _slack_post(reply_channel, '⚠️ Usage: `onboard <buyer name> cid:<xleads_contact_id>`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT id, name, phone, onboarding_stage FROM buyers
                WHERE LOWER(name) LIKE LOWER(%s) AND is_dnc = FALSE
                ORDER BY created_at DESC LIMIT 1
            """, (f'%{name}%',))
            buyer = cur.fetchone()
            if not buyer:
                db.close()
                _slack_post(reply_channel, f'⚠️ No buyer found matching `{name}`.')
                return

            updates, params = [], []
            if cid:
                updates.extend(['xleads_contact_id = %s', 'onboarding_stage = %s', 'onboarding_started_at = NOW()'])
                params.extend([cid, 'new'])
            params.append(str(buyer['id']))
            if updates:
                set_clause = ', '.join(updates)
                cur.execute("UPDATE buyers SET " + set_clause + " WHERE id = %s", params)
                db.commit()

            # Fire welcome SMS if contact ID provided
            if cid:
                first_name = (buyer['name'] or 'there').split()[0]
                welcome_msg = (
                    f"Hey {first_name}, this is Brock with Shue Box LLC — a Memphis wholesaler. "
                    "I'll be sending you off-market deals in your buy box. "
                    "Reply with your price range and preferred areas so I can match you first. Talk soon!"
                )
                try:
                    xleads.send_sms(cid, welcome_msg)
                    cur.execute("""
                        UPDATE buyers SET onboarding_stage = 'welcomed' WHERE id = %s
                    """, (str(buyer['id']),))
                    db.commit()
                    _slack_post(reply_channel,
                        f'✅ Welcome SMS sent to *{buyer["name"]}*.\n'
                        f'Stage: new → welcomed.')
                except Exception as sms_e:
                    _slack_post(reply_channel, f'⚠️ Buyer updated but SMS failed: {sms_e}')
            else:
                _slack_post(reply_channel,
                    f'✅ Buyer *{buyer["name"]}* found. Add `cid:<xleads_contact_id>` to send welcome SMS.')
            db.close()
        except Exception as e:
            _slack_post(reply_channel, f'❌ Onboard buyer failed: {type(e).__name__}: {e}')
        return

    # ── FINANCE — BROCK ONLY ──────────────────────────────────────────────────
    BROCK_SLACK_UID = os.environ.get('BROCK_SLACK_UID', 'U0B5C32BJ6B')
    if action in ('finance_dashboard', 'finance_sync', 'finance_balances',
                  'finance_subscriptions', 'finance_spending', 'finance_biz_card',
                  'finance_invest', 'finance_mark_biz_card'):
        if sender_uid and sender_uid != BROCK_SLACK_UID:
            _slack_post(reply_channel, '🔒 Finance commands are private.')
            return

    # ── FINANCE DASHBOARD ─────────────────────────────────────────────────────
    if action == 'finance_dashboard':
        _slack_post(reply_channel, '_Pulling finance dashboard…_')
        try:
            _slack_post(reply_channel, finance_bot.finance_dashboard())
        except Exception as e:
            _slack_post(reply_channel, f'❌ Finance dashboard error: {type(e).__name__}: {e}')
        return

    # ── FINANCE SYNC ──────────────────────────────────────────────────────────
    if action == 'finance_sync':
        _slack_post(reply_channel, '_Syncing accounts + transactions from Teller.io…_')
        try:
            result = finance_bot.full_sync()
            _slack_post(reply_channel, result)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Finance sync error: {type(e).__name__}: {e}')
        return

    # ── FINANCE BALANCES ──────────────────────────────────────────────────────
    if action == 'finance_balances':
        try:
            bal = finance_bot.get_balances()
            if not bal['accounts']:
                _slack_post(reply_channel, '⚠️ No accounts synced yet. Run `finance sync` first.')
                return
            lines = ['*💰 Account Balances*', '']
            for a in bal['accounts']:
                b = float(a['last_balance'] or 0)
                v = float(a['available_balance'] or 0)
                tag = ' ⭐ BIZ' if a['is_biz_card'] else ''
                if a['account_type'] == 'depository':
                    lines.append(f'  {a["institution_name"]} — {a["account_name"]}: *${b:,.2f}*{tag}')
                elif a['account_type'] == 'credit':
                    lines.append(f'  {a["institution_name"]} — {a["account_name"]}: ${b:,.2f} used | *${v:,.2f} avail*{tag}')
            lines.append('')
            lines.append(f'*Net liquid:* ${bal["net_liquid"]:,.2f}  |  *Total deposits:* ${bal["total_deposits"]:,.2f}  |  *Total credit used:* ${bal["total_credit"]:,.2f}')
            if bal.get('reserved', 0) > 0:
                lines.append(f'*Reserved ({bal.get("reserved_label","Reserved funds")}):* -${bal["reserved"]:,.2f}')
                lines.append(f'*True available liquid:* ${bal["true_liquid"]:,.2f}')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Balances error: {type(e).__name__}: {e}')
        return

    # ── FINANCE SUBSCRIPTIONS ─────────────────────────────────────────────────
    if action == 'finance_subscriptions':
        try:
            subs = finance_bot.get_subscriptions()
            if not subs:
                _slack_post(reply_channel, '⚠️ No subscriptions detected yet. Run `finance sync` to analyze your transactions.')
                return
            total = sum(float(s['amount']) for s in subs)
            lines = [f'*🔄 Active Subscriptions — {len(subs)} detected (${total:,.2f}/mo)*', '']
            for s in subs:
                amt    = float(s['amount'])
                annual = round(amt * 12, 2)
                freq   = f' ({s["frequency"]})' if s['frequency'] != 'monthly' else ''
                acct   = f' via {s["account_name"]}' if s.get('account_name') else ''
                nxt    = f' | next: {s["next_expected"]}' if s.get('next_expected') else ''
                lines.append(f'  • *{s["merchant_name"]}*: ${amt:,.2f}/mo (${annual:,.0f}/yr){freq}{acct}{nxt}')
            lines.append('')
            lines.append(f'*Annual subscription cost: ${total * 12:,.0f}*')
            lines.append('Review unused subscriptions and cancel to free up cash flow.')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Subscriptions error: {type(e).__name__}: {e}')
        return

    # ── FINANCE SPENDING ──────────────────────────────────────────────────────
    if action == 'finance_spending':
        period = cmd.get('period', 'month')
        try:
            report = finance_bot.get_spending_report(period)
            lines  = [f'*📊 Spending Report — {report["period"]}*', '']
            lines.append(f'  *Total spend:* ${report["total_spend"]:,.2f}')
            lines.append(f'  *Daily avg:* ${report["daily_avg"]:,.2f}')
            if report.get('biz_card_spend'):
                lines.append(f'  *Biz card:* ${report["biz_card_spend"]:,.2f}')
            lines.append('')
            if report['by_category']:
                lines.append('*By Category:*')
                for cat in report['by_category'][:8]:
                    pct = round(float(cat['total']) / report['total_spend'] * 100, 1) if report['total_spend'] else 0
                    lines.append(f'  • {cat["cat"]}: ${float(cat["total"]):,.2f} ({pct}%) — {cat["tx_count"]} txns')
            if report['top_merchants']:
                lines.append('')
                lines.append('*Top Merchants:*')
                for m in report['top_merchants'][:6]:
                    lines.append(f'  • {m["merchant"]}: ${float(m["total"]):,.2f} ({m["tx_count"]} txns)')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Spending report error: {type(e).__name__}: {e}')
        return

    # ── BIZ CARD STATUS ───────────────────────────────────────────────────────
    if action == 'finance_biz_card':
        try:
            biz = finance_bot.get_biz_card()
            if not biz:
                _slack_post(reply_channel,
                    '⚠️ No biz card marked yet.\n'
                    'Run `balances` to see accounts, then `mark biz card <account name>` to designate one.')
                return
            lines = [
                f'*💳 Business Card — {biz["institution_name"]}*',
                f'Account: {biz["account_name"]}',
                f'Limit:     *${biz["limit"]:,.0f}*',
                f'Used:      ${biz["balance"]:,.2f} ({biz["used_pct"]}%)',
                f'Available: *${biz["available"]:,.2f}*',
                '',
                'This card is your business growth fund — exclusively for ROI-generating investments.',
                '`invest` for AI recommendation on where to deploy available balance.',
            ]
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Biz card error: {type(e).__name__}: {e}')
        return

    # ── BIZ CARD INVEST ADVISOR ───────────────────────────────────────────────
    if action == 'finance_invest':
        _slack_post(reply_channel, '_Analyzing best ROI deployment for your biz card balance… (15–20 sec)_')
        try:
            result = finance_bot.analyze_biz_card_investment()
            _slack_post(reply_channel, result)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Investment analysis error: {type(e).__name__}: {e}')
        return

    # ── MARK BIZ CARD ─────────────────────────────────────────────────────────
    if action == 'finance_mark_biz_card':
        name = cmd.get('name', '').strip()
        if not name:
            _slack_post(reply_channel, '⚠️ Usage: `mark biz card <account name or bank name>`')
            return
        try:
            result = finance_bot.mark_biz_card(name)
            _slack_post(reply_channel, result)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Mark biz card error: {type(e).__name__}: {e}')
        return

    # ── KNOWLEDGE SEARCH ──────────────────────────────────────────────────────
    if action == 'knowledge_search':
        query = cmd.get('query', '').strip()
        if not query:
            _slack_post(reply_channel,
                'Usage: `find knowledge <query>`\n'
                'Example: `find knowledge objection handling price too low`\n'
                'Example: `find knowledge iOS cold calling workaround`')
            return
        try:
            db = get_db()
            cur = db.cursor()
            cur.execute("""
                SELECT title, source,
                       ts_headline('english', content, query,
                           'MaxWords=200, MinWords=60, StartSel=>>>,StopSel=<<<,MaxFragments=2') AS excerpt
                FROM transcript_knowledge,
                     plainto_tsquery('english', %s) query
                WHERE content_tsv @@ query
                ORDER BY ts_rank(content_tsv, query) DESC
                LIMIT 8
            """, (query,))
            rows = cur.fetchall()
            cur.close()
            if not rows:
                _slack_post(reply_channel, f'No results found for: *{query}*\nTry different keywords.')
                return

            source_labels = {'flip_with_rick': 'Flip With Rick', 'dan_martell': 'Dan Martell'}
            context_parts = []
            for title, source, excerpt in rows:
                label = source_labels.get(source, source)
                clean = (excerpt or '').replace('>>>', '').replace('<<<', '')
                context_parts.append(f'[{label} — "{title}"]\n{clean}')
            context_block = '\n\n---\n\n'.join(context_parts)

            ai_prompt = (
                f'You are ODIN, a business intelligence system. '
                f'A user searched their transcript knowledge base for: "{query}"\n\n'
                f'Here are the most relevant excerpts from their training transcripts '
                f'(Flip With Rick = real estate wholesaling, Dan Martell = SaaS/business growth):\n\n'
                f'{context_block}\n\n'
                f'Synthesize a concise, actionable answer to the query "{query}" based on these excerpts. '
                f'Pull out the specific tactics, scripts, or frameworks mentioned. '
                f'Be direct — bullet points preferred. 200 words max. '
                f'Cite the source (FWR or DM) next to each point.'
            )
            import anthropic as _anthropic
            _ac = _anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))
            ai_resp = _ac.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=400,
                messages=[{'role': 'user', 'content': ai_prompt}]
            )
            answer = ai_resp.content[0].text.strip()
            _slack_post(reply_channel, f'*Knowledge: "{query}"*\n\n{answer}')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Knowledge search error: {type(e).__name__}: {e}')
        return

    # ── EMAIL TRIAGE (autonomous inbox — Brock only) ──────────────────────────
    if action == 'email_triage':
        if sender_uid and sender_uid != BROCK_SLACK_UID:
            _slack_post(reply_channel, '🔒 Email triage is private.')
            return
        _slack_post(reply_channel, '_Scanning inbox…_')
        try:
            _slack_post(reply_channel, email_triage.run_triage(_haiku))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Email triage error: {type(e).__name__}: {e}')
        return

    # ── OUTBOUND VOICE BRIEFING (ODIN calls Brock — Brock only) ────────────────
    if action == 'call_me':
        if sender_uid and sender_uid != BROCK_SLACK_UID:
            _slack_post(reply_channel, '🔒 Voice briefing is private.')
            return
        if not twilio_voice.is_available():
            _slack_post(reply_channel,
                '📞 Voice briefing not configured. Set TWILIO_ACCOUNT_SID, '
                'TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, BROCK_PHONE_NUMBER in Railway.')
            return
        try:
            briefing = heartbeat.build_briefing_text() if hasattr(heartbeat, 'build_briefing_text') \
                else 'This is ODIN. Your on-demand briefing. Check Slack for full details.'
            ok = twilio_voice.call_briefing(briefing)
            _slack_post(reply_channel,
                '📞 Calling you now…' if ok else '❌ Twilio rejected the call request.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Voice briefing error: {type(e).__name__}: {e}')
        return

    # ── LEAD SNIPER (manual) ──────────────────────────────────────────────────
    if action == 'snipe_leads':
        niche = cmd.get('niche', '').strip()
        count = int(cmd.get('count', 5))
        valid_niches = ('plumbing', 'roofing', 'auto_repair', 'auto', 'dental', 'law')
        if niche not in valid_niches:
            _slack_post(reply_channel,
                f'Usage: `snipe <niche> [count]`\n'
                f'Niches: plumbing, roofing, auto_repair, dental, law')
            return
        if niche == 'auto':
            niche = 'auto_repair'
        try:
            lead_sniper.init(_slack_post, get_db, CHANNELS)
            import threading
            threading.Thread(
                target=lead_sniper.snipe,
                args=(niche, count),
                daemon=True
            ).start()
            _slack_post(reply_channel,
                f'🔍 Sniping {count} *{niche}* leads in Columbus OH… '
                f'I\'ll post results when done.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Snipe error: {type(e).__name__}: {e}')
        return

    # ── OUTREACH QUEUE STATUS ─────────────────────────────────────────────────
    if action == 'outreach_queue':
        try:
            email_sender.init(_slack_post, get_db, CHANNELS)
            _slack_post(reply_channel, email_sender.get_queue_status())
        except Exception as e:
            _slack_post(reply_channel, f'❌ Queue status error: {type(e).__name__}: {e}')
        return

    # ── OUTREACH PIPELINE ─────────────────────────────────────────────────────
    if action == 'outreach':
        sub     = cmd.get('sub', '').strip().lower()
        email   = cmd.get('email', '').strip()
        status  = cmd.get('status', '').strip()
        try:
            outreach_tracker.init(_slack_post, get_db, CHANNELS)
            if sub == 'stats':
                _slack_post(reply_channel, outreach_tracker.get_stats())
            elif sub in ('plumbing','roofing','dental','law','auto_repair','auto'):
                niche = 'auto_repair' if sub == 'auto' else sub
                _slack_post(reply_channel, outreach_tracker.get_pipeline(niche=niche))
            else:
                _slack_post(reply_channel, outreach_tracker.get_pipeline())
        except Exception as e:
            _slack_post(reply_channel, f'❌ Outreach error: {type(e).__name__}: {e}')
        return

    # log sent <email> [step:N] [hook:type]
    if action == 'log_sent':
        email     = cmd.get('email', '').strip()
        step      = int(cmd.get('step', 1))
        hook_type = cmd.get('hook', None)
        if not email:
            _slack_post(reply_channel, 'Usage: `log sent <email> [step:2] [hook:missed_calls]`')
            return
        try:
            outreach_tracker.init(_slack_post, get_db, CHANNELS)
            _slack_post(reply_channel, outreach_tracker.log_sent(email, step=step, hook_type=hook_type))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Log sent error: {type(e).__name__}: {e}')
        return

    # log reply <email> [status:call_booked]
    if action == 'log_reply':
        email      = cmd.get('email', '').strip()
        new_status = cmd.get('status', 'replied').strip()
        if not email:
            _slack_post(reply_channel,
                'Usage: `log reply <email> [status:call_booked]`\n'
                'Statuses: replied, call_booked, audit_sold, build_sold, retainer, not_interested, dead')
            return
        try:
            outreach_tracker.init(_slack_post, get_db, CHANNELS)
            _slack_post(reply_channel, outreach_tracker.log_reply_manual(email, new_status=new_status))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Log reply error: {type(e).__name__}: {e}')
        return

    # ── ERROR / UNKNOWN ────────────────────────────────────────────────────────
    if action == 'error':
        _slack_post(reply_channel, f'⚠️ {cmd.get("msg", "Invalid command")}')
        return

    _slack_post(reply_channel,
        f'🤔 I didn\'t understand that. Type `help` to see all commands.')

DATABASE_URL = os.environ.get('DATABASE_URL', '')
JWT_SECRET   = os.environ.get('JWT_SECRET_KEY', 'odin-dev-secret-change-in-prod')
JWT_EXPIRES  = int(os.environ.get('JWT_EXPIRES_HOURS', 24))
BASE_URL     = os.environ.get('BASE_URL', 'http://localhost:5000')

VALID_ROLES = (
    'super_admin', 're_partner', 'acquisition_manager',
    'caller', 'disposition_agent', 'business_standard', 'virtual_assistant'
)


# ─── Database ────────────────────────────────────────────────────

def get_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set. Add it to .env or Railway env vars.')
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def log_action(user_id, action_type, resource=None, resource_id=None, data=None, result='ok'):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_actions
                   (user_id, action_type, resource, resource_id, data, result, ip_address)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(user_id) if user_id else None,
                    action_type, resource,
                    str(resource_id) if resource_id else None,
                    json.dumps(data) if data else None,
                    result,
                    request.remote_addr,
                )
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f'[log_action] Failed: {e}')


def _run_agent(name: str, fn, input_summary: str = '',
               triggered_by: str = 'brock', **kwargs):
    """
    Call any agent function, log the result (success or error) to agent_logs.
    Returns the result dict on success. Re-raises exceptions so existing
    error handling in callers still works.

    Usage:
        result = _run_agent('scout', scout_agent.run,
                            input_summary=keywords, triggered_by=who,
                            keywords=keywords, budget_usd=500)
    """
    import time, traceback as tb
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


# ─── Auth decorators ─────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = auth_header.replace('Bearer ', '').strip()
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired — please log in again'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """Requires the authenticated user to have one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if g.user.get('role') not in roles:
                return jsonify({'error': 'Permission denied'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_permission_for(action, resource):
    """Checks permission matrix for the authenticated user."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            role = g.user.get('role', '')
            if not check_permission_role(role, action, resource):
                return jsonify({'error': f'Permission denied: cannot {action} {resource}'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Dashboard (serve static HTML) ───────────────────────────────

@app.route('/')
def serve_dashboard():
    return send_from_directory(app.static_folder, 'index.html')


# ─── AUTH ─────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM users WHERE email = %s AND active = true',
                (email,)
            )
            user = cur.fetchone()
    finally:
        conn.close()

    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401

    if not user['password_hash']:
        return jsonify({'error': 'Account not activated — check your invite link'}), 401

    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        log_action(user['id'], 'login_failed', result='fail')
        return jsonify({'error': 'Invalid credentials'}), 401

    # Update last_active
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute('UPDATE users SET last_active = NOW() WHERE id = %s', (user['id'],))
        conn.commit()
    conn.close()

    token = jwt.encode({
        'user_id':   str(user['id']),
        'name':      user['name'],
        'email':     user['email'],
        'role':      user['role'],
        'namespace': user['namespace'],
        'exp':       datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRES),
    }, JWT_SECRET, algorithm='HS256')

    log_action(user['id'], 'login', result='ok')
    return jsonify({
        'token': token,
        'user': {
            'id':        str(user['id']),
            'name':      user['name'],
            'email':     user['email'],
            'role':      user['role'],
            'namespace': user['namespace'],
        }
    })


@app.route('/api/auth/set-password', methods=['POST'])
def set_password():
    """Called when a new user clicks their invite link and sets a password."""
    data  = request.get_json() or {}
    token = (data.get('token') or '').strip()
    pw    = (data.get('password') or '').strip()

    if not token or not pw:
        return jsonify({'error': 'Token and password required'}), 400
    if len(pw) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT it.*, u.id as user_id, u.name
                   FROM invite_tokens it
                   JOIN users u ON u.id = it.user_id
                   WHERE it.token = %s AND it.used = false AND it.expires_at > NOW()""",
                (token,)
            )
            invite = cur.fetchone()

        if not invite:
            return jsonify({'error': 'Invalid or expired invite link'}), 400

        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE users SET password_hash = %s, onboarded = true WHERE id = %s',
                (hashed, invite['user_id'])
            )
            cur.execute(
                'UPDATE invite_tokens SET used = true WHERE id = %s',
                (invite['id'],)
            )
            conn.commit()

        log_action(invite['user_id'], 'set_password', result='ok')
        return jsonify({'message': f'Password set for {invite["name"]}. You can now log in.'})
    finally:
        conn.close()


# ─── USERS ────────────────────────────────────────────────────────

@app.route('/api/users', methods=['GET'])
@require_auth
@require_permission_for('read', 'users')
def list_users():
    role_filter = request.args.get('role')  # AM can only see callers
    requesting_role = g.user.get('role')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if requesting_role == 'acquisition_manager':
                # AM sees only callers
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users WHERE role = 'caller' ORDER BY name"""
                )
            elif role_filter:
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users WHERE role = %s ORDER BY name""",
                    (role_filter,)
                )
            else:
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users ORDER BY
                           CASE role
                               WHEN 'super_admin' THEN 1
                               WHEN 're_partner' THEN 2
                               WHEN 'acquisition_manager' THEN 3
                               WHEN 'caller' THEN 4
                               WHEN 'disposition_agent' THEN 5
                               WHEN 'business_standard' THEN 6
                               WHEN 'virtual_assistant' THEN 7
                           END, name"""
                )
            users = [dict(r) for r in cur.fetchall()]
            # Stringify UUIDs
            for u in users:
                u['id'] = str(u['id'])
                if u.get('last_active'):
                    u['last_active'] = u['last_active'].isoformat()
                if u.get('created_at'):
                    u['created_at'] = u['created_at'].isoformat()
    finally:
        conn.close()

    return jsonify({'users': users})


@app.route('/api/users', methods=['POST'])
@require_auth
@require_role('super_admin')
def create_user():
    """Component 4: Add User — super_admin only."""
    data  = request.get_json() or {}
    name  = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    role  = (data.get('role') or '').strip()
    spend = data.get('spend_limit')
    max_fee = data.get('max_approvable_fee')

    if not name or not email or not role:
        return jsonify({'error': 'name, email, and role are required'}), 400
    if role not in VALID_ROLES:
        return jsonify({'error': f'Invalid role. Must be one of: {", ".join(VALID_ROLES)}'}), 400
    if role == 'super_admin' and g.user.get('role') == 'super_admin':
        return jsonify({'error': 'Cannot create another super_admin'}), 403

    # Derive namespace from name
    namespace = name.lower().replace(' ', '_').replace('-', '_')[:50]

    can_approve = role in ('super_admin', 're_partner')
    if not max_fee and role == 're_partner':
        max_fee = 15000

    conn = get_db()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """INSERT INTO users
                       (name, email, phone, namespace, role, spend_limit,
                        can_approve_deals, max_approvable_fee, created_by,
                        invited_at, active, onboarded)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), true, false)
                       RETURNING id, name, email, role, namespace""",
                    (name, email, phone or None, namespace, role,
                     spend, can_approve, max_fee, g.user.get('user_id'))
                )
                new_user = dict(cur.fetchone())
                new_user['id'] = str(new_user['id'])
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                return jsonify({'error': 'Email or namespace already exists'}), 409

            # Generate invite token
            invite_token = secrets.token_urlsafe(32)
            cur.execute(
                """INSERT INTO invite_tokens (user_id, token, expires_at)
                   VALUES (%s, %s, NOW() + INTERVAL '48 hours')""",
                (new_user['id'], invite_token)
            )
            conn.commit()

        invite_url = f"{BASE_URL}/set-password?token={invite_token}"

        log_action(g.user['user_id'], 'create_user', 'users',
                   new_user['id'], {'role': role, 'name': name})

        # Send Slack notification to new user's channel if caller
        if role == 'caller':
            msg = new_user_invite_notification(name, role, invite_url)
            send_slack_notification('caller', msg, namespace=namespace)

    finally:
        conn.close()

    return jsonify({
        'user': new_user,
        'invite_url': invite_url,
        'message': f'User created. Invite link expires in 48 hours.'
    }), 201


@app.route('/api/users/<user_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def update_user(user_id):
    data = request.get_json() or {}
    allowed = ('role', 'spend_limit', 'max_approvable_fee', 'phone', 'name')
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400
    if 'role' in updates and updates['role'] not in VALID_ROLES:
        return jsonify({'error': 'Invalid role'}), 400
    if 'role' in updates and updates['role'] == 'super_admin':
        return jsonify({'error': 'Cannot assign super_admin role'}), 403

    values = list(updates.values()) + [user_id]

    conn = get_db()
    try:
        with conn.cursor() as cur:
            set_clause = psycopg2.sql.SQL(', ').join(
                psycopg2.sql.SQL('{} = %s').format(psycopg2.sql.Identifier(k))
                for k in updates
            )
            cur.execute(
                psycopg2.sql.SQL('UPDATE users SET {} WHERE id = %s RETURNING id, name, role').format(set_clause),
                values
            )
            updated = cur.fetchone()
            if not updated:
                return jsonify({'error': 'User not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'update_user', 'users', user_id, updates)
    finally:
        conn.close()

    return jsonify({'user': dict(updated), 'message': 'User updated'})


@app.route('/api/users/<user_id>/deactivate', methods=['POST'])
@require_auth
@require_role('super_admin')
def deactivate_user(user_id):
    if str(user_id) == str(g.user.get('user_id')):
        return jsonify({'error': 'Cannot deactivate your own account'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE users SET active = false WHERE id = %s RETURNING name',
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'User not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'deactivate_user', 'users', user_id)
    finally:
        conn.close()

    return jsonify({'message': f'{row["name"]} deactivated'})


@app.route('/api/users/<user_id>/invite', methods=['POST'])
@require_auth
@require_role('super_admin')
def resend_invite(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT name, email, namespace, role FROM users WHERE id = %s', (user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify({'error': 'User not found'}), 404

            invite_token = secrets.token_urlsafe(32)
            cur.execute(
                """INSERT INTO invite_tokens (user_id, token, expires_at)
                   VALUES (%s, %s, NOW() + INTERVAL '48 hours')""",
                (user_id, invite_token)
            )
            conn.commit()
    finally:
        conn.close()

    invite_url = f"{BASE_URL}/set-password?token={invite_token}"
    return jsonify({'invite_url': invite_url, 'message': 'Invite link generated (48 hours)'})


@app.route('/api/users/<user_id>/activity', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def user_activity(user_id):
    limit = min(int(request.args.get('limit', 50)), 200)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT action_type, resource, result, created_at
                   FROM agent_actions WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit)
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].isoformat()
    finally:
        conn.close()

    return jsonify({'activity': rows})


# ─── BUYERS ────────────────────────────────────────────────────────

@app.route('/api/buyers', methods=['GET'])
@require_auth
def list_buyers():
    role = g.user.get('role')
    if not check_permission_role(role, 'read', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    market   = request.args.get('market', '')
    active   = request.args.get('active', '')
    search   = request.args.get('search', '')
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    offset   = (page - 1) * per_page

    conn = get_db()
    try:
        with conn.cursor() as cur:
            conditions, params = ['1=1'], []
            if market:
                conditions.append("(market ILIKE %s OR state = %s OR city ILIKE %s)")
                params += [f'%{market}%', market.upper(), f'%{market}%']
            if active == 'true':
                conditions.append("active = true AND is_dnc = false")
            elif active == 'false':
                conditions.append("active = false")
            if search:
                conditions.append("(name ILIKE %s OR email ILIKE %s OR phone ILIKE %s OR company ILIKE %s)")
                params += [f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%']

            where = ' AND '.join(conditions)
            cur.execute("SELECT COUNT(*) as cnt FROM buyers WHERE " + where, params)
            total = cur.fetchone()['cnt']

            cur.execute(
                "SELECT * FROM buyers WHERE " + where + " ORDER BY active DESC, is_flipper DESC, portfolio_owned DESC LIMIT %s OFFSET %s",
                params + [per_page, offset]
            )
            buyers = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('created_at'): row['created_at'] = row['created_at'].isoformat()
                if row.get('updated_at'): row['updated_at'] = row['updated_at'].isoformat()
                buyers.append(row)
    finally:
        conn.close()

    return jsonify({'buyers': buyers, 'total': total, 'page': page, 'per_page': per_page})


@app.route('/api/leads/sync-xleads', methods=['POST'])
@require_auth
def sync_xleads_leads():
    """Pull contacts from XLeads and upsert into leads table."""
    role = g.user.get('role')
    if role not in ('super_admin', 're_partner', 'acquisition_manager'):
        return jsonify({'error': 'Permission denied'}), 403

    limit  = int(request.json.get('limit', 100)) if request.json else 100
    tags   = request.json.get('tags', []) if request.json else []

    try:
        contacts = xleads.search_contacts(tags=tags, limit=limit) if tags else xleads.get_contacts(limit=limit)
    except Exception as e:
        return jsonify({'error': f'XLeads fetch failed: {str(e)}'}), 500

    if not contacts:
        return jsonify({'synced': 0, 'message': 'No contacts returned from XLeads'})

    conn = get_db()
    synced, skipped = 0, 0
    try:
        with conn.cursor() as cur:
            for c in contacts:
                # Build address from XLeads contact fields
                address_parts = [
                    c.get('address1') or c.get('address', ''),
                    c.get('city', ''),
                    c.get('state', ''),
                    c.get('postalCode') or c.get('zip', ''),
                ]
                address = ', '.join(p for p in address_parts if p).strip(', ')
                if not address:
                    skipped += 1
                    continue

                # Check if lead already exists
                cur.execute("SELECT id FROM leads WHERE address = %s", (address,))
                if cur.fetchone():
                    skipped += 1
                    continue

                city  = c.get('city', '')
                state = c.get('state', '')
                zip_  = c.get('postalCode') or c.get('zip', '')

                cur.execute("""
                    INSERT INTO leads (id, address, city, state, zip, status, spoke, created_at, updated_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, 'new', 'real_estate', NOW(), NOW())
                """, (address, city, state, zip_))
                synced += 1

        conn.commit()
    finally:
        conn.close()

    return jsonify({'synced': synced, 'skipped': skipped, 'total_fetched': len(contacts)})


# ─── LEADS / MCTP ─────────────────────────────────────────────────

@app.route('/api/leads', methods=['GET'])
@require_auth
def list_leads():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')
    status  = request.args.get('status')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            base = """
                SELECT l.*, u.name as assigned_to_name
                FROM leads l
                LEFT JOIN users u ON u.id = l.assigned_to
            """
            if role == 'caller':
                # Caller sees only their assigned leads + unassigned pool
                query = base + """
                    WHERE (l.assigned_to = %s OR l.assigned_to IS NULL)
                """
                params = [user_id]
                if status:
                    query += ' AND l.status = %s'
                    params.append(status)
                query += ' ORDER BY l.created_at DESC'
                cur.execute(query, params)
            elif role == 'disposition_agent':
                # Dispo sees only contracted deals
                query = base + " WHERE l.status = 'contracted' ORDER BY l.updated_at DESC"
                cur.execute(query)
            else:
                # Everyone else with permission sees all
                if not check_permission_role(role, 'read', 'leads'):
                    return jsonify({'error': 'Permission denied'}), 403
                params = []
                query = base + ' WHERE 1=1'
                if status:
                    query += ' AND l.status = %s'
                    params.append(status)
                query += ' ORDER BY l.mctp_total DESC NULLS LAST, l.created_at DESC'
                cur.execute(query, params)

            leads = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('user_id'):
                    row['user_id'] = str(row['user_id'])
                if row.get('assigned_to'):
                    row['assigned_to'] = str(row['assigned_to'])
                if row.get('created_at'):
                    row['created_at'] = row['created_at'].isoformat()
                if row.get('updated_at'):
                    row['updated_at'] = row['updated_at'].isoformat()
                leads.append(row)
    finally:
        conn.close()

    return jsonify({'leads': leads})


@app.route('/api/leads/mctp', methods=['POST'])
@require_auth
def log_mctp():
    """Log a seller MCTP call. Caller + any role with leads write permission."""
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    if not check_permission_role(role, 'write', 'assigned_leads') and \
       not check_permission_role(role, 'write', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}

    # Required fields
    address = (data.get('address') or '').strip()
    if not address:
        return jsonify({'error': 'address is required'}), 400

    # MCTP scoring
    mot_score      = int(data.get('motivation_score', 0))
    cond_score     = int(data.get('condition_score', 0))
    timeline_score = int(data.get('timeline_score', 0))
    price_score    = int(data.get('price_score', 0))
    mctp_total     = mot_score + cond_score + timeline_score + price_score

    status = 'hot' if mctp_total >= 8 else ('warm' if mctp_total >= 5 else 'new')

    # Auto-run deal math if arv_mid not provided
    if not data.get('arv_mid') and address:
        try:
            deal_result = offer_calc.analyze_deal(
                address=address,
                beds=int(data.get('beds', 3)),
                baths=float(data.get('baths', 2.0)),
                sqft=int(data.get('sqft', 0)),
                condition=data.get('condition', 'unknown'),
                zip_code=data.get('zip', ''),
            )
            arv  = deal_result['arv']
            deal = deal_result['deal']
            data.setdefault('arv_mid',          arv['arv_mid'])
            data.setdefault('rehab_mid',         deal_result['rehab_used'])
            data.setdefault('lao',               deal['lao'])
            data.setdefault('walkup',            deal['walk_up_max'])
            data.setdefault('assign_price',      deal['assign_price'])
            data.setdefault('fee_at_lao',        deal['fee_at_lao'])
            data.setdefault('fee_at_walkup',     deal['fee_at_walkup'])
            data.setdefault('rec_max_contract',  deal['contract_for_target'])
        except Exception as e:
            print(f'[log_mctp] auto deal math failed: {e}')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Check if lead exists by address + user
            cur.execute(
                'SELECT id FROM leads WHERE address = %s AND user_id = %s',
                (address, user_id)
            )
            existing = cur.fetchone()

            if existing:
                lead_id = existing['id']
                cur.execute(
                    """UPDATE leads SET
                        motivation = %s, motivation_score = %s,
                        condition_score = %s, timeline = %s, timeline_score = %s,
                        asking_price = %s, price_score = %s,
                        condition = %s, caller_notes = %s,
                        next_action = %s, follow_up_date = %s,
                        status = %s, beds = %s, baths = %s, sqft = %s,
                        arv_mid = %s, rehab_mid = %s, lao = %s, walkup = %s,
                        buyer_max = %s, assign_price = %s, fee_at_lao = %s,
                        fee_at_walkup = %s, rec_max_contract = %s
                       WHERE id = %s""",
                    (
                        data.get('motivation'), mot_score, cond_score,
                        data.get('timeline'), timeline_score,
                        data.get('asking_price'), price_score,
                        data.get('condition'), data.get('caller_notes'),
                        data.get('next_action'), data.get('follow_up_date'),
                        status,
                        data.get('beds'), data.get('baths'), data.get('sqft'),
                        data.get('arv_mid'), data.get('rehab_mid'),
                        data.get('lao'), data.get('walkup'), data.get('buyer_max'),
                        data.get('assign_price'), data.get('fee_at_lao'),
                        data.get('fee_at_walkup'), data.get('rec_max_contract'),
                        lead_id
                    )
                )
            else:
                cur.execute(
                    """INSERT INTO leads (
                        user_id, assigned_to, address, city, state, zip,
                        beds, baths, sqft, condition,
                        motivation, motivation_score, condition_score,
                        timeline, timeline_score, asking_price, price_score,
                        arv_mid, rehab_mid, lao, walkup, buyer_max,
                        assign_price, fee_at_lao, fee_at_walkup, rec_max_contract,
                        status, caller_notes, next_action, follow_up_date
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id""",
                    (
                        user_id, user_id,
                        address, data.get('city', 'Memphis'), data.get('state', 'TN'),
                        data.get('zip'),
                        data.get('beds'), data.get('baths'), data.get('sqft'),
                        data.get('condition'),
                        data.get('motivation'), mot_score, cond_score,
                        data.get('timeline'), timeline_score,
                        data.get('asking_price'), price_score,
                        data.get('arv_mid'), data.get('rehab_mid'),
                        data.get('lao'), data.get('walkup'), data.get('buyer_max'),
                        data.get('assign_price'), data.get('fee_at_lao'),
                        data.get('fee_at_walkup'), data.get('rec_max_contract'),
                        status,
                        data.get('caller_notes'), data.get('next_action'),
                        data.get('follow_up_date'),
                    )
                )
                lead_id = cur.fetchone()['id']

            conn.commit()
    finally:
        conn.close()

    log_action(user_id, 'log_mctp', 'leads', lead_id,
               {'address': address, 'mctp_total': mctp_total, 'status': status})

    response = {
        'lead_id': str(lead_id),
        'mctp_total': mctp_total,
        'status': status,
        'message': f'MCTP logged. Score: {mctp_total}/10 — {status.upper()}',
    }

    # Sync MCTP score to XLeads if xleads_contact_id provided
    xleads_contact_id = data.get('xleads_contact_id')
    if xleads_contact_id and mctp_total >= 5:
        try:
            workflow_id = data.get('xleads_workflow_id')
            xl_result = xleads.sync_mctp_to_xleads(
                xleads_contact_id, mctp_total, address, workflow_id
            )
            response['xleads_sync'] = xl_result
        except Exception as e:
            response['xleads_sync'] = {'error': str(e)}

    # Route hot/warm leads through approval system
    if mctp_total >= 5:
        lead_payload = {**data, 'mctp_total': mctp_total,
                        'caller_name': g.user.get('name', 'Caller'),
                        'resource_id': str(lead_id)}
        try:
            approval_result = route_approval(
                'hot_lead_review', lead_payload, user_id, get_db
            )
            response['approval_queue_id'] = approval_result.get('approval_queue_id')
            response['routed_to']         = approval_result.get('approver_role')

            # Send Slack notification
            if mctp_total >= 8:
                lead_payload['approval_queue_id'] = approval_result.get('approval_queue_id')
                slack_msg = hot_lead_notification(
                    lead_payload, mctp_total, approval_result.get('approver_role')
                )
            else:
                slack_msg = warm_lead_notification(lead_payload, mctp_total)

            send_slack_notification(
                approval_result.get('approver_role', 'super_admin'),
                slack_msg
            )
        except Exception as e:
            print(f'[route_approval] Error: {e}')

    return jsonify(response), 201


@app.route('/api/leads/<lead_id>/assign', methods=['POST'])
@require_auth
def assign_lead(lead_id):
    role = g.user.get('role')
    if not check_permission_role(role, 'write', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    data        = request.get_json() or {}
    assignee_id = data.get('assignee_id')
    if not assignee_id:
        return jsonify({'error': 'assignee_id required'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE leads SET assigned_to = %s WHERE id = %s RETURNING address',
                (assignee_id, lead_id)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Lead not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'assign_lead', 'leads', lead_id,
                   {'assignee_id': assignee_id})
    finally:
        conn.close()

    return jsonify({'message': f'Lead assigned', 'address': row['address']})


# ─── APPROVAL QUEUE ───────────────────────────────────────────────

@app.route('/api/approval', methods=['GET'])
@require_auth
def list_approvals():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if role == 'super_admin':
                cur.execute(
                    """SELECT aq.*, u.name as initiated_by_name
                       FROM approval_queue aq
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.status = 'pending'
                       ORDER BY aq.priority DESC, aq.created_at ASC"""
                )
            else:
                cur.execute(
                    """SELECT aq.*, u.name as initiated_by_name
                       FROM approval_queue aq
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.assigned_to = %s AND aq.status = 'pending'
                       ORDER BY aq.priority DESC, aq.created_at ASC""",
                    (user_id,)
                )
            items = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('initiated_by'):
                    row['initiated_by'] = str(row['initiated_by'])
                if row.get('assigned_to'):
                    row['assigned_to'] = str(row['assigned_to'])
                if row.get('resource_id'):
                    row['resource_id'] = str(row['resource_id'])
                if row.get('created_at'):
                    row['created_at'] = row['created_at'].isoformat()
                items.append(row)
    finally:
        conn.close()

    return jsonify({'approvals': items})


@app.route('/api/approval/<approval_id>/approve', methods=['POST'])
@require_auth
def approve_item(approval_id):
    """Component 4 test step 4: Brock approves the lead."""
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT aq.*, l.address, l.lao, l.arv_mid, u.namespace as caller_namespace
                   FROM approval_queue aq
                   LEFT JOIN leads l ON l.id = aq.resource_id
                   LEFT JOIN users u ON u.id = aq.initiated_by
                   WHERE aq.id = %s AND aq.status = 'pending'""",
                (approval_id,)
            )
            item = cur.fetchone()

        if not item:
            return jsonify({'error': 'Approval not found or already resolved'}), 404

        # Permission check: super_admin can approve anything
        # re_partner can approve under threshold
        # AM can approve warm leads
        if role not in ('super_admin', 're_partner', 'acquisition_manager'):
            return jsonify({'error': 'Permission denied'}), 403

        data  = request.get_json() or {}
        notes = data.get('notes', '')

        # Build offer script if this is a lead review
        offer_script = None
        if item['action_type'] == 'hot_lead_review' and item['lao']:
            offer_script = build_offer_script(item['address'], item['lao'])

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE approval_queue SET
                       status = 'approved', approver_notes = %s,
                       offer_script = %s, resolved_at = NOW()
                   WHERE id = %s""",
                (notes, offer_script, approval_id)
            )
            # Update lead status to hot
            if item['resource_id']:
                cur.execute(
                    "UPDATE leads SET status = 'hot' WHERE id = %s",
                    (item['resource_id'],)
                )
            conn.commit()

        log_action(user_id, 'approve_item', 'approval_queue', approval_id,
                   {'action_type': item['action_type']})

        # Notify the caller
        if item['initiated_by'] and item['address']:
            caller_msg = approval_response_notification(
                item.get('caller_namespace', 'caller'),
                item['address'],
                approved=True,
                offer_script=offer_script,
                notes=notes
            )
            send_slack_notification(
                'caller', caller_msg,
                namespace=item.get('caller_namespace')
            )

    finally:
        conn.close()

    return jsonify({
        'message': 'Approved',
        'offer_script': offer_script,
        'address': item['address'],
    })


@app.route('/api/approval/<approval_id>/reject', methods=['POST'])
@require_auth
def reject_item(approval_id):
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    if role not in ('super_admin', 're_partner', 'acquisition_manager'):
        return jsonify({'error': 'Permission denied'}), 403

    data  = request.get_json() or {}
    notes = data.get('notes', 'Lead rejected.')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT aq.*, l.address, u.namespace as caller_namespace
                   FROM approval_queue aq
                   LEFT JOIN leads l ON l.id = aq.resource_id
                   LEFT JOIN users u ON u.id = aq.initiated_by
                   WHERE aq.id = %s AND aq.status = 'pending'""",
                (approval_id,)
            )
            item = cur.fetchone()
            if not item:
                return jsonify({'error': 'Approval not found'}), 404

            cur.execute(
                """UPDATE approval_queue SET
                       status = 'rejected', approver_notes = %s, resolved_at = NOW()
                   WHERE id = %s""",
                (notes, approval_id)
            )
            if item['resource_id']:
                cur.execute(
                    "UPDATE leads SET status = 'new' WHERE id = %s",
                    (item['resource_id'],)
                )
            conn.commit()

        caller_msg = approval_response_notification(
            item.get('caller_namespace', 'caller'),
            item['address'] or 'Lead',
            approved=False, offer_script=None, notes=notes
        )
        send_slack_notification(
            'caller', caller_msg, namespace=item.get('caller_namespace')
        )
        log_action(user_id, 'reject_item', 'approval_queue', approval_id)
    finally:
        conn.close()

    return jsonify({'message': 'Rejected', 'notes': notes})


# ─── DASHBOARD SUMMARY ────────────────────────────────────────────

@app.route('/api/dashboard/summary', methods=['GET'])
@require_auth
def dashboard_summary():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if role == 'super_admin':
                cur.execute("SELECT COUNT(*) as total FROM users WHERE active = true")
                total_users = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE status NOT IN ('dead', 'closed')")
                active_leads = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE mctp_total >= 8")
                hot_leads = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE mctp_total BETWEEN 5 AND 7")
                warm_leads = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM approval_queue WHERE status = 'pending'")
                pending = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE status = 'closed'")
                closed = cur.fetchone()['total']
                return jsonify({
                    'role': role,
                    'total_users': total_users,
                    'active_leads': active_leads,
                    'hot_leads': hot_leads,
                    'warm_leads': warm_leads,
                    'pending_approvals': pending,
                    'deals_closed': closed,
                })

            if role == 'caller':
                cur.execute(
                    "SELECT COUNT(*) as total FROM leads WHERE assigned_to = %s", (user_id,)
                )
                my_leads = cur.fetchone()['total']
                cur.execute(
                    "SELECT COUNT(*) as total FROM leads WHERE assigned_to = %s AND mctp_total >= 8",
                    (user_id,)
                )
                hot = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE assigned_to IS NULL")
                pool = cur.fetchone()['total']
                return jsonify({
                    'role': role,
                    'my_leads': my_leads,
                    'hot_leads_sent': hot,
                    'unassigned_pool': pool,
                })

            # Default summary
            return jsonify({'role': role, 'message': 'Dashboard active'})
    finally:
        conn.close()


# ─── XLEADS GATEWAY ──────────────────────────────────────────────

@app.route('/api/xleads/contacts', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_search_contacts():
    """
    Search XLeads contacts by keyword or tags.
    Query params: q (keyword), tags (comma-separated), limit, skip
    Example: /api/xleads/contacts?tags=pre-foreclosure,38111&limit=50
    """
    query  = request.args.get('q')
    tags   = request.args.get('tags', '').split(',') if request.args.get('tags') else None
    limit  = min(int(request.args.get('limit', 20)), 100)
    skip   = int(request.args.get('skip', 0))
    try:
        contacts = xleads.search_contacts(query=query, tags=tags, limit=limit, skip=skip)
        log_action(g.user['user_id'], 'xl_search_contacts', data={'query': query, 'tags': tags})
        return jsonify({'contacts': contacts, 'count': len(contacts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact(contact_id):
    """Get a single XLeads contact with all details."""
    try:
        contact = xleads.get_contact(contact_id)
        return jsonify({'contact': contact})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/tag', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_tag_contact(contact_id):
    """
    Add or remove tags on an XLeads contact.
    Body: { "add": ["Hot", "38111"], "remove": ["Cold"] }
    """
    data = request.get_json() or {}
    try:
        result = {}
        if data.get('add'):
            result['add'] = xleads.add_contact_tags(contact_id, data['add'])
        if data.get('remove'):
            result['remove'] = xleads.remove_contact_tags(contact_id, data['remove'])
        log_action(g.user['user_id'], 'xl_tag_contact', 'xleads', contact_id, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/sms', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_send_sms(contact_id):
    """
    Send an individual SMS to an XLeads contact.
    A2P 10DLC confirmed active.
    Body: { "message": "Hey, following up on your property at..." }
    """
    data    = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'message is required'}), 400
    try:
        result = xleads.send_sms(contact_id, message, data.get('from_number'))
        log_action(g.user['user_id'], 'xl_send_sms', 'xleads', contact_id,
                   {'message_length': len(message)})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/workflow', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_trigger_workflow(contact_id):
    """
    Enroll a contact into an XLeads SMS workflow (cold text sequence).
    Body: { "workflow_id": "abc123" }
    Get workflow_id from XLeads → Automations → copy ID from URL.
    """
    data        = request.get_json() or {}
    workflow_id = (data.get('workflow_id') or '').strip()
    if not workflow_id:
        return jsonify({'error': 'workflow_id is required'}), 400
    try:
        result = xleads.trigger_workflow(contact_id, workflow_id)
        log_action(g.user['user_id'], 'xl_trigger_workflow', 'xleads', contact_id,
                   {'workflow_id': workflow_id})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/conversations', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_conversations():
    """
    Pull seller conversation threads from XLeads.
    Query params: contact_id (optional), limit
    """
    contact_id = request.args.get('contact_id')
    limit      = min(int(request.args.get('limit', 20)), 100)
    try:
        convos = xleads.get_conversations(contact_id=contact_id, limit=limit)
        return jsonify({'conversations': convos, 'count': len(convos)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/pipeline', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'disposition_agent')
def xl_get_pipeline():
    """Pull deal opportunities from XLeads pipeline."""
    try:
        deals = xleads.get_opportunities(
            pipeline_id=request.args.get('pipeline_id'),
            stage_id=request.args.get('stage_id'),
            limit=min(int(request.args.get('limit', 20)), 100),
        )
        return jsonify({'opportunities': deals, 'count': len(deals)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/bulk-workflow', methods=['POST'])
@require_auth
@require_role('super_admin', 'acquisition_manager')
def xl_bulk_trigger_workflow():
    """
    Pull contacts matching criteria and enroll ALL of them into an SMS workflow.
    This is the cold text blast trigger — A2P approved required (confirmed).
    Body: {
        "tags": ["pre-foreclosure", "38111"],
        "query": "optional keyword",
        "workflow_id": "abc123",
        "limit": 50
    }
    """
    data        = request.get_json() or {}
    workflow_id = (data.get('workflow_id') or '').strip()
    tags        = data.get('tags')
    query       = data.get('query')
    limit       = min(int(data.get('limit', 50)), 200)

    if not workflow_id:
        return jsonify({'error': 'workflow_id is required'}), 400
    if not tags and not query:
        return jsonify({'error': 'tags or query required to target contacts'}), 400

    try:
        contacts  = xleads.search_contacts(query=query, tags=tags, limit=limit)
        triggered = []
        failed    = []
        for c in contacts:
            cid = c.get('id')
            try:
                xleads.trigger_workflow(cid, workflow_id)
                triggered.append(cid)
            except Exception as e:
                failed.append({'id': cid, 'error': str(e)})

        log_action(g.user['user_id'], 'xl_bulk_workflow', 'xleads', None, {
            'workflow_id': workflow_id,
            'tags': tags,
            'triggered': len(triggered),
            'failed': len(failed),
        })
        return jsonify({
            'triggered': len(triggered),
            'failed': len(failed),
            'failed_details': failed,
            'message': f'{len(triggered)} contacts enrolled in workflow {workflow_id}',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CONTRACTS ────────────────────────────────────────────

@app.route('/api/xleads/contracts/templates', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_contract_templates():
    """List all contract templates — use these IDs to send contracts to sellers."""
    try:
        templates = xleads.list_contract_templates()
        return jsonify({'templates': templates, 'count': len(templates)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contracts', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'disposition_agent')
def xl_list_contracts():
    """List all sent contracts and their signing status."""
    try:
        contracts = xleads.list_contracts()
        return jsonify({'contracts': contracts, 'count': len(contracts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contracts/send', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_send_contract():
    """
    Send an assignment contract to a seller for e-signature.
    Called automatically when a deal is approved, or manually from the dashboard.

    Body: {
        "template_id": "abc123",   // from /api/xleads/contracts/templates
        "contact_id":  "xyz456",   // XLeads contact ID of the seller
        "signers": [               // optional — uses contact info if omitted
            {"name": "Jane Smith", "email": "jane@example.com"}
        ]
    }
    """
    data        = request.get_json() or {}
    template_id = (data.get('template_id') or '').strip()
    contact_id  = (data.get('contact_id') or '').strip()

    if not template_id or not contact_id:
        return jsonify({'error': 'template_id and contact_id are required'}), 400

    try:
        result = xleads.send_contract_from_template(
            template_id, contact_id, data.get('signers')
        )
        log_action(g.user['user_id'], 'xl_send_contract', 'xleads', contact_id,
                   {'template_id': template_id})
        return jsonify({'message': 'Contract sent', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS WORKFLOWS LIST ────────────────────────────────────────

@app.route('/api/xleads/workflows', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_workflows():
    """
    List all XLeads workflows with their IDs and names.
    Use this to find the workflow_id needed for bulk-workflow and contact/workflow endpoints.
    """
    try:
        workflows = xleads.list_workflows()
        # Return only name + id for easy reference
        summary = [{'id': w.get('id'), 'name': w.get('name'), 'status': w.get('status')}
                   for w in workflows]
        return jsonify({'workflows': summary, 'count': len(summary)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CONTACTS (CRUD + NOTES + TASKS) ──────────────────────

@app.route('/api/xleads/contacts', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_create_contact():
    """
    Create a new contact in XLeads CRM.
    Body: { "first_name", "last_name", "phone", "email", "address", "city", "state", "zip_code", "tags" }
    """
    data = request.get_json() or {}
    first_name = (data.get('first_name') or '').strip()
    if not first_name:
        return jsonify({'error': 'first_name is required'}), 400
    try:
        contact = xleads.create_contact(
            first_name=first_name,
            last_name=data.get('last_name'),
            phone=data.get('phone'),
            email=data.get('email'),
            address=data.get('address'),
            city=data.get('city'),
            state=data.get('state'),
            zip_code=data.get('zip_code'),
            tags=data.get('tags'),
        )
        log_action(g.user['user_id'], 'xl_create_contact', 'xleads', contact.get('id'),
                   {'name': first_name})
        return jsonify({'contact': contact}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_contact(contact_id):
    """
    Update any fields on an XLeads contact.
    Body: any subset of { "firstName", "lastName", "phone", "email", "address1", ... }
    """
    data = request.get_json() or {}
    if not data:
        return jsonify({'error': 'No fields to update'}), 400
    try:
        contact = xleads.update_contact(contact_id, **data)
        log_action(g.user['user_id'], 'xl_update_contact', 'xleads', contact_id, data)
        return jsonify({'contact': contact})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_contact(contact_id):
    """Delete a contact from XLeads CRM. super_admin only."""
    try:
        result = xleads.delete_contact(contact_id)
        log_action(g.user['user_id'], 'xl_delete_contact', 'xleads', contact_id)
        return jsonify({'message': 'Contact deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/notes', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact_notes(contact_id):
    """Get all notes on an XLeads contact."""
    try:
        notes = xleads.get_contact_notes(contact_id)
        return jsonify({'notes': notes, 'count': len(notes)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/notes', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_add_contact_note(contact_id):
    """
    Add a note to an XLeads contact.
    Body: { "body": "Called seller, motivated to sell in 30 days" }
    """
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'body is required'}), 400
    try:
        result = xleads.add_contact_note(contact_id, body)
        log_action(g.user['user_id'], 'xl_add_note', 'xleads', contact_id,
                   {'note_length': len(body)})
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/tasks', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact_tasks(contact_id):
    """Get tasks associated with an XLeads contact."""
    try:
        tasks = xleads.get_contact_tasks(contact_id)
        return jsonify({'tasks': tasks, 'count': len(tasks)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/tasks', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_add_contact_task(contact_id):
    """
    Add a task to an XLeads contact.
    Body: { "title": "Follow-up call", "due_date": "2026-06-01", "description": "..." }
    """
    data  = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400
    try:
        result = xleads.add_contact_task(
            contact_id, title,
            due_date=data.get('due_date'),
            description=data.get('description'),
        )
        log_action(g.user['user_id'], 'xl_add_task', 'xleads', contact_id,
                   {'title': title})
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CONVERSATIONS (EMAIL + MESSAGES) ─────────────────────

@app.route('/api/xleads/conversations/<conversation_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_conversation(conversation_id):
    """Get a single conversation thread."""
    try:
        convo = xleads.get_conversation(conversation_id)
        return jsonify({'conversation': convo})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/conversations/<conversation_id>/messages', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_messages(conversation_id):
    """Get all messages in a conversation thread."""
    limit = min(int(request.args.get('limit', 20)), 100)
    try:
        messages = xleads.get_messages(conversation_id, limit=limit)
        return jsonify({'messages': messages, 'count': len(messages)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/contacts/<contact_id>/email', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_send_email(contact_id):
    """
    Send an email to an XLeads contact.
    Body: { "subject": "...", "body": "...", "from_name": "...", "from_email": "..." }
    """
    data    = request.get_json() or {}
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body') or '').strip()
    if not subject or not body:
        return jsonify({'error': 'subject and body are required'}), 400
    try:
        result = xleads.send_email(
            contact_id, subject, body,
            from_name=data.get('from_name'),
            from_email=data.get('from_email'),
        )
        log_action(g.user['user_id'], 'xl_send_email', 'xleads', contact_id,
                   {'subject': subject})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS OPPORTUNITIES (FULL CRUD) ────────────────────────────

@app.route('/api/xleads/pipeline/<opportunity_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'disposition_agent')
def xl_get_opportunity(opportunity_id):
    """Get a single pipeline deal by ID."""
    try:
        opp = xleads.get_opportunity(opportunity_id)
        return jsonify({'opportunity': opp})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/pipeline', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_create_opportunity():
    """
    Create a new deal in the XLeads pipeline.
    Body: { "contact_id", "pipeline_id", "stage_id", "name", "monetary_value" }
    """
    data = request.get_json() or {}
    required = ('contact_id', 'pipeline_id', 'stage_id', 'name')
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Required: {", ".join(missing)}'}), 400
    try:
        opp = xleads.create_opportunity(
            data['contact_id'], data['pipeline_id'], data['stage_id'],
            data['name'], monetary_value=data.get('monetary_value'),
        )
        log_action(g.user['user_id'], 'xl_create_opportunity', 'xleads',
                   opp.get('id'), {'name': data['name']})
        return jsonify({'opportunity': opp}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/pipeline/<opportunity_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_opportunity(opportunity_id):
    """
    Update a pipeline deal — stage, value, status, etc.
    Body: any subset of opportunity fields
    """
    data = request.get_json() or {}
    if not data:
        return jsonify({'error': 'No fields to update'}), 400
    try:
        opp = xleads.update_opportunity(opportunity_id, **data)
        log_action(g.user['user_id'], 'xl_update_opportunity', 'xleads',
                   opportunity_id, data)
        return jsonify({'opportunity': opp})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/pipeline/<opportunity_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_delete_opportunity(opportunity_id):
    """Remove a deal from the XLeads pipeline."""
    try:
        result = xleads.delete_opportunity(opportunity_id)
        log_action(g.user['user_id'], 'xl_delete_opportunity', 'xleads',
                   opportunity_id)
        return jsonify({'message': 'Opportunity deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CALENDARS & APPOINTMENTS ─────────────────────────────

@app.route('/api/xleads/calendars', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_list_calendars():
    """List all calendars in the XLeads location."""
    try:
        calendars = xleads.list_calendars()
        return jsonify({'calendars': calendars, 'count': len(calendars)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/calendars/events', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_calendar_events():
    """
    Get scheduled appointments.
    Query params: calendar_id, start_time (ISO), end_time (ISO)
    """
    try:
        events = xleads.get_calendar_events(
            calendar_id=request.args.get('calendar_id'),
            start_time=request.args.get('start_time'),
            end_time=request.args.get('end_time'),
        )
        return jsonify({'events': events, 'count': len(events)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/calendars/events', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_book_appointment():
    """
    Book a follow-up appointment with a seller.
    Body: { "contact_id", "calendar_id", "start_time" (ISO), "end_time" (ISO), "title" }
    """
    data = request.get_json() or {}
    required = ('contact_id', 'calendar_id', 'start_time', 'end_time')
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Required: {", ".join(missing)}'}), 400
    try:
        event = xleads.book_appointment(
            data['contact_id'], data['calendar_id'],
            data['start_time'], data['end_time'],
            title=data.get('title'),
        )
        log_action(g.user['user_id'], 'xl_book_appointment', 'xleads',
                   data['contact_id'], {'start': data['start_time']})
        return jsonify({'event': event}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/calendars/events/<event_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_appointment(event_id):
    """Update an appointment (reschedule, title, etc.)."""
    data = request.get_json() or {}
    try:
        result = xleads.update_appointment(event_id, **data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/calendars/events/<event_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_cancel_appointment(event_id):
    """Cancel an appointment."""
    try:
        result = xleads.cancel_appointment(event_id)
        log_action(g.user['user_id'], 'xl_cancel_appointment', 'xleads', event_id)
        return jsonify({'message': 'Appointment cancelled', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS PHONE NUMBERS ────────────────────────────────────────

@app.route('/api/xleads/phone-numbers', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_phone_numbers():
    """List all phone numbers on the XLeads account."""
    try:
        numbers = xleads.list_phone_numbers()
        return jsonify({'phone_numbers': numbers, 'count': len(numbers)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/phone-numbers/search', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_search_available_numbers():
    """
    Search for available numbers to purchase.
    Query params: area_code (required), limit, country (default US)
    """
    area_code = request.args.get('area_code', '').strip()
    if not area_code:
        return jsonify({'error': 'area_code is required'}), 400
    try:
        numbers = xleads.search_available_numbers(
            area_code,
            limit=min(int(request.args.get('limit', 5)), 20),
            country=request.args.get('country', 'US'),
        )
        return jsonify({'available_numbers': numbers, 'count': len(numbers)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/phone-numbers/buy', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_buy_phone_number():
    """
    Purchase and provision a phone number. super_admin only — charges apply.
    Body: { "phone_number": "+19015551234", "area_code": "901" }
    Use /api/xleads/phone-numbers/search first to find available numbers.
    """
    data         = request.get_json() or {}
    phone_number = (data.get('phone_number') or '').strip()
    if not phone_number:
        return jsonify({'error': 'phone_number is required (E.164 format, e.g. +19015551234)'}), 400
    try:
        result = xleads.buy_phone_number(phone_number, area_code=data.get('area_code'))
        log_action(g.user['user_id'], 'xl_buy_phone_number', 'xleads', None,
                   {'phone_number': phone_number})
        return jsonify({'message': f'Number {phone_number} purchased', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/phone-numbers/<number_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_phone_number(number_id):
    """Release a phone number from the account. super_admin only."""
    try:
        result = xleads.delete_phone_number(number_id)
        log_action(g.user['user_id'], 'xl_delete_phone_number', 'xleads', number_id)
        return jsonify({'message': 'Number released', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/phone-numbers/pools', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_number_pools():
    """List number pools (rotation groups)."""
    try:
        pools = xleads.get_number_pools()
        return jsonify({'pools': pools})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/twilio', methods=['GET'])
@require_auth
@require_role('super_admin')
def xl_get_twilio_account():
    """View Twilio account info and SMS limits for the XLeads sub-account."""
    try:
        info = xleads.get_twilio_account()
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS VOICE AI (MAYA) ───────────────────────────────────────

@app.route('/api/xleads/voice-ai/agents', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_voice_agents():
    """List all Voice AI agents (includes Maya)."""
    try:
        agents = xleads.list_voice_agents()
        return jsonify({'agents': agents, 'count': len(agents)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/voice-ai/agents/<agent_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_voice_agent(agent_id):
    """Get a Voice AI agent config."""
    try:
        agent = xleads.get_voice_agent(agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/voice-ai/agents/<agent_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_voice_agent(agent_id):
    """
    Update Maya's prompt, name, voice, or first message.
    Body: { "prompt": "...", "name": "...", "firstMessage": "..." }
    """
    data = request.get_json() or {}
    try:
        agent = xleads.update_voice_agent(agent_id, **data)
        log_action(g.user['user_id'], 'xl_update_voice_agent', 'xleads', agent_id, data)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/voice-ai/agents/<agent_id>/goals', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_voice_agent_goals(agent_id):
    """Get goals configured for a Voice AI agent."""
    try:
        goals = xleads.get_voice_agent_goals(agent_id)
        return jsonify({'goals': goals})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/voice-ai/agents/<agent_id>/goals', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_voice_agent_goals(agent_id):
    """
    Update goals for a Voice AI agent.
    Body: { "goals": [ { ... }, { ... } ] }
    """
    data  = request.get_json() or {}
    goals = data.get('goals')
    if not isinstance(goals, list):
        return jsonify({'error': 'goals must be a list'}), 400
    try:
        result = xleads.update_voice_agent_goals(agent_id, goals)
        log_action(g.user['user_id'], 'xl_update_voice_goals', 'xleads', agent_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CONVERSATION AI ───────────────────────────────────────

@app.route('/api/xleads/conversation-ai/bots', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_conversation_bots():
    """List all Conversation AI bots."""
    try:
        bots = xleads.list_conversation_bots()
        return jsonify({'bots': bots, 'count': len(bots)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/conversation-ai/bots/<bot_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_conversation_bot(bot_id):
    """Get a Conversation AI bot config."""
    try:
        bot = xleads.get_conversation_bot(bot_id)
        return jsonify({'bot': bot})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/conversation-ai/bots/<bot_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_conversation_bot(bot_id):
    """Update a Conversation AI bot's prompt or settings."""
    data = request.get_json() or {}
    try:
        bot = xleads.update_conversation_bot(bot_id, **data)
        log_action(g.user['user_id'], 'xl_update_convo_bot', 'xleads', bot_id, data)
        return jsonify({'bot': bot})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS AGENT STUDIO ─────────────────────────────────────────

@app.route('/api/xleads/agent-studio', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_studio_agents():
    """List all Agent Studio agents."""
    try:
        agents = xleads.list_studio_agents()
        return jsonify({'agents': agents, 'count': len(agents)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/agent-studio/<agent_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_studio_agent(agent_id):
    """Get a specific Agent Studio agent."""
    try:
        agent = xleads.get_studio_agent(agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/agent-studio', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_create_studio_agent():
    """
    Create a new Agent Studio agent.
    Body: { "name": "...", "prompt": "...", ...optional fields }
    """
    data = request.get_json() or {}
    name   = (data.pop('name', '') or '').strip()
    prompt = (data.pop('prompt', '') or '').strip()
    if not name or not prompt:
        return jsonify({'error': 'name and prompt are required'}), 400
    try:
        agent = xleads.create_studio_agent(name, prompt, **data)
        log_action(g.user['user_id'], 'xl_create_studio_agent', 'xleads',
                   agent.get('id'), {'name': name})
        return jsonify({'agent': agent}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/agent-studio/<agent_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_studio_agent(agent_id):
    """Update an Agent Studio agent."""
    data = request.get_json() or {}
    try:
        agent = xleads.update_studio_agent(agent_id, **data)
        log_action(g.user['user_id'], 'xl_update_studio_agent', 'xleads', agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS TAGS ─────────────────────────────────────────────────

@app.route('/api/xleads/tags', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_tags():
    """List all tags defined in the XLeads location."""
    try:
        tags = xleads.list_tags()
        return jsonify({'tags': tags, 'count': len(tags)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/tags', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_create_tag():
    """
    Create a new location-level tag.
    Body: { "name": "pre-foreclosure-38111" }
    """
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    try:
        tag = xleads.create_tag(name)
        log_action(g.user['user_id'], 'xl_create_tag', 'xleads', None, {'name': name})
        return jsonify({'tag': tag}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/tags/<tag_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_tag(tag_id):
    """Delete a tag from the location. super_admin only."""
    try:
        result = xleads.delete_tag(tag_id)
        log_action(g.user['user_id'], 'xl_delete_tag', 'xleads', tag_id)
        return jsonify({'message': 'Tag deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CUSTOM FIELDS ────────────────────────────────────────

@app.route('/api/xleads/custom-fields', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_custom_fields():
    """List all custom fields in the XLeads location."""
    try:
        fields = xleads.list_custom_fields()
        return jsonify({'custom_fields': fields, 'count': len(fields)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/custom-fields', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_create_custom_field():
    """
    Create a new custom field.
    Body: { "name": "ARV Estimate", "data_type": "MONETARY", "placeholder": "..." }
    data_type options: TEXT, LARGE_TEXT, NUMERICAL, PHONE, MONETARY, DATE
    """
    data      = request.get_json() or {}
    name      = (data.get('name') or '').strip()
    data_type = (data.get('data_type') or '').strip().upper()
    if not name or not data_type:
        return jsonify({'error': 'name and data_type are required'}), 400
    try:
        field = xleads.create_custom_field(name, data_type,
                                           placeholder=data.get('placeholder'))
        log_action(g.user['user_id'], 'xl_create_custom_field', 'xleads', None,
                   {'name': name, 'type': data_type})
        return jsonify({'custom_field': field}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS CAMPAIGNS ────────────────────────────────────────────

@app.route('/api/xleads/campaigns', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_campaigns():
    """List all XLeads campaigns and their status."""
    try:
        campaigns = xleads.list_campaigns()
        return jsonify({'campaigns': campaigns, 'count': len(campaigns)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── XLEADS SOCIAL PLANNER ───────────────────────────────────────

@app.route('/api/xleads/social', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_social_posts():
    """List scheduled and published social media posts."""
    limit = min(int(request.args.get('limit', 20)), 100)
    try:
        posts = xleads.list_social_posts(limit=limit)
        return jsonify({'posts': posts, 'count': len(posts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/xleads/social', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_create_social_post():
    """
    Schedule a social media post.
    Body: {
        "content": "We buy houses Memphis! Fast close...",
        "platforms": ["facebook", "instagram"],
        "scheduled_at": "2026-06-01T10:00:00Z",
        "media_urls": ["https://..."]  // optional
    }
    """
    data      = request.get_json() or {}
    content   = (data.get('content') or '').strip()
    platforms = data.get('platforms')
    if not content or not platforms:
        return jsonify({'error': 'content and platforms are required'}), 400
    try:
        post = xleads.create_social_post(
            content, platforms,
            scheduled_at=data.get('scheduled_at'),
            media_urls=data.get('media_urls'),
        )
        log_action(g.user['user_id'], 'xl_create_social_post', 'xleads', None,
                   {'platforms': platforms})
        return jsonify({'post': post}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── BUSINESS AGENT ENDPOINTS ────────────────────────────────────

@app.route('/api/agents/scout', methods=['POST'])
@require_auth
def agent_scout():
    """
    Score and rank business ideas.
    Body: { "keywords": "printables etsy", "budget": 500, "hours_per_week": 10, "skills": "..." }
    """
    data = request.get_json() or {}
    try:
        result = scout_agent.run(
            keywords=data.get('keywords', ''),
            skills=data.get('skills', ''),
            budget_usd=int(data.get('budget', 500)),
            hours_per_week=int(data.get('hours_per_week', 10)),
        )
        log_action(g.user['user_id'], 'agent_scout', data={'keywords': data.get('keywords')})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/agents/income', methods=['POST'])
@require_auth
def agent_income():
    """
    Revenue model for a business.
    Body: { "business": "Digital Printables Store", "hours_per_week": 10, "starting_budget": 200 }
    """
    data = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = income_agent.run(
            business_name=business,
            hours_per_week=int(data.get('hours_per_week', 10)),
            starting_budget=int(data.get('starting_budget', 200)),
            platform=data.get('platform', ''),
        )
        log_action(g.user['user_id'], 'agent_income', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/agents/build', methods=['POST'])
@require_auth
def agent_build():
    """
    Step-by-step build plan for a business.
    Body: { "business": "Digital Printables Store" }
    """
    data = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = builder_agent.run(business_name=business)
        log_action(g.user['user_id'], 'agent_build', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/agents/audit', methods=['POST'])
@require_auth
def agent_audit():
    """
    Automation audit for a business.
    Body: { "business": "Digital Printables Store" }
    """
    data = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = auditor_agent.run(business_name=business)
        log_action(g.user['user_id'], 'agent_audit', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/agents/debug', methods=['POST'])
@require_auth
def agent_debug():
    """
    IT support and debugging.
    Body: { "problem": "ODIN returns 502 when I submit a lead", "health_check": true }
    """
    data    = request.get_json() or {}
    problem = (data.get('problem') or '').strip()
    if not problem:
        return jsonify({'error': 'problem is required'}), 400
    try:
        result = it_agent.run(
            problem=problem,
            include_health_check=data.get('health_check', True),
        )
        log_action(g.user['user_id'], 'agent_debug', data={'problem': problem[:200]})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@app.route('/api/agents/plan', methods=['POST'])
@require_auth
def agent_plan():
    """
    Full chain: scout → income → builder → auditor.
    Body: { "business": "Digital Printables Store", "hours_per_week": 10, "budget": 200 }
    """
    data = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400

    results = {}
    errors  = {}

    for name, fn, kwargs in [
        ('scout',  scout_agent.run,   {'keywords': business, 'budget_usd': int(data.get('budget', 500)), 'hours_per_week': int(data.get('hours_per_week', 10))}),
        ('income', income_agent.run,  {'business_name': business, 'hours_per_week': int(data.get('hours_per_week', 10)), 'starting_budget': int(data.get('budget', 200))}),
        ('build',  builder_agent.run, {'business_name': business}),
        ('audit',  auditor_agent.run, {'business_name': business}),
    ]:
        try:
            results[name] = fn(**kwargs)
        except Exception as e:
            errors[name] = str(e)

    log_action(g.user['user_id'], 'agent_plan', data={'business': business})
    return jsonify({'business': business, 'results': results, 'errors': errors})


# ─── SLACK COMMAND CENTER ────────────────────────────────────────

@app.route('/api/slack/events', methods=['POST'])
def slack_events():
    """
    Receives all Slack events (DMs + @mentions).
    1. Verifies signature
    2. Handles URL challenge (one-time setup)
    3. Parses command and executes
    """
    # Signature verification — reject anything not from Slack
    if SLACK_SIGNING_SECRET and not _slack_verify(request):
        return jsonify({'error': 'Invalid signature'}), 403

    data      = request.get_json(silent=True) or {}
    event_type = data.get('type')

    # ── Step 0: Ignore Slack retries — advisor calls take >3s, Slack retries the event
    if request.headers.get('X-Slack-Retry-Num'):
        return jsonify({'ok': True})

    # ── Step 1: URL verification challenge (one-time when you register the URL)
    if event_type == 'url_verification':
        return jsonify({'challenge': data.get('challenge')})

    # ── Step 2: Event callback
    if event_type == 'event_callback':
        event   = data.get('event', {})
        subtype = event.get('subtype')

        # Ignore bot's own messages and edits
        if event.get('bot_id') or subtype in ('bot_message', 'message_changed', 'message_deleted'):
            return jsonify({'ok': True})

        msg_type  = event.get('type')
        text      = (event.get('text') or '').strip()
        channel   = event.get('channel', '')
        slack_uid = event.get('user', '')

        if msg_type in ('app_mention', 'message') and text:
            # Parse the command first — if it's a known command, always execute it.
            # Only fall through to the business advisor for unrecognized input.
            katelyn_uid = os.environ.get('KATELYN_SLACK_UID', '')
            is_katelyn  = bool(katelyn_uid and slack_uid == katelyn_uid)
            cmd         = parse_command(text)
            is_command  = cmd.get('action') != 'unknown'

            # Route: known command → command handler (always, for both Brock and Katelyn)
            #        unknown input → Business Advisor (Katelyn always, Brock only for natural language)
            if is_command:
                log_action(None, 'slack_command', data={'action': cmd.get('action'), 'raw': text[:200]})
                _execute_slack_command(cmd, reply_channel=channel, sender_uid=slack_uid)
            else:
                # Before falling through to advisor, check if this matches a custom agent trigger
                first_word = text.strip().lower().split()[0] if text.strip() else ''
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
                    pass  # Not a custom agent — fall through to advisor

                log_action(None, 'odin_chat', data={'user': slack_uid, 'msg': text[:200]})
                try:
                    # Layer 2 — load memories for this user before advising
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

                    # Layer 2 — extract and save new facts after reply
                    if db_user_id:
                        try:
                            memory.extract_and_save(db_user_id, text, reply, get_db)
                        except Exception:
                            pass  # never let memory failure affect the response

                except RuntimeError:
                    _slack_post(channel,
                        '⚠️ ODIN\'s business advisor needs an Anthropic API key. '
                        'Ask Brock to add ANTHROPIC_API_KEY to Railway.')
                except Exception as e:
                    _slack_post(channel, f'⚠️ Advisor error: {e}')

    return jsonify({'ok': True})


# ─── OUTBOUND VOICE BRIEFING (Twilio TwiML callback) ─────────────

@app.route('/api/voice/briefing', methods=['GET', 'POST'])
def voice_briefing():
    """
    Twilio fetches this when an ODIN outbound call connects.
    Returns TwiML that reads the staged briefing aloud, then hangs up.
    """
    msg = request.values.get('msg', '')
    return Response(twilio_voice.build_twiml(msg), mimetype='text/xml')


# ─── TELEGRAM WEBHOOK (second command channel) ───────────────────

@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """
    Receives Telegram bot updates. Only Brock's chat id may run commands.
    Routes the message text through the same parser as Slack.
    """
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
        # Reuse the Slack executor, but capture output to Telegram by posting
        # through a thin shim: commands post via _slack_post(reply_channel,...).
        # We pass the Telegram chat id as the reply channel and let the
        # _slack_post wrapper detect Telegram ids (see _slack_post).
        log_action(None, 'telegram_command', data={'action': cmd.get('action'), 'raw': text[:200]})
        _execute_slack_command(cmd, reply_channel=f'tg:{chat_id}',
                               sender_uid=os.environ.get('BROCK_SLACK_UID', 'U0B5C32BJ6B'))
    except Exception as e:
        telegram_notify.send(chat_id, f'❌ Error: {type(e).__name__}: {e}')
    return jsonify({'ok': True})


# ─── XLEADS INBOUND WEBHOOK (seller replies → Slack) ─────────────

@app.route('/api/xleads/inbound', methods=['POST'])
def xleads_inbound():
    """
    Receives inbound SMS/email events from XLeads (GoHighLevel).
    Register this URL in XLeads → Settings → Webhooks → InboundMessage.

    Flow:
      Seller replies to text → XLeads fires webhook → ODIN receives →
        if negative (STOP/not interested) → auto-tag + remove from workflows → log
        if neutral/positive → post to #odin-brock with reply instructions
    """
    data = request.get_json(silent=True) or {}

    event_type   = data.get('type', '')
    contact_id   = data.get('contactId', '')
    body         = (data.get('body') or data.get('message') or '').strip()
    first_name   = data.get('firstName', 'Unknown')
    last_name    = data.get('lastName', '')
    phone        = data.get('phone', '—')
    msg_type     = data.get('messageType', 'SMS')

    # Only process inbound messages
    if 'inbound' not in event_type.lower() and data.get('direction', '') != 'inbound':
        return jsonify({'ok': True})

    if not body:
        return jsonify({'ok': True})

    sender_name = f'{first_name} {last_name}'.strip()
    brock_channel = os.environ.get('SLACK_CHANNEL_BROCK', '')

    log_action(None, 'xleads_inbound_message', 'xleads', contact_id,
               {'from': sender_name, 'message_preview': body[:100]})

    # ── NEGATIVE / OPT-OUT reply
    if is_negative_reply(body):
        try:
            xleads.add_contact_tags(contact_id, ['Do-Not-Contact', 'Opted-Out'])
            xleads.remove_contact_tags(contact_id, ['Hot', 'Warm', 'Cold'])
            # Remove from all active workflows
            workflows = xleads.list_workflows()
            for wf in workflows:
                try:
                    xleads.remove_from_workflow(contact_id, wf['id'])
                except Exception:
                    pass
        except Exception as e:
            print(f'[xleads_inbound] Auto-ignore failed: {e}')

        opt_out_msg = (
            f'🚫 *Auto-ignored* — {sender_name} ({phone})\n'
            f'Message: _{body}_\n'
            f'Tagged Do-Not-Contact and removed from all workflows.'
        )
        if brock_channel:
            _slack_post(brock_channel, opt_out_msg)
        eddie_ch = os.environ.get('SLACK_CHANNEL_EDDIE', '')
        if eddie_ch and eddie_ch != brock_channel:
            _slack_post(eddie_ch, opt_out_msg)
        return jsonify({'ok': True})

    # ── POSITIVE / NEUTRAL reply — pull conversation history + auto-score + alert Brock
    score_block = ''
    try:
        score_result = lead_scorer.score(
            notes=body,
            address=data.get('address', ''),
            caller_name=sender_name,   # fixed: was seller_name (invalid kwarg)
        )
        total = score_result.get('total', 0)
        tier  = score_result.get('tier', '')
        tier_emoji = '🔥' if tier == 'Hot' else ('⚡' if tier == 'Warm' else '🧊')
        score_block = (
            f'\n*{tier_emoji} Auto-MCTP: {total}/10 ({tier})*\n'
            f'M:{score_result.get("motivation",0)} '
            f'C:{score_result.get("condition",0)} '
            f'T:{score_result.get("timeline",0)} '
            f'P:{score_result.get("price",0)}'
        )
        # Auto-tag in XLeads based on score
        if total >= 8:
            try:
                xleads.add_contact_tags(contact_id, ['Hot'])
                xleads.remove_contact_tags(contact_id, ['Warm', 'Cold'])
            except Exception:
                pass
        elif total >= 5:
            try:
                xleads.add_contact_tags(contact_id, ['Warm'])
                xleads.remove_contact_tags(contact_id, ['Hot', 'Cold'])
            except Exception:
                pass
    except Exception:
        pass

    # ── Upsert lead + stamp blast_campaign_id ────────────────────────────────
    try:
        _db = get_db()
        _cur = _db.cursor()
        # Find most recent active blast campaign (last 7 days) for stamping
        _cur.execute("""
            SELECT id FROM blast_campaigns
            WHERE created_at >= NOW() - INTERVAL '7 days'
              AND sent_count > 0
              AND health_status != 'flagged'
            ORDER BY created_at DESC LIMIT 1
        """)
        _camp = _cur.fetchone()
        _camp_id = str(_camp[0]) if _camp else None

        _mctp_total = score_result.get('total', 0) if 'score_result' in dir() else None

        # Upsert lead keyed on xleads_contact_id
        _cur.execute("""
            INSERT INTO leads (user_id, address, xleads_contact_id, mctp_total, blast_campaign_id,
                               motivation_score, condition_score, timeline_score, price_score,
                               spoke, status)
            VALUES (
                (SELECT id FROM users WHERE role = 'super_admin' LIMIT 1),
                %s, %s, %s, %s, %s, %s, %s, %s, 'real_estate', 'new'
            )
            ON CONFLICT (xleads_contact_id) DO UPDATE SET
                mctp_total        = COALESCE(EXCLUDED.mctp_total, leads.mctp_total),
                blast_campaign_id = COALESCE(leads.blast_campaign_id, EXCLUDED.blast_campaign_id),
                motivation_score  = COALESCE(EXCLUDED.motivation_score, leads.motivation_score),
                condition_score   = COALESCE(EXCLUDED.condition_score, leads.condition_score),
                timeline_score    = COALESCE(EXCLUDED.timeline_score, leads.timeline_score),
                price_score       = COALESCE(EXCLUDED.price_score, leads.price_score),
                updated_at        = NOW()
        """, (
            data.get('address') or f'{first_name} {last_name}'.strip() or 'Unknown',
            contact_id,
            _mctp_total,
            _camp_id,
            score_result.get('motivation', 0) if 'score_result' in dir() else None,
            score_result.get('condition', 0)  if 'score_result' in dir() else None,
            score_result.get('timeline', 0)   if 'score_result' in dir() else None,
            score_result.get('price', 0)      if 'score_result' in dir() else None,
        ))
        _db.commit()
        _cur.close()
        _db.close()
    except Exception as _le:
        print(f'[xleads_inbound] Lead upsert failed: {_le}')

    # ── Pull last 5 conversation messages for context ──────────────────────
    history_block = ''
    try:
        convos = xleads.get_conversations(contact_id=contact_id, limit=1)
        if convos:
            convo_id = convos[0].get('id', '')
            if convo_id:
                messages = xleads.get_messages(convo_id, limit=6)
                # Exclude the current message (it's the last one) — show prior 5
                prior = [m for m in messages if (m.get('body') or m.get('message', '')) != body][-5:]
                if prior:
                    history_lines = ['*📜 Prior messages:*']
                    for m in prior:
                        direction = m.get('direction', 'unknown')
                        arrow     = '→' if direction == 'outbound' else '←'
                        msg_body  = (m.get('body') or m.get('message', ''))[:120]
                        history_lines.append(f'  {arrow} _{msg_body}_')
                    history_block = '\n' + '\n'.join(history_lines)
    except Exception:
        pass  # never block the main alert over history failure

    alert_text = (
        f'📩 *{msg_type} Reply — {sender_name}* ({phone})\n'
        f'Message: _{body}_'
        f'{score_block}'
        f'{history_block}\n\n'
        f'Contact ID: `{contact_id}`\n'
        f'Reply: `text {contact_id} your message here`\n'
        f'Score full call: `score {body[:80]}`\n'
        f'Lookup property: `lookup {data.get("address", sender_name)}`\n'
        f'Ignore: `ignore {contact_id}`'
    )
    if brock_channel:
        _slack_post(brock_channel, alert_text)
    eddie_ch = os.environ.get('SLACK_CHANNEL_EDDIE', '')
    if eddie_ch and eddie_ch != brock_channel:
        _slack_post(eddie_ch, alert_text)

        # All non-opt-out replies → Discord alert (color-coded by score)
        try:
            total_score = 0
            try:
                total_score = int(re.search(r'Auto-MCTP: (\d+)/10', score_block).group(1))
            except Exception:
                pass

            if total_score >= 8:
                tier_str = 'Hot'
                color    = 0xFF4444   # red
                emoji    = '🔥'
            elif total_score >= 5:
                tier_str = 'Warm'
                color    = 0xFFAA00   # orange
                emoji    = '⚡'
            else:
                tier_str = 'Reply'
                color    = 0x4A90D9   # blue — unscored but still needs attention
                emoji    = '📩'

            score_line = (
                f'**Score:** {total_score}/10 ({tier_str})\n'
                if total_score > 0 else ''
            )
            history_plain = ''
            if history_block:
                # Strip Slack markdown for Discord embed description
                history_plain = '\n' + history_block.replace('*', '**').replace('_', '*')

            desc = (
                f'**Message:** {body[:400]}\n\n'
                f'{score_line}'
                f'**Phone:** {phone}\n'
                f'**Contact ID:** `{contact_id}`'
                f'{history_plain}'
            )
            discord_notify.post_embed(
                title=f'{emoji} Seller Reply — {sender_name}',
                description=desc,
                color=color,
                target='brock',
            )
        except Exception:
            pass

    return jsonify({'ok': True})


# ─── HEALTH CHECK ─────────────────────────────────────────────────

@app.route('/api/health', methods=['GET'])
def health():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
        conn.close()
        db_status = 'ok'
    except Exception as e:
        db_status = str(e)
    return jsonify({'status': 'ok', 'db': db_status, 'version': 'odin-phase3'})


# ─── Set-password page (served from dashboard) ───────────────────

@app.route('/set-password')
def set_password_page():
    return send_from_directory(app.static_folder, 'index.html')


# ─── HEARTBEAT STARTUP ────────────────────────────────────────────
# Init and start Heartbeat after all functions are defined.
# Only runs in gunicorn (--workers 1 required) or local dev.
heartbeat.init(
    slack_post_fn=_slack_post,
    get_db_fn=get_db,
    xleads_mod=xleads,
    channels=CHANNELS,
)
heartbeat.start_heartbeat()
outreach_tracker.init(_slack_post, get_db, CHANNELS)
email_sender.init(_slack_post, get_db, CHANNELS)
lead_sniper.init(_slack_post, get_db, CHANNELS)

# Discord bot (inbound command interface)
try:
    import utils.discord_bot as discord_bot
    discord_bot.start_bot()
except Exception as _dbot_err:
    print(f'[discord_bot] Failed to start: {_dbot_err}')


# ─── Run ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    print(f'ODIN API running on http://localhost:{port}')
    print(f'Database: {"connected" if DATABASE_URL else "NOT SET"}')
    app.run(host='0.0.0.0', port=port, debug=debug)
