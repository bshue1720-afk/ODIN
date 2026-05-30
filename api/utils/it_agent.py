"""
ODIN Agent: it_agent
Professional IT support and debugging for Brock + Katelyn.
Knows the full ODIN stack, Railway, XLeads/GHL, and common platform issues.

Input:  problem description (free text)
Output: structured diagnosis + resolution steps + prevention notes
"""
import os, json, requests as http, anthropic

# Full architecture context injected into every debug session
ODIN_CONTEXT = """
ODIN SYSTEM ARCHITECTURE (use this for any ODIN-related issues):
- Hosting: Railway.app — project "natural-analysis"
- API: Python 3.13, Flask, gunicorn — deployed from /ODIN root, Procfile: cd api && gunicorn app:app
- Database: PostgreSQL (Railway Postgres-9BFG) — connected via DATABASE_URL env var
- Auth: JWT (PyJWT), bcrypt passwords, 24hr token expiry
- Live URL: https://odin-api-production-e85d.up.railway.app
- Key env vars: DATABASE_URL, JWT_SECRET_KEY, BASE_URL, SLACK_BOT_TOKEN,
  SLACK_CHANNEL_BROCK, SLACK_CHANNEL_WIFE, SLACK_SIGNING_SECRET,
  XLEADS_API_KEY, XLEADS_LOCATION_ID, XLEADS_BASE_URL, ANTHROPIC_API_KEY
- XLeads: GoHighLevel CRM API v2.0 — base URL services.leadconnectorhq.com
- Slack: Bot app A0B5X5ZRU4U, Events API endpoint /api/slack/events
- Python deps: flask, psycopg2-binary, PyJWT, bcrypt, requests, python-dotenv, gunicorn, anthropic
- Dashboard: Single HTML SPA at /dashboard/index.html served at /
- Agents: business_scout, income_calculator, business_builder, automation_auditor, it_agent

COMMON FAILURE MODES:
- 502 Bad Gateway: gunicorn crashed, check Railway logs for Python errors
- JWT errors: token expired (re-login), or JWT_SECRET_KEY mismatch between deploys
- DB connection errors: DATABASE_URL malformed, Postgres service restarted
- XLeads 401: XLEADS_API_KEY expired or wrong, regenerate in XLeads → Settings → Private Integrations
- XLeads 422: wrong payload format for that GHL API endpoint
- Slack events not firing: Event Subscriptions URL not verified, or bot not in channel
- Slack DMs not working: App Home → "Allow users to send messages" not enabled
- Import errors on deploy: missing package in requirements.txt, or wrong import path
- Static files 404: dashboard/index.html path wrong relative to Procfile working dir

KATELYN'S BUSINESS PLATFORMS (common issues):
- Etsy: listing fees, SEO title/tag limits, shop suspension triggers, payment holds
- Canva: export quality settings, brand kit setup, commercial license questions
- Gumroad: payout thresholds, product delivery, affiliate setup
- Pinterest: business account, rich pins, claim website
- Shopify: theme customization, payment gateway setup, shipping zones
- Instagram/Facebook: business account, Meta Business Suite, ad account issues
"""

SYSTEM = f"""You are ODIN's dedicated IT support agent — a senior full-stack engineer and
systems troubleshooter. You debug problems methodically: understand the symptom, identify the
root cause, provide a clear resolution, and note how to prevent recurrence.

Your knowledge base:
{ODIN_CONTEXT}

RESPONSE FORMAT — always return valid JSON only (no markdown outside the JSON):
{{
  "problem_summary": "string — your understanding of the issue in 1 sentence",
  "likely_cause": "string — most probable root cause",
  "confidence": "low|medium|high",
  "diagnosis_steps": [
    {{
      "step": number,
      "action": "string — what to check or do",
      "expected_result": "string — what you should see if this is the cause",
      "command_or_url": "string|null — exact command, URL, or code to run"
    }}
  ],
  "resolution": {{
    "summary": "string — what to do to fix it",
    "steps": ["step1", "step2", "step3"],
    "estimated_time_minutes": number
  }},
  "prevention": "string — how to prevent this from happening again",
  "escalate_if": "string — conditions under which this needs deeper investigation",
  "related_docs": ["string — relevant Railway/Slack/GHL/platform doc or setting name"]
}}

Be specific. 'Check the logs' is not useful — 'Run `railway logs --tail 50` and look for
NameError or ImportError in the last 10 lines' is useful. If you need more information to
diagnose accurately, say so in problem_summary and put clarifying questions in diagnosis_steps."""


def _get_system_health() -> dict:
    """Ping ODIN's health endpoint and return current status."""
    base = os.environ.get('BASE_URL', 'https://odin-api-production-e85d.up.railway.app')
    try:
        r = http.get(f'{base}/api/health', timeout=5)
        return r.json()
    except Exception as e:
        return {'status': 'unreachable', 'error': str(e)}


def run(problem: str, include_health_check: bool = True, get_db=None) -> dict:
    """
    Diagnose a problem and return structured resolution steps + slack_text.
    If get_db is provided, recent agent error logs are injected into the diagnosis.
    """
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    # Live health check
    health_context = ''
    if include_health_check:
        health = _get_system_health()
        health_context = f'\n\nCurrent ODIN health check: {json.dumps(health)}'

    # Recent agent error logs — gives IT agent actual stack traces to work with
    log_context = ''
    if get_db:
        try:
            from utils import agent_log as _agent_log
            log_context = '\n\n' + _agent_log.get_error_context(get_db, limit=10)
        except Exception:
            pass

    prompt = (
        f"Problem reported: {problem}"
        f"{health_context}"
        f"{log_context}"
        f"\n\nReturn only the JSON diagnosis."
    )

    resp = client.messages.create(
        model='claude-sonnet-4-6',   # Use Sonnet for IT — deeper reasoning needed
        max_tokens=1500,
        system=SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith('```'):
        raw = raw.split('```')[1]
        if raw.startswith('json'):
            raw = raw[4:]
    data = json.loads(raw)

    # Build Slack text
    conf_emoji = {'high': '🟢', 'medium': '🟡', 'low': '🔴'}.get(
        data.get('confidence', 'low'), '⚪')

    lines = [f'*🖥️ IT Diagnosis*\n']
    lines.append(f'*Problem:* {data.get("problem_summary", problem)}')
    lines.append(
        f'*Likely cause:* {data.get("likely_cause", "—")} '
        f'{conf_emoji} _{data.get("confidence", "?")} confidence_\n'
    )

    steps = data.get('diagnosis_steps', [])
    if steps:
        lines.append('*🔍 Diagnose first:*')
        for s in steps:
            lines.append(f'  {s["step"]}. {s["action"]}')
            if s.get('command_or_url'):
                lines.append(f'     `{s["command_or_url"]}`')
            if s.get('expected_result'):
                lines.append(f'     → _{s["expected_result"]}_')

    res = data.get('resolution', {})
    if res:
        lines.append(f'\n*✅ Resolution* (~{res.get("estimated_time_minutes", "?")} min):')
        lines.append(f'_{res.get("summary", "")}_')
        for i, step in enumerate(res.get('steps', []), 1):
            lines.append(f'  {i}. {step}')

    if data.get('prevention'):
        lines.append(f'\n*🛡️ Prevention:* {data["prevention"]}')

    if data.get('escalate_if'):
        lines.append(f'*⚠️ Escalate if:* {data["escalate_if"]}')

    lines.append('\n_Reply `debug <follow-up question>` for more detail_')

    data['slack_text'] = '\n'.join(lines)
    return data
