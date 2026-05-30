"""
ODIN Skill — Script Generator
Generates a personalized MCTP seller call script using Claude.
Based on Flip With Rick / Rick Ginn MCTP framework.
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


SYSTEM_PROMPT = """You are ODIN's script writer for Shue Box LLC, a real estate wholesaling company in Memphis TN.
Write personalized seller call scripts for Brock (the wholesaler).

VOICE: Warm, conversational, genuine. NOT salesy. Brock is the "good cop" — his partner Rick is the "bad cop" who ran the numbers.

MCTP FRAMEWORK:
  M — Motivation: Find out WHY they're selling (not just that they want to)
  C — Condition: Get exact condition details (what needs work)
  T — Timeline: When do they NEED to be done
  P — Price: What number works for them (not what they want, what WORKS)

KEY TACTICS:
  - Use "my partner Rick ran the numbers" to deflect on low offers
  - Never give a number first if avoidable
  - Acknowledge hardship before business
  - "What would make this a no-brainer for you?" to get price
  - Always set a next step / follow-up before hanging up

LAO FORMULA (for reference — don't share with seller):
  Opening offer = ARV × 42%  ("my partner says we can do...")
  Walk-up max   = ARV × 55%  (absolute ceiling)

Output a ready-to-read script with:
1. Opening / rapport (3-4 lines)
2. MCTP discovery questions (4 questions, one per component)
3. Offer delivery (with partner Rick deflection)
4. Objection handlers (3 most likely)
5. Close / next step"""


def generate(seller_name: str = '', address: str = '',
             motivation_hint: str = '', condition_hint: str = '',
             timeline_hint: str = '', price_hint: str = '',
             mctp_score: dict = None) -> str:
    """
    Generate a personalized MCTP call script.

    Args:
        seller_name:     Seller's name
        address:         Property address
        motivation_hint: Any known motivation (e.g. "probate", "divorce")
        condition_hint:  Known condition info
        timeline_hint:   Known timeline info
        price_hint:      Known asking price or price expectation
        mctp_score:      Existing MCTP score dict (if available)

    Returns ready-to-read script as plain text.
    """
    client = _get_client()

    context_parts = []
    if seller_name:  context_parts.append(f'Seller name: {seller_name}')
    if address:      context_parts.append(f'Property: {address}')
    if motivation_hint: context_parts.append(f'Known motivation: {motivation_hint}')
    if condition_hint:  context_parts.append(f'Known condition: {condition_hint}')
    if timeline_hint:   context_parts.append(f'Known timeline: {timeline_hint}')
    if price_hint:      context_parts.append(f'Known price expectation: {price_hint}')
    if mctp_score:
        context_parts.append(
            f'MCTP scores so far — M:{mctp_score.get("motivation_score",0)} '
            f'C:{mctp_score.get("condition_score",0)} '
            f'T:{mctp_score.get("timeline_score",0)} '
            f'P:{mctp_score.get("price_score",0)} '
            f'(focus script on weakest areas)'
        )

    context = '\n'.join(context_parts) if context_parts else 'No prior info — cold approach'

    msg = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{
            'role': 'user',
            'content': f'Generate a call script for this seller:\n{context}'
        }]
    )

    return msg.content[0].text.strip()
