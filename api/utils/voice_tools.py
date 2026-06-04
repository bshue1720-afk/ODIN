"""
ODIN Voice Tools
Functions callable by the voice agent during a live call.
Each returns a plain string that gets read back via TTS.
"""
import os
from core.db import get_db
from core.helpers import _slack_post


def _safe_query(sql, params=None):
    """Run a read-only DB query, return rows or []."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, params or ())
        return cur.fetchall()
    except Exception as e:
        return []


def get_hot_leads() -> str:
    """Return hot leads as a voice-friendly summary."""
    rows = _safe_query("""
        SELECT address, city, mctp_total
        FROM leads
        WHERE status NOT IN ('dead','closed','contracted','assigned')
          AND mctp_total >= 9
        ORDER BY mctp_total DESC
        LIMIT 5
    """)
    if not rows:
        return "No hot leads right now. Pipeline is clean."
    lines = []
    for r in rows:
        address = r['address'] if isinstance(r, dict) else r[0]
        city    = r['city'] if isinstance(r, dict) else r[1]
        score   = r['mctp_total'] if isinstance(r, dict) else r[2]
        lines.append(f"{address} in {city}, score {score}")
    return f"You have {len(lines)} hot leads. " + ". ".join(lines)


def get_pending_approvals() -> str:
    """Return deals waiting for approval."""
    rows = _safe_query("""
        SELECT id, action_type, data->>'address' AS address
        FROM approval_queue
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 5
    """)
    if not rows:
        return "No pending approvals. You're all caught up."
    lines = []
    for r in rows:
        aid  = str(r['id'] if isinstance(r, dict) else r[0])[:8]
        act  = r['action_type'] if isinstance(r, dict) else r[1]
        addr = (r['address'] if isinstance(r, dict) else r[2]) or 'no address'
        lines.append(f"ID {aid}: {addr} — {act}")
    return f"{len(lines)} pending approval{'s' if len(lines) > 1 else ''}. " + ". ".join(lines)


def get_pending_decisions() -> str:
    """Return open decisions that need an outcome."""
    rows = _safe_query("""
        SELECT id, issue_summary
        FROM decision_queue
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 3
    """)
    if not rows:
        return "No open decisions."
    lines = []
    for r in rows:
        did  = (r['id'] if isinstance(r, dict) else r[0])
        text = r['issue_summary'] if isinstance(r, dict) else r[1]
        lines.append(f"{str(did)[:8]}: {str(text)[:80]}")
    return f"{len(lines)} open decision{'s' if len(lines) > 1 else ''}. " + ". ".join(lines)


def get_blast_stats() -> str:
    """Return last text blast results."""
    rows = _safe_query("""
        SELECT data->>'sent' AS sent, data->>'failed' AS failed,
               data->>'tag' AS tag, created_at
        FROM agent_actions
        WHERE action_type = 'textblast_complete'
        ORDER BY created_at DESC
        LIMIT 1
    """)
    if not rows:
        return "No blast history yet."
    r    = rows[0]
    sent = r['sent'] if isinstance(r, dict) else r[0]
    fail = r['failed'] if isinstance(r, dict) else r[1]
    tag  = r['tag'] if isinstance(r, dict) else r[2]
    return f"Last blast: {sent} sent, {fail} failed. Tag: {tag or 'none'}."


def get_morning_briefing() -> str:
    """Combined morning briefing — leads, approvals, decisions."""
    leads      = get_hot_leads()
    approvals  = get_pending_approvals()
    decisions  = get_pending_decisions()
    blast      = get_blast_stats()
    return (
        f"Good morning. Here's your ODIN briefing. "
        f"Leads: {leads} "
        f"Approvals: {approvals} "
        f"Decisions: {decisions} "
        f"Blast: {blast}"
    )


def approve_deal(approval_id: str) -> str:
    """Approve a deal by partial approval ID."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            UPDATE approval_queue
            SET status = 'approved', resolved_at = NOW()
            WHERE id::text ILIKE %s AND status = 'pending'
        """, (f"{approval_id.strip()}%",))
        updated = cur.rowcount
        conn.commit()
        if updated:
            return f"Deal approved. ODIN will proceed."
        return f"No pending approval found matching that ID."
    except Exception as e:
        return f"Approval failed: {e}"


def _voice_channel() -> str:
    """Return the Slack channel for voice-originated posts."""
    # Use the same env var the rest of ODIN uses — set in Railway
    ch = os.environ.get('SLACK_CHANNEL_BROCK', '')
    if ch:
        return ch
    # Fallback: Brock's Slack user ID (DM)
    return os.environ.get('BROCK_SLACK_UID', 'U0B5C32BJ6B')


def spawn_agent_task(task_description: str) -> str:
    """
    Research a task with a direct Anthropic call and post results to Slack.
    Runs synchronously in asyncio.to_thread — takes ~5-10 seconds.
    """
    import anthropic
    import traceback

    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return "Can't research — ANTHROPIC_API_KEY is not set."

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=800,
            system=(
                "You are ODIN, an AI chief of staff for Brock Shue (real estate wholesaler, Memphis TN). "
                "Give concise, actionable research results. Use bullet points. Be specific."
            ),
            messages=[{'role': 'user', 'content': f'Research: {task_description}'}],
        )
        result = resp.content[0].text.strip()

        # Post full result to Slack
        slack_msg = f"🎙️ *Voice Research:* {task_description}\n\n{result}"
        _slack_post(_voice_channel(), slack_msg)

        # Return a spoken summary (first 200 chars)
        spoken = result[:200].replace('*', '').replace('_', '').replace('#', '')
        return f"Here's what I found. {spoken}. Full results posted to Slack."

    except Exception as e:
        err = traceback.format_exc()
        print(f"[voice spawn error] {e}\n{err}", flush=True)
        return f"Research failed: {type(e).__name__}: {e}"


def send_slack_note(message: str) -> str:
    """Post a note to Slack from the voice session."""
    try:
        _slack_post(_voice_channel(), f"🎙️ *Voice note:* {message}")
        return "Note posted to Slack."
    except Exception as e:
        return f"Failed to post note: {e}"
