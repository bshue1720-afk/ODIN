"""
ODIN Skill — Offer Calculator
Combines ARV analysis + LAO math into a single full deal summary.
Calls arv_analyzer (Claude) then runs lao_calculator (pure math).
"""

import utils.arv_analyzer as arv_mod
import utils.lao_calculator as lao_mod


def analyze_deal(address: str, beds: int = 3, baths: float = 2.0,
                 sqft: int = 0, year_built: int = 0,
                 condition: str = 'unknown', zip_code: str = '',
                 rehab_override: float = None,
                 target_fee: float = 20000.0) -> dict:
    """
    Full deal analysis: ARV estimate → LAO deal math.

    Args:
        address:        Property address
        beds/baths/sqft/year_built/condition/zip_code: Property details
        rehab_override: Use this rehab estimate instead of ARV analyzer's mid estimate
        target_fee:     Target assignment fee (default $20k)

    Returns combined arv + deal math dict.
    """
    # Step 1: ARV estimate
    arv = arv_mod.analyze(
        address=address, beds=beds, baths=baths,
        sqft=sqft, year_built=year_built,
        condition=condition, zip_code=zip_code
    )

    # Step 2: LAO math using mid ARV + mid rehab
    rehab_est = rehab_override if rehab_override is not None else (
        (arv['rehab_low'] + arv['rehab_high']) / 2
    )
    deal = lao_mod.calculate(
        arv=arv['arv_mid'],
        rehab_est=rehab_est,
        target_fee=target_fee,
    )

    return {
        'address':      address,
        'arv':          arv,
        'deal':         deal,
        'rehab_used':   rehab_est,
    }


def format_slack(result: dict) -> str:
    """Full deal summary for Slack."""
    arv  = result['arv']
    deal = result['deal']
    addr = result['address']

    lines = [f'*🏠 Full Deal Analysis — {addr}*']
    lines.append(f'_{arv["beds"]}bd / {arv["baths"]}ba'
                 f'{" / " + str(arv["sqft"]) + "sqft" if arv["sqft"] else ""}'
                 f' | Condition: {arv["condition"]} | Confidence: {arv["confidence"]}_')
    lines.append('')
    lines.append(f'*ARV range:*   ${arv["arv_low"]:,} – ${arv["arv_high"]:,}  (mid: *${arv["arv_mid"]:,}*)')
    lines.append(f'*Rehab est:*   ${arv["rehab_low"]:,} – ${arv["rehab_high"]:,}  (used: ${result["rehab_used"]:,.0f})')
    lines.append('')
    lines.append(f'*Opening LAO:*     `${deal["lao"]:,}` → your fee: *${deal["fee_at_lao"]:,}*')
    lines.append(f'*Walk-up max:*     `${deal["walk_up_max"]:,}` → your fee: *${deal["fee_at_walkup"]:,}*')
    lines.append(f'*Assign price:*    ${deal["assign_price"]:,}')
    lines.append(f'*Contract for ${deal["target_fee"]:,.0f} fee:* `${deal["contract_for_target"]:,}`')
    lines.append('')
    lines.append(f'*Verdict: {deal["verdict"]}*')
    if arv['notes']:
        lines.append(f'_Notes: {arv["notes"]}_')
    return '\n'.join(lines)
