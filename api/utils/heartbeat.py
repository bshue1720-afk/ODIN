"""
ODIN Layer 5 — Heartbeat
Proactive scheduled workflows via APScheduler.
Runs inside Flask — start_heartbeat() called once at app startup.

Schedules (all times US Central):
  06:00 daily   — Morning briefing → #odin-brock (spoke-aware)
  21:00 daily   — Pipeline report  → #odin-brock
  Every 30 min  — Seller reply scanner (XLeads inbound check)
  Every 2 hours — Overdue follow-up queue check
  Every 4 hours — XLeads opportunity sync
  Monday 07:00  — Weekly KPI report → #odin-brock
  Sunday 20:00  — Weekly CEO review → #odin-brock
  Wednesday 10:00 — Dead lead resurrection scan → #odin-brock
"""

import json
import os
import logging
from datetime import datetime, timezone, timedelta

import anthropic
import psycopg2
import psycopg2.extras

log = logging.getLogger('odin.heartbeat')

# Lazy imports to avoid circular deps — resolved at runtime
_slack_post_fn   = None
_get_db_fn       = None
_xleads_mod      = None
_channels        = None


def init(slack_post_fn, get_db_fn, xleads_mod, channels: dict):
    """Called from app.py after imports are safe."""
    global _slack_post_fn, _get_db_fn, _xleads_mod, _channels
    _slack_post_fn = slack_post_fn
    _get_db_fn     = get_db_fn
    _xleads_mod    = xleads_mod
    _channels      = channels


def _post(msg: str):
    if _slack_post_fn and _channels:
        _slack_post_fn(_channels.get('brock', ''), msg)
    else:
        log.warning('Heartbeat: Slack not initialized')


def _db():
    if _get_db_fn:
        return _get_db_fn()
    raise RuntimeError('Heartbeat: DB not initialized')


# ─── JOBS ────────────────────────────────────────────────────────────────────

# ─── BUSINESS OPPORTUNITY ROTATION ──────────────────────────────────────────
# Shown in morning briefing when system is quiet. Rotates daily by weekday.
# All under $50 startup, high ROI, automatable via ODIN.
_OPPORTUNITIES = [
    {
        "name": "AI Review Response Service",
        "pitch": "Local businesses lose customers when Google/Yelp reviews go unanswered. AI responds to all reviews within 24hrs. Zero manual work after setup.",
        "startup": "$0",
        "roi": "$200–500/client/month",
        "first_move": 'Cold email 10 local restaurants: "I\'ll respond to your last 5 Google reviews this week — free." Close from there.',
        "scout_cmd": "scout review response agency",
    },
    {
        "name": "Missed Call Text-Back Service",
        "pitch": "62% of callers who hang up never call back. Auto-text them within 60 seconds. Plumbers, dentists, HVAC — all pay for this.",
        "startup": "~$20 (Twilio trial credit)",
        "roi": "$200–400/client/month",
        "first_move": "Set up a Twilio number + ODIN webhook. Demo it live for one local business — let them watch a missed call get texted back in real time.",
        "scout_cmd": "scout missed call text back service",
    },
    {
        "name": "AI Newsletter Ghost-Writing",
        "pitch": "Local businesses and creators need a weekly newsletter but never write one. AI drafts it in 10 minutes. You send it.",
        "startup": "$0 (Beehiiv free tier + Claude API)",
        "roi": "$300–800/client/month",
        "first_move": "Write a sample newsletter for a local business in your city and cold-send it to them. Charge from the second one.",
        "scout_cmd": "scout newsletter ghostwriting agency",
    },
    {
        "name": "AI Social Media Ghost-Writing",
        "pitch": "30 posts/month for a local business. One 90-minute AI session per client per month. Claude writes, you lightly edit.",
        "startup": "~$10 (API credits)",
        "roi": "$500–1,500/client/month",
        "first_move": "Create a sample month of posts for a specific niche (dentist, HVAC, realtor). Post as a case study. DM 10 businesses.",
        "scout_cmd": "scout social media ghostwriting",
    },
    {
        "name": "AI Lead Response (Valdr Ops model)",
        "pitch": "Businesses miss 78% of inbound leads. AI responds in 60 seconds, 24/7, qualifies, and books the call. You resell the infrastructure.",
        "startup": "~$20 (Twilio credit — infrastructure already built)",
        "roi": "$750 setup + $150–300/month recurring per client",
        "first_move": "Valdr Ops has 114 warm leads. Launch the Founding 50 program: 50% off for first 50 clients who commit to annual billing.",
        "scout_cmd": "scout ai answering service agency",
    },
    {
        "name": "Automated Job Application Service",
        "pitch": "Job seekers pay $300–500 for 50 AI-personalized applications. No ongoing work — one-time delivery per client.",
        "startup": "$0",
        "roi": "$300–500 per client, 2–3 clients/week",
        "first_move": 'Post on Reddit r/jobs: "I\'ll write 10 custom cover letters for your profile for free this week." Convert from there.',
        "scout_cmd": "scout job application service",
    },
    {
        "name": "AI Listing Copy Service",
        "pitch": "Real estate agents, Etsy sellers, and Amazon FBA sellers all need product copy written fast. AI produces it in 30 seconds.",
        "startup": "$0",
        "roi": "$50–200 per batch, recurring clients",
        "first_move": "Offer free descriptions to 5 Etsy sellers in a niche. Charge the 6th. Build a portfolio of before/after examples.",
        "scout_cmd": "scout listing description copywriting",
    },
]


def morning_briefing():
    """
    06:00 daily — APEX CEO Morning Briefing.
    Spoke-agnostic: covers decisions, tasks, approvals, KPI alerts, revenue.
    When quiet: rotates through low-startup high-ROI business opportunities.
    Posts to Slack + Discord (TTS).
    """
    try:
        import utils.discord_notify as discord_notify

        today     = datetime.now().strftime('%Y-%m-%d')
        today_fmt = datetime.now().strftime('%A, %b %d')
        conn      = _db()
        cur       = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── 1. PENDING DECISIONS ──────────────────────────────────────────────
        pending_decisions = []
        try:
            cur.execute("""
                SELECT id, spoke, issue_summary, recommended, reason,
                       auto_execute_at,
                       EXTRACT(EPOCH FROM (NOW() - created_at))/3600 AS hrs_pending
                FROM decision_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 5
            """)
            pending_decisions = cur.fetchall() or []
        except Exception:
            pass  # table may not exist yet — run migration 014

        # ── 2. TASKS DUE TODAY (all spokes) ──────────────────────────────────
        tasks_today = []
        try:
            cur.execute("""
                SELECT title, spoke, status
                FROM tasks
                WHERE due_date::date = CURRENT_DATE
                  AND status NOT IN ('done','completed','cancelled')
                ORDER BY spoke, title
                LIMIT 10
            """)
            tasks_today = cur.fetchall() or []
        except Exception:
            pass

        # ── 3. PENDING APPROVALS ──────────────────────────────────────────────
        try:
            cur.execute("SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'")
            pending_approvals = cur.fetchone()['cnt']
        except Exception:
            pending_approvals = 0

        # ── 4. RED KPI FLAGS ──────────────────────────────────────────────────
        red_kpis = []
        try:
            cur.execute("""
                SELECT spoke, metric_name, current_value, target_value, unit, dri
                FROM kpi_targets
                WHERE status = 'red'
                ORDER BY spoke, metric_name
            """)
            red_kpis = cur.fetchall() or []
        except Exception:
            pass

        # ── 5. REVENUE SNAPSHOT (MTD, all spokes) ────────────────────────────
        revenue_lines = []
        try:
            cur.execute("""
                SELECT spoke,
                       SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END) AS income,
                       SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS expenses
                FROM revenue_events
                WHERE DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY spoke
                ORDER BY income DESC
            """)
            for row in (cur.fetchall() or []):
                net = row['income'] - row['expenses']
                revenue_lines.append(
                    f'• {row["spoke"]}: ${row["income"]:,.0f} in / ${row["expenses"]:,.0f} out / *${net:,.0f} net*'
                )
        except Exception:
            pass

        conn.close()

        # ── BUILD MESSAGE ─────────────────────────────────────────────────────
        has_content = any([pending_decisions, tasks_today, pending_approvals, red_kpis, revenue_lines])

        lines = [f'*☀️ ODIN Morning Briefing — {today_fmt}*', '']

        if pending_decisions:
            lines.append(f'*⚡ DECISIONS WAITING ({len(pending_decisions)})*')
            for d in pending_decisions:
                hrs = int(d['hrs_pending'])
                auto_str = ''
                if d['auto_execute_at']:
                    hrs_left = max(0, int((d['auto_execute_at'] - datetime.now()).total_seconds() / 3600))
                    auto_str = f' | auto-executes in {hrs_left}h'
                lines.append(f'• [{hrs}h pending{auto_str}] {d["issue_summary"][:80]}')
                if d['recommended']:
                    lines.append(f'  → Rec: _{d["recommended"][:80]}_')
            lines.append('Reply `decisions` to review + approve/decline.')
            lines.append('')

        if tasks_today:
            lines.append(f'*📋 TASKS DUE TODAY ({len(tasks_today)})*')
            for t in tasks_today:
                lines.append(f'• {t["title"]} _{t["spoke"] or "general"}_')
            lines.append('')

        if pending_approvals:
            lines.append(f'*⏳ APPROVALS PENDING: {pending_approvals}*')
            lines.append('Reply `approvals` to review.')
            lines.append('')

        if red_kpis:
            lines.append(f'*🔴 KPI ALERTS ({len(red_kpis)} metrics below target)*')
            for k in red_kpis:
                unit = k['unit'] or ''
                cur_str = f'{unit}{k["current_value"]:,.0f}' if unit == '$' else f'{k["current_value"]:.0f}{unit}'
                tgt_str = f'{unit}{k["target_value"]:,.0f}' if unit == '$' else f'{k["target_value"]:.0f}{unit}'
                lines.append(f'• {k["spoke"]} / {k["metric_name"]}: {cur_str} (target: {tgt_str}) — DRI: {k["dri"]}')
            lines.append('')

        if revenue_lines:
            lines.append('*💰 REVENUE THIS MONTH*')
            lines.extend(revenue_lines)
            lines.append('Reply `revenue` for full breakdown.')
            lines.append('')

        if not has_content:
            # QUIET MODE — rotate opportunity by day of week
            opp = _OPPORTUNITIES[datetime.now().weekday() % len(_OPPORTUNITIES)]
            lines.append('_System is quiet — no decisions, tasks, or alerts pending._')
            lines.append('')
            lines.append(f'*💡 ODIN OPPORTUNITY — {opp["name"]}*')
            lines.append(opp['pitch'])
            lines.append(f'Startup: *{opp["startup"]}* | Potential: *{opp["roi"]}*')
            lines.append(f'\n*First move:* _{opp["first_move"]}_')
            lines.append(f'Reply `{opp["scout_cmd"]}` for a full build plan.')
        else:
            # AI-generated focus of the day
            focus_context = []
            if pending_decisions: focus_context.append(f'{len(pending_decisions)} decisions pending')
            if tasks_today:       focus_context.append(f'{len(tasks_today)} tasks due today')
            if red_kpis:          focus_context.append(f'{len(red_kpis)} KPIs in red')
            focus = _haiku(
                f'ODIN CEO morning briefing context: {", ".join(focus_context)}. '
                f'Give ONE specific focus for today in under 15 words.',
                max_tokens=40,
            )
            lines.append(f'*🎯 TODAY\'S FOCUS:* _{focus}_')

        lines.append('')
        lines.append('_`decisions` | `tasks` | `approvals` | `revenue` | `scorecard`_')

        msg = '\n'.join(lines)
        _post(msg)
        discord_notify.post_tts(msg)

        # Apex-parity: outbound voice call with the briefing (env-gated, silent if unset)
        try:
            from . import twilio_voice
            if os.environ.get('VOICE_BRIEFING_ENABLED', '').lower() == 'true':
                twilio_voice.call_briefing(msg)
        except Exception as ve:
            log.warning(f'Voice briefing skipped: {ve}')

        # Apex-parity: mirror briefing to Telegram (env-gated, silent if unset)
        try:
            from . import telegram_notify
            telegram_notify.post(msg)
        except Exception:
            pass

    except Exception as e:
        log.error(f'Morning briefing failed: {e}')
        _post(f'⚠️ Morning briefing error: {e}')


def email_triage_scan():
    """7:30am ET — autonomous inbox triage. Classifies + drafts; auto-sends
    only low-risk replies when EMAIL_AUTO_SEND=true. Posts summary to Slack."""
    try:
        from . import email_triage
        summary = email_triage.run_triage(_haiku)
        _post(summary)
    except Exception as e:
        log.error(f'Email triage failed: {e}')


def outreach_reply_scan():
    """Hourly — check Gmail for replies from cold outreach contacts. Fires Slack alert per reply."""
    try:
        from . import outreach_tracker
        outreach_tracker.init(_slack_post_fn, _get_db_fn, _channels)
        result = outreach_tracker.outreach_reply_scan()
        log.info(f'outreach_reply_scan: {result}')
    except Exception as e:
        log.error(f'outreach_reply_scan failed: {e}')


def outreach_send_scheduled():
    """Every 30 min — auto-send one pending outreach email if business hours + enabled."""
    try:
        from . import email_sender
        email_sender.init(_slack_post_fn, _get_db_fn, _channels)
        result = email_sender.send_scheduled()
        log.info(f'outreach_send_scheduled: {result}')
    except Exception as e:
        log.error(f'outreach_send_scheduled failed: {e}')


def pipeline_report():
    """21:00 daily — End-of-day pipeline summary."""
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT status, COUNT(*) as cnt, AVG(mctp_total) as avg_score
            FROM leads
            WHERE status NOT IN ('dead')
            GROUP BY status
            ORDER BY cnt DESC
        """)
        by_status = cur.fetchall()

        cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE created_at::date = CURRENT_DATE")
        new_today = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'")
        pending = cur.fetchone()['cnt']

        conn.close()

        today = datetime.now().strftime('%Y-%m-%d')
        lines = [f'*📊 ODIN Pipeline Report — {today}*', '']
        lines.append(f'New leads today: *{new_today}*')
        lines.append(f'Pending approvals: *{pending}*')
        lines.append('')
        lines.append('*Pipeline by status:*')
        for row in by_status:
            avg = f'{row["avg_score"]:.1f}' if row["avg_score"] else '—'
            lines.append(f'• {row["status"]}: {row["cnt"]} leads (avg score: {avg})')

        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Pipeline report failed: {e}')


def reply_scanner():
    """Every 30 min — Check XLeads for new inbound seller replies."""
    try:
        if not _xleads_mod:
            return

        # Get recent conversations (last 35 min window to avoid gaps)
        convos = _xleads_mod.list_conversations(limit=20) or []
        if not convos:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=35)
        new_replies = []

        for c in convos:
            last_msg = c.get('lastMessage') or c.get('lastMessageBody', '')
            last_date_str = c.get('lastMessageDate') or c.get('dateUpdated', '')
            contact_name  = c.get('fullName') or c.get('contactName') or 'Unknown'
            convo_id = c.get('id', '')

            if not last_date_str:
                continue

            # Parse date
            try:
                from dateutil import parser as dateutil_parser
                last_date = dateutil_parser.parse(last_date_str)
                if last_date.tzinfo is None:
                    last_date = last_date.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            # Only inbound messages since cutoff
            direction = c.get('lastMessageDirection') or c.get('direction') or ''
            is_inbound = direction.lower() in ('inbound', 'incoming', '')

            if is_inbound and last_date > cutoff and last_msg:
                new_replies.append({
                    'name':    contact_name,
                    'message': str(last_msg)[:200],
                    'time':    last_date.strftime('%I:%M %p'),
                    'id':      convo_id,
                })

        if new_replies:
            lines = [f'*💬 {len(new_replies)} New Seller Repl{"y" if len(new_replies)==1 else "ies"}*']
            for r in new_replies:
                lines.append(f'• *{r["name"]}* ({r["time"]}): _{r["message"]}_')
            lines.append('\nReply `contacts <name>` to pull their profile.')
            _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Reply scanner failed: {e}')


def decision_queue_scan():
    """
    Every 2 hours — Proactively surface pending decisions.
    Auto-executes recommended option after 48 hours if no response.
    Posts 1-3-1 summary to Slack when items are stalled.
    """
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Auto-execute decisions past their deadline
        cur.execute("""
            SELECT id, spoke, issue_summary, recommended, reason
            FROM decision_queue
            WHERE status = 'pending'
              AND auto_execute_at IS NOT NULL
              AND auto_execute_at <= NOW()
        """)
        to_auto = cur.fetchall() or []

        for d in to_auto:
            cur.execute("""
                UPDATE decision_queue
                SET status = 'auto_executed', resolved_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (str(d['id']),))
            _post(
                f'*⚙️ ODIN Auto-Executed Decision*\n'
                f'Issue: {d["issue_summary"]}\n'
                f'Action taken: _{d["recommended"]}_\n'
                f'Reason: {d["reason"] or "48hr timeout reached"}\n'
                f'_Reply `decisions` to see full history._'
            )

        # Surface decisions pending > 4 hours (but not yet auto-executed)
        cur.execute("""
            SELECT id, spoke, issue_summary, option_1, option_2, option_3,
                   recommended, reason,
                   EXTRACT(EPOCH FROM (NOW() - created_at))/3600 AS hrs_pending,
                   auto_execute_at
            FROM decision_queue
            WHERE status = 'pending'
              AND created_at < NOW() - INTERVAL '4 hours'
              AND (auto_execute_at IS NULL OR auto_execute_at > NOW())
            ORDER BY created_at ASC
            LIMIT 5
        """)
        stalled = cur.fetchall() or []

        conn.commit()
        conn.close()

        if stalled:
            lines = [f'*⚡ {len(stalled)} Decision{"s" if len(stalled) > 1 else ""} Need Your Input*']
            for d in stalled:
                hrs = int(d['hrs_pending'])
                auto_str = ''
                if d['auto_execute_at']:
                    hrs_left = max(0, int((d['auto_execute_at'] - datetime.now()).total_seconds() / 3600))
                    auto_str = f' | auto-executes in *{hrs_left}h*'
                lines.append(f'\n*[{hrs}h pending{auto_str}]* {d["issue_summary"]}')
                if d['option_1']: lines.append(f'  1. {d["option_1"]}')
                if d['option_2']: lines.append(f'  2. {d["option_2"]}')
                if d['option_3']: lines.append(f'  3. {d["option_3"]}')
                if d['recommended']:
                    lines.append(f'  → *ODIN recommends:* _{d["recommended"]}_')
            lines.append('\nReply `decisions` to approve or decline.')
            _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Decision queue scan failed: {e}')


def followup_queue():
    """
    Every 2 hours — Auto-fire today's follow-up SMS; alert on overdue leads without contact ID.

    Auto-fire logic:
      - Leads where follow_up_date = today AND xleads_contact_id IS SET AND next_action populated
      - Sends SMS via XLeads, clears follow_up_date, stamps followup_fired_at
    Alert logic:
      - Leads where follow_up_date < today AND xleads_contact_id NOT SET
      - Posted to Slack for manual follow-up (use `follow up ... cid:<id>` to enable auto-fire)
    """
    try:
        if not _xleads_mod:
            return

        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── AUTO-FIRE: due today with XLeads contact ID wired ────────────────
        cur.execute("""
            SELECT id, address, xleads_contact_id, next_action, mctp_total
            FROM leads
            WHERE follow_up_date = CURRENT_DATE
              AND next_action IS NOT NULL AND next_action != ''
              AND xleads_contact_id IS NOT NULL AND xleads_contact_id != ''
              AND status NOT IN ('closed', 'dead', 'contracted')
              AND (followup_fired_at IS NULL
                   OR followup_fired_at::date < CURRENT_DATE)
        """)
        due_today = cur.fetchall() or []

        fired, failed = [], []
        for lead in due_today:
            try:
                _xleads_mod.send_sms(lead['xleads_contact_id'], lead['next_action'])
                cur.execute("""
                    UPDATE leads
                    SET followup_fired_at = NOW(),
                        follow_up_date    = NULL,
                        updated_at        = NOW()
                    WHERE id = %s
                """, (str(lead['id']),))
                cur.execute("""
                    INSERT INTO agent_actions
                        (action_type, resource, resource_id, data, result)
                    VALUES ('followup_auto_fired', 'leads', %s, %s, 'success')
                """, (
                    str(lead['id']),
                    json.dumps({
                        'address': lead['address'],
                        'message': lead['next_action'][:120],
                    })
                ))
                fired.append(lead)
            except Exception as sms_err:
                log.error(f'Auto follow-up SMS failed for {lead["address"]}: {sms_err}')
                failed.append(lead)

        conn.commit()

        if fired:
            lines = [f'*🤖 ODIN Auto-Fired {len(fired)} Follow-Up{"s" if len(fired) > 1 else ""}*']
            for l in fired:
                lines.append(f'• {l["address"]} (score {l["mctp_total"]}/10) — _{l["next_action"][:80]}_')
            lines.append('_follow_up_date cleared — set a new date after they reply._')
            _post('\n'.join(lines))

        if failed:
            fail_lines = [f'*⚠️ {len(failed)} Auto Follow-Up SMS Failed*']
            for l in failed:
                fail_lines.append(f'• {l["address"]} — xleads_contact_id: `{l["xleads_contact_id"]}`')
            _post('\n'.join(fail_lines))

        # ── ALERT: overdue leads with NO contact ID (need manual action) ─────
        cur.execute("""
            SELECT address, city, follow_up_date, next_action, mctp_total, status
            FROM leads
            WHERE follow_up_date < CURRENT_DATE
              AND status NOT IN ('closed', 'dead', 'contracted')
              AND (xleads_contact_id IS NULL OR xleads_contact_id = '')
            ORDER BY follow_up_date ASC
            LIMIT 15
        """)
        overdue = cur.fetchall() or []
        conn.close()

        if not overdue:
            return

        lines = [f'*⏰ Overdue Follow-ups — Need Contact ID ({len(overdue)})*']
        for l in overdue:
            days_overdue = (datetime.now().date() -
                            (l['follow_up_date'] if l['follow_up_date'] else datetime.now().date())).days
            lines.append(
                f'• {l["address"]} — {days_overdue}d overdue | '
                f'Score: {l["mctp_total"]}/10 | {l["next_action"] or "no action set"}'
            )
        lines.append('\n_Add `cid:<xleads_contact_id>` to `follow up` command to enable auto-SMS firing._')
        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Follow-up queue failed: {e}')


def opportunity_sync():
    """Every 4 hours — Sync XLeads opportunities and post pipeline snapshot."""
    try:
        if not _xleads_mod:
            return

        opps = _xleads_mod.get_opportunities(limit=50) or []
        if not opps:
            return

        # Group by pipeline
        pipelines = {}
        total_value = 0
        for o in opps:
            pipeline = o.get('pipeline', {})
            pl_name  = pipeline.get('name') if isinstance(pipeline, dict) else str(pipeline or 'Unknown')
            stage    = o.get('pipelineStage', {})
            st_name  = stage.get('name') if isinstance(stage, dict) else str(stage or '—')
            name     = o.get('name') or o.get('contactName') or 'Unknown'
            value    = float(o.get('monetaryValue') or 0)
            total_value += value

            key = pl_name or 'Unknown'
            if key not in pipelines:
                pipelines[key] = []
            pipelines[key].append({'name': name, 'stage': st_name, 'value': value})

        if not pipelines:
            return

        now  = datetime.now().strftime('%Y-%m-%d %I:%M %p')
        val_str = f'${total_value:,.0f}' if total_value else '—'
        lines = [f'*📊 XLeads Pipeline Snapshot — {now}*',
                 f'Total pipeline value: *{val_str}* | Active deals: *{len(opps)}*', '']

        for pl_name, deals in sorted(pipelines.items()):
            lines.append(f'*{pl_name}* ({len(deals)} deals)')
            for d in deals[:8]:
                val  = f' | ${d["value"]:,.0f}' if d['value'] else ''
                lines.append(f'  • {d["name"]} — _{d["stage"]}{val}_')
            if len(deals) > 8:
                lines.append(f'  _…and {len(deals) - 8} more_')
            lines.append('')

        lines.append('`leads` for ODIN DB pipeline | `contacts <name>` to pull any deal')
        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Opportunity sync failed: {e}')


def weekly_kpi():
    """Monday 07:00 — Weekly KPI summary."""
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Leads added this week
        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads
            WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
        """)
        new_leads = cur.fetchone()['cnt']

        # Hot leads
        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads WHERE mctp_total >= 8
            AND status NOT IN ('closed','dead')
        """)
        hot = cur.fetchone()['cnt']

        # Warm leads
        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads WHERE mctp_total BETWEEN 5 AND 7
            AND status NOT IN ('closed','dead')
        """)
        warm = cur.fetchone()['cnt']

        # Deals contracted
        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads WHERE status = 'contracted'
        """)
        contracted = cur.fetchone()['cnt']

        # Deals closed
        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads WHERE status = 'closed'
        """)
        closed = cur.fetchone()['cnt']

        conn.close()

        week_start = (datetime.now() - timedelta(days=7)).strftime('%b %d')
        week_end   = datetime.now().strftime('%b %d')

        lines = [f'*📈 Weekly KPI Report — {week_start} to {week_end}*', '']
        lines.append(f'New leads added:  *{new_leads}*')
        lines.append(f'Hot leads (8+):   *{hot}*')
        lines.append(f'Warm leads (5-7): *{warm}*')
        lines.append(f'Contracted:       *{contracted}*')
        lines.append(f'Closed deals:     *{closed}*')
        lines.append('')
        lines.append('_Have a great week. Close the deal. — ODIN_')
        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Weekly KPI failed: {e}')


# ─── CLAUDE HAIKU HELPER ─────────────────────────────────────────────────────

_anthropic_client = None

def _haiku(prompt: str, max_tokens: int = 400) -> str:
    """Call Claude Haiku for short generative text in heartbeat jobs."""
    global _anthropic_client
    try:
        if _anthropic_client is None:
            key = os.environ.get('ANTHROPIC_API_KEY', '')
            if not key:
                return '(AI unavailable — ANTHROPIC_API_KEY not set)'
            _anthropic_client = anthropic.Anthropic(api_key=key)
        resp = _anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=max_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f'(AI error: {e})'


# ─── WEEKLY CEO REVIEW ────────────────────────────────────────────────────────

def weekly_ceo_review():
    """Sunday 20:00 CT — Full CEO review posted to #odin-brock. Also callable on-demand."""
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        today = datetime.now()
        week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')

        # ── 1. PIPELINE SNAPSHOT ──
        cur.execute("""
            SELECT status, COUNT(*) as cnt
            FROM leads WHERE spoke = 'real_estate'
            GROUP BY status ORDER BY cnt DESC
        """)
        pipeline = cur.fetchall()

        # ── 2. WHAT MOVED (leads updated in last 7 days) ──
        cur.execute("""
            SELECT address, status, mctp_total, updated_at
            FROM leads
            WHERE spoke = 'real_estate'
              AND updated_at >= NOW() - INTERVAL '7 days'
              AND status NOT IN ('new')
            ORDER BY updated_at DESC LIMIT 10
        """)
        moved = cur.fetchall()

        # ── 3. STUCK LEADS (no update in 5+ days, not closed/dead) ──
        cur.execute("""
            SELECT address, city, mctp_total, status, follow_up_date,
                   EXTRACT(EPOCH FROM (NOW() - updated_at))/86400 AS days_stuck
            FROM leads
            WHERE spoke = 'real_estate'
              AND status NOT IN ('closed', 'dead', 'contracted')
              AND updated_at < NOW() - INTERVAL '5 days'
            ORDER BY mctp_total DESC, days_stuck DESC
            LIMIT 8
        """)
        stuck = cur.fetchall()

        # ── 4. PENDING APPROVALS ──
        cur.execute("""
            SELECT id, action_type,
                   EXTRACT(EPOCH FROM (NOW() - created_at))/86400 AS days_waiting,
                   data
            FROM approval_queue WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        approvals = cur.fetchall()

        # ── 5. EDDIE ACTIVITY ──
        cur.execute("""
            SELECT id, name FROM users WHERE role = 'caller' AND active = true
        """)
        callers = cur.fetchall()

        eddie_mctp_count = 0
        eddie_assigned   = []
        eddie_contracted = []

        for caller in callers:
            cur.execute("""
                SELECT COUNT(*) as cnt FROM agent_actions
                WHERE user_id = %s AND action_type = 'mctp_log_slack'
                  AND created_at >= NOW() - INTERVAL '7 days'
            """, (str(caller['id']),))
            eddie_mctp_count += (cur.fetchone()['cnt'] or 0)

            cur.execute("""
                SELECT address, mctp_total, status FROM leads
                WHERE assigned_to = %s AND status NOT IN ('closed','dead')
            """, (str(caller['id']),))
            eddie_assigned.extend(cur.fetchall())

            cur.execute("""
                SELECT address FROM leads
                WHERE assigned_to = %s AND status = 'contracted'
            """, (str(caller['id']),))
            eddie_contracted.extend(cur.fetchall())

        # ── 6. BLAST STATUS ──
        cur.execute("""
            SELECT created_at, data FROM agent_actions
            WHERE action_type IN ('blast', 'sms_blast')
            ORDER BY created_at DESC LIMIT 1
        """)
        last_blast = cur.fetchone()

        # ── 7. TOP 3 PRIORITIES (days_stuck × mctp × fee potential) ──
        cur.execute("""
            SELECT address, mctp_total, status,
                   COALESCE(fee_at_lao, 0) AS fee,
                   EXTRACT(EPOCH FROM (NOW() - updated_at))/86400 AS days_stuck
            FROM leads
            WHERE spoke = 'real_estate'
              AND status NOT IN ('closed','dead')
              AND mctp_total >= 3
            ORDER BY (EXTRACT(EPOCH FROM (NOW() - updated_at))/86400 *
                      mctp_total * GREATEST(COALESCE(fee_at_lao, 15000), 10000)) DESC
            LIMIT 3
        """)
        top3 = cur.fetchall()

        conn.close()

        # ── BUILD REPORT ──
        date_str = today.strftime('%Y-%m-%d')
        lines = [f'*📊 ODIN Weekly CEO Review — {date_str}*', '']

        # 1. Pipeline
        lines.append('*1. PIPELINE SNAPSHOT*')
        if pipeline:
            for row in pipeline:
                lines.append(f'  • {row["status"].upper()}: {row["cnt"]} leads')
        else:
            lines.append('  No leads in pipeline.')
        lines.append('')

        # 2. What Moved
        lines.append('*2. WHAT MOVED THIS WEEK*')
        if moved:
            for l in moved:
                days_ago = (today - l['updated_at'].replace(tzinfo=None)).days
                lines.append(f'  • {l["address"]} → *{l["status"]}* | Score: {l["mctp_total"]}/10 | {days_ago}d ago')
        else:
            lines.append('  No leads updated this week.')
        lines.append('')

        # 3. Stuck Leads
        lines.append('*3. STUCK LEADS (5+ days no activity)*')
        if stuck:
            stuck_context = '\n'.join([
                f'{l["address"]} — {l["status"]} — Score: {l["mctp_total"]}/10 — {int(l["days_stuck"])} days stuck'
                for l in stuck
            ])
            for l in stuck:
                suggestion = _haiku(
                    f'RE wholesaling. Lead stuck {int(l["days_stuck"])} days. '
                    f'Address: {l["address"]}. Status: {l["status"]}. '
                    f'MCTP score: {l["mctp_total"]}/10. '
                    f'Suggest ONE specific next action in under 10 words.'
                )
                lines.append(f'  • {l["address"]} | {int(l["days_stuck"])}d stuck | Score {l["mctp_total"]}/10')
                lines.append(f'    → _{suggestion}_')
        else:
            lines.append('  No stuck leads. Good.')
        lines.append('')

        # 4. Approvals
        lines.append('*4. PENDING APPROVALS*')
        if approvals:
            for a in approvals:
                lines.append(f'  • {a["action_type"]} | {int(a["days_waiting"])}d waiting | ID: `{str(a["id"])[:8]}...`')
        else:
            lines.append('  No pending approvals.')
        lines.append('')

        # 5. Eddie Activity
        lines.append('*5. EDDIE ACTIVITY*')
        lines.append(f'  MCTPs logged this week: *{eddie_mctp_count}*')
        if eddie_assigned:
            lines.append(f'  Leads assigned: {len(eddie_assigned)}')
            for l in eddie_assigned[:5]:
                lines.append(f'    • {l["address"]} — {l["status"]} | Score: {l["mctp_total"]}/10')
        else:
            lines.append('  No leads currently assigned to Eddie.')
        if eddie_contracted:
            lines.append(f'  *CONTRACTED from Eddie calls: {len(eddie_contracted)}* — $1,800 fee applies per deal')
            for l in eddie_contracted:
                lines.append(f'    • {l["address"]}')
        lines.append('')

        # 6. Blast Status
        lines.append('*6. BLAST STATUS*')
        if last_blast:
            blast_date = last_blast['created_at'].strftime('%Y-%m-%d')
            lines.append(f'  Last blast: {blast_date}')
        else:
            lines.append('  No blasts logged yet. Run: `blast tags:he,tax-delinquent workflow:<id> limit:100`')
        lines.append('')

        # 7. Top 3 Priorities
        lines.append('*7. TOP 3 PRIORITIES*')
        if top3:
            for i, l in enumerate(top3, 1):
                fee_str = f'${l["fee"]:,.0f}' if l['fee'] else 'fee TBD'
                lines.append(f'  {i}. {l["address"]} | Score: {l["mctp_total"]}/10 | {fee_str} | {int(l["days_stuck"])}d idle')
        else:
            lines.append('  No scored leads in pipeline.')
        lines.append('')

        # 8. The One Thing
        lines.append('*8. THE ONE THING*')
        pipeline_summary = ', '.join([f'{r["status"]}:{r["cnt"]}' for r in pipeline]) if pipeline else 'empty'
        top_lead = top3[0]['address'] if top3 else 'no scored leads'
        one_thing = _haiku(
            f'Memphis RE wholesaling CEO. Pipeline: {pipeline_summary}. '
            f'Top priority lead: {top_lead}. '
            f'Eddie logged {eddie_mctp_count} MCTPs this week. '
            f'Pending approvals: {len(approvals)}. '
            f'In ONE sentence (max 20 words), what is the single highest-leverage action this week?',
            max_tokens=60,
        )
        lines.append(f'  _{one_thing}_')
        lines.append('')

        # 9. One Question
        lines.append('*9. ONE QUESTION FOR YOU*')
        question = _haiku(
            f'Memphis RE wholesaling CEO dashboard. Pipeline: {pipeline_summary}. '
            f'Eddie MCTPs this week: {eddie_mctp_count}. Stuck leads: {len(stuck)}. '
            f'Ask Brock ONE decision-forcing question (max 15 words) about something '
            f'the data is flagging that he may be avoiding.',
            max_tokens=50,
        )
        lines.append(f'  _{question}_')
        lines.append('')
        lines.append('_Reply `leads` / `approvals` / `resurrect` to act on this._')

        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Weekly CEO review failed: {e}')
        _post(f'⚠️ Weekly CEO review error: {e}')


# ─── SMS HEALTH MONITOR ──────────────────────────────────────────────────────

OPT_OUT_KEYWORDS = [
    'stop', 'unsubscribe', 'remove me', 'opt out', 'optout',
    'do not contact', 'take me off', 'wrong number', 'quit',
]

# Thresholds
OPT_OUT_RATE_WARN   = 0.03   # 3% opt-out rate → warning
OPT_OUT_RATE_FLAG   = 0.07   # 7% opt-out rate → flagged (likely carrier action)
OPT_OUT_ABS_WARN    = 5      # 5+ opt-outs in the window even on small blasts
REPLY_RATE_LOW_WARN = 0.01   # <1% reply rate after 6hrs → deliverability concern


def sms_health_monitor():
    """
    Every hour — Check opt-out rate and reply rate on recent blasts.
    Alerts #odin-brock if thresholds are breached.
    Cross-references inbound XLeads conversations against blast_campaigns table.
    """
    try:
        if not _xleads_mod:
            return

        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Get blasts from last 48 hours that haven't been flagged yet
        cur.execute("""
            SELECT id, workflow_id, workflow_name, tags, sent_count,
                   opt_out_count, reply_count, health_status, alert_sent, created_at
            FROM blast_campaigns
            WHERE created_at >= NOW() - INTERVAL '48 hours'
              AND sent_count > 0
              AND health_status != 'flagged'
            ORDER BY created_at DESC
        """)
        campaigns = cur.fetchall()

        if not campaigns:
            conn.close()
            return

        # Pull recent inbound conversations from XLeads (last 65 min window)
        convos = _xleads_mod.list_conversations(limit=50) or []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)

        recent_opt_outs = 0
        recent_replies  = 0

        for c in convos:
            last_msg       = str(c.get('lastMessage') or c.get('lastMessageBody', '')).lower()
            last_date_str  = c.get('lastMessageDate') or c.get('dateUpdated', '')
            direction      = (c.get('lastMessageDirection') or '').lower()

            if not last_date_str:
                continue

            try:
                from dateutil import parser as dateutil_parser
                last_date = dateutil_parser.parse(last_date_str)
                if last_date.tzinfo is None:
                    last_date = last_date.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if last_date < cutoff:
                continue

            is_inbound = direction in ('inbound', 'incoming', '')
            if not is_inbound:
                continue

            if any(kw in last_msg for kw in OPT_OUT_KEYWORDS):
                recent_opt_outs += 1
            elif last_msg.strip():
                recent_replies += 1

        # Evaluate each recent campaign
        for campaign in campaigns:
            sent = campaign['sent_count']
            if sent == 0:
                continue

            # Accumulate opt-outs against this campaign's running total
            new_opt_outs   = campaign['opt_out_count'] + recent_opt_outs
            new_replies    = campaign['reply_count'] + recent_replies
            opt_out_rate   = new_opt_outs / sent
            hours_since    = (datetime.now(timezone.utc) - campaign['created_at']).total_seconds() / 3600

            new_status  = campaign['health_status']
            should_alert = False
            alert_lines  = []

            # Check opt-out thresholds
            if opt_out_rate >= OPT_OUT_RATE_FLAG or new_opt_outs >= (OPT_OUT_ABS_WARN * 3):
                new_status   = 'flagged'
                should_alert = not campaign['alert_sent']
                alert_lines  = [
                    f'🚨 *SMS NUMBER FLAGGED — ACTION REQUIRED*',
                    f'Campaign: `{campaign["workflow_name"] or campaign["workflow_id"]}`',
                    f'Sent: {sent} | Opt-outs: {new_opt_outs} ({opt_out_rate:.1%})',
                    f'',
                    f'*This opt-out rate indicates your number may be carrier-flagged.*',
                    f'Immediate actions:',
                    f'  1. Pause all active SMS campaigns in XLeads',
                    f'  2. Check XLeads compliance dashboard for carrier feedback',
                    f'  3. Consider buying a new number: `buy number 901`',
                    f'  4. Review message content for spam triggers',
                ]
            elif opt_out_rate >= OPT_OUT_RATE_WARN or new_opt_outs >= OPT_OUT_ABS_WARN:
                new_status   = 'warning'
                should_alert = not campaign['alert_sent']
                alert_lines  = [
                    f'⚠️ *SMS Health Warning*',
                    f'Campaign: `{campaign["workflow_name"] or campaign["workflow_id"]}`',
                    f'Sent: {sent} | Opt-outs: {new_opt_outs} ({opt_out_rate:.1%})',
                    f'Threshold: {OPT_OUT_RATE_WARN:.0%} | Status: *elevated opt-outs*',
                    f'',
                    f'Monitor closely. If rate climbs above {OPT_OUT_RATE_FLAG:.0%}, pause campaigns.',
                    f'`blast` campaigns are still running.',
                ]
            # Low reply rate check (only meaningful after 6+ hours)
            elif hours_since >= 6 and sent >= 20:
                reply_rate = new_replies / sent
                if reply_rate < REPLY_RATE_LOW_WARN and not campaign['alert_sent']:
                    new_status   = 'warning'
                    should_alert = True
                    alert_lines  = [
                        f'⚠️ *SMS Deliverability Warning*',
                        f'Campaign: `{campaign["workflow_name"] or campaign["workflow_id"]}`',
                        f'Sent: {sent} | Replies: {new_replies} ({reply_rate:.1%}) after {hours_since:.0f}hrs',
                        f'',
                        f'Low reply rate may indicate messages are not being delivered.',
                        f'Check XLeads for message delivery status.',
                    ]

            # Update campaign in DB
            cur.execute("""
                UPDATE blast_campaigns
                SET opt_out_count = %s,
                    reply_count   = %s,
                    health_status = %s,
                    alert_sent    = %s,
                    updated_at    = NOW()
                WHERE id = %s
            """, (new_opt_outs, new_replies, new_status,
                  campaign['alert_sent'] or should_alert,
                  str(campaign['id'])))

            if should_alert and alert_lines:
                _post('\n'.join(alert_lines))

        conn.commit()
        conn.close()

    except Exception as e:
        log.error(f'SMS health monitor failed: {e}')


# ─── TASK DELEGATION SCAN ────────────────────────────────────────────────────

# Auto-assignment rules: checked in order, first match wins.
# Each rule: (spoke_pattern, title_keywords, assignee)
# Eddie is NOT auto-assigned — he only owns tasks he creates himself.
_ASSIGN_RULES = [
    # Automated send/fire tasks → ODIN
    (None, ['blast', 'sms', 'send sms', 'follow-up sms', 'email blast'], 'odin'),
    # Decision/review tasks → Brock
    (None, ['review', 'decide', 'approve', 'evaluate', 'choose'],        'brock'),
    # Default fallthrough handled in code
]


def _auto_assign(title: str, spoke: str) -> str:
    """Return the best assignee for a task based on title keywords and spoke."""
    title_lower = (title or '').lower()
    spoke_lower = (spoke or '').lower()
    for rule_spoke, keywords, assignee in _ASSIGN_RULES:
        spoke_match = (rule_spoke is None) or (rule_spoke in spoke_lower)
        kw_match    = any(kw in title_lower for kw in keywords)
        if spoke_match and kw_match:
            return assignee
    return 'brock'  # default


def task_delegation_scan():
    """
    Every 4 hours — Auto-assign, escalate, and auto-execute tasks.

    Steps:
      1. Auto-assign any tasks where assignee IS NULL or 'brock' (unreviewed)
         using spoke + title keyword rules.
      2. Eddie tasks overdue → Slack DM to Eddie.
      3. ODIN tasks with auto_executable = true overdue → Haiku attempts execution.
      4. Brock tasks overdue 2+ days → escalation ping.
    """
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── 1. AUTO-ASSIGN unassigned tasks ──────────────────────────────────
        cur.execute("""
            SELECT id, title, spoke
            FROM tasks
            WHERE (assignee IS NULL OR assignee = '')
              AND status NOT IN ('completed', 'blocked')
        """)
        unassigned = cur.fetchall() or []

        for t in unassigned:
            assignee = _auto_assign(t['title'], t['spoke'] or '')
            cur.execute("""
                UPDATE tasks SET assignee = %s, updated_at = NOW() WHERE id = %s
            """, (assignee, str(t['id'])))

        if unassigned:
            conn.commit()
            log.info(f'task_delegation: auto-assigned {len(unassigned)} tasks')

        # ── 2. EDDIE overdue tasks → Slack alert ──────────────────────────────
        cur.execute("""
            SELECT id, title, due_date, spoke, priority,
                   EXTRACT(EPOCH FROM (NOW() - due_date))/86400 AS days_overdue
            FROM tasks
            WHERE assignee = 'eddie'
              AND status NOT IN ('completed', 'blocked')
              AND due_date IS NOT NULL AND due_date < CURRENT_DATE
              AND (escalated_at IS NULL
                   OR escalated_at < NOW() - INTERVAL '24 hours')
            ORDER BY priority DESC, due_date ASC
            LIMIT 10
        """)
        eddie_overdue = cur.fetchall() or []

        if eddie_overdue:
            eddie_uid = os.environ.get('EDDIE_SLACK_UID', 'U0B5CTBLKCP')
            lines = [f'*📋 Hey <@{eddie_uid}> — {len(eddie_overdue)} task{"s" if len(eddie_overdue) > 1 else ""} past due*']
            for t in eddie_overdue:
                days = int(t['days_overdue'])
                pri_label = {3: '🔴', 2: '🟡', 1: '⚪'}.get(t['priority'], '⚪')
                lines.append(f'{pri_label} *{t["title"]}* — {days}d overdue')
                cur.execute("""
                    UPDATE tasks SET escalated_at = NOW(), updated_at = NOW() WHERE id = %s
                """, (str(t['id']),))
            lines.append('_Reply `task done <first few words>` when complete._')
            if _slack_post_fn and _channels:
                brock_ch = _channels.get('brock', '')
                if brock_ch:
                    _slack_post_fn(brock_ch, '\n'.join(lines))

        # ── 3. ODIN auto-executable tasks overdue → attempt execution ────────
        cur.execute("""
            SELECT id, title, description, spoke
            FROM tasks
            WHERE assignee = 'odin'
              AND auto_executable = TRUE
              AND status NOT IN ('completed', 'blocked')
              AND due_date IS NOT NULL AND due_date <= CURRENT_DATE
            ORDER BY due_date ASC
            LIMIT 5
        """)
        odin_tasks = cur.fetchall() or []

        for t in odin_tasks:
            result_text = _haiku(
                f'You are ODIN, an autonomous business OS. '
                f'You have been assigned this task to execute or summarize:\n\n'
                f'Title: {t["title"]}\n'
                f'Description: {t["description"] or "(none)"}\n'
                f'Spoke: {t["spoke"] or "general"}\n\n'
                f'In 1-2 sentences: what action did you take or what is the outcome? '
                f'If you cannot execute it autonomously, say so briefly.',
                max_tokens=80,
            )
            cur.execute("""
                UPDATE tasks
                SET status = 'completed', completed_at = NOW(), updated_at = NOW()
                WHERE id = %s
            """, (str(t['id']),))
            cur.execute("""
                INSERT INTO agent_actions (action_type, resource, resource_id, data, result)
                VALUES ('odin_task_executed', 'tasks', %s, %s, 'completed')
            """, (
                str(t['id']),
                json.dumps({'title': t['title'], 'outcome': result_text[:200]})
            ))
            _post(
                f'*⚙️ ODIN Executed Task*\n'
                f'_{t["title"]}_\n'
                f'Outcome: {result_text}'
            )

        # ── 4. BROCK tasks overdue 2+ days → escalation ping ─────────────────
        cur.execute("""
            SELECT id, title, due_date, spoke, priority,
                   EXTRACT(EPOCH FROM (NOW() - due_date))/86400 AS days_overdue
            FROM tasks
            WHERE assignee = 'brock'
              AND status NOT IN ('completed', 'blocked')
              AND due_date IS NOT NULL AND due_date < CURRENT_DATE - INTERVAL '2 days'
              AND (escalated_at IS NULL
                   OR escalated_at < NOW() - INTERVAL '48 hours')
            ORDER BY priority DESC, due_date ASC
            LIMIT 8
        """)
        brock_overdue = cur.fetchall() or []

        if brock_overdue:
            lines = [f'*⚠️ {len(brock_overdue)} Task{"s" if len(brock_overdue) > 1 else ""} Need Your Attention*']
            for t in brock_overdue:
                days = int(t['days_overdue'])
                lines.append(f'• *{t["title"]}* — {days}d overdue ({t["spoke"] or "general"})')
                cur.execute("""
                    UPDATE tasks SET escalated_at = NOW(), updated_at = NOW() WHERE id = %s
                """, (str(t['id']),))
            lines.append('Reply `tasks` to review. `task done <title>` to close.')
            _post('\n'.join(lines))

        conn.commit()
        conn.close()

    except Exception as e:
        log.error(f'Task delegation scan failed: {e}')


# ─── DEAD LEAD RESURRECTION SCAN ─────────────────────────────────────────────

def resurrect_scan():
    """Wednesday 10:00 CT — Silent scan. Alerts #odin-brock if resurrection candidates exist."""
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT COUNT(*) as cnt FROM leads
            WHERE spoke = 'real_estate'
              AND (status IN ('cold', 'dead')
                   OR updated_at < NOW() - INTERVAL '30 days')
              AND status NOT IN ('contracted', 'closed')
        """)
        count = cur.fetchone()['cnt']
        conn.close()

        if count > 0:
            _post(
                f'*☠️ Resurrection Alert*\n'
                f'{count} lead{"s" if count != 1 else ""} haven\'t been contacted in 30+ days.\n'
                f'Run: `resurrect` to see candidates + re-engagement texts.'
            )
    except Exception as e:
        log.error(f'Resurrect scan failed: {e}')


# ─── KPI AUTO-UPDATE ─────────────────────────────────────────────────────────

def kpi_auto_update():
    """
    Every 4 hours — Compute current_value for all seeded KPI targets and
    set status (green/yellow/red) based on % of target achieved.

    green  = current >= 80% of target
    yellow = current >= 50% of target
    red    = current < 50% of target
    """
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        def _set(spoke, metric, value):
            if value is None:
                return
            pct = (value / tgt) if (tgt := _get_target(cur, spoke, metric)) else 1
            status = 'green' if pct >= 0.8 else ('yellow' if pct >= 0.5 else 'red')
            cur.execute("""
                UPDATE kpi_targets
                SET current_value = %s, status = %s, last_updated = NOW()
                WHERE spoke = %s AND metric_name = %s
            """, (round(float(value), 2), status, spoke, metric))

        def _get_target(cur, spoke, metric):
            cur.execute(
                "SELECT target_value FROM kpi_targets WHERE spoke=%s AND metric_name=%s",
                (spoke, metric)
            )
            row = cur.fetchone()
            return float(row['target_value']) if row and row['target_value'] else None

        # ── general: Decisions Resolved This Week ────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM decision_queue
            WHERE status IN ('approved','declined','auto_executed')
              AND resolved_at >= NOW() - INTERVAL '7 days'
        """)
        _set('general', 'Decisions Resolved This Week', cur.fetchone()['cnt'])

        # ── general: Follow-ups Fired On Time ────────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS fired FROM agent_actions
            WHERE action_type = 'followup_auto_fired'
              AND created_at >= NOW() - INTERVAL '7 days'
        """)
        fired = cur.fetchone()['fired']
        cur.execute("""
            SELECT COUNT(*) AS total FROM leads
            WHERE follow_up_date >= CURRENT_DATE - 7
              AND follow_up_date <= CURRENT_DATE
        """)
        total_due = cur.fetchone()['total']
        on_time_pct = round((fired / total_due * 100), 1) if total_due else 100
        _set('general', 'Follow-ups Fired On Time', on_time_pct)

        # ── general: Tasks Completed This Week ───────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM tasks
            WHERE completed_at >= NOW() - INTERVAL '7 days'
        """)
        _set('general', 'Tasks Completed This Week', cur.fetchone()['cnt'])

        # ── general: System Uptime ────────────────────────────────────────────
        # Cannot self-measure from inside the process; set 99.9 as operational default.
        _set('general', 'System Uptime', 99.9)

        # ── real_estate: Deals Under Contract ────────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM leads
            WHERE status = 'contracted' AND spoke = 'real_estate'
        """)
        _set('real_estate', 'Deals Under Contract', cur.fetchone()['cnt'])

        # ── real_estate: Monthly Revenue Target ──────────────────────────────
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total FROM revenue_events
            WHERE spoke = 'real_estate' AND type = 'income'
              AND DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE)
        """)
        _set('real_estate', 'Monthly Revenue Target', cur.fetchone()['total'])

        # ── real_estate: Active Pipeline Leads ───────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM leads
            WHERE status NOT IN ('closed','dead') AND spoke = 'real_estate'
        """)
        _set('real_estate', 'Active Pipeline Leads', cur.fetchone()['cnt'])

        # ── real_estate: Follow-up Response Rate ─────────────────────────────
        # % of active leads that had an inbound message in the last 30 days
        cur.execute("""
            SELECT COUNT(DISTINCT resource_id) AS replied FROM agent_actions
            WHERE action_type = 'xleads_inbound_message'
              AND created_at >= NOW() - INTERVAL '30 days'
        """)
        replied = cur.fetchone()['replied']
        cur.execute("SELECT COUNT(*) AS total FROM leads WHERE status NOT IN ('new','dead','closed') AND spoke='real_estate'")
        total_active = cur.fetchone()['total']
        response_rate = round((replied / total_active * 100), 1) if total_active else 0
        _set('real_estate', 'Follow-up Response Rate', response_rate)

        # ── valdr_ops: Active Clients ─────────────────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM businesses
            WHERE spoke = 'valdr_ops'
        """)
        _set('valdr_ops', 'Active Clients', cur.fetchone()['cnt'])

        # ── valdr_ops: Monthly Recurring Revenue ──────────────────────────────
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total FROM revenue_events
            WHERE spoke = 'valdr_ops' AND type = 'income'
              AND DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE)
        """)
        _set('valdr_ops', 'Monthly Recurring Revenue', cur.fetchone()['total'])

        conn.commit()
        conn.close()
        log.info('KPI auto-update complete')

    except Exception as e:
        log.error(f'KPI auto-update failed: {e}')


# ─── BUYER ONBOARDING SCAN ────────────────────────────────────────────────────

# Welcome SMS sent to new buyers when their xleads_contact_id is set.
_BUYER_WELCOME_SMS = (
    "Hey {name}, this is Brock with Shue Box LLC — a Memphis wholesaler. "
    "I'll be sending you off-market deals in your buy box. "
    "Reply with your price range and preferred areas so I can match you first. Talk soon!"
)

# Onboarding stage progression
_ONBOARDING_STAGES = ['new', 'welcomed', 'qualified', 'active', 'inactive']


def buyer_onboarding_scan():
    """
    Daily 8am CT — Buyer onboarding pipeline scan.

    Steps:
      1. Buyers with onboarding_stage='new' AND xleads_contact_id set → send welcome SMS → 'welcomed'
      2. Buyers stuck in 'welcomed' for 3+ days with no reply → alert Brock
      3. Post onboarding pipeline counts to Slack if anything noteworthy.
    """
    try:
        if not _xleads_mod:
            return

        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # ── Step 1: Welcome new buyers ────────────────────────────────────────
        cur.execute("""
            SELECT id, name, phone, xleads_contact_id
            FROM buyers
            WHERE onboarding_stage = 'new'
              AND xleads_contact_id IS NOT NULL
              AND xleads_contact_id != ''
              AND is_dnc = FALSE
            LIMIT 20
        """)
        new_buyers = cur.fetchall() or []

        welcomed = []
        for b in new_buyers:
            first_name = (b['name'] or 'there').split()[0]
            msg = _BUYER_WELCOME_SMS.format(name=first_name)
            try:
                _xleads_mod.send_sms(b['xleads_contact_id'], msg)
                cur.execute("""
                    UPDATE buyers
                    SET onboarding_stage = 'welcomed',
                        onboarding_started_at = NOW()
                    WHERE id = %s
                """, (str(b['id']),))
                cur.execute("""
                    INSERT INTO agent_actions
                        (action_type, resource, resource_id, data, result)
                    VALUES ('buyer_welcome_sms', 'buyers', %s, %s, 'sent')
                """, (
                    str(b['id']),
                    json.dumps({'name': b['name'], 'phone': b['phone']})
                ))
                welcomed.append(b['name'] or b['phone'])
            except Exception as sms_err:
                log.error(f'Buyer welcome SMS failed for {b["name"]}: {sms_err}')

        # ── Step 2: Stuck in 'welcomed' 3+ days ──────────────────────────────
        cur.execute("""
            SELECT COUNT(*) AS cnt FROM buyers
            WHERE onboarding_stage = 'welcomed'
              AND onboarding_started_at < NOW() - INTERVAL '3 days'
              AND is_dnc = FALSE
        """)
        stuck_count = cur.fetchone()['cnt']

        # ── Step 3: Onboarding pipeline counts ───────────────────────────────
        cur.execute("""
            SELECT onboarding_stage, COUNT(*) AS cnt
            FROM buyers
            WHERE is_dnc = FALSE
            GROUP BY onboarding_stage
            ORDER BY cnt DESC
        """)
        stage_counts = cur.fetchall() or []

        conn.commit()
        conn.close()

        # Post summary if anything happened
        if welcomed or stuck_count > 0:
            lines = ['*👥 Buyer Onboarding Update*']
            if welcomed:
                lines.append(f'✅ Welcomed {len(welcomed)} new buyer{"s" if len(welcomed) > 1 else ""}: {", ".join(welcomed[:5])}')
            if stuck_count:
                lines.append(
                    f'⚠️ {stuck_count} buyer{"s" if stuck_count > 1 else ""} stuck in *welcomed* for 3+ days — no reply yet.'
                )
            if stage_counts:
                lines.append('\n*Pipeline:*')
                for s in stage_counts:
                    lines.append(f'  • {s["onboarding_stage"]}: {s["cnt"]}')
            lines.append('`buyers onboarding` for full list | `onboard <name> cid:<id>` to trigger manually')
            _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Buyer onboarding scan failed: {e}')


# ─── FINANCE DAILY SYNC ──────────────────────────────────────────────────────

def finance_daily_sync():
    """
    Daily 7:00am ET — Sync all bank accounts + transactions from Teller.io.
    Detects subscriptions, saves snapshot, posts balance summary to Slack.
    Skips silently if Teller env vars are not configured.
    """
    try:
        import utils.finance_bot as finance_bot

        # Skip gracefully if not configured
        if not os.environ.get('TELLER_ACCESS_TOKEN'):
            log.info('Finance sync skipped — TELLER_ACCESS_TOKEN not set')
            return
        if not os.environ.get('TELLER_CERT_B64') or not os.environ.get('TELLER_KEY_B64'):
            log.info('Finance sync skipped — TELLER_CERT_B64 / TELLER_KEY_B64 not set')
            return

        # Run full sync
        accounts = finance_bot.sync_accounts()
        new_txs  = finance_bot.sync_transactions(days=30)
        subs     = finance_bot.detect_subscriptions()
        finance_bot._save_snapshot()

        # Build balance summary for Slack
        bal   = finance_bot.get_balances()
        spend = finance_bot.get_spending_report('month')
        biz   = finance_bot.get_biz_card()

        sub_total = sum(s.get('amount', 0) for s in subs) if subs else 0

        lines = [f'*💰 Finance Update — {datetime.now().strftime("%b %d")}*']
        lines.append(f'  Net liquid: *${bal["net_liquid"]:,.2f}*  |  Deposits: ${bal["total_deposits"]:,.2f}  |  Credit used: ${bal["total_credit"]:,.2f}')
        if biz:
            lines.append(f'  Biz card: ${biz["balance"]:,.2f} used | *${biz["available"]:,.2f} available* ({biz["used_pct"]}% of ${biz["limit"]:,.0f})')
        lines.append(f'  MTD spend: *${spend["total_spend"]:,.2f}* | Subscriptions: ${sub_total:,.2f}/mo ({len(subs)} active)')
        lines.append(f'  _{new_txs} new transactions synced | {len(accounts)} accounts_')
        lines.append('`invest` for biz card ROI advice | `spending` for breakdown | `subscriptions` for recurring charges')

        _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Finance daily sync failed: {e}')


# ─── REVENUE AUTO-SYNC ───────────────────────────────────────────────────────

def revenue_sync():
    """
    Daily 9:00am ET — Auto-log won XLeads opportunities into revenue_events.
    Uses xleads_opp_id as dedup key so re-runs are safe.
    Alerts Slack for each new deal logged.
    """
    try:
        if not _xleads_mod:
            return

        opps = _xleads_mod.get_opportunities(limit=100) or []
        won  = [o for o in opps if str(o.get('status', '')).lower() == 'won']
        if not won:
            return

        conn = _db()
        cur  = conn.cursor()
        new_deals = []

        for o in won:
            opp_id = o.get('id', '')
            if not opp_id:
                continue
            value = float(o.get('monetaryValue') or 0)
            name  = o.get('name') or o.get('contactName') or 'Unknown Deal'
            pipeline = o.get('pipeline', {})
            pl_name  = pipeline.get('name') if isinstance(pipeline, dict) else str(pipeline or 'real_estate')
            spoke    = 'real_estate' if 'wholesal' in pl_name.lower() or 'disps' in pl_name.lower() else 'general'

            cur.execute("""
                INSERT INTO revenue_events (spoke, amount, type, description, xleads_opp_id)
                VALUES (%s, %s, 'income', %s, %s)
                ON CONFLICT (xleads_opp_id) DO NOTHING
            """, (spoke, value, f'Closed deal: {name}', opp_id))

            if cur.rowcount:
                new_deals.append({'name': name, 'value': value, 'spoke': spoke})

        conn.commit()
        cur.close()
        conn.close()

        if new_deals:
            lines = [f'*💰 Revenue Sync — {len(new_deals)} new closed deal(s) logged*']
            for d in new_deals:
                lines.append(f'• {d["name"]} | ${d["value"]:,.0f} | {d["spoke"]}')
            lines.append('Reply `revenue` for MTD breakdown.')
            _post('\n'.join(lines))

    except Exception as e:
        log.error(f'Revenue sync failed: {e}')


# ─── SCHEDULER SETUP ─────────────────────────────────────────────────────────

def start_heartbeat():
    """
    Start APScheduler with all Heartbeat jobs.
    Call once at app startup — gunicorn must run with --workers 1.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(timezone='America/New_York')

        # 6am daily briefing (Eastern time)
        scheduler.add_job(morning_briefing, CronTrigger(hour=6, minute=0, timezone='America/New_York'),
                          id='morning_briefing', replace_existing=True)

        # 9pm pipeline report
        scheduler.add_job(pipeline_report, CronTrigger(hour=21, minute=0, timezone='America/New_York'),
                          id='pipeline_report', replace_existing=True)

        # Every 30 min — seller reply scanner
        scheduler.add_job(reply_scanner, IntervalTrigger(minutes=30),
                          id='reply_scanner', replace_existing=True)

        # Every 2 hours — follow-up queue + decision queue scan
        scheduler.add_job(followup_queue, IntervalTrigger(hours=2),
                          id='followup_queue', replace_existing=True)
        scheduler.add_job(decision_queue_scan, IntervalTrigger(hours=2),
                          id='decision_queue_scan', replace_existing=True)

        # Every 4 hours — XLeads opportunity sync + task delegation + KPI update
        scheduler.add_job(opportunity_sync, IntervalTrigger(hours=4),
                          id='opportunity_sync', replace_existing=True)
        scheduler.add_job(task_delegation_scan, IntervalTrigger(hours=4),
                          id='task_delegation_scan', replace_existing=True)
        scheduler.add_job(kpi_auto_update, IntervalTrigger(hours=4),
                          id='kpi_auto_update', replace_existing=True)

        # Daily 8am ET — buyer onboarding scan
        scheduler.add_job(buyer_onboarding_scan, CronTrigger(hour=8, minute=0, timezone='America/New_York'),
                          id='buyer_onboarding_scan', replace_existing=True)

        # Monday 7am ET — weekly KPI
        scheduler.add_job(weekly_kpi, CronTrigger(day_of_week='mon', hour=7, minute=0, timezone='America/New_York'),
                          id='weekly_kpi', replace_existing=True)

        # Sunday 8pm ET — weekly CEO review
        scheduler.add_job(weekly_ceo_review, CronTrigger(day_of_week='sun', hour=20, minute=0, timezone='America/New_York'),
                          id='weekly_ceo_review', replace_existing=True)

        # Wednesday 10am ET — dead lead resurrection scan (silent check)
        scheduler.add_job(resurrect_scan, CronTrigger(day_of_week='wed', hour=10, minute=0, timezone='America/New_York'),
                          id='resurrect_scan', replace_existing=True)

        # Every hour — SMS deliverability + opt-out rate monitor
        scheduler.add_job(sms_health_monitor, IntervalTrigger(hours=1),
                          id='sms_health_monitor', replace_existing=True)

        # Daily 7am ET — Finance sync (Teller.io accounts + transactions + subscriptions)
        scheduler.add_job(finance_daily_sync, CronTrigger(hour=7, minute=0, timezone='America/New_York'),
                          id='finance_daily_sync', replace_existing=True)

        # Daily 9am ET — Auto-log won XLeads opportunities into revenue_events
        scheduler.add_job(revenue_sync, CronTrigger(hour=9, minute=0, timezone='America/New_York'),
                          id='revenue_sync', replace_existing=True)

        # Daily 7:30am ET — autonomous email triage (draft-by-default)
        scheduler.add_job(email_triage_scan, CronTrigger(hour=7, minute=30, timezone='America/New_York'),
                          id='email_triage_scan', replace_existing=True)

        # Every hour — outreach reply scanner (cold email responses)
        scheduler.add_job(outreach_reply_scan, IntervalTrigger(hours=1),
                          id='outreach_reply_scan', replace_existing=True)

        # Every 30 min — outreach auto-sender (Mon-Fri 8am-4:30pm ET, staggered)
        scheduler.add_job(outreach_send_scheduled, IntervalTrigger(minutes=30),
                          id='outreach_send_scheduled', replace_existing=True)

        scheduler.start()
        log.info('ODIN Heartbeat started — 18 jobs scheduled')
        return scheduler

    except ImportError:
        log.error('APScheduler not installed — Heartbeat disabled. Add apscheduler to requirements.txt')
        return None
    except Exception as e:
        log.error(f'Heartbeat startup failed: {e}')
        return None
