"""
ODIN Agent: business_builder
Step-by-step build breakdown for a specific business.
Input:  business_name
Output: accounts to create, setup tasks, recurring tasks — each tagged ODIN/manual + hours
"""
import os, json, anthropic

SYSTEM = """You are a business launch specialist. Given a business name, produce a complete
ordered task breakdown covering every step from zero to first sale and ongoing operation.

Always return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "business": "string",
  "total_setup_hours": number,
  "accounts_to_create": [
    {
      "platform": "string",
      "url": "string",
      "purpose": "string",
      "free": true|false,
      "monthly_cost_usd": number,
      "priority": 1-5
    }
  ],
  "setup_tasks": [
    {
      "step": number,
      "task": "string",
      "hours": number,
      "who": "you|ODIN|both",
      "notes": "string",
      "can_odin_automate": true|false
    }
  ],
  "recurring_tasks": [
    {
      "task": "string",
      "frequency": "daily|weekly|monthly",
      "hours": number,
      "who": "you|ODIN|both",
      "notes": "string"
    }
  ],
  "week_1_priority_actions": ["action1", "action2", "action3"]
}

Tag tasks as "ODIN" only if they can realistically be automated with Python scripts,
email/SMS sequences, social scheduling, or CRM workflows. Be specific in notes about HOW
ODIN would automate it. Tasks requiring human creativity or judgment = "you"."""


def run(business_name: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    prompt = (
        f"Create a complete launch breakdown for: {business_name}\n"
        f"Include every account, every setup task in order, and all ongoing recurring tasks.\n"
        f"Return only the JSON schema as specified."
    )

    resp = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=2500,
        system=SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = resp.content[0].text.strip()
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    data = json.loads(raw)

    lines = [f'*🏗️ Build Plan — {data["business"]}*\n']

    # Accounts
    accounts = data.get('accounts_to_create', [])
    if accounts:
        lines.append('*📋 Accounts to Create (in order):*')
        for a in sorted(accounts, key=lambda x: x.get('priority', 5)):
            cost = 'Free' if a.get('free') else f'${a.get("monthly_cost_usd", 0)}/mo'
            lines.append(f'  {a["priority"]}. *{a["platform"]}* — {a["purpose"]} ({cost})')

    # Setup tasks
    setup = data.get('setup_tasks', [])
    odin_count = sum(1 for t in setup if t.get('can_odin_automate'))
    you_count  = len(setup) - odin_count
    lines.append(f'\n*⚙️ Setup Tasks* ({data.get("total_setup_hours", "?")} hrs total '
                 f'| 🤖 {odin_count} ODIN · 👤 {you_count} you)')
    for t in setup:
        who_icon = '🤖' if t.get('can_odin_automate') else '👤'
        lines.append(
            f'  {t["step"]}. {who_icon} {t["task"]} '
            f'({t["hours"]}h)' +
            (f'\n     _{t["notes"]}_' if t.get('notes') else '')
        )

    # Recurring tasks
    recurring = data.get('recurring_tasks', [])
    if recurring:
        lines.append('\n*🔄 Recurring Tasks (ongoing):*')
        for t in recurring:
            who_icon = '🤖' if t.get('who') == 'ODIN' else '👤'
            lines.append(
                f'  {who_icon} {t["task"]} — {t["frequency"]} ({t["hours"]}h)'
            )

    # Week 1 priorities
    w1 = data.get('week_1_priority_actions', [])
    if w1:
        lines.append('\n*🎯 Do These First (Week 1):*')
        for i, a in enumerate(w1, 1):
            lines.append(f'  {i}. {a}')

    lines.append('\n_`audit <business>` to see full automation map · `income <business>` for revenue projections_')

    data['slack_text'] = '\n'.join(lines)
    return data
