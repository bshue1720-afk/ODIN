"""
ODIN — Public Comps Scraper
Pulls recent sold comps from Redfin's public search API.
No API key required — uses the same endpoint the Redfin website uses.

Caches results in memory for the session (not DB-persisted — comps change daily).

Usage:
  comps = comps_scraper.get_comps('4314 Leatherwood Ave Memphis TN', zip_code='38111')
  block = comps_scraper.format_slack(comps)

Called automatically when `analyze` command runs (appended to deal analysis).
"""

import re
import requests

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*',
    'Referer': 'https://www.redfin.com/',
}

# Simple in-memory cache: address_norm → result
_cache: dict = {}


def get_comps(address: str, zip_code: str = '', radius_miles: float = 0.5,
              limit: int = 6) -> dict:
    """
    Get recent sold comps near an address.
    Returns dict with: address, comps (list), avg_price_sqft, median_sold, count.
    """
    cache_key = f'{address.lower().strip()}|{zip_code}'
    if cache_key in _cache:
        return _cache[cache_key]

    result = _fetch_redfin_comps(address, zip_code, radius_miles, limit)
    _cache[cache_key] = result
    return result


def format_slack(comps_result: dict) -> str:
    """Format comps as a Slack block to append to deal analysis."""
    if comps_result.get('error') or not comps_result.get('comps'):
        return ''  # Silently omit if no comps — don't clutter the analysis

    comps  = comps_result['comps']
    median = comps_result.get('median_sold', 0)
    avg_ppsf = comps_result.get('avg_price_sqft', 0)
    count  = len(comps)

    lines = [f'\n*🏘️ Recent Sold Comps ({count} nearby):*']
    for c in comps:
        addr    = c.get('address', 'Unknown')[:40]
        price   = f'${c["price"]:,.0f}'  if c.get('price') else '—'
        sqft    = f'{c["sqft"]:,} sqft'  if c.get('sqft')  else ''
        beds    = f'{c["beds"]}bd'        if c.get('beds')  else ''
        baths   = f'{c["baths"]}ba'       if c.get('baths') else ''
        sold    = c.get('sold_date', '')
        details = '  '.join(filter(None, [beds, baths, sqft]))
        lines.append(f'• *{price}* — {addr} ({details}) _{sold}_')

    if median:
        lines.append(f'\nMedian sold: *${median:,.0f}*'
                     + (f' | Avg $/sqft: *${avg_ppsf:.0f}*' if avg_ppsf else ''))
    lines.append('_Source: Redfin public data_')
    return '\n'.join(lines)


# ─── Internal ────────────────────────────────────────────────────────────────

def _fetch_redfin_comps(address: str, zip_code: str, radius_miles: float,
                        limit: int) -> dict:
    """
    Uses Redfin's internal search + nearby-sold API.
    Step 1: Geocode address → get property ID
    Step 2: Pull nearby solds
    """
    try:
        # Step 1 — Autocomplete to get Redfin property ID
        search_query = f'{address} {zip_code}'.strip()
        auto_resp = requests.get(
            'https://www.redfin.com/stingray/do/location-autocomplete',
            params={
                'location':         search_query,
                'start':            0,
                'count':            5,
                'v':                2,
                'market':           'false',
                'iss':              'false',
                'obc':              'false',
            },
            headers=_HEADERS,
            timeout=10,
        )

        if auto_resp.status_code != 200:
            return {'error': f'Redfin autocomplete returned {auto_resp.status_code}',
                    'address': address}

        # Redfin prefixes JSON responses with "{}&&\n" to prevent JSON hijacking
        auto_text = auto_resp.text
        if auto_text.startswith('{}&&'):
            auto_text = auto_text[4:].strip()

        import json
        try:
            auto_data = json.loads(auto_text)
        except Exception:
            return {'error': 'Could not parse Redfin response', 'address': address}

        payload = auto_data.get('payload', {})
        sections = payload.get('sections', [])
        property_url = None
        lat, lng = None, None

        for section in sections:
            for row in section.get('rows', []):
                rtype = row.get('type', '')
                if 'property' in rtype.lower() or rtype == '1':
                    property_url = row.get('url', '')
                    lat = row.get('lat')
                    lng = row.get('lng')
                    break
            if property_url:
                break

        if not lat or not lng:
            # Fall back to zip-based search
            return _fetch_by_zip(zip_code, limit)

        # Step 2 — Pull nearby sold homes
        comps = _fetch_nearby_sold(lat, lng, radius_miles, limit)
        return {'address': address, 'comps': comps, **_stats(comps)}

    except requests.Timeout:
        return {'error': 'Redfin timed out', 'address': address}
    except Exception as e:
        return {'error': str(e), 'address': address}


def _fetch_nearby_sold(lat: float, lng: float, radius_miles: float,
                       limit: int) -> list:
    """Pull recently sold homes near a lat/lng from Redfin."""
    try:
        import json, time

        # Redfin GIS search for sold homes
        resp = requests.get(
            'https://www.redfin.com/stingray/api/gis',
            params={
                'al':               1,
                'market':           'memphis',
                'num_homes':        limit,
                'ord':              'redfin-recommended-asc',
                'page_number':      1,
                'sf':               '1,2,3,5,6,7',   # sold filter
                'sold_within_days': 180,
                'sp':               'true',
                'status':           9,               # sold
                'uipt':             '1,2,3,4',       # house types
                'v':                8,
                'lat':              lat,
                'lng':              lng,
                'radius':           radius_miles,
            },
            headers=_HEADERS,
            timeout=10,
        )

        if resp.status_code != 200:
            return []

        text = resp.text
        if text.startswith('{}&&'):
            text = text[4:].strip()

        data  = json.loads(text)
        homes = (data.get('payload', {})
                     .get('homes', []))

        comps = []
        for h in homes[:limit]:
            hd = h.get('homeData', h)
            price = (hd.get('priceInfo', {}).get('amount')
                     or hd.get('price', {}).get('value'))
            sqft  = hd.get('sqFt', {}).get('value') if isinstance(hd.get('sqFt'), dict) else hd.get('sqFt')
            beds  = hd.get('beds')
            baths = hd.get('baths')
            addr  = (hd.get('streetLine', {}).get('value', '')
                     if isinstance(hd.get('streetLine'), dict)
                     else str(hd.get('streetLine', '')))
            sold_ts = hd.get('soldDate') or hd.get('lastSaleDate')
            sold_str = ''
            if sold_ts:
                try:
                    from datetime import datetime
                    sold_str = datetime.fromtimestamp(sold_ts / 1000).strftime('%b %Y')
                except Exception:
                    pass

            if price:
                comps.append({
                    'address':   addr,
                    'price':     float(price),
                    'sqft':      int(sqft) if sqft else None,
                    'beds':      beds,
                    'baths':     baths,
                    'sold_date': sold_str,
                })

        return comps

    except Exception:
        return []


def _fetch_by_zip(zip_code: str, limit: int) -> dict:
    """Fallback: search by zip code if geocoding fails."""
    if not zip_code:
        return {'error': 'No zip code for fallback search', 'comps': []}
    try:
        import json
        resp = requests.get(
            'https://www.redfin.com/stingray/api/gis',
            params={
                'al': 1, 'num_homes': limit, 'ord': 'redfin-recommended-asc',
                'page_number': 1, 'sf': '1,2,3,5,6,7',
                'sold_within_days': 180, 'status': 9, 'uipt': '1,2,3,4',
                'v': 8, 'region_id': zip_code, 'region_type': 2,
            },
            headers=_HEADERS, timeout=10,
        )
        if resp.status_code != 200:
            return {'comps': []}
        text = resp.text
        if text.startswith('{}&&'):
            text = text[4:].strip()
        data  = json.loads(text)
        homes = data.get('payload', {}).get('homes', [])
        comps = []
        for h in homes[:limit]:
            hd    = h.get('homeData', h)
            price = (hd.get('priceInfo', {}).get('amount') or
                     hd.get('price', {}).get('value'))
            sqft  = hd.get('sqFt', {}).get('value') if isinstance(hd.get('sqFt'), dict) else None
            addr  = (hd.get('streetLine', {}).get('value', '')
                     if isinstance(hd.get('streetLine'), dict) else '')
            if price:
                comps.append({'address': addr, 'price': float(price),
                              'sqft': sqft, 'beds': None, 'baths': None, 'sold_date': ''})
        return {'comps': comps, **_stats(comps)}
    except Exception:
        return {'comps': []}


def _stats(comps: list) -> dict:
    """Compute median sold price and avg price/sqft from comps list."""
    prices  = [c['price'] for c in comps if c.get('price')]
    ppsf    = [c['price'] / c['sqft'] for c in comps
               if c.get('price') and c.get('sqft') and c['sqft'] > 0]

    median = sorted(prices)[len(prices) // 2] if prices else 0
    avg_ppsf = sum(ppsf) / len(ppsf) if ppsf else 0

    return {'median_sold': median, 'avg_price_sqft': avg_ppsf, 'count': len(comps)}
