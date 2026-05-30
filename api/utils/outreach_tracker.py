"""
ODIN — Outreach Tracker
Monitors cold outreach replies across all channels (email, SMS, etc.)
and surfaces them in Slack. Tracks pipeline from cold → retainer.

Heartbeat: outreach_reply_scan() runs every hour.
Slack commands handled in app.py:
  outreach              — pipeline overview
  outreach stats        — response rates + best-performing hooks/niches
  outreach <niche>      — drill into one niche
  log sent <email>      — mark an email as sent (manual sends)
  log reply <email>     — manually mark a reply received
"""

import os
import logging
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

log = logging.getLogger('odin.outreach')

# Injected at init from heartbeat.py / app.py
_slack_post_fn = None
_get_db_fn     = None
_channels      = None


def init(slack_post_fn, get_db_fn, channels: dict):
    global _slack_post_fn, _get_db_fn, _channels
    _slack_post_fn = slack_post_fn
    _get_db_fn     = get_db_fn
    _channels      = channels


def _db():
    if _get_db_fn:
        return _get_db_fn()
    import psycopg2
    return psycopg2.connect(os.environ['DATABASE_URL'])


def _post(msg: str):
    if _slack_post_fn and _channels:
        _slack_post_fn(_channels.get('brock', ''), msg)


# ─── HEARTBEAT ────────────────────────────────────────────────────────────────

def outreach_reply_scan():
    """
    Hourly heartbeat: check Gmail for replies from outreach contacts.
    Fires Slack alert for each new reply. Updates contact status to 'replied'.
    """
    try:
        from api.utils import gmail_client
        if not gmail_client.is_available():
            return 'Gmail not authorized — skipping outreach reply scan'
    except Exception as e:
        log.warning(f'outreach_reply_scan: gmail import failed: {e}')
        return f'Gmail unavailable: {e}'

    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # All sent email touches not yet marked replied
    cur.execute("""
        SELECT ot.id, ot.contact_id, ot.gmail_thread_id, ot.sent_at,
               ot.subject, ot.sequence_step,
               oc.email, oc.name, oc.business_name, oc.niche, oc.status AS contact_status
        FROM outreach_touches ot
        JOIN outreach_contacts oc ON oc.id = ot.contact_id
        WHERE ot.channel = 'email'
          AND ot.status  = 'sent'
          AND ot.sent_at IS NOT NULL
          AND oc.email   IS NOT NULL
        ORDER BY ot.sent_at DESC
    """)
    touches = cur.fetchall()

    new_replies = []

    for touch in touches:
        email    = touch['email']
        sent_at  = touch['sent_at']

        try:
            # Search Gmail for any message from this sender since we sent
            msgs = gmail_client.get_recent_threads(max_results=5, query=f'from:{email}')
            for msg in msgs:
                # Parse date — Gmail returns RFC 2822 string, compare loosely
                msg_date_str = msg.get('date', '')
                try:
                    from email.utils import parsedate_to_datetime
                    msg_dt = parsedate_to_datetime(msg_date_str)
                    if msg_dt.tzinfo is None:
                        msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                    sent_aware = sent_at.replace(tzinfo=timezone.utc) if sent_at.tzinfo is None else sent_at
                    if msg_dt <= sent_aware:
                        continue  # Older than our send — skip
                except Exception:
                    continue

                reply_preview = msg.get('snippet', '')[:300]

                # Mark touch as replied
                cur.execute("""
                    UPDATE outreach_touches
                    SET status = 'replied', replied_at = %s, reply_body = %s
                    WHERE id = %s
                """, (msg_dt, reply_preview, touch['id']))

                # Advance contact status (only if not already further along)
                cur.execute("""
                    UPDATE outreach_contacts
                    SET status = 'replied', status_updated_at = NOW()
                    WHERE id = %s
                      AND status NOT IN ('call_booked','audit_sold','build_sold','retainer')
                """, (touch['contact_id'],))

                # Cancel any pending follow-up touches — they replied, stop the sequence
                cur.execute("""
                    UPDATE outreach_touches SET status='cancelled'
                    WHERE contact_id=%s AND status='pending'
                """, (touch['contact_id'],))

                new_replies.append({
                    'name':     touch['name'] or 'Unknown',
                    'business': touch['business_name'],
                    'niche':    touch['niche'] or '—',
                    'email':    email,
                    'subject':  touch['subject'] or '—',
                    'step':     touch['sequence_step'],
                    'preview':  reply_preview,
                })
                break  # One alert per contact per scan

        except Exception as e:
            log.warning(f'outreach_reply_scan: error checking {email}: {e}')
            continue

    conn.commit()
    cur.close()
    conn.close()

    # Slack alert for each new reply
    for r in new_replies:
        step_label = {1: 'initial email', 2: 'day-3 follow-up', 3: 'day-7 final'}.get(r['step'], f'step {r["step"]}')
        msg = (
            f"📬 *Reply from {r['name']} — {r['business']}*\n"
            f"Niche: {r['niche']} | Replied to: _{r['subject']}_ ({step_label})\n"
            f">{r['preview']}\n"
            f"Reply to: {r['email']}\n"
            f"_Update status: `log reply {r['email']} status:call_booked`_"
        )
        _post(msg)

    # Follow-up reminders: contacts in 'contacted' with no reply after 3 days
    _check_followup_reminders()

    result = f"Scanned {len(touches)} outreach touches — {len(new_replies)} new replies"
    log.info(result)
    return result


def _check_followup_reminders():
    """Alert when a contacted prospect has gone 3+ days without reply (day-3 follow-up due)."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Contacts where last touch was sent 3 days ago, no reply, no follow-up touch yet
    cur.execute("""
        SELECT oc.id, oc.name, oc.business_name, oc.niche, oc.email,
               MAX(ot.sent_at) AS last_sent, MAX(ot.sequence_step) AS last_step
        FROM outreach_contacts oc
        JOIN outreach_touches ot ON ot.contact_id = oc.id
        WHERE oc.status = 'contacted'
          AND ot.status = 'sent'
          AND ot.channel = 'email'
        GROUP BY oc.id, oc.name, oc.business_name, oc.niche, oc.email
        HAVING MAX(ot.sent_at) < NOW() - INTERVAL '3 days'
           AND MAX(ot.sequence_step) < 3
    """)
    due = cur.fetchall()
    cur.close()
    conn.close()

    if not due:
        return

    lines = [f'*⏰ Follow-up due — {len(due)} contact(s)*']
    for c in due:
        step_next = (c['last_step'] or 1) + 1
        label = 'Day-3 follow-up' if step_next == 2 else 'Day-7 final touch'
        lines.append(f'• {c["name"] or c["business_name"]} ({c["niche"]}) — {label} — {c["email"]}')
    lines.append('_Use `outreach <niche>` to see full list_')
    _post('\n'.join(lines))


# ─── PIPELINE STATS ───────────────────────────────────────────────────────────

def get_pipeline(niche: str = None) -> str:
    """Return a Slack-formatted pipeline overview, optionally filtered by niche."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    niche_filter = 'AND niche = %s' if niche else ''
    params       = (niche,) if niche else ()

    cur.execute(f"""
        SELECT
            niche,
            COUNT(*) FILTER (WHERE status = 'cold')         AS cold,
            COUNT(*) FILTER (WHERE status = 'contacted')    AS contacted,
            COUNT(*) FILTER (WHERE status = 'replied')      AS replied,
            COUNT(*) FILTER (WHERE status = 'call_booked')  AS call_booked,
            COUNT(*) FILTER (WHERE status = 'audit_sold')   AS audit_sold,
            COUNT(*) FILTER (WHERE status = 'build_sold')   AS build_sold,
            COUNT(*) FILTER (WHERE status = 'retainer')     AS retainer,
            COUNT(*) FILTER (WHERE status = 'not_interested') AS not_interested,
            COUNT(*)                                         AS total
        FROM outreach_contacts
        WHERE 1=1 {niche_filter}
        GROUP BY niche
        ORDER BY total DESC
    """, params)

    rows = cur.fetchall()

    # Overall totals
    cur.execute(f"""
        SELECT
            COUNT(*)                                                  AS total,
            COUNT(*) FILTER (WHERE status IN ('contacted','replied','call_booked',
                             'audit_sold','build_sold','retainer'))   AS active,
            COUNT(*) FILTER (WHERE status = 'replied')               AS replied,
            COUNT(*) FILTER (WHERE status = 'audit_sold')            AS sold,
            COUNT(DISTINCT ot.id) FILTER (WHERE ot.status = 'sent')  AS emails_sent,
            SUM(CASE WHEN status = 'audit_sold' THEN 497 ELSE 0 END) AS revenue
        FROM outreach_contacts oc
        LEFT JOIN outreach_touches ot ON ot.contact_id = oc.id AND ot.channel = 'email'
        WHERE 1=1 {niche_filter}
    """, params)
    totals = cur.fetchone()

    cur.close()
    conn.close()

    title = f'*Outreach Pipeline — {niche.title()}*' if niche else '*Outreach Pipeline — All Niches*'
    lines = [title, '']

    if not rows:
        return f'{title}\n_No contacts tracked yet. Use `log sent` to add contacts._'

    for r in rows:
        n = r['niche'] or 'unknown'
        reply_rate = f"{round(r['replied']/r['contacted']*100)}%" if r['contacted'] else '—'
        lines.append(
            f"*{n.upper()}* ({r['total']} contacts)\n"
            f"  Cold: {r['cold']} | Contacted: {r['contacted']} | "
            f"Replied: {r['replied']} ({reply_rate} rate)\n"
            f"  Call booked: {r['call_booked']} | Audit sold: {r['audit_sold']} | "
            f"Build sold: {r['build_sold']} | Retainer: {r['retainer']}"
        )

    if totals:
        t = totals
        overall_rate = f"{round(t['replied']/t['emails_sent']*100)}%" if t['emails_sent'] else '—'
        lines += [
            '',
            f"*Totals* — {t['total']} contacts | {t['emails_sent']} emails sent | "
            f"{t['replied']} replies ({overall_rate}) | "
            f"{t['sold']} audits sold | ${t['revenue'] or 0} revenue"
        ]

    return '\n'.join(lines)


def get_stats() -> str:
    """Return response rate stats by niche and hook type for improving outreach."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Reply rates by niche
    cur.execute("""
        SELECT
            oc.niche,
            COUNT(ot.id) FILTER (WHERE ot.status = 'sent')    AS sent,
            COUNT(ot.id) FILTER (WHERE ot.status = 'replied') AS replied,
            ROUND(COUNT(ot.id) FILTER (WHERE ot.status = 'replied')::numeric /
                  NULLIF(COUNT(ot.id) FILTER (WHERE ot.status = 'sent'), 0) * 100, 1)
                AS reply_rate_pct
        FROM outreach_touches ot
        JOIN outreach_contacts oc ON oc.id = ot.contact_id
        WHERE ot.channel = 'email'
        GROUP BY oc.niche
        ORDER BY reply_rate_pct DESC NULLS LAST
    """)
    niche_stats = cur.fetchall()

    # Reply rates by hook type
    cur.execute("""
        SELECT
            hook_type,
            COUNT(*) FILTER (WHERE status = 'sent')    AS sent,
            COUNT(*) FILTER (WHERE status = 'replied') AS replied,
            ROUND(COUNT(*) FILTER (WHERE status = 'replied')::numeric /
                  NULLIF(COUNT(*) FILTER (WHERE status = 'sent'), 0) * 100, 1)
                AS reply_rate_pct
        FROM outreach_touches
        WHERE channel = 'email' AND hook_type IS NOT NULL
        GROUP BY hook_type
        ORDER BY reply_rate_pct DESC NULLS LAST
    """)
    hook_stats = cur.fetchall()

    # Reply rates by sequence step
    cur.execute("""
        SELECT
            sequence_step,
            COUNT(*) FILTER (WHERE status = 'sent')    AS sent,
            COUNT(*) FILTER (WHERE status = 'replied') AS replied,
            ROUND(COUNT(*) FILTER (WHERE status = 'replied')::numeric /
                  NULLIF(COUNT(*) FILTER (WHERE status = 'sent'), 0) * 100, 1)
                AS reply_rate_pct
        FROM outreach_touches
        WHERE channel = 'email'
        GROUP BY sequence_step
        ORDER BY sequence_step
    """)
    step_stats = cur.fetchall()

    cur.close()
    conn.close()

    lines = ['*📊 Outreach Stats — What\'s Working*', '']

    lines.append('*By Niche:*')
    if niche_stats:
        for r in niche_stats:
            bar = '█' * int((r['reply_rate_pct'] or 0) / 10)
            lines.append(f"  {(r['niche'] or 'unknown').ljust(12)} {r['sent']} sent / {r['replied']} replied — {r['reply_rate_pct'] or 0}% {bar}")
    else:
        lines.append('  _No data yet_')

    lines.append('')
    lines.append('*By Hook:*')
    if hook_stats:
        for r in hook_stats:
            lines.append(f"  {(r['hook_type'] or 'unknown').ljust(20)} {r['sent']} sent / {r['replied']} replied — {r['reply_rate_pct'] or 0}%")
    else:
        lines.append('  _No data yet — hooks logged when emails are marked sent_')

    lines.append('')
    lines.append('*By Sequence Step:*')
    step_labels = {1: 'Initial', 2: 'Day-3 follow-up', 3: 'Day-7 final'}
    if step_stats:
        for r in step_stats:
            label = step_labels.get(r['sequence_step'], f'Step {r["sequence_step"]}')
            lines.append(f"  {label.ljust(18)} {r['sent']} sent / {r['replied']} replied — {r['reply_rate_pct'] or 0}%")
    else:
        lines.append('  _No data yet_')

    return '\n'.join(lines)


# ─── HELPERS (called from app.py Slack handlers) ──────────────────────────────

def log_sent(email: str, step: int = 1, gmail_message_id: str = None,
             gmail_thread_id: str = None, hook_type: str = None) -> str:
    """Mark a touch as sent. Creates contact record if not found."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id, name, business_name, status FROM outreach_contacts WHERE LOWER(email) = LOWER(%s)", (email,))
    contact = cur.fetchone()

    if not contact:
        cur.close()
        conn.close()
        return f"Contact not found for {email}. Add them first with `outreach add`."

    # Upsert the touch for this step
    cur.execute("""
        INSERT INTO outreach_touches (contact_id, channel, status, sent_at, sequence_step,
                                      gmail_message_id, gmail_thread_id, hook_type)
        VALUES (%s, 'email', 'sent', NOW(), %s, %s, %s, %s)
    """, (contact['id'], step, gmail_message_id, gmail_thread_id, hook_type))

    # Advance contact status if still cold
    if contact['status'] == 'cold':
        cur.execute("""
            UPDATE outreach_contacts SET status='contacted', status_updated_at=NOW() WHERE id=%s
        """, (contact['id'],))

    conn.commit()
    cur.close()
    conn.close()
    return f"✅ Logged step {step} sent to {contact['name'] or contact['business_name']} ({email})"


def log_reply_manual(email: str, new_status: str = 'replied') -> str:
    """Manually mark a contact as replied / call_booked / audit_sold etc."""
    valid = ('replied', 'call_booked', 'audit_sold', 'build_sold', 'retainer', 'not_interested', 'dead')
    if new_status not in valid:
        return f"Invalid status '{new_status}'. Options: {', '.join(valid)}"

    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        UPDATE outreach_contacts SET status=%s, status_updated_at=NOW()
        WHERE LOWER(email)=LOWER(%s)
        RETURNING name, business_name
    """, (new_status, email))
    row = cur.fetchone()

    if row:
        cur.execute("""
            UPDATE outreach_touches SET status='replied', replied_at=NOW()
            WHERE contact_id=(SELECT id FROM outreach_contacts WHERE LOWER(email)=LOWER(%s))
              AND status='sent'
              AND sequence_step=(
                  SELECT MAX(sequence_step) FROM outreach_touches ot2
                  JOIN outreach_contacts oc2 ON oc2.id=ot2.contact_id
                  WHERE LOWER(oc2.email)=LOWER(%s) AND ot2.status='sent'
              )
        """, (email, email))

    conn.commit()
    cur.close()
    conn.close()

    if row:
        return f"✅ {row['name'] or row['business_name']} → *{new_status}*"
    return f"Contact not found for {email}"
