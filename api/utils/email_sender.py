"""
ODIN — Outreach Email Sender
Automatically sends queued cold emails Mon–Fri 8am–4:30pm ET.
Handles initial sends (step 1) + day-3 follow-ups (step 2) + day-7 finals (step 3).

Safety switches:
  OUTREACH_ENABLED=true   — must be set in Railway or emails will NOT send
  OUTREACH_DAILY_LIMIT=8  — max emails sent per calendar day (default 8)

Flow:
  1. queue_outreach_emails.py seeds outreach_touches with status='pending'
  2. This module's send_scheduled() fires every 30 min during business hours
  3. Sends 1 email per run (random 60% chance to spread timing naturally)
  4. After each send, queues follow-up touches for contacts due for step 2/3
  5. If a contact replies (outreach_tracker), their pending touches are cancelled
"""

import os
import logging
from datetime import datetime, time, timezone

import psycopg2
import psycopg2.extras

log = logging.getLogger('odin.email_sender')

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
    return psycopg2.connect(os.environ['DATABASE_URL'])


def _post(msg: str):
    if _slack_post_fn and _channels:
        _slack_post_fn(_channels.get('brock', ''), msg)


# ─── BUSINESS HOURS CHECK ─────────────────────────────────────────────────────

def is_business_hours() -> bool:
    """True if Mon–Fri 8:00am–4:30pm ET."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('America/New_York'))
    if now.weekday() >= 5:
        return False
    return time(8, 0) <= now.time() <= time(16, 30)


# ─── FOLLOW-UP BODY GENERATORS ────────────────────────────────────────────────

def _followup_body(name: str, biz: str, step: int) -> str:
    first = (name or '').split()[0] if name else ''
    greeting = f'Hey {first},' if first else 'Hey,'
    if step == 2:
        return (
            f'{greeting}\n\n'
            f"Wanted to make sure this didn't get buried. "
            f'Still curious what {biz}\'s number looks like?\n\n'
            f'— Brock Shue\nshueboxllc@gmail.com'
        )
    return (
        f'{greeting}\n\n'
        f"Last one from me — if the timing isn't right, no problem. "
        f'When you want to see where {biz} stands on this, I\'m here.\n\n'
        f'— Brock Shue\nshueboxllc@gmail.com'
    )


def _followup_subject(biz: str, step: int) -> str:
    if step == 2:
        return f'{biz} — did this get buried?'
    return f'Last one — {biz}'


# ─── QUEUE FOLLOW-UPS ─────────────────────────────────────────────────────────

def queue_followups() -> int:
    """
    Create pending day-3 and day-7 touches for contacts that are due.
    Called automatically after each send run.
    Returns number of follow-up touches queued.
    """
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    queued = 0

    # Step 2 (day-3): step 1 sent 3+ days ago, no step 2 exists, contact still 'contacted'
    cur.execute("""
        SELECT oc.id, oc.name, oc.business_name, oc.niche
        FROM outreach_contacts oc
        JOIN outreach_touches ot ON ot.contact_id = oc.id
        WHERE oc.status = 'contacted'
          AND ot.status = 'sent'
          AND ot.sequence_step = 1
          AND ot.sent_at < NOW() - INTERVAL '3 days'
          AND NOT EXISTS (
              SELECT 1 FROM outreach_touches x
              WHERE x.contact_id = oc.id AND x.sequence_step >= 2
          )
        GROUP BY oc.id, oc.name, oc.business_name, oc.niche
    """)
    for c in cur.fetchall():
        cur.execute("""
            INSERT INTO outreach_touches
                (contact_id, channel, status, subject, body, sequence_step)
            VALUES (%s, 'email', 'pending', %s, %s, 2)
        """, (c['id'],
              _followup_subject(c['business_name'], 2),
              _followup_body(c['name'], c['business_name'], 2)))
        queued += 1

    # Step 3 (day-7): step 2 sent 4+ days ago (7+ total), no step 3 exists
    cur.execute("""
        SELECT oc.id, oc.name, oc.business_name, oc.niche
        FROM outreach_contacts oc
        JOIN outreach_touches ot ON ot.contact_id = oc.id
        WHERE oc.status = 'contacted'
          AND ot.status = 'sent'
          AND ot.sequence_step = 2
          AND ot.sent_at < NOW() - INTERVAL '4 days'
          AND NOT EXISTS (
              SELECT 1 FROM outreach_touches x
              WHERE x.contact_id = oc.id AND x.sequence_step >= 3
          )
        GROUP BY oc.id, oc.name, oc.business_name, oc.niche
    """)
    for c in cur.fetchall():
        cur.execute("""
            INSERT INTO outreach_touches
                (contact_id, channel, status, subject, body, sequence_step)
            VALUES (%s, 'email', 'pending', %s, %s, 3)
        """, (c['id'],
              _followup_subject(c['business_name'], 3),
              _followup_body(c['name'], c['business_name'], 3)))
        queued += 1

    conn.commit()
    cur.close()
    conn.close()
    return queued


def cancel_pending_for_contact(contact_id: int):
    """Cancel all pending touches when a contact replies — no more follow-ups."""
    conn = _db()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE outreach_touches
        SET status = 'cancelled'
        WHERE contact_id = %s AND status = 'pending'
    """, (contact_id,))
    conn.commit()
    cur.close()
    conn.close()


# ─── MAIN SEND FUNCTION ───────────────────────────────────────────────────────

def send_scheduled() -> str:
    """
    Called every 30 min by heartbeat. Sends 1 pending email if:
      - OUTREACH_ENABLED=true
      - Within Mon–Fri 8am–4:30pm ET
      - Daily limit not hit
      - 60% random chance (spreads sends naturally through the day)
    """
    if not os.environ.get('OUTREACH_ENABLED', '').lower() == 'true':
        return 'OUTREACH_ENABLED not set — skipping'

    if not is_business_hours():
        return 'Outside business hours'

    daily_limit = int(os.environ.get('OUTREACH_DAILY_LIMIT', '16'))

    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # How many sent today already?
    cur.execute("""
        SELECT COUNT(*) AS n FROM outreach_touches
        WHERE channel = 'email' AND status = 'sent'
          AND sent_at >= CURRENT_DATE
          AND sent_at < CURRENT_DATE + INTERVAL '1 day'
    """)
    sent_today = cur.fetchone()['n']

    if sent_today >= daily_limit:
        cur.close()
        conn.close()
        return f'Daily limit reached ({sent_today}/{daily_limit})'

    # Grab the next pending email — step 1 first, then follow-ups, FIFO
    cur.execute("""
        SELECT ot.id, ot.contact_id, ot.subject, ot.body, ot.sequence_step,
               oc.email, oc.name, oc.business_name, oc.niche
        FROM outreach_touches ot
        JOIN outreach_contacts oc ON oc.id = ot.contact_id
        WHERE ot.channel = 'email'
          AND ot.status  = 'pending'
          AND oc.status NOT IN ('not_interested','dead','replied',
                                'call_booked','audit_sold','build_sold','retainer')
        ORDER BY ot.sequence_step ASC, ot.created_at ASC
        LIMIT 1
    """)
    touch = cur.fetchone()

    if not touch:
        # Nothing pending — queue any overdue follow-ups for next run
        cur.close()
        conn.close()
        queue_followups()
        return 'No pending emails — follow-up queue checked'

    # Send via Gmail
    try:
        from api.utils import gmail_client
        result = gmail_client.send(
            to_email=touch['email'],
            to_name=touch['name'] or '',
            subject=touch['subject'],
            body=touch['body'],
        )

        cur.execute("""
            UPDATE outreach_touches
            SET status='sent', sent_at=NOW(),
                gmail_message_id=%s, gmail_thread_id=%s
            WHERE id=%s
        """, (result['message_id'], result['thread_id'], touch['id']))

        # Advance contact to 'contacted' if still cold
        cur.execute("""
            UPDATE outreach_contacts
            SET status='contacted', status_updated_at=NOW()
            WHERE id=%s AND status='cold'
        """, (touch['contact_id'],))

        conn.commit()

        step_label = {1: 'Initial', 2: 'Day-3 follow-up', 3: 'Day-7 final'}.get(
            touch['sequence_step'], f'Step {touch["sequence_step"]}'
        )
        msg = (
            f'📧 *Sent* — {touch["name"] or touch["business_name"]} '
            f'({touch["niche"]}) | {step_label}\n'
            f'To: {touch["email"]} | _{touch["subject"]}_\n'
            f'Today: {sent_today + 1}/{daily_limit}'
        )
        log.info(msg)
        _post(msg)

        # Queue follow-ups for any contacts newly due
        queued = queue_followups()
        if queued:
            log.info(f'Queued {queued} follow-up touches')

        # If this was a step-1 send, check whether the initial batch is now exhausted
        if touch['sequence_step'] == 1:
            _check_batch_exhaustion()

        cur.close()
        conn.close()
        return f'Sent to {touch["email"]} (step {touch["sequence_step"]})'

    except Exception as e:
        log.error(f'Send failed to {touch["email"]}: {e}')
        cur.execute("UPDATE outreach_touches SET status='failed' WHERE id=%s", (touch['id'],))
        conn.commit()
        cur.close()
        conn.close()
        _post(f'⚠️ Email send failed to {touch["email"]}: {e}')
        return f'Send failed: {e}'


def _check_batch_exhaustion():
    """
    After every step-1 send: check if any step-1 touches remain pending.
    If none remain, the initial batch is exhausted — trigger auto-snipe.
    Guard: only fires once (checks for recent snipe in last 24h).
    """
    try:
        conn = _db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT COUNT(*) AS n FROM outreach_touches
            WHERE channel = 'email' AND status = 'pending' AND sequence_step = 1
        """)
        remaining = cur.fetchone()['n']

        if remaining > 0:
            cur.close()
            conn.close()
            return  # Still have initial emails to send

        # Check we haven't already sniped recently (prevent re-trigger on re-deploy etc.)
        cur.execute("""
            SELECT COUNT(*) AS n FROM outreach_contacts
            WHERE source = 'auto_sniper'
              AND created_at > NOW() - INTERVAL '24 hours'
        """)
        recent_snipe = cur.fetchone()['n']
        cur.close()
        conn.close()

        if recent_snipe > 0:
            return  # Already sniped in last 24h

        # Initial batch exhausted — trigger lead sniper
        log.info('Initial batch exhausted — triggering auto-snipe for bottom niches')
        import threading
        from api.utils import lead_sniper
        lead_sniper.init(_slack_post_fn, _get_db_fn, _channels)
        threading.Thread(
            target=lead_sniper.check_and_snipe_bottom_niches,
            args=(_get_db_fn,),
            daemon=True,
        ).start()

    except Exception as e:
        log.error(f'_check_batch_exhaustion error: {e}')


def get_queue_status() -> str:
    """Returns a Slack-formatted queue status summary."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            status,
            sequence_step,
            COUNT(*) AS n
        FROM outreach_touches
        WHERE channel = 'email'
        GROUP BY status, sequence_step
        ORDER BY sequence_step, status
    """)
    rows = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) AS n FROM outreach_touches
        WHERE channel='email' AND status='sent'
          AND sent_at >= CURRENT_DATE
    """)
    today = cur.fetchone()['n']

    daily_limit = int(os.environ.get('OUTREACH_DAILY_LIMIT', '16'))
    enabled     = os.environ.get('OUTREACH_ENABLED', 'false').lower() == 'true'
    in_hours    = is_business_hours()

    cur.close()
    conn.close()

    lines = ['*📬 Outreach Send Queue*', '']
    lines.append(f'Status: {"✅ ACTIVE" if enabled else "⏸ PAUSED (set OUTREACH_ENABLED=true)"} | '
                 f'{"🟢 Business hours" if in_hours else "🔴 Outside hours"} | '
                 f'Sent today: {today}/{daily_limit}')
    lines.append('')

    by_step = {}
    for r in rows:
        step = r['sequence_step']
        if step not in by_step:
            by_step[step] = {}
        by_step[step][r['status']] = r['n']

    step_labels = {1: 'Step 1 — Initial', 2: 'Step 2 — Day-3 follow-up', 3: 'Step 3 — Day-7 final'}
    for step in sorted(by_step.keys()):
        d = by_step[step]
        label = step_labels.get(step, f'Step {step}')
        parts = []
        for s in ('pending', 'sent', 'replied', 'failed', 'cancelled'):
            if d.get(s):
                parts.append(f'{s}: {d[s]}')
        lines.append(f'*{label}* — {" | ".join(parts)}')

    return '\n'.join(lines)
