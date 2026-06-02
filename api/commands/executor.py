"""
ODIN Commands — Slack/Telegram/Discord command executor.
Contains _execute_slack_command (the full command dispatch table)
and Discord virtual-channel helpers.
"""
import os, uuid, json, re, secrets
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import requests
import anthropic

from utils.slack_commands import parse_command, is_negative_reply, HELP_TEXT
from utils.slack_templates import (
    send_slack_notification,
    hot_lead_notification,
    warm_lead_notification,
    approval_response_notification,
    new_user_invite_notification,
    CHANNELS,
)
import utils.xleads as xleads
import utils.business_advisor as advisor
import utils.business_scout as scout_agent
import utils.income_calculator as income_agent
import utils.business_builder as builder_agent
import utils.automation_auditor as auditor_agent
import utils.it_agent as it_agent
import utils.business_plan_pdf as biz_pdf
import utils.agent_builder as agent_builder
import utils.agent_log as agent_log
import utils.lao_calculator    as lao_calc
import utils.arv_analyzer      as arv_analyzer
import utils.offer_calculator  as offer_calc
import utils.lead_scorer       as lead_scorer
import utils.script_generator  as script_gen
import utils.buyer_matcher     as buyer_matcher
import utils.content_engine    as content_engine
import utils.email_drafter     as email_drafter
import utils.finance_bot       as finance_bot
import utils.memory            as memory
import utils.property_lookup   as property_lookup
import utils.comps_scraper     as comps_scraper
import utils.discord_notify    as discord_notify
import utils.google_calendar   as google_calendar
import utils.gmail_client      as gmail_client
import utils.telegram_notify   as telegram_notify
import utils.twilio_voice      as twilio_voice
import utils.email_triage      as email_triage
import utils.outreach_tracker  as outreach_tracker
import utils.email_sender      as email_sender
import utils.lead_sniper       as lead_sniper
import utils.agent_spawner     as agent_spawner

from core.db      import get_db, log_action
from core.helpers import _run_agent, MAYA_PROMPT  # _slack_post + _haiku redefined locally with Discord/Telegram routing
from core.discord_state import _discord_queues


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


def _parse_kv(text: str) -> dict:
    """
    Parse key:value and key:"quoted value" pairs from a raw string.
    Used by audit_log, audit_sold, and similar freeform command handlers.
    Examples:
      'client:"Acme Roofing" niche:roofing stage:emailed'
      'client:Acme amount:497 tier:audit'
    """
    result = {}
    # Quoted values first: key:"multi word value"
    for m in re.finditer(r'(\w+)[:\s]+"([^"]+)"', text):
        result[m.group(1).lower()] = m.group(2)
    # Unquoted values: key:word (skip keys already captured above)
    for m in re.finditer(r'(\w+):(\S+)', text):
        key = m.group(1).lower()
        if key not in result:
            result[key] = m.group(2).strip('"')
    return result


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

        # Budget gate: check monthly spend before large blasts
        if limit > 50:
            try:
                _bg_db  = get_db()
                _bg_cur = _bg_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                _bg_cur.execute("""
                    SELECT COALESCE(SUM(amount), 0) AS mtd_spend
                    FROM transactions
                    WHERE type = 'debit'
                      AND date >= DATE_TRUNC('month', CURRENT_DATE)
                """)
                _bg_row = _bg_cur.fetchone()
                _bg_db.close()
                mtd_spend = float(_bg_row['mtd_spend']) if _bg_row else 0
                # Warn if >$500 MTD spend (soft limit)
                if mtd_spend > 500:
                    _slack_post(reply_channel,
                        f'💳 *Budget Gate Warning*\n'
                        f'MTD spend is *${mtd_spend:,.2f}*.\n'
                        f'Proceeding with blast of {limit} contacts — reply `blast stats` after to monitor opt-out rate.'
                    )
            except Exception:
                pass  # Finance data unavailable — proceed without gate

        _slack_post(reply_channel,
            f'⏳ Blasting up to {limit} contacts... (tags: {tags or "—"}, query: {query or "—"})\n'
            f'_Running in background — results will post here when done._')

        blast_variant       = cmd.get('variant', 'A')
        blast_variant_label = cmd.get('variant_label')

        def _run_blast(wf_id, tags, query, limit, reply_channel, blast_variant='A', blast_variant_label=None):
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
                          (workflow_id, workflow_name, tags, sent_count, failed_count, variant, variant_label)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (wf_id, wf_name,
                          ','.join(tags) if isinstance(tags, list) else (tags or ''),
                          sent_count, failed_count, blast_variant, blast_variant_label))
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
        threading.Thread(target=_run_blast, args=(wf_id, tags, query, limit, reply_channel, blast_variant, blast_variant_label), daemon=True).start()
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

    # ── DECISION OUTCOME ──────────────────────────────────────────────────────
    if action == 'decision_outcome':
        try:
            did    = str(cmd.get('decision_id', '') or '').strip()
            result = str(cmd.get('result', '') or '').strip()
            if not did or not result:
                _slack_post(reply_channel, '⚠️ Usage: `decision outcome <id> result:"What happened"`')
                return
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                UPDATE decision_queue
                SET outcome_notes       = %s,
                    outcome_recorded_at = NOW(),
                    updated_at          = NOW()
                WHERE id::text LIKE %s
                RETURNING id, issue_summary, status
            """, (result[:500], f'{did}%'))
            row = cur.fetchone()
            db.commit()
            db.close()
            if row:
                _slack_post(reply_channel,
                    f'✅ *Outcome recorded* on decision `{str(row["id"])[:8]}`\n'
                    f'_{row["issue_summary"]}_\n'
                    f'Result: {result}')
            else:
                _slack_post(reply_channel, f'⚠️ Decision `{did}` not found.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Decision outcome error: {e}')
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

            # Prior month totals for MoM comparison
            cur.execute("""
                SELECT spoke,
                       SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END) AS income,
                       SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expenses
                FROM revenue_events
                WHERE DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                GROUP BY spoke
            """)
            prior_rows = {r['spoke']: r for r in (cur.fetchall() or [])}

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

            now = datetime.now()
            month_str      = now.strftime('%B %Y')
            prior_month_str = (now.replace(day=1) - timedelta(days=1)).strftime('%b')
            lines = [f'*💰 Revenue — {month_str}*', '']

            if rows:
                total_net = sum(r['income'] - r['expenses'] for r in rows)
                for r in rows:
                    net   = r['income'] - r['expenses']
                    prior = prior_rows.get(r['spoke'])
                    tgt   = targets.get(r['spoke'])
                    tgt_str = f' / ${tgt["target_value"]:,.0f} target' if tgt else ''
                    pct   = f' ({net/tgt["target_value"]*100:.0f}%)' if tgt and tgt['target_value'] else ''
                    if prior:
                        prior_net = prior['income'] - prior['expenses']
                        delta     = net - prior_net
                        mom_str   = f' | {prior_month_str}: ${prior_net:,.0f} (Δ{delta:+,.0f})'
                    else:
                        mom_str   = ''
                    lines.append(f'*{r["spoke"]}*: ${r["income"]:,.0f} in — ${r["expenses"]:,.0f} out = *${net:,.0f} net*{tgt_str}{pct}{mom_str}')
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

                # A/B variant breakdown
                cur.execute("""
                    SELECT
                      blast_variant,
                      COUNT(*) AS total,
                      COUNT(*) FILTER (WHERE mctp_total >= 5) AS replied
                    FROM leads
                    WHERE blast_campaign_id = %s
                      AND blast_variant IS NOT NULL
                    GROUP BY blast_variant
                    ORDER BY blast_variant
                """, (str(c['id']),))
                variants = cur.fetchall() or []
                if variants and len(variants) > 1:
                    v_parts = []
                    for v in variants:
                        rate = f'{v["replied"]/v["total"]*100:.0f}%' if v['total'] else '—'
                        v_parts.append(f'Variant {v["blast_variant"]}: {v["total"]} sent / {rate} reply')
                    lines.append(f'  A/B: {" | ".join(v_parts)}')
                lines.append('')

            lines.append('`blast stats <id>` for specific campaign | `offer stats` for funnel')
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

            # Live spoke snapshots
            spoke_snapshots = {}
            try:
                cur.execute("""
                    SELECT
                      COUNT(*) FILTER (WHERE mctp_total >= 8)                    AS hot,
                      COUNT(*) FILTER (WHERE mctp_total >= 5 AND mctp_total < 8) AS warm,
                      COUNT(*) FILTER (WHERE follow_up_date::date = CURRENT_DATE
                                       AND opted_out_at IS NULL)                 AS followups,
                      COUNT(*) FILTER (WHERE offer_status = 'drafted')           AS open_offers
                    FROM leads WHERE spoke = 'real_estate'
                """)
                spoke_snapshots['real_estate'] = cur.fetchone()
            except Exception:
                pass
            try:
                cur.execute("""
                    SELECT ba.name, ba.balance FROM bank_accounts ba
                    ORDER BY ba.balance DESC LIMIT 1
                """)
                spoke_snapshots['financial_health'] = cur.fetchone()
            except Exception:
                pass
            try:
                cur.execute("""
                    SELECT COUNT(*) AS open_tasks FROM tasks
                    WHERE spoke = 'katelyn_business'
                      AND status NOT IN ('done','completed','cancelled')
                """)
                spoke_snapshots['katelyn_business'] = cur.fetchone()
            except Exception:
                pass

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
            lines = ['*📊 ODIN KPI Scorecard — All Spokes*', '']

            # Live snapshot header
            re_s = spoke_snapshots.get('real_estate')
            if re_s:
                lines.append(f'🏠 *RE*: 🔥{re_s["hot"]} hot | ⚡{re_s["warm"]} warm | 📅{re_s["followups"]} due today | 📋{re_s["open_offers"]} open offers')
            fin_s = spoke_snapshots.get('financial_health')
            if fin_s and fin_s.get('balance') is not None:
                lines.append(f'💳 *Finance*: {fin_s["name"]} ${fin_s["balance"]:,.2f}')
            kat_s = spoke_snapshots.get('katelyn_business')
            if kat_s:
                lines.append(f'✨ *Katelyn*: {kat_s["open_tasks"]} open tasks')
            lines.append('')

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

            lines.append('\n`revenue` | `offer stats` | `source stats` | `scorecard trends`')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ KPI scorecard error: {type(e).__name__}: {e}')
        return

    # ── KPI TRENDS (week-over-week) ───────────────────────────────────────────
    if action == 'kpi_trends':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT t.spoke, t.metric_name, t.current_value, t.target_value,
                       t.unit, t.status,
                       this_wk.value  AS this_week,
                       last_wk.value  AS last_week
                FROM kpi_targets t
                LEFT JOIN kpi_snapshots this_wk
                    ON this_wk.metric_name = t.metric_name
                   AND this_wk.spoke      = t.spoke
                   AND this_wk.snapshot_date = CURRENT_DATE
                LEFT JOIN kpi_snapshots last_wk
                    ON last_wk.metric_name = t.metric_name
                   AND last_wk.spoke      = t.spoke
                   AND last_wk.snapshot_date = CURRENT_DATE - INTERVAL '7 days'
                ORDER BY t.spoke, t.metric_name
            """)
            rows = cur.fetchall() or []
            db.close()

            if not rows:
                _slack_post(reply_channel, 'No KPI snapshots yet — data accumulates after the first kpi_auto_update cycle.')
                return

            TREND = lambda c, p: ('📈' if c > p else ('📉' if c < p else '➡️')) if (c is not None and p is not None) else '—'
            STATUS_EMOJI = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
            lines = ['*📊 KPI Trends — This Week vs Last Week*', '']
            current_spoke = None
            for r in rows:
                if r['spoke'] != current_spoke:
                    current_spoke = r['spoke']
                    lines.append(f'*{current_spoke.upper()}*')
                this_w = r['this_week']
                last_w = r['last_week']
                trend  = TREND(this_w, last_w)
                delta  = f'Δ{this_w - last_w:+.0f}' if (this_w is not None and last_w is not None) else 'no prior data'
                emoji  = STATUS_EMOJI.get(r['status'] or 'green', '⚪')
                cur_str = f'{r["current_value"] or 0:.0f}' if r['current_value'] is not None else '?'
                lines.append(f'  {emoji}{trend} {r["metric_name"]}: {cur_str} ({delta})')
            lines.append('\n_Trends populate after 7 days of snapshots. Run `scorecard` for current targets._')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ KPI trends error: {type(e).__name__}: {e}')
        return

    # ── OFFER CONVERSION STATS ───────────────────────────────────────────────
    if action == 'offer_stats':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE offer_status = 'drafted')   AS drafted,
                  COUNT(*) FILTER (WHERE offer_status = 'submitted')  AS submitted,
                  COUNT(*) FILTER (WHERE offer_status = 'accepted')   AS accepted,
                  COUNT(*) FILTER (WHERE offer_status = 'rejected')   AS rejected,
                  COUNT(*) FILTER (WHERE offer_status = 'expired')    AS expired,
                  COUNT(*) FILTER (WHERE offer_amount IS NOT NULL)    AS total_with_offer,
                  COALESCE(AVG(offer_amount) FILTER (WHERE offer_amount IS NOT NULL), 0) AS avg_offer,
                  COALESCE(SUM(offer_amount) FILTER (WHERE offer_status = 'accepted'), 0) AS accepted_value
                FROM leads
                WHERE spoke = 'real_estate'
            """)
            r = cur.fetchone()
            cur.execute("""
                SELECT
                  AVG(EXTRACT(EPOCH FROM (offer_drafted_at - created_at))/86400) AS avg_days_to_offer
                FROM leads
                WHERE offer_drafted_at IS NOT NULL AND created_at IS NOT NULL
            """)
            timing = cur.fetchone()
            db.close()

            drafted   = r['drafted'] or 0
            submitted = r['submitted'] or 0
            accepted  = r['accepted'] or 0
            rejected  = r['rejected'] or 0
            expired   = r['expired'] or 0
            total     = r['total_with_offer'] or 0

            sub_rate  = f'{submitted/drafted*100:.0f}%' if drafted else '—'
            acc_rate  = f'{accepted/submitted*100:.0f}%' if submitted else '—'
            avg_days  = f'{timing["avg_days_to_offer"]:.1f}d' if timing and timing['avg_days_to_offer'] else '—'

            lines = ['*📋 Offer Conversion Funnel*', '']
            lines.append(f'📝 Drafted:   {drafted}')
            lines.append(f'📤 Submitted: {submitted} ({sub_rate} of drafted)')
            lines.append(f'✅ Accepted:  {accepted} ({acc_rate} of submitted)')
            lines.append(f'❌ Rejected:  {rejected}')
            lines.append(f'⏰ Expired:   {expired}')
            lines.append('')
            lines.append(f'💰 Avg offer: ${r["avg_offer"]:,.0f} | Accepted value: ${r["accepted_value"]:,.0f}')
            lines.append(f'⏱ Avg days lead→offer: {avg_days}')
            lines.append('\n`draft offer <address>` to create | `offer status <address>` to check')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Offer stats error: {type(e).__name__}: {e}')
        return

    # ── LEAD SOURCE ATTRIBUTION ───────────────────────────────────────────────
    if action == 'source_stats':
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT
                  COALESCE(lead_source, 'unknown') AS source,
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE mctp_total >= 8)                        AS hot,
                  COUNT(*) FILTER (WHERE mctp_total >= 5 AND mctp_total < 8)     AS warm,
                  COUNT(*) FILTER (WHERE opted_out_at IS NOT NULL)               AS opted_out
                FROM leads
                WHERE spoke = 'real_estate'
                GROUP BY lead_source
                ORDER BY hot DESC, total DESC
                LIMIT 15
            """)
            source_rows = cur.fetchall() or []

            cur.execute("""
                SELECT
                  COALESCE(zip, 'unknown') AS zip,
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE mctp_total >= 8) AS hot,
                  COUNT(*) FILTER (WHERE mctp_total >= 5 AND mctp_total < 8) AS warm
                FROM leads
                WHERE spoke = 'real_estate'
                GROUP BY zip
                ORDER BY hot DESC, total DESC
                LIMIT 10
            """)
            zip_rows = cur.fetchall() or []
            db.close()

            lines = ['*📍 Lead Source Attribution*', '']
            if source_rows:
                lines.append('*By Source Tag*')
                for r in source_rows:
                    total = r['total'] or 1
                    hot_pct = f'{r["hot"]/total*100:.0f}%'
                    lines.append(f'• `{r["source"]}`: {r["total"]} leads | 🔥{r["hot"]} hot ({hot_pct}) | ⚡{r["warm"]} warm | 🚫{r["opted_out"]} opt-out')
                lines.append('')

            if zip_rows:
                lines.append('*By Zip Code*')
                for r in zip_rows:
                    total = r['total'] or 1
                    hot_pct = f'{r["hot"]/total*100:.0f}%'
                    lines.append(f'• `{r["zip"]}`: {r["total"]} leads | 🔥{r["hot"]} ({hot_pct}) | ⚡{r["warm"]} warm')

            if not source_rows and not zip_rows:
                lines.append('No leads with source data yet. Source is stamped on new inbound webhooks going forward.')

            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Source stats error: {type(e).__name__}: {e}')
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

    # ── BUYER MATCH (criteria-based) ──────────────────────────────────────────
    if action == 'buyer_match_criteria':
        address = cmd.get('address', '').strip()
        arv     = cmd.get('arv') or 0
        if not address:
            _slack_post(reply_channel, '⚠️ Usage: `buyer match <address> arv:165000`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Extract zip from address or use known Memphis zips
            zip_m = re.search(r'\b(3\d{4})\b', address)
            zip_code = zip_m.group(1) if zip_m else None

            buyer_type_expr = """
                           CASE WHEN b.is_flipper THEN 'flipper'
                                WHEN b.is_landlord THEN 'landlord'
                                WHEN b.is_lender THEN 'lender'
                                ELSE 'buyer' END AS buyer_type"""
            if zip_code:
                cur.execute(f"""
                    SELECT b.name, b.phone, b.email, {buyer_type_expr},
                           bc.min_price, bc.max_price, bc.zip_codes,
                           bc.deal_types, bc.max_rehab, bc.notes
                    FROM buyer_criteria bc
                    JOIN buyers b ON b.id = bc.buyer_id
                    WHERE b.is_dnc = FALSE
                      AND b.onboarding_stage IN ('qualified', 'active')
                      AND (bc.min_price IS NULL OR bc.min_price <= %s)
                      AND (bc.max_price IS NULL OR bc.max_price >= %s)
                      AND (bc.zip_codes IS NULL OR bc.zip_codes = '{{}}' OR %s = ANY(bc.zip_codes))
                    ORDER BY bc.max_price DESC NULLS LAST
                    LIMIT 8
                """, (arv or 999999, arv or 0, zip_code))
            else:
                cur.execute(f"""
                    SELECT b.name, b.phone, b.email, {buyer_type_expr},
                           bc.min_price, bc.max_price, bc.zip_codes,
                           bc.deal_types, bc.max_rehab, bc.notes
                    FROM buyer_criteria bc
                    JOIN buyers b ON b.id = bc.buyer_id
                    WHERE b.is_dnc = FALSE
                      AND b.onboarding_stage IN ('qualified', 'active')
                      AND (bc.min_price IS NULL OR bc.min_price <= %s)
                      AND (bc.max_price IS NULL OR bc.max_price >= %s)
                    ORDER BY bc.max_price DESC NULLS LAST
                    LIMIT 8
                """, (arv or 999999, arv or 0))

            matches = cur.fetchall() or []
            db.close()

            if not matches:
                _slack_post(reply_channel,
                    f'No criteria-qualified buyers match {address} (ARV ${arv:,.0f}).\n'
                    f'Run `buyers onboarding` to see qualification pipeline.')
                return

            lines = [f'*🏠 Criteria Buyers for {address}*{f" (ARV ${arv:,.0f})" if arv else ""}', '']
            for b in matches:
                price_range = f'${b["min_price"]:,.0f}–${b["max_price"]:,.0f}' if b['max_price'] else 'no range set'
                zips_str    = ', '.join(b['zip_codes'] or []) or 'any'
                types_str   = ', '.join(b['deal_types'] or []) or 'any'
                lines.append(
                    f'• *{b["name"] or "Unknown"}* ({b["buyer_type"] or "buyer"})\n'
                    f'  Range: {price_range} | Zips: {zips_str} | Types: {types_str}\n'
                    f'  Contact: {b["phone"] or b["email"] or "—"}'
                )
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Buyer match error: {type(e).__name__}: {e}')
        return

    # ── BUYER CRITERIA VIEW ───────────────────────────────────────────────────
    if action == 'buyer_criteria_view':
        name = cmd.get('name', '').strip()
        if not name:
            _slack_post(reply_channel, '⚠️ Usage: `buyer criteria <name>`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT b.name, b.phone, b.onboarding_stage,
                       bc.min_price, bc.max_price, bc.zip_codes,
                       bc.deal_types, bc.max_rehab, bc.notes, bc.raw_reply, bc.captured_at
                FROM buyers b
                JOIN buyer_criteria bc ON bc.buyer_id = b.id
                WHERE LOWER(b.name) LIKE LOWER(%s)
                  AND b.is_dnc = FALSE
                ORDER BY bc.captured_at DESC LIMIT 1
            """, (f'%{name}%',))
            row = cur.fetchone()
            db.close()
            if not row:
                _slack_post(reply_channel, f'No buyer criteria found for `{name}`. They may not have replied yet.')
                return
            lines = [
                f'*📋 Buyer Criteria — {row["name"]}*',
                f'Stage: {row["onboarding_stage"]}',
                f'Price range: ${row["min_price"] or "?"} – ${row["max_price"] or "?"}',
                f'Zip codes: {", ".join(row["zip_codes"] or []) or "not specified"}',
                f'Deal types: {", ".join(row["deal_types"] or []) or "not specified"}',
                f'Max rehab: ${row["max_rehab"] or "not specified"}',
                f'Notes: {row["notes"] or "—"}',
            ]
            if row['raw_reply']:
                lines.append(f'Raw reply: _{row["raw_reply"][:200]}_')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Buyer criteria error: {type(e).__name__}: {e}')
        return

    # ── SET FOLLOWUP SEQUENCE ─────────────────────────────────────────────────
    if action == 'set_followup_sequence':
        address = cmd.get('address', '').strip()
        steps   = max(1, min(int(cmd.get('steps') or 3), 5))
        if not address:
            _slack_post(reply_channel, '⚠️ Usage: `set followup sequence <address> steps:3`')
            return
        try:
            db  = get_db()
            cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                UPDATE leads
                SET followup_max_steps = %s,
                    followup_step      = 1,
                    updated_at         = NOW()
                WHERE LOWER(address) LIKE LOWER(%s)
                  AND status NOT IN ('closed','dead','contracted')
                RETURNING address, followup_max_steps
            """, (steps, f'%{address}%'))
            row = cur.fetchone()
            db.commit()
            db.close()
            if row:
                _slack_post(reply_channel,
                    f'✅ Follow-up sequence set on *{row["address"]}*: *{steps} steps*, 4 days apart.\n'
                    f'Auto-advances when heartbeat fires each step. Resets to step 1 when seller replies.')
            else:
                _slack_post(reply_channel, f'⚠️ No active lead found matching `{address}`.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Set followup sequence error: {e}')
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

    # ── FINANCIAL ADVISOR ─────────────────────────────────────────────────────
    if action == 'finance_advisor':
        question = cmd.get('question', '').strip()
        if not question:
            _slack_post(reply_channel, 'Usage: `financial advisor <your question>`\nExample: `financial advisor should I pay off the biz card or hold cash for a deal?`')
            return
        _slack_post(reply_channel, '💼 Pulling financial data + thinking...')
        try:
            result = finance_bot.finance_advisor(question=question, get_db=get_db)
            _slack_post(reply_channel, result)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Advisor error: {e}')
        return

    # ── MONEY MOVES ───────────────────────────────────────────────────────────
    if action == 'money_moves':
        _slack_post(reply_channel, '💰 Analyzing your finances for money-move recommendations...')
        try:
            result = finance_bot.recommend_money_moves(get_db=get_db)
            _slack_post(reply_channel, result)
        except Exception as e:
            _slack_post(reply_channel, f'❌ Money moves error: {e}')
        return

    # ── AI AUDIT PIPELINE ─────────────────────────────────────────────────────
    if action == 'audit_pipeline':
        try:
            conn = get_db()
            cur  = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
            cur.execute("""
                SELECT stage, COUNT(*) as cnt
                FROM audit_prospects
                GROUP BY stage
                ORDER BY CASE stage
                    WHEN 'cold' THEN 1 WHEN 'emailed' THEN 2 WHEN 'replied' THEN 3
                    WHEN 'booked' THEN 4 WHEN 'paid' THEN 5 WHEN 'delivered' THEN 6
                    WHEN 'converted' THEN 7 ELSE 8 END
            """)
            stages = cur.fetchall()
            cur.execute("SELECT COUNT(*) as total, SUM(amount_paid) as revenue FROM audit_prospects WHERE amount_paid > 0")
            totals = cur.fetchone()
            conn.close()

            lines = ['*🔍 AI Audit Pipeline*', '']
            icons = {'cold':'🧊','emailed':'📧','replied':'💬','booked':'📅','paid':'💰','delivered':'📄','converted':'🔁'}
            for s in stages:
                lines.append(f'{icons.get(s["stage"],"•")} {s["stage"].title()}: *{s["cnt"]}*')
            lines.append('')
            rev = float(totals['revenue'] or 0)
            lines.append(f'Total sold: {totals["total"]} | Revenue: *${rev:,.2f}*')
            lines.append('`audit log` | `audit sold` | `audit stats`')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit pipeline error: {e}')
        return

    if action == 'audit_stats':
        try:
            conn = get_db()
            cur  = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
            cur.execute("""
                SELECT niche, COUNT(*) as total,
                       COUNT(*) FILTER (WHERE stage IN ('paid','delivered','converted')) as sold,
                       SUM(amount_paid) FILTER (WHERE amount_paid > 0) as revenue
                FROM audit_prospects
                GROUP BY niche ORDER BY sold DESC
            """)
            rows = cur.fetchall()
            conn.close()
            lines = ['*📊 AI Audit Stats by Niche*', '']
            for r in rows:
                rev  = float(r['revenue'] or 0)
                rate = round((r['sold'] / r['total']) * 100, 1) if r['total'] else 0
                lines.append(f'*{(r["niche"] or "Unknown").title()}*: {r["total"]} prospects → {r["sold"]} sold ({rate}%) | ${rev:,.2f}')
            _slack_post(reply_channel, '\n'.join(lines) if rows else '📭 No audit prospects yet. `audit log` to add one.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit stats error: {e}')
        return

    if action == 'audit_log':
        raw = cmd.get('raw', '')
        params = _parse_kv(raw)
        client_name = params.get('client', params.get('name', '')).strip()
        niche  = params.get('niche', '').strip()
        stage  = params.get('stage', 'emailed').strip()
        email  = params.get('email', '').strip()
        notes  = params.get('notes', '').strip()
        if not client_name:
            _slack_post(reply_channel, 'Usage: `audit log client:"Name" niche:roofing stage:emailed email:x@x.com notes:"..."`')
            return
        try:
            conn = get_db()
            cur  = conn.cursor()
            cur.execute("""
                INSERT INTO audit_prospects (user_id, name, niche, stage, email, notes, last_contact)
                VALUES (
                    (SELECT id FROM users WHERE role='super_admin' LIMIT 1),
                    %s, %s, %s, %s, %s, NOW()
                )
            """, (client_name, niche or None, stage, email or None, notes or None))
            conn.commit()
            conn.close()
            _slack_post(reply_channel, f'✅ Audit prospect added: *{client_name}* ({niche}, stage: {stage})')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit log error: {e}')
        return

    if action == 'audit_sold':
        raw    = cmd.get('raw', '')
        params = _parse_kv(raw)
        client_name = params.get('client', params.get('name', '')).strip()
        amount = float(params.get('amount', '497').replace('$','').replace(',','') or 497)
        tier   = params.get('tier', 'audit').strip()
        try:
            conn = get_db()
            cur  = conn.cursor()
            cur.execute("""
                UPDATE audit_prospects SET stage='paid', amount_paid=%s, tier=%s, last_contact=NOW(), updated_at=NOW()
                WHERE LOWER(name) LIKE LOWER(%s)
            """, (amount, tier, f'%{client_name}%'))
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO audit_prospects (user_id, name, stage, amount_paid, tier, last_contact)
                    VALUES ((SELECT id FROM users WHERE role='super_admin' LIMIT 1), %s, 'paid', %s, %s, NOW())
                """, (client_name, amount, tier))
            # Log revenue event
            cur.execute("""
                INSERT INTO revenue_events (spoke, type, amount, description, recorded_at)
                VALUES ('ai_audit', 'income', %s, %s, NOW())
            """, (amount, f'AI Audit sale — {client_name} ({tier})'))
            conn.commit()
            conn.close()
            _slack_post(reply_channel, f'🎉 *Audit sale logged!* {client_name} — ${amount:,.2f} ({tier})\nRevenue recorded to AI Audit spoke.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit sold error: {e}')
        return

    if action == 'audit_followup':
        try:
            conn = get_db()
            cur  = conn.cursor(cursor_factory=__import__('psycopg2').extras.RealDictCursor)
            cur.execute("""
                SELECT name, niche, stage, email, last_contact
                FROM audit_prospects
                WHERE stage IN ('emailed','replied','booked')
                  AND (last_contact IS NULL OR last_contact < NOW() - INTERVAL '3 days')
                ORDER BY last_contact ASC NULLS FIRST
                LIMIT 15
            """)
            rows = cur.fetchall()
            conn.close()
            if not rows:
                _slack_post(reply_channel, '✅ No overdue audit follow-ups.')
                return
            lines = [f'*📧 AI Audit — {len(rows)} Overdue Follow-Ups*', '']
            for r in rows:
                days = ''
                if r['last_contact']:
                    d = (datetime.utcnow() - r['last_contact'].replace(tzinfo=None)).days
                    days = f' ({d}d ago)'
                lines.append(f'• *{r["name"]}* ({r["niche"] or "?"}) — {r["stage"]}{days} | {r["email"] or "no email"}')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Audit followup error: {e}')
        return

    # ── SPAWN (dynamic sub-agent) ─────────────────────────────────────────────
    if action == 'spawn':
        task = cmd.get('task', '').strip()
        if not task:
            _slack_post(reply_channel, 'Usage: `spawn <task description>`\nExample: `spawn research printables business and build a 30-day launch plan`')
            return
        _slack_post(reply_channel, f':robot_face: Spawning agents for: _{task}_...')
        try:
            result = agent_spawner.spawn(
                task=task,
                get_db=get_db,
                xleads_mod=xleads,
                triggered_by=sender_uid or 'slack',
            )
            _slack_post(reply_channel, result['slack_text'])
        except Exception as e:
            _slack_post(reply_channel, f'❌ Spawn error: {type(e).__name__}: {e}')
        return

    # ── AGENT RUNS (spawn history) ────────────────────────────────────────────
    if action == 'agent_runs':
        try:
            runs = agent_spawner.get_runs(get_db, limit=10)
            if not runs:
                _slack_post(reply_channel, 'No spawn runs found yet. Try: `spawn <task>`')
                return
            lines = ['*Recent Spawn Runs*\n']
            for r in runs:
                ts   = str(r.get('created_at', ''))[:16].replace('T', ' ')
                dur  = f' {r["duration_ms"]}ms' if r.get('duration_ms') else ''
                emoji = {'done': '✅', 'running': '⏳', 'error': '❌'}.get(r.get('status', ''), '⚪')
                lines.append(f'{emoji} `{str(r["id"])[:8]}` {r["task"][:60]}{dur} — {ts}')
            _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Agent runs error: {type(e).__name__}: {e}')
        return

    # ── TONE PROFILES (APEX Agent Files parity) ──────────────────────────────
    def _resolve_uid(slack_uid, gdb):
        """Resolve Slack UID to DB user_id, falling back to super_admin."""
        try:
            _c = gdb()
            _r = _c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if slack_uid:
                _r.execute("SELECT id FROM users WHERE slack_uid = %s LIMIT 1", (slack_uid,))
                row = _r.fetchone()
                if row:
                    _c.close()
                    return str(row['id'])
            _r.execute("SELECT id FROM users WHERE role = 'super_admin' LIMIT 1")
            row = _r.fetchone()
            _c.close()
            return str(row['id']) if row else None
        except Exception:
            return None

    if action == 'tone_view':
        try:
            user_id = _resolve_uid(sender_uid, get_db)
            _slack_post(reply_channel, memory.format_tone_profiles(user_id, get_db))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Tone view error: {e}')
        return

    if action == 'tone_update':
        channel     = cmd.get('channel', '')
        description = cmd.get('description', '')
        try:
            user_id = _resolve_uid(sender_uid, get_db)
            ok = memory.set_tone_profile(user_id, channel, description, get_db)
            if ok:
                _slack_post(reply_channel,
                    f'✅ *Tone profile updated — {channel}*\n'
                    f'_{description}_\n\n'
                    f'ODIN will now match this style when drafting {channel} messages.\n'
                    f'`tone` to view all profiles.')
            else:
                _slack_post(reply_channel, f'⚠️ Invalid channel. Use: email, sms, or slack.')
        except Exception as e:
            _slack_post(reply_channel, f'❌ Tone update error: {e}')
        return

    # ── SENTIMENT REPORT ──────────────────────────────────────────────────────
    if action == 'sentiment_report':
        sentiment_filter = cmd.get('filter', '')
        try:
            conn = get_db()
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            if sentiment_filter:
                cur.execute("""
                    SELECT address, reply_sentiment, last_reply_body, mctp_total, status, last_reply_at
                    FROM leads
                    WHERE reply_sentiment = %s
                      AND last_reply_at IS NOT NULL
                      AND status NOT IN ('closed','dead')
                    ORDER BY last_reply_at DESC
                    LIMIT 15
                """, (sentiment_filter,))
                rows = cur.fetchall() or []
                conn.close()
                emoji_map = {'curious':'🤔','interested':'👍','stalling':'⏳','angry':'😠','negotiating':'🤝','neutral':'😐'}
                emoji = emoji_map.get(sentiment_filter, '•')
                if not rows:
                    _slack_post(reply_channel, f'📭 No leads tagged *{sentiment_filter}* yet.')
                    return
                lines = [f'*{emoji} {sentiment_filter.title()} Leads ({len(rows)})*']
                for r in rows:
                    ts = r['last_reply_at'].strftime('%m/%d') if r['last_reply_at'] else '?'
                    lines.append(f'• *{r["address"]}* | Score: {r["mctp_total"] or "?"}/10 | {ts}')
                    if r['last_reply_body']:
                        lines.append(f'  _{r["last_reply_body"][:80]}_')
                _slack_post(reply_channel, '\n'.join(lines))
            else:
                cur.execute("""
                    SELECT reply_sentiment, COUNT(*) as cnt
                    FROM leads
                    WHERE reply_sentiment IS NOT NULL
                      AND status NOT IN ('closed','dead')
                    GROUP BY reply_sentiment
                    ORDER BY cnt DESC
                """)
                rows = cur.fetchall() or []
                conn.close()
                if not rows:
                    _slack_post(reply_channel, '_No sentiment data yet — replies will be classified as they come in._')
                    return
                emoji_map = {'curious':'🤔','interested':'👍','stalling':'⏳','angry':'😠','negotiating':'🤝','neutral':'😐'}
                lines = ['*📊 Seller Reply Sentiment Breakdown*', '']
                for r in rows:
                    em = emoji_map.get(r['reply_sentiment'] or '', '•')
                    lines.append(f'{em} *{(r["reply_sentiment"] or "unknown").title()}*: {r["cnt"]} lead(s)')
                lines.append('\n`sentiment curious` to see all curious leads | `sentiment stalling` for stalled leads')
                _slack_post(reply_channel, '\n'.join(lines))
        except Exception as e:
            _slack_post(reply_channel, f'❌ Sentiment report error: {e}')
        return

    # ── ERROR / UNKNOWN ────────────────────────────────────────────────────────
    if action == 'error':
        _slack_post(reply_channel, f'⚠️ {cmd.get("msg", "Invalid command")}')
        return

    _slack_post(reply_channel,
        f'🤔 I didn\'t understand that. Type `help` to see all commands.')

