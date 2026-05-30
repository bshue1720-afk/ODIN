"""
ODIN Agent: income_calculator
Realistic revenue modeling for a given business type.
Input:  business_name, hours_per_week, starting_budget, platform (optional)
Output: conservative / realistic / optimistic monthly projections + assumptions
"""
import os, json, anthropic

SYSTEM = """You are a financial modeler specializing in online business revenue projections.
You produce REALISTIC numbers — not best-case fantasies. Factor in:
- Typical platform fees (Etsy 6.5% + listing fees, Shopify $39/mo, etc.)
- Real ramp time (most businesses take 3-6 months to get traction)
- Churn, refunds, slow months
- The specific hours/week and budget provided

Always return ONLY valid JSON matching this schema (no markdown, no explanation):
{
  "business": "string",
  "currency": "USD",
  "scenarios": {
    "conservative": {
      "month_1": number,
      "month_3": number,
      "month_6": number,
      "month_12": number,
      "annual": number
    },
    "realistic": {
      "month_1": number,
      "month_3": number,
      "month_6": number,
      "month_12": number,
      "annual": number
    },
    "optimistic": {
      "month_1": number,
      "month_3": number,
      "month_6": number,
      "month_12": number,
      "annual": number
    }
  },
  "monthly_costs": number,
  "break_even_month": number,
  "key_assumptions": ["assumption1", "assumption2", "assumption3", "assumption4"],
  "biggest_risk": "string",
  "biggest_opportunity": "string"
}

Be honest. If month_1 is likely $0 for a new Etsy store, say $0. Don't inflate early numbers."""


def run(business_name: str, hours_per_week: int = 10,
        starting_budget: int = 200, platform: str = '') -> dict:
    """
    Returns revenue model dict + slack_text for display.
    """
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

    prompt = (
        f"Model revenue for: {business_name}\n"
        f"- Hours/week available: {hours_per_week}\n"
        f"- Starting budget: ${starting_budget}\n"
        f"- Primary platform: {platform or 'most common for this business type'}\n"
        f"Return only the JSON schema as specified."
    )

    resp = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=1800,
        system=SYSTEM,
        messages=[{'role': 'user', 'content': prompt}],
    )

    raw = resp.content[0].text.strip()
    # Extract JSON — find first { and last } to handle any surrounding text/fences
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    data = json.loads(raw)

    s = data.get('scenarios', {})
    c = s.get('conservative', {})
    r = s.get('realistic', {})
    o = s.get('optimistic', {})

    lines = [f'*💰 Income Model — {data["business"]}*\n']
    lines.append('```')
    lines.append(f'{"Month":<8} {"Conservative":>14} {"Realistic":>12} {"Optimistic":>12}')
    lines.append('-' * 50)
    for m, key in [(1, 'month_1'), (3, 'month_3'), (6, 'month_6'), (12, 'month_12')]:
        lines.append(
            f'Month {m:<3} '
            f'${c.get(key, 0):>12,.0f} '
            f'${r.get(key, 0):>10,.0f} '
            f'${o.get(key, 0):>10,.0f}'
        )
    lines.append('-' * 50)
    lines.append(
        f'{"Annual":<8} '
        f'${c.get("annual", 0):>12,.0f} '
        f'${r.get("annual", 0):>10,.0f} '
        f'${o.get("annual", 0):>10,.0f}'
    )
    lines.append('```')

    lines.append(
        f'📋 Monthly costs: ~${data.get("monthly_costs", 0):.0f}  '
        f'| Break-even: Month {data.get("break_even_month", "?")}')
    lines.append(f'⚠️ Biggest risk: {data.get("biggest_risk", "—")}')
    lines.append(f'🚀 Best opportunity: {data.get("biggest_opportunity", "—")}')
    lines.append('\n*Key assumptions:*')
    for a in data.get('key_assumptions', []):
        lines.append(f'  • {a}')
    lines.append('\n_`build <business>` for task breakdown · `audit <business>` for automation map_')

    data['slack_text'] = '\n'.join(lines)
    return data
