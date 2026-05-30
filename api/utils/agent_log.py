"""
ODIN — Agent Log Utility
Every agent run writes one row to agent_logs.
IT agent queries this for diagnosis. `logs` Slack command exposes it.

Usage (in app.py around every agent call):

    with AgentTimer('scout', input_summary=keywords, triggered_by='brock', get_db=get_db) as t:
        result = scout_agent.run(keywords=keywords)
        t.set_output(result.get('slack_text', '')[:400])

On exception, AgentTimer auto-logs status='error' with full traceback.
On success, logs status='ok' with output summary and duration.
"""

import os
import time
import traceback
import psycopg2
import psycopg2.extras

# How much of input/output to store (keeps rows small)
_MAX_INPUT  = 500
_MAX_OUTPUT = 500
_MAX_ERROR  = 2000


# ─── Context Manager ─────────────────────────────────────────────────────────

class AgentTimer:
    """
    Context manager that auto-logs every agent run to agent_logs.

    with AgentTimer('scout', input_summary=x, triggered_by='brock', get_db=get_db) as t:
        result = scout_agent.run(...)
        t.set_output(result['slack_text'])
    """

    def __init__(self, agent_name: str, input_summary: str = '',
                 action: str = '', triggered_by: str = 'brock', get_db=None):
        self.agent_name    = agent_name
        self.input_summary = str(input_summary)[:_MAX_INPUT]
        self.action        = action or agent_name
        self.triggered_by  = triggered_by
        self.get_db        = get_db
        self._output       = ''
        self._start        = None

    def set_output(self, text: str):
        """Call this inside the with block after the agent returns."""
        self._output = str(text)[:_MAX_OUTPUT]

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.monotonic() - self._start) * 1000)

        if exc_type is not None:
            # Agent raised an exception
            error_detail = ''.join(traceback.format_exception(exc_type, exc_val, exc_tb))
            write(
                agent_name    = self.agent_name,
                action        = self.action,
                status        = 'error',
                input_summary = self.input_summary,
                output_summary= self._output or '(none — error before output)',
                error_detail  = error_detail[:_MAX_ERROR],
                duration_ms   = duration_ms,
                triggered_by  = self.triggered_by,
                get_db        = self.get_db,
            )
            return False  # re-raise the exception

        write(
            agent_name    = self.agent_name,
            action        = self.action,
            status        = 'ok',
            input_summary = self.input_summary,
            output_summary= self._output,
            error_detail  = None,
            duration_ms   = duration_ms,
            triggered_by  = self.triggered_by,
            get_db        = self.get_db,
        )
        return False


# ─── Direct write ─────────────────────────────────────────────────────────────

def write(agent_name: str, action: str = '', status: str = 'ok',
          input_summary: str = '', output_summary: str = '',
          error_detail: str = None, duration_ms: int = None,
          triggered_by: str = 'brock', get_db=None):
    """
    Write one row to agent_logs. Never raises — swallows all DB errors
    so logging failures never break the agent itself.
    """
    if get_db is None:
        return

    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO agent_logs
              (agent_name, action, status, input_summary, output_summary,
               error_detail, duration_ms, triggered_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            agent_name,
            (action or agent_name)[:200],
            status,
            str(input_summary)[:_MAX_INPUT],
            str(output_summary)[:_MAX_OUTPUT],
            str(error_detail)[:_MAX_ERROR] if error_detail else None,
            duration_ms,
            triggered_by,
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Logging must never break the calling agent


# ─── Query helpers (used by IT agent and `logs` command) ─────────────────────

def get_recent(get_db, limit: int = 20, agent_name: str = None,
               status: str = None) -> list:
    """
    Return recent log rows as list of dicts.
    Filters by agent_name or status if provided.
    """
    try:
        conn   = get_db()
        cur    = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        wheres = []
        params = []

        if agent_name:
            wheres.append('agent_name = %s')
            params.append(agent_name)
        if status:
            wheres.append('status = %s')
            params.append(status)

        where_clause = ('WHERE ' + ' AND '.join(wheres)) if wheres else ''
        params.append(limit)

        cur.execute(f"""
            SELECT id, agent_name, action, status, input_summary,
                   output_summary, error_detail, duration_ms, triggered_by, created_at
            FROM agent_logs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %s
        """, params)

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return [{'error': str(e)}]


def get_error_context(get_db, limit: int = 10) -> str:
    """
    Returns a formatted string of recent errors for injection into IT agent prompts.
    """
    errors = get_recent(get_db, limit=limit, status='error')
    if not errors:
        return 'No recent agent errors found.'

    lines = [f'Recent agent errors (last {len(errors)}):']
    for e in errors:
        ts = str(e.get('created_at', ''))[:19]
        lines.append(
            f'[{ts}] {e["agent_name"]} — {e.get("action", "")} — '
            f'{str(e.get("error_detail", ""))[:300]}'
        )
    return '\n'.join(lines)


def format_slack(rows: list, title: str = 'Agent Logs') -> str:
    """Format log rows for Slack display."""
    if not rows:
        return f'*{title}*\nNo logs found.'

    lines = [f'*{title} ({len(rows)})*\n']
    for r in rows:
        if 'error' in r and len(r) == 1:
            lines.append(f'⚠️ Could not query logs: {r["error"]}')
            continue

        status_emoji = {'ok': '✅', 'error': '❌', 'warning': '⚠️'}.get(
            r.get('status', 'ok'), '⚪')
        ts      = str(r.get('created_at', ''))[:16].replace('T', ' ')
        dur     = f' {r["duration_ms"]}ms' if r.get('duration_ms') else ''
        who     = r.get('triggered_by', '?')
        action  = r.get('action') or r.get('agent_name', '?')

        lines.append(
            f'{status_emoji} *{r["agent_name"]}* — _{action}_ '
            f'| {ts}{dur} | by {who}'
        )
        if r.get('status') == 'error' and r.get('error_detail'):
            # Show first line of error only in list view
            first_line = str(r['error_detail']).split('\n')[-1][:150]
            lines.append(f'  `{first_line}`')

    return '\n'.join(lines)
