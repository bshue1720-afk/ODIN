"""
ODIN Skill — LAO Calculator
Pure math. No API calls. Instant deal math for Memphis wholesaling.

LAO Formula (Shue Box LLC / Flip With Rick):
  LAO          = ARV × 0.42   (opening offer — blame partner Rick)
  Walk-up max  = ARV × 0.55   (absolute ceiling under pressure)
  Assign price = ARV × 0.70 − rehab_est
  Fee at LAO   = assign_price − LAO
  Fee at walkup= assign_price − walk_up_max
"""


def calculate(arv: float, rehab_est: float = 0.0, target_fee: float = 20000.0) -> dict:
    """
    Run full LAO deal math.

    Args:
        arv:        After-repair value estimate ($)
        rehab_est:  Estimated rehab cost ($) — use 0 if unknown
        target_fee: Your target assignment fee ($) — default $20k

    Returns dict with all deal math fields.
    """
    lao            = round(arv * 0.42)
    walk_up_max    = round(arv * 0.55)
    assign_price   = round(arv * 0.70 - rehab_est)
    fee_at_lao     = round(assign_price - lao)
    fee_at_walkup  = round(assign_price - walk_up_max)

    # What contract price do you need to hit target fee?
    contract_for_target = round(assign_price - target_fee)

    # Is this deal worth pursuing?
    verdict = 'PASS'
    if fee_at_lao >= 30000:
        verdict = 'HOT — $30k+ fee at LAO'
    elif fee_at_lao >= 20000:
        verdict = 'STRONG — hits $20k target at LAO'
    elif fee_at_lao >= 10000:
        verdict = 'WORKABLE — negotiate hard'
    elif fee_at_walkup >= 5000:
        verdict = 'THIN — only viable at walk-up, risky'
    else:
        verdict = 'PASS — not enough margin'

    return {
        'arv':                  arv,
        'rehab_est':            rehab_est,
        'lao':                  lao,
        'walk_up_max':          walk_up_max,
        'assign_price':         assign_price,
        'fee_at_lao':           fee_at_lao,
        'fee_at_walkup':        fee_at_walkup,
        'contract_for_target':  contract_for_target,
        'target_fee':           target_fee,
        'verdict':              verdict,
    }


def format_slack(result: dict, address: str = '') -> str:
    """Format deal math as a Slack message."""
    lines = [f'*📊 Deal Math{" — " + address if address else ""}*']
    lines.append(f'ARV:            ${result["arv"]:,.0f}')
    lines.append(f'Rehab est:      ${result["rehab_est"]:,.0f}')
    lines.append(f'Assign price:   ${result["assign_price"]:,.0f}')
    lines.append('')
    lines.append(f'*LAO (opening):*    `${result["lao"]:,.0f}` → fee: *${result["fee_at_lao"]:,.0f}*')
    lines.append(f'*Walk-up max:*      `${result["walk_up_max"]:,.0f}` → fee: *${result["fee_at_walkup"]:,.0f}*')
    lines.append(f'Contract for ${result["target_fee"]:,.0f} fee: `${result["contract_for_target"]:,.0f}`')
    lines.append('')
    lines.append(f'Verdict: *{result["verdict"]}*')
    return '\n'.join(lines)
