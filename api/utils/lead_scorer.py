"""
ODIN Skill — Lead Scorer
Scores a lead using the MCTP framework via Claude.
Returns individual component scores + total + recommendation.

MCTP scoring (0-10 total):
  Motivation  0-3
  Condition   0-2
  Timeline    0-3
  Price       0-2
  Hot=8+  Warm=5-7  Cold=0-4
"""

import os
import anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        _client = anthropic.Anthropic(api_key=key)
    return _client


SYSTEM_PROMPT = """You are ODIN's lead scoring engine for Shue Box LLC, a real estate wholesaling company.
Score leads using the MCTP framework:

MOTIVATION (0-3):
  0 = Just curious, no real reason to sell
  1 = Some motivation, not urgent
  2 = Clear motivation, wants to sell
  3 = Highly motivated — financial distress, divorce, probate, foreclosure, moving, tired landlord

CONDITION (0-2):
  0 = Property in good shape, retail buyer can buy it
  1 = Needs some work — cosmetic or moderate repairs
  2 = Distressed — major rehab needed, can't get traditional financing

TIMELINE (0-3):
  0 = No urgency, "whenever"
  1 = 3-6 months
  2 = Within 60-90 days
  3 = ASAP — needs to close fast (30 days or less)

PRICE (0-2):
  0 = Wants full retail or above market
  1 = Flexible, open to discussing
  2 = Already at or near wholesale range, motivated by speed over price

Respond EXACTLY in this format:
MOTIVATION_SCORE: [0-3]
CONDITION_SCORE: [0-2]
TIMELINE_SCORE: [0-3]
PRICE_SCORE: [0-2]
TOTAL: [sum]
TIER: [Hot/Warm/Cold]
RECOMMENDATION: [1-2 sentences — what to do next]
KEY_FACTOR: [single most important thing about this lead]"""


def score(notes: str, address: str = '', caller_name: str = '') -> dict:
    """
    Score a lead from call notes or conversation text.

    Args:
        notes: Raw call notes, conversation transcript, or lead summary
        address: Property address (for context)
        caller_name: Seller name (for context)

    Returns MCTP scores + recommendation.
    """
    client = _get_client()

    context = ''
    if address:
        context += f'Property: {address}\n'
    if caller_name:
        context += f'Seller: {caller_name}\n'
    if context:
        context += '\n'

    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': f'{context}Call notes / conversation:\n{notes}'
        }]
    )

    text = msg.content[0].text.strip()
    result = {
        'address':          address,
        'caller_name':      caller_name,
        'motivation_score': 0,
        'condition_score':  0,
        'timeline_score':   0,
        'price_score':      0,
        'mctp_total':       0,
        'tier':             'Cold',
        'recommendation':   '',
        'key_factor':       '',
        'raw_notes':        notes,
    }

    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().upper()
        val = val.strip()
        try:
            if key == 'MOTIVATION_SCORE': result['motivation_score'] = int(val)
            elif key == 'CONDITION_SCORE': result['condition_score']  = int(val)
            elif key == 'TIMELINE_SCORE':  result['timeline_score']  = int(val)
            elif key == 'PRICE_SCORE':     result['price_score']     = int(val)
            elif key == 'TOTAL':           result['mctp_total']      = int(val)
            elif key == 'TIER':            result['tier']            = val
            elif key == 'RECOMMENDATION':  result['recommendation']  = val
            elif key == 'KEY_FACTOR':      result['key_factor']      = val
        except ValueError:
            pass

    # Recalculate total in case Claude drifted
    calc_total = (result['motivation_score'] + result['condition_score'] +
                  result['timeline_score'] + result['price_score'])
    result['mctp_total'] = calc_total
    if calc_total >= 8:
        result['tier'] = 'Hot'
    elif calc_total >= 5:
        result['tier'] = 'Warm'
    else:
        result['tier'] = 'Cold'

    return result


def format_slack(r: dict) -> str:
    tier_emoji = {'Hot': '🔥', 'Warm': '⚠️', 'Cold': '🧊'}.get(r['tier'], '❓')
    lines = [f'*{tier_emoji} MCTP Score — {r.get("address") or r.get("caller_name") or "Lead"}*']
    lines.append(f'Motivation: {r["motivation_score"]}/3 | Condition: {r["condition_score"]}/2 | '
                 f'Timeline: {r["timeline_score"]}/3 | Price: {r["price_score"]}/2')
    lines.append(f'*Total: {r["mctp_total"]}/10 — {r["tier"]}*')
    if r['key_factor']:
        lines.append(f'Key factor: _{r["key_factor"]}_')
    if r['recommendation']:
        lines.append(f'Next step: {r["recommendation"]}')
    return '\n'.join(lines)
