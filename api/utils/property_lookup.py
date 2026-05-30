"""
ODIN — Property Lookup
Queries Shelby County TN Assessor public search for free property data.
Caches results in property_lookup_cache table (30-day TTL).

Usage:
  result = property_lookup.lookup('4314 Leatherwood Ave', get_db)
  _slack_post(channel, property_lookup.format_slack(result))

Slack command:
  `lookup 4314 Leatherwood Ave Memphis`
"""

import re
import requests

SHELBY_BASE   = 'https://assessor.shelby.tn.gov'
SHELBY_SEARCH = f'{SHELBY_BASE}/TaxSearch/Search'

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


# ─── Public API ──────────────────────────────────────────────────────────────

def lookup(address: str, get_db=None) -> dict:
    """
    Look up property info from Shelby County Assessor.
    Returns dict with: address, owner, mailing_address, assessed_value,
    market_value, tax_status, year_built, sqft, bedrooms, parcel_id, source.
    Caches in DB for 30 days to avoid re-scraping the same address.
    """
    address_norm = _normalize(address)

    # ── Cache check ────────────────────────────────────────────────────────
    if get_db:
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT owner_name, mailing_address, assessed_value, market_value,
                           tax_status, year_built, sqft, bedrooms, parcel_id, fetched_at
                    FROM property_lookup_cache
                    WHERE address_norm = %s
                      AND fetched_at > NOW() - INTERVAL '30 days'
                """, (address_norm,))
                row = cur.fetchone()
            conn.close()
            if row:
                return {
                    'source':           'cache',
                    'address':          address,
                    'owner':            row['owner_name'],
                    'mailing_address':  row['mailing_address'],
                    'assessed_value':   float(row['assessed_value']) if row['assessed_value'] else None,
                    'market_value':     float(row['market_value'])   if row['market_value']   else None,
                    'tax_status':       row['tax_status'],
                    'year_built':       row['year_built'],
                    'sqft':             row['sqft'],
                    'bedrooms':         row['bedrooms'],
                    'parcel_id':        row['parcel_id'],
                    'fetched_at':       row['fetched_at'].isoformat() if row['fetched_at'] else None,
                }
        except Exception:
            pass  # Cache miss — fall through to live scrape

    # ── Live scrape ────────────────────────────────────────────────────────
    result = _scrape_shelby(address)
    result['address'] = address

    # ── Cache result ───────────────────────────────────────────────────────
    if get_db and not result.get('error'):
        try:
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO property_lookup_cache
                        (address, address_norm, owner_name, mailing_address,
                         assessed_value, market_value, tax_status,
                         year_built, sqft, bedrooms, parcel_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (address_norm) DO UPDATE SET
                        owner_name      = EXCLUDED.owner_name,
                        mailing_address = EXCLUDED.mailing_address,
                        assessed_value  = EXCLUDED.assessed_value,
                        market_value    = EXCLUDED.market_value,
                        tax_status      = EXCLUDED.tax_status,
                        year_built      = EXCLUDED.year_built,
                        sqft            = EXCLUDED.sqft,
                        bedrooms        = EXCLUDED.bedrooms,
                        parcel_id       = EXCLUDED.parcel_id,
                        fetched_at      = NOW()
                """, (
                    address, address_norm,
                    result.get('owner'),
                    result.get('mailing_address'),
                    result.get('assessed_value'),
                    result.get('market_value'),
                    result.get('tax_status'),
                    result.get('year_built'),
                    result.get('sqft'),
                    result.get('bedrooms'),
                    result.get('parcel_id'),
                ))
                conn.commit()
            conn.close()
        except Exception:
            pass

    return result


def format_slack(result: dict) -> str:
    """Format a property lookup result for Slack."""
    if result.get('error') and not result.get('owner'):
        return (
            f'❌ *Property lookup failed* — {result["address"]}\n'
            f'Reason: {result["error"]}\n\n'
            f'_Shelby County Assessor may not have this address. '
            f'Try: assessor.shelby.tn.gov and search manually._'
        )

    address  = result.get('address', 'Unknown address')
    owner    = result.get('owner') or '_(not found)_'
    mailing  = result.get('mailing_address') or '—'
    assessed = f'${result["assessed_value"]:,.0f}' if result.get('assessed_value') else '—'
    market   = f'${result["market_value"]:,.0f}'   if result.get('market_value')   else '—'
    tax      = result.get('tax_status') or '—'
    year     = str(result.get('year_built')) if result.get('year_built') else '—'
    sqft     = f'{result["sqft"]:,}'  if result.get('sqft')     else '—'
    beds     = str(result.get('bedrooms')) if result.get('bedrooms') else '—'
    parcel   = result.get('parcel_id') or '—'
    source   = '_(cached)_' if result.get('source') == 'cache' else '_(live — Shelby County Assessor)_'

    # Flag if owner mailing != property address (absentee owner = motivated seller signal)
    absentee = ''
    if mailing and mailing != '—' and address.lower().split()[0] not in mailing.lower():
        absentee = '\n   ⚡ _Absentee owner — mailing address differs (strong seller signal)_'

    return (
        f'*🏠 Property Lookup — {address}* {source}\n'
        f'• *Owner:* {owner}{absentee}\n'
        f'• *Mailing:* {mailing}\n'
        f'• *Assessed:* {assessed} | *Market:* {market}\n'
        f'• *Tax Status:* {tax}\n'
        f'• *Built:* {year} | *Sqft:* {sqft} | *Beds:* {beds}\n'
        f'• *Parcel:* {parcel}\n\n'
        f'`analyze {address}` to run full deal math | `match {address}` to find buyers'
    )


# ─── Internal helpers ────────────────────────────────────────────────────────

def _normalize(address: str) -> str:
    """Normalize address string for use as a cache key."""
    s = address.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    # Strip common city/state suffixes that vary between lookups
    s = re.sub(r',?\s*(memphis|tn|tennessee|38\d{3})\s*$', '', s).strip()
    return s


def _scrape_shelby(address: str) -> dict:
    """
    Scrape the Shelby County Assessor public search.
    Falls back to an empty result with error key on any failure.
    The Shelby County site uses a simple GET search with ?searchString=<address>
    """
    result = {}
    try:
        # The public parcel search form
        resp = requests.get(
            SHELBY_SEARCH,
            params={'searchString': address, 'searchType': 'address'},
            headers=_HEADERS,
            timeout=12,
        )

        if resp.status_code != 200:
            return {'error': f'Assessor returned HTTP {resp.status_code}'}

        html = resp.text

        # If no results found
        if 'no records found' in html.lower() or 'no results' in html.lower():
            return {'error': 'Address not found in Shelby County Assessor'}

        # ── Owner name ──────────────────────────────────────────────────────
        owner_m = (
            re.search(r'Owner(?:\s*Name)?[:\s]*</(?:td|th|span|div)[^>]*>\s*<(?:td|th|span|div)[^>]*>\s*([A-Z][A-Z\s,&.]+?)(?:\s*<|\s*$)', html)
            or re.search(r'(?:owner|name)["\s]+[^>]*>([A-Z][A-Z\s,&.]{5,50})<', html)
        )
        if owner_m:
            result['owner'] = owner_m.group(1).strip().title()

        # ── Mailing address ─────────────────────────────────────────────────
        mail_m = re.search(
            r'Mailing[:\s]*</[^>]+>\s*<[^>]+>\s*([^<]{10,80})<',
            html, re.IGNORECASE
        )
        if mail_m:
            result['mailing_address'] = mail_m.group(1).strip()

        # ── Assessed value ──────────────────────────────────────────────────
        assessed_m = re.search(
            r'Assessed[:\s]*(?:Value)?[:\s]*</[^>]+>\s*<[^>]+>\s*\$?([\d,]+)',
            html, re.IGNORECASE
        )
        if assessed_m:
            result['assessed_value'] = float(assessed_m.group(1).replace(',', ''))

        # ── Market / Appraised value ────────────────────────────────────────
        market_m = re.search(
            r'(?:Market|Appraised)[:\s]*(?:Value)?[:\s]*</[^>]+>\s*<[^>]+>\s*\$?([\d,]+)',
            html, re.IGNORECASE
        )
        if market_m:
            result['market_value'] = float(market_m.group(1).replace(',', ''))

        # ── Tax status ──────────────────────────────────────────────────────
        tax_m = re.search(
            r'Tax[:\s]*Status[:\s]*</[^>]+>\s*<[^>]+>\s*([^<]{3,40})<',
            html, re.IGNORECASE
        )
        if tax_m:
            result['tax_status'] = tax_m.group(1).strip()

        # ── Year built ──────────────────────────────────────────────────────
        year_m = re.search(
            r'Year\s*Built[:\s]*</[^>]+>\s*<[^>]+>\s*(\d{4})',
            html, re.IGNORECASE
        )
        if year_m:
            result['year_built'] = int(year_m.group(1))

        # ── Square footage ──────────────────────────────────────────────────
        sqft_m = re.search(
            r'(?:Living\s*Area|Sq\.?\s*Ft|Square\s*Feet)[:\s]*</[^>]+>\s*<[^>]+>\s*([\d,]+)',
            html, re.IGNORECASE
        )
        if sqft_m:
            result['sqft'] = int(sqft_m.group(1).replace(',', ''))

        # ── Bedrooms ────────────────────────────────────────────────────────
        beds_m = re.search(
            r'(?:Bedrooms?|Beds?)[:\s]*</[^>]+>\s*<[^>]+>\s*(\d+)',
            html, re.IGNORECASE
        )
        if beds_m:
            result['bedrooms'] = int(beds_m.group(1))

        # ── Parcel ID ───────────────────────────────────────────────────────
        parcel_m = re.search(
            r'(?:Parcel|Account|Tax\s*ID)[:\s]*</[^>]+>\s*<[^>]+>\s*([\w\-]+)',
            html, re.IGNORECASE
        )
        if parcel_m:
            result['parcel_id'] = parcel_m.group(1).strip()

        if not result:
            result['error'] = 'Could not parse assessor response — site layout may have changed'

        result['source'] = 'shelby_county'
        return result

    except requests.Timeout:
        return {'error': 'Shelby County Assessor timed out (>12s)'}
    except Exception as e:
        return {'error': str(e)}
