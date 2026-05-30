"""
ODIN Agent: automation_auditor
Maps every automatable touchpoint in a business and produces ODIN's build list.
Input:  business_name
Output: automation score, automatable list with specific scripts, manual list with reasons
"""
import os, json, anthropic

SYSTEM = """You are an automation architect. Given a business type, you audit every customer
and operational touchpoint to determine what can be automated using:
- Python scripts
- Email sequences (via GoHighLevel/XLeads CRM)
- SMS sequences (A2P 10DLC compliant)
- Social media scheduling
- Web scraping / data collection
- Invoice/contract generation
- Lead capture forms and CRM workflows

Always return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "business": "string",
  "automation_score": 0-100,
  "automatable_touchpoints": [
    {
      "touchpoint": "string — what business process this is",
      "trigger": "string — what event triggers this",
      "automation_type": "email_sequence|sms_sequence|social_post|script|webhook|crm_workflow",
      "tool": "string — GHL/XLeads, Python, Zapier, etc.",
      "script_name": "string — what ODIN would name this file e.g. etsy_followup.py",
      "estimated_build_hours": number,
      "impact": "low|medium|high"
    }
  ],
  "manual_tasks": [
    {
      "task": "string",
      "reason": "string — why this cannot be automated"
    }
  ],
  "total_build_hours": number,
  "scripts_to_build": ["script1.py", "script2.py"],
  "highest_impact_automation": "string — the single most valuable thing to automate first"
}

Be specific. Don't just say 'email automation' — say 'post-purchase thank you + upsell sequence
(3 emails over 7 days, triggered by Etsy order webhook)'. Only list things ODIN can realistically
build. Don't pad the list."""


def run(business_name: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    prompt = (
        f"Audit all automation opportunities for: {business_name}\n"
        f"ODIN has access to: GoHighLevel CRM (XLeads), Python scripting, "
        f"SMS/email via A2P, social scheduling via GHL Social Planner, "
        f"webhook integrations, and Claude AI for content generation.\n"
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

    score = data.get('automation_score', 0)
    score_bar = '█' * (score // 10) + '░' * (10 - score // 10)

    lines = [f'*🤖 Automation Audit — {data["business"]}*\n']
    lines.append(f'*Score: {score}/100*  `{score_bar}`\n')

    # Automatable touchpoints
    autos = data.get('automatable_touchpoints', [])
    high   = [a for a in autos if a.get('impact') == 'high']
    medium = [a for a in autos if a.get('impact') == 'medium']
    low    = [a for a in autos if a.get('impact') == 'low']

    if high:
        lines.append('*🔴 High Impact Automations:*')
        for a in high:
            lines.append(
                f'  • *{a["touchpoint"]}*\n'
                f'    Trigger: {a["trigger"]}\n'
                f'    Tool: {a["tool"]} → `{a["script_name"]}` ({a["estimated_build_hours"]}h build)'
            )

    if medium:
        lines.append('\n*🟡 Medium Impact:*')
        for a in medium:
            lines.append(f'  • {a["touchpoint"]} → `{a["script_name"]}`')

    if low:
        lines.append('\n*⚪ Lower Priority:*')
        for a in low:
            lines.append(f'  • {a["touchpoint"]} → `{a["script_name"]}`')

    # Manual tasks
    manual = data.get('manual_tasks', [])
    if manual:
        lines.append('\n*👤 Requires You (cannot automate):*')
        for m in manual:
            lines.append(f'  • {m["task"]} — _{m["reason"]}_')

    lines.append(
        f'\n*Build Summary:* {len(autos)} automations · '
        f'{data.get("total_build_hours", "?")} total build hours'
    )
    if data.get('highest_impact_automation'):
        lines.append(f'*Start with:* {data["highest_impact_automation"]}')

    lines.append('\n_Say `build it` to queue these scripts for ODIN to create_')

    data['slack_text'] = '\n'.join(lines)
    return data
