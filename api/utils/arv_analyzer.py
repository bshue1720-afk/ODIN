"""
ODIN Skill — ARV Analyzer
Estimates ARV for Memphis TN properties using Claude + baked-in market data.
Uses Haiku for speed (cheap, fast, good enough for range estimates).
"""

import os
import anthropic

_client = None

MEMPHIS_MARKET_DATA = """
MEMPHIS TN MARKET DATA (Shelby County — current as of 2026):

ZIP 38109 (Southwest Memphis):
  Median sale price: $103,450 | YoY: -19% decline
  Distressed/wholesale range: $40,000–$85,000
  ARV ceiling (fully rehabbed): $120,000–$140,000
  Typical rehab: $25,000–$45,000
  Best lead types: Pre-foreclosure, tax delinquent, probate
  Target assignment fee: $12,000–$22,000
  Avg days on market: 45–75

ZIP 38111 (Berclair/East Memphis):
  Median sale price: $206,000 | Distressed comps: $89,500
  ARV ceiling (fully rehabbed): $180,000–$220,000
  Typical rehab: $30,000–$60,000
  Best lead types: Estate/probate, absentee owners, divorce
  Target assignment fee: $20,000–$50,000+ (BIG FEE zip)
  Avg days on market: 30–55

ZIP 38118 (Whitehaven):
  Median sale price: $130,000 | Strong rental demand
  ARV ceiling (fully rehabbed): $140,000–$165,000
  Typical rehab: $20,000–$40,000
  Best lead types: Absentee landlords, code violations, tired landlords
  Target assignment fee: $15,000–$28,000
  Avg days on market: 35–60

ZIP 38115 (Hickory Hill):
  Median sale price: $112,000 | Strong rental demand
  ARV ceiling (fully rehabbed): $130,000–$155,000
  Typical rehab: $20,000–$40,000
  Best lead types: Absentee landlords, tired landlords, code violations
  Target assignment fee: $15,000–$25,000
  Avg days on market: 35–55

ZIP 38125 (Southeast Memphis / Windyke):
  Median sale price: $155,000 | More stable neighborhood
  ARV ceiling (fully rehabbed): $170,000–$200,000
  Typical rehab: $25,000–$50,000
  Best lead types: Estate/probate, divorce, job relocation
  Target assignment fee: $20,000–$40,000
  Avg days on market: 30–50

ZIP 38128 (Raleigh):
  Median sale price: $95,000 | High investor activity
  ARV ceiling (fully rehabbed): $115,000–$140,000
  Typical rehab: $20,000–$40,000
  Best lead types: Pre-foreclosure, tax delinquent, absentee
  Target assignment fee: $12,000–$22,000
  Avg days on market: 40–65

ZIP 38016 (Cordova):
  Median sale price: $230,000 | Suburban, higher-end
  ARV ceiling (fully rehabbed): $240,000–$280,000
  Typical rehab: $30,000–$60,000
  Best lead types: Divorce, estate, tired landlords
  Target assignment fee: $25,000–$55,000
  Avg days on market: 25–45

ZIP 38671 (Southaven MS — adjacent market):
  Median sale price: $185,000 | Growing suburb
  ARV ceiling (fully rehabbed): $200,000–$230,000
  Typical rehab: $25,000–$50,000
  Target assignment fee: $20,000–$40,000
  Notes: Mississippi market — different title/closing process

ZIP 38654 (Olive Branch MS — adjacent market):
  Median sale price: $210,000 | Fast-growing
  ARV ceiling (fully rehabbed): $220,000–$260,000
  Typical rehab: $25,000–$55,000
  Target assignment fee: $22,000–$45,000
  Notes: Mississippi market — strong buyer demand

GENERAL MEMPHIS RULES:
  - Use 3 bed / same zip / sold last 90 days / similar sqft for comps
  - Cash buyer discount: 15–20% below retail ARV
  - Rehab light = $15k–$25k | medium = $30k–$50k | heavy = $55k–$90k
  - Properties pre-1970: add $5k–$10k for unknowns (lead paint, knob-and-tube)
  - Slab foundation preferred; crawl space adds $3k–$8k risk
  - Primary target zips: 38109, 38111, 38118 (Shue Box LLC active market)
  - Secondary zips: 38115, 38125, 38128, 38016
  - Adjacent markets: Southaven MS (38671), Olive Branch MS (38654)
"""


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        _client = anthropic.Anthropic(api_key=key)
    return _client


def analyze(address: str, beds: int = 3, baths: float = 2.0,
            sqft: int = 0, year_built: int = 0,
            condition: str = 'unknown', zip_code: str = '') -> dict:
    """
    Estimate ARV range for a Memphis property.

    Returns:
        arv_low, arv_mid, arv_high, rehab_est_low, rehab_est_high,
        confidence, notes, zip_code
    """
    client = _get_client()

    details = f"""
Property: {address}
Beds: {beds} | Baths: {baths} | Sqft: {sqft or 'unknown'} | Year built: {year_built or 'unknown'}
Condition: {condition}
ZIP: {zip_code or 'unknown — infer from address'}
"""

    prompt = f"""{MEMPHIS_MARKET_DATA}

Analyze this property and provide ARV and rehab estimates:
{details}

Respond in EXACTLY this format (no extra text):
ARV_LOW: [number]
ARV_MID: [number]
ARV_HIGH: [number]
REHAB_LOW: [number]
REHAB_HIGH: [number]
ZIP_USED: [zip code used]
CONFIDENCE: [LOW/MEDIUM/HIGH]
NOTES: [1-2 sentence explanation of key factors]"""

    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=300,
        messages=[{'role': 'user', 'content': prompt}]
    )

    text = msg.content[0].text.strip()
    result = {
        'address':      address,
        'beds':         beds,
        'baths':        baths,
        'sqft':         sqft,
        'year_built':   year_built,
        'condition':    condition,
        'arv_low':      0,
        'arv_mid':      0,
        'arv_high':     0,
        'rehab_low':    0,
        'rehab_high':   0,
        'zip_used':     zip_code,
        'confidence':   'LOW',
        'notes':        '',
    }

    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip().upper()
        val = val.strip()
        try:
            if key == 'ARV_LOW':      result['arv_low']    = int(val.replace(',', '').replace('$', ''))
            elif key == 'ARV_MID':    result['arv_mid']    = int(val.replace(',', '').replace('$', ''))
            elif key == 'ARV_HIGH':   result['arv_high']   = int(val.replace(',', '').replace('$', ''))
            elif key == 'REHAB_LOW':  result['rehab_low']  = int(val.replace(',', '').replace('$', ''))
            elif key == 'REHAB_HIGH': result['rehab_high'] = int(val.replace(',', '').replace('$', ''))
            elif key == 'ZIP_USED':   result['zip_used']   = val
            elif key == 'CONFIDENCE': result['confidence'] = val
            elif key == 'NOTES':      result['notes']      = val
        except ValueError:
            pass

    # Fallback mid if not parsed
    if result['arv_mid'] == 0 and result['arv_low'] and result['arv_high']:
        result['arv_mid'] = (result['arv_low'] + result['arv_high']) // 2

    return result


def format_slack(arv: dict, deal: dict = None, address: str = '') -> str:
    """Format ARV analysis as Slack message, optionally with deal math appended."""
    lines = [f'*🏠 ARV Analysis — {address or arv.get("address", "")}*']
    lines.append(f'Beds/Baths/Sqft: {arv["beds"]}bd / {arv["baths"]}ba / {arv["sqft"] or "?"}sqft')
    lines.append(f'Condition: {arv["condition"]} | Confidence: {arv["confidence"]}')
    lines.append('')
    lines.append(f'ARV range:  ${arv["arv_low"]:,} – ${arv["arv_high"]:,}')
    lines.append(f'ARV mid:    *${arv["arv_mid"]:,}*')
    lines.append(f'Rehab est:  ${arv["rehab_low"]:,} – ${arv["rehab_high"]:,}')
    if arv['notes']:
        lines.append(f'_Notes: {arv["notes"]}_')
    if deal:
        lines.append('')
        import utils.lao_calculator as lao
        lines.append(lao.format_slack(deal, address=''))
    return '\n'.join(lines)
