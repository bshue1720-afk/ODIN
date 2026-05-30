"""
ODIN Skill — Buyer Matcher
Matches a deal to buyers in the ODIN database + known Memphis buyers.
Uses Claude to rank and explain fit.
"""

import os
import anthropic

_client = None

# Known Memphis cash buyers (from context doc + research)
# These supplement whatever is in the DB buyers table
KNOWN_BUYERS = [
    {
        'name': 'Spencer Buys Houses',
        'market': 'Memphis TN',
        'website': 'spencerbuyshouses.com',
        'volume': '6-7 deals/month',
        'notes': 'Active Memphis cash buyer, high volume',
    },
    {
        'name': '901 Real Estate Investments',
        'market': 'Memphis TN',
        'phone': '(901) 667-8111',
        'notes': 'Local Memphis investor',
    },
    {
        'name': 'Simply Sold RE',
        'market': 'Memphis TN',
        'website': 'simplesoldre.com',
        'notes': 'Memphis cash buyer',
    },
    {
        'name': 'Memphis Investment Properties',
        'market': 'Memphis TN',
        'website': 'mymemphisinvestmentproperties.com',
        'notes': 'Memphis investor, focuses on rentals',
    },
]


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        _client = anthropic.Anthropic(api_key=key)
    return _client


def match(deal: dict, db_buyers: list = None) -> dict:
    """
    Rank buyers for a deal.

    Args:
        deal: dict with keys: address, zip_code, arv_mid, assign_price,
              beds, baths, sqft, condition, fee_at_lao
        db_buyers: list of buyer dicts from DB (optional)

    Returns ranked list of buyers with fit explanation.
    """
    client = _get_client()

    all_buyers = list(KNOWN_BUYERS)
    if db_buyers:
        all_buyers.extend(db_buyers)

    def _buyer_line(b):
        parts = [f'- {b.get("name","?")}']
        market = b.get('market') or f'{b.get("city","")}, {b.get("state","")}'.strip(', ')
        parts.append(f'Market: {market}')
        buyer_types = []
        if b.get('is_flipper'):     buyer_types.append('Flipper')
        if b.get('is_landlord'):    buyer_types.append('Landlord')
        if b.get('is_note_holder'): buyer_types.append('Note Holder')
        if buyer_types: parts.append(f'Type: {"/".join(buyer_types)}')
        if b.get('flipped_count'):
            parts.append(f'{b["flipped_count"]} flips (avg ${int(b.get("flipped_avg_profit",0)):,} profit)')
        if b.get('portfolio_owned'):
            parts.append(f'{b["portfolio_owned"]} props owned')
        if b.get('max_price'):      parts.append(f'Max: ${b["max_price"]:,}')
        if b.get('buy_box'):        parts.append(f'Buy box: {b["buy_box"]}')
        if b.get('notes'):          parts.append(b['notes'])
        return ' | '.join(parts)

    buyers_text = '\n'.join(_buyer_line(b) for b in all_buyers)

    deal_text = (
        f'Address: {deal.get("address","")}\n'
        f'ZIP: {deal.get("zip_code","")}\n'
        f'Beds/Baths/Sqft: {deal.get("beds",3)}bd / {deal.get("baths",2)}ba / {deal.get("sqft",0)}sqft\n'
        f'Condition: {deal.get("condition","unknown")}\n'
        f'ARV: ${deal.get("arv_mid",0):,}\n'
        f'Assignment price: ${deal.get("assign_price",0):,}\n'
        f'Your fee at LAO: ${deal.get("fee_at_lao",0):,}'
    )

    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=500,
        messages=[{
            'role': 'user',
            'content': (
                f'Rank these buyers for this Memphis wholesale deal. '
                f'Consider price range, market, buy box, and deal fit.\n\n'
                f'DEAL:\n{deal_text}\n\n'
                f'BUYERS:\n{buyers_text}\n\n'
                f'Respond with ranked list:\n'
                f'1. [Buyer Name] — [1 sentence why they fit]\n'
                f'2. ...\n'
                f'Then one line: CONTACT_FIRST: [best buyer name]'
            )
        }]
    )

    ranked_text = msg.content[0].text.strip()

    # Extract top buyer name
    contact_first = ''
    for line in ranked_text.splitlines():
        if line.upper().startswith('CONTACT_FIRST:'):
            contact_first = line.split(':', 1)[1].strip()
            break

    return {
        'deal':          deal,
        'all_buyers':    all_buyers,
        'ranked_text':   ranked_text,
        'contact_first': contact_first,
    }


def format_slack(result: dict) -> str:
    lines = ['*🤝 Buyer Match Results*']
    lines.append(f'Deal: {result["deal"].get("address","")}')
    lines.append(f'Assign price: ${result["deal"].get("assign_price",0):,}')
    lines.append('')
    # Filter out the CONTACT_FIRST line from display
    ranked_lines = [l for l in result['ranked_text'].splitlines()
                    if not l.upper().startswith('CONTACT_FIRST:')]
    lines.extend(ranked_lines)
    if result['contact_first']:
        lines.append(f'\n*→ Call first: {result["contact_first"]}*')
    return '\n'.join(lines)
