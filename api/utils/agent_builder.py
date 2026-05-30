"""
ODIN — Agent Builder
Creates new custom agents from a plain-English description via Claude.

Flow:
  User: "add agent that monitors my Etsy shop and alerts me on new orders"
  → Claude generates a Python module with a run() function
  → Code stored in custom_agents table
  → Agent is immediately invokable via its trigger keyword

Every generated agent follows this contract:
  def run(params: str = '', get_db=None, xleads=None) -> dict:
      # params = raw text after the trigger keyword
      # Returns: {'slack_text': str, 'discord_text': str (optional)}

The generated code is stored as-is and exec()'d at call time.
Only Brock and Katelyn can create agents (Slack workspace access required).
"""

import os
import re
import json
import anthropic

AGENT_SYSTEM_PROMPT = """You are ODIN's agent generator. Your job is to write a Python agent module
that will be stored in a database and executed dynamically inside a Flask app on Railway.

ODIN CONTEXT:
- Flask API on Railway (Python 3.11)
- PostgreSQL database (psycopg2)
- XLeads/GoHighLevel CRM available via xleads module (58 routes)
- Claude API available (anthropic library)
- Slack notifications via _slack_post (not available inside agent — return slack_text instead)
- Discord notifications via discord_notify (not available inside agent — return discord_text)
- Heartbeat (APScheduler) for scheduled jobs (can be added separately)

AVAILABLE LIBRARIES (already installed):
  requests, psycopg2, anthropic, dateutil, re, json, os, datetime

AGENT CONTRACT — your code MUST follow this exactly:
  1. The module must define: def run(params: str = '', get_db=None, xleads=None) -> dict
  2. run() must return a dict with at least: {"slack_text": "..."}
  3. Optionally include "discord_text" key for Discord mirroring
  4. Never use print() — use the return dict to communicate results
  5. All errors must be caught and returned in slack_text, never raised
  6. If the agent needs the Anthropic API: import anthropic; key = os.environ.get('ANTHROPIC_API_KEY')
  7. If the agent needs XLeads: use the xleads parameter (already imported module)
  8. If the agent needs DB: use get_db() which returns a psycopg2 connection

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no explanation:
{
  "name": "snake_case_name",
  "trigger_keyword": "single word or short phrase user types to invoke",
  "description": "one sentence plain English description",
  "code": "full Python module as a single string — use \\n for newlines"
}

IMPORTANT CODE RULES:
- Use \\n for newlines inside the JSON string (not actual newlines)
- Escape any double quotes inside the code with \\"
- The trigger_keyword must be lowercase, no spaces (use underscore if needed), max 3 words
- Make the agent genuinely useful — pull real data, do real work
- Keep the agent focused on one specific task
- Include helpful Slack-formatted output with emoji"""


def generate(description: str, created_by: str = 'brock') -> dict:
    """
    Takes a plain-English description, generates agent code via Claude,
    returns the parsed agent spec dict.
    Raises ValueError with a user-friendly message on failure.
    """
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    prompt = (
        f"Create an ODIN agent for this request:\n\n"
        f"{description}\n\n"
        f"Return only the JSON schema as specified. "
        f"Make the trigger_keyword intuitive — something the user would naturally type."
    )

    resp = client.messages.create(
        model='claude-sonnet-4-6',   # Sonnet for code quality
        max_tokens=3000,
        system=AGENT_SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = resp.content[0].text.strip()
    start = raw.find('{')
    end   = raw.rfind('}')
    if start == -1 or end == -1:
        raise ValueError('Agent generator returned unexpected output. Try rephrasing your description.')

    try:
        spec = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f'Agent code could not be parsed: {e}')

    # Validate required fields
    for field in ('name', 'trigger_keyword', 'description', 'code'):
        if not spec.get(field):
            raise ValueError(f'Agent spec missing required field: {field}')

    # Sanitize trigger keyword — lowercase, no spaces
    spec['trigger_keyword'] = re.sub(r'\s+', '_', spec['trigger_keyword'].lower().strip())
    spec['name']            = re.sub(r'\s+', '_', spec['name'].lower().strip())
    spec['created_by']      = created_by

    return spec


def save(spec: dict, get_db) -> dict:
    """
    Save a generated agent spec to the custom_agents table.
    Returns the saved row as a dict.
    Raises ValueError if trigger_keyword or name already exists.
    """
    conn = get_db()
    cur  = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO custom_agents
              (name, trigger_keyword, description, code, created_by, status)
            VALUES (%s, %s, %s, %s, %s, 'active')
            RETURNING id, name, trigger_keyword, description, created_at
        """, (
            spec['name'],
            spec['trigger_keyword'],
            spec['description'],
            spec['code'],
            spec.get('created_by', 'brock'),
        ))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return {
            'id':              str(row[0]),
            'name':            row[1],
            'trigger_keyword': row[2],
            'description':     row[3],
            'created_at':      str(row[4]),
        }
    except Exception as e:
        conn.rollback()
        conn.close()
        if 'unique' in str(e).lower():
            raise ValueError(
                f'An agent with that name or trigger keyword already exists. '
                f'Try: `agents` to see existing agents.'
            )
        raise


def load_and_run(trigger_keyword: str, params: str, get_db, xleads_mod) -> dict:
    """
    Load a custom agent from the DB by trigger keyword and execute it.
    Every run (success or error) is logged to agent_logs automatically.
    Returns the agent's result dict, or an error dict.
    """
    import time, traceback as tb
    # Import here to avoid circular imports at module load time
    try:
        from utils import agent_log as _agent_log
    except ImportError:
        _agent_log = None

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT name, code, created_by FROM custom_agents
        WHERE trigger_keyword = %s AND status = 'active'
    """, (trigger_keyword.lower(),))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {'slack_text': f'⚠️ No active agent found for trigger: `{trigger_keyword}`'}

    agent_name, code, created_by = row[0], row[1], (row[2] or 'brock')

    # Execute the agent code in an isolated namespace
    namespace = {
        'os':           os,
        're':           re,
        'json':         json,
        '__builtins__': __builtins__,
    }
    start = time.monotonic()
    try:
        exec(code, namespace)   # noqa: S102
    except Exception as e:
        err = tb.format_exc()
        if _agent_log:
            _agent_log.write(agent_name=agent_name, action=f'load:{params[:100]}',
                             status='error', input_summary=params,
                             error_detail=err, duration_ms=int((time.monotonic()-start)*1000),
                             triggered_by=created_by, get_db=get_db)
        return {'slack_text': f'❌ Agent `{agent_name}` failed to load: {e}'}

    run_fn = namespace.get('run')
    if not callable(run_fn):
        return {'slack_text': f'❌ Agent `{agent_name}` has no run() function.'}

    try:
        result = run_fn(params=params, get_db=get_db, xleads=xleads_mod)
        if not isinstance(result, dict):
            result = {'slack_text': str(result)}
        duration = int((time.monotonic() - start) * 1000)
        if _agent_log:
            _agent_log.write(agent_name=agent_name, action=params[:200] or trigger_keyword,
                             status='ok',
                             input_summary=params,
                             output_summary=str(result.get('slack_text',''))[:500],
                             duration_ms=duration, triggered_by=created_by, get_db=get_db)
        return result
    except Exception as e:
        err = tb.format_exc()
        duration = int((time.monotonic() - start) * 1000)
        if _agent_log:
            _agent_log.write(agent_name=agent_name, action=params[:200] or trigger_keyword,
                             status='error', input_summary=params,
                             error_detail=err, duration_ms=duration,
                             triggered_by=created_by, get_db=get_db)
        return {'slack_text': f'❌ Agent `{agent_name}` error: {e}'}


def list_agents(get_db) -> list:
    """Return all active custom agents as a list of dicts."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT name, trigger_keyword, description, created_by, created_at
        FROM custom_agents
        WHERE status = 'active'
        ORDER BY created_at ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            'name':            r[0],
            'trigger_keyword': r[1],
            'description':     r[2],
            'created_by':      r[3],
            'created_at':      str(r[4]),
        }
        for r in rows
    ]


def disable_agent(name_or_trigger: str, get_db) -> bool:
    """Disable a custom agent by name or trigger keyword. Returns True if found."""
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE custom_agents SET status = 'disabled', updated_at = NOW()
        WHERE (name = %s OR trigger_keyword = %s) AND status = 'active'
        RETURNING id
    """, (name_or_trigger, name_or_trigger))
    found = cur.fetchone() is not None
    conn.commit()
    conn.close()
    return found
