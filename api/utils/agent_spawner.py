"""
ODIN — Dynamic Agent Spawner
Decomposes complex tasks into sub-agent steps, runs them in sequence,
and synthesizes a final response.

Slack: spawn <task description>
API:   POST /api/agents/spawn  {"task": "..."}

Flow:
  1. Claude (Haiku) decomposes task into ≤5 sequential steps
  2. Each step maps to a known utility or an ad-hoc Claude prompt
  3. Step outputs chain via {{step_N_output}} placeholders
  4. Claude synthesizes a final Slack-ready summary
  5. Run is logged to agent_runs + agent_logs tables
"""
import json
import os
import time

import anthropic
import psycopg2
import psycopg2.extras

_ODIN_CONTEXT = (
    "You are ODIN — Orchestrated Decision Intelligence Network. "
    "You are a business OS running for Brock Shue (Shue Box LLC). "
    "Brock runs: (1) real estate wholesaling in Memphis TN — 1,900 leads, buyer DB, XLeads/GHL pipeline; "
    "(2) AI Audit — $497 AI automation scored-PDF offer, active email campaign to Columbus local businesses (roofing/dental/auto/law/plumbing); Valdr Ops is ON HOLD (Twilio blocked); "
    "(3) PartSync Pro — HVAC contractor CRM/parts tool (warm leads, on hold). "
    "You have access to: Slack commands, XLeads API, buyer DB, memory table, heartbeat jobs. "
    "Be concise, direct, and business-first. No generic AI assistant behavior."
)

_DECOMPOSE_PROMPT = """You are ODIN's task decomposer. Break the user's task into sequential sub-agent steps.

Available built-in agents:
- scout    — find and score business opportunities (params: keywords, budget_usd, hours_per_week)
- income   — revenue model for a business (params: business_name, hours_per_week, starting_budget)
- build    — step-by-step launch plan (params: business_name)
- audit    — automation map (params: business_name)
- it       — diagnose / fix technical problems (params: problem)
- claude   — run a custom Claude prompt (params: prompt, context)

Return ONLY a valid JSON array of steps — no markdown, no explanation:
[
  {"step": 1, "agent": "scout", "description": "...", "params": {...}},
  {"step": 2, "agent": "claude", "description": "...", "params": {"prompt": "Summarize: ...", "context": "{{step_1_output}}"}}
]

Rules:
- Max 5 steps
- Use {{step_N_output}} in any param string to inject that step's output
- Prefer built-in agents over ad-hoc claude steps when they fit
- Keep steps focused; avoid redundancy"""


def spawn(task: str, get_db=None, xleads_mod=None, triggered_by: str = 'api') -> dict:
    """
    Decompose task → run sub-agents → synthesize result.

    Returns:
        {
          'slack_text':   str,    # formatted Slack output
          'steps':        list,   # per-step results
          'run_id':       str,    # UUID from agent_runs (or None)
          'duration_ms':  int,
        }
    """
    import utils.business_scout    as scout_agent
    import utils.income_calculator as income_agent
    import utils.business_builder  as builder_agent
    import utils.automation_auditor as auditor_agent
    import utils.it_agent          as it_agent
    import utils.agent_log         as agent_log

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return {
            'slack_text':  '⚠️ ANTHROPIC_API_KEY not set — spawner unavailable.',
            'steps':       [],
            'run_id':      None,
            'duration_ms': 0,
        }

    client = anthropic.Anthropic(api_key=api_key)
    run_id = None
    start  = time.monotonic()

    # ── 1. Create run record ──────────────────────────────────────────────────
    if get_db:
        try:
            conn = get_db()
            cur  = conn.cursor()
            cur.execute(
                "INSERT INTO agent_runs (task, triggered_by, status) VALUES (%s, %s, 'running') RETURNING id",
                (task[:500], triggered_by),
            )
            run_id = str(cur.fetchone()[0])
            conn.commit()
            conn.close()
        except Exception:
            pass

    steps_run: list = []

    try:
        # ── 2. Decompose task ─────────────────────────────────────────────────
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            system=_DECOMPOSE_PROMPT,
            messages=[{'role': 'user', 'content': f'Task: {task}'}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.lower().startswith('json'):
                raw = raw[4:]
        steps: list = json.loads(raw)
        if not isinstance(steps, list) or not steps:
            raise ValueError('Empty plan')
    except Exception:
        # If task is too short/vague to decompose, bail early
        if len(task.strip().split()) <= 2:
            msg = (
                f":robot_face: *spawn* needs more context — `{task}` is too vague.\n"
                "Try: `spawn find hot leads in Memphis` or `spawn build a follow-up sequence for cold leads`"
            )
            return {'slack_text': msg, 'steps': [], 'run_id': run_id, 'duration_ms': 0}
        # Fall back to a single ad-hoc Claude step
        steps = [{'step': 1, 'agent': 'claude', 'description': task,
                  'params': {'prompt': task}}]

    # ── 3. Execute steps ──────────────────────────────────────────────────────
    step_outputs: dict = {}

    for step_def in steps[:5]:
        step_num = int(step_def.get('step', len(steps_run) + 1))
        agent    = str(step_def.get('agent', 'claude')).lower()
        params   = _resolve_placeholders(step_def.get('params', {}), step_outputs)
        desc     = step_def.get('description', '')

        step_result = {
            'step':        step_num,
            'agent':       agent,
            'description': desc,
            'output':      '',
            'status':      'ok',
        }

        try:
            if agent == 'scout':
                r = scout_agent.run(
                    keywords=params.get('keywords', task),
                    skills=params.get('skills', ''),
                    budget_usd=int(params.get('budget_usd', 500)),
                    hours_per_week=int(params.get('hours_per_week', 10)),
                )
                step_result['output'] = r.get('slack_text', str(r))[:800]

            elif agent == 'income':
                r = income_agent.run(
                    business_name=params.get('business_name', task),
                    hours_per_week=int(params.get('hours_per_week', 10)),
                    starting_budget=int(params.get('starting_budget', params.get('budget_usd', 200))),
                    platform=params.get('platform', ''),
                )
                step_result['output'] = r.get('slack_text', str(r))[:800]

            elif agent == 'build':
                r = builder_agent.run(business_name=params.get('business_name', task))
                step_result['output'] = r.get('slack_text', str(r))[:800]

            elif agent == 'audit':
                r = auditor_agent.run(business_name=params.get('business_name', task))
                step_result['output'] = r.get('slack_text', str(r))[:800]

            elif agent == 'it':
                r = it_agent.run(
                    problem=params.get('problem', task),
                    include_health_check=bool(params.get('health_check', False)),
                )
                step_result['output'] = r.get('slack_text', str(r))[:800]

            else:
                # ad-hoc Claude prompt (agent == 'claude' or unknown agent name)
                prompt_text = params.get('prompt', desc or task)
                ctx         = params.get('context', '')
                if ctx:
                    prompt_text = f"{prompt_text}\n\nContext:\n{ctx}"
                claude_resp = client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=700,
                    system=_ODIN_CONTEXT,
                    messages=[{'role': 'user', 'content': prompt_text}],
                )
                step_result['output'] = claude_resp.content[0].text.strip()[:800]

        except Exception as step_err:
            step_result['status'] = 'error'
            step_result['output'] = f'Step {step_num} error ({agent}): {step_err}'

        step_outputs[step_num] = step_result['output']
        steps_run.append(step_result)

    # ── 4. Synthesize final output ────────────────────────────────────────────
    if len(steps_run) == 1 and steps_run[0]['agent'] in ('claude',):
        final_text = steps_run[0]['output']
    else:
        steps_summary = '\n\n'.join(
            f"Step {s['step']} ({s['agent']}): {s['output']}"
            for s in steps_run
        )
        try:
            synth = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=900,
                system=(
                    _ODIN_CONTEXT + ' '
                    'Synthesize the agent outputs into a concise, actionable Slack summary. '
                    'Use bullet points and bold key findings. '
                    'Keep it under 900 characters. No intro fluff.'
                ),
                messages=[{
                    'role': 'user',
                    'content': (
                        f'Original task: {task}\n\n'
                        f'Agent outputs:\n{steps_summary}\n\n'
                        f'Synthesize into a final answer.'
                    ),
                }],
            )
            final_text = synth.content[0].text.strip()
        except Exception:
            final_text = steps_summary[:1400]

    duration_ms = int((time.monotonic() - start) * 1000)

    # ── 5. Persist run ────────────────────────────────────────────────────────
    if run_id and get_db:
        try:
            conn = get_db()
            cur  = conn.cursor()
            cur.execute(
                """UPDATE agent_runs
                   SET status='done', steps=%s, result=%s,
                       duration_ms=%s, completed_at=NOW()
                   WHERE id=%s""",
                (json.dumps(steps_run), final_text[:2000], duration_ms, run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    try:
        import utils.agent_log as agent_log
        agent_log.write(
            agent_name='agent_spawner',
            action=f'spawn: {task[:80]}',
            status='ok',
            input_summary=task[:500],
            output_summary=final_text[:500],
            duration_ms=duration_ms,
            triggered_by=triggered_by,
            get_db=get_db,
        )
    except Exception:
        pass

    # ── 6. Build Slack output ─────────────────────────────────────────────────
    steps_header = ' → '.join(f"*{s['agent']}*" for s in steps_run)
    slack_text = (
        f":robot_face: *Spawned Task:* {task}\n"
        f"_Steps: {steps_header} | {duration_ms}ms_\n\n"
        f"{final_text}"
    )

    return {
        'slack_text':  slack_text,
        'steps':       steps_run,
        'run_id':      run_id,
        'duration_ms': duration_ms,
    }


def get_runs(get_db, limit: int = 10) -> list:
    """Return recent spawn runs for the `agent runs` Slack command."""
    if not get_db:
        return []
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """SELECT id, task, triggered_by, status, duration_ms, created_at
               FROM agent_runs
               ORDER BY created_at DESC
               LIMIT %s""",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _resolve_placeholders(params: dict, step_outputs: dict) -> dict:
    """Replace {{step_N_output}} tokens inside string param values."""
    resolved = {}
    for k, v in params.items():
        if isinstance(v, str):
            for step_num, output in step_outputs.items():
                v = v.replace(f'{{{{step_{step_num}_output}}}}', output)
        resolved[k] = v
    return resolved
