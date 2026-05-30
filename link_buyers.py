"""
ODIN Buyer Bulk-Link — Match ODIN buyers to XLeads contact IDs by phone.

Strategy: fetch ALL XLeads contacts in bulk pages (1 pass), build phone→ID map,
then match against ODIN buyers locally. Fast: O(pages) API calls not O(buyers).

Usage:
    py link_buyers.py            -- dry run
    py link_buyers.py --apply    -- write matches to DB
"""

import sys
import re
import os
import requests
import psycopg2
import psycopg2.extras

DB_CONFIG = {
    "host":     "kodama.proxy.rlwy.net",
    "port":     55551,
    "user":     "postgres",
    "password": "yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC",
    "dbname":   "railway",
    "sslmode":  "require",
}

XLEADS_API_KEY  = os.environ.get("XLEADS_API_KEY", "pit-6e8c008e-50e0-47b6-bcb1-ff464247c0bf")
XLEADS_LOCATION = os.environ.get("XLEADS_LOCATION_ID", "4uY1kQOrvNT6L9Vh4Tp2")
XLEADS_BASE_URL = os.environ.get("XLEADS_BASE_URL", "https://services.leadconnectorhq.com")
HEADERS = {
    "Authorization": f"Bearer {XLEADS_API_KEY}",
    "Content-Type":  "application/json",
    "Version":       "2021-07-28",
}

DRY_RUN = "--apply" not in sys.argv


def normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def fetch_all_xleads_contacts():
    """Fetch all contacts from XLeads using cursor pagination. Returns phone→contact_id dict."""
    phone_map     = {}
    total_fetched = 0
    page_num      = 0

    # XLeads uses cursor pagination via meta.nextPageUrl
    next_url = f"{XLEADS_BASE_URL}/contacts/?locationId={XLEADS_LOCATION}&limit=100"

    print("Fetching XLeads contacts (cursor pagination)...")
    while next_url:
        try:
            r = requests.get(next_url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data     = r.json()
            contacts = data.get("contacts", [])
            if not contacts:
                break
            for c in contacts:
                cid = c.get("id", "")
                if not cid:
                    continue
                for phone_field in ("phone", "mobilePhone", "workPhone"):
                    raw = c.get(phone_field, "")
                    if raw:
                        norm = normalize_phone(raw)
                        if norm and norm not in phone_map:
                            phone_map[norm] = cid
            total_fetched += len(contacts)
            page_num += 1
            if page_num % 10 == 0:
                print(f"  {total_fetched} contacts fetched...")
            # Get next page URL from meta
            meta     = data.get("meta") or {}
            next_url = meta.get("nextPageUrl")
            # Stop if full page wasn't returned (no more data)
            if len(contacts) < 100 and not next_url:
                break
        except Exception as e:
            print(f"  [page {page_num} error] {e}")
            break

    print(f"Done. {total_fetched} XLeads contacts, {len(phone_map)} unique phones indexed.")
    return phone_map


def run():
    print("=" * 60)
    print(f"ODIN Buyer Bulk-Link  |  mode: {'DRY RUN' if DRY_RUN else 'APPLY'}")
    print("=" * 60)

    # Step 1: Build XLeads phone → contact_id map
    phone_map = fetch_all_xleads_contacts()
    if not phone_map:
        print("ERROR: No XLeads contacts fetched. Check API key.")
        return

    # Step 2: Load ODIN buyers without xleads_contact_id
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, name, phone, phone2
        FROM buyers
        WHERE (xleads_contact_id IS NULL OR xleads_contact_id = '')
          AND (phone IS NOT NULL AND phone != '')
        ORDER BY name
    """)
    buyers = cur.fetchall()
    print(f"\nODIN buyers to match: {len(buyers)}")

    # Step 3: Match locally
    matched   = []
    not_found = 0

    for buyer in buyers:
        bid   = str(buyer["id"])
        name  = buyer["name"] or "Unknown"
        cid   = None

        for phone_field in (buyer["phone"], buyer.get("phone2")):
            if phone_field:
                norm = normalize_phone(phone_field)
                if norm in phone_map:
                    cid = phone_map[norm]
                    break

        if cid:
            matched.append((bid, cid, name))
        else:
            not_found += 1

    print(f"Matched: {len(matched)} | Not found: {not_found}")

    # Step 4: Write if --apply
    if not DRY_RUN and matched:
        wc = conn.cursor()
        updated = 0
        for bid, cid, name in matched:
            try:
                wc.execute(
                    "UPDATE buyers SET xleads_contact_id = %s WHERE id = %s",
                    (cid, bid)
                )
                updated += 1
            except Exception as e:
                print(f"  Error updating {name}: {e}")
        conn.commit()
        wc.close()
        print(f"DB updated: {updated} buyers linked.")

        # Verify
        cur.execute("SELECT COUNT(*) as cnt FROM buyers WHERE xleads_contact_id IS NOT NULL AND xleads_contact_id != ''")
        total_linked = cur.fetchone()["cnt"]
        print(f"Total buyers with xleads_contact_id: {total_linked}")

    cur.close()
    conn.close()

    print()
    print("=" * 60)
    if DRY_RUN:
        print(f"DRY RUN complete. {len(matched)} matches found.")
        print("Run with --apply to write to DB.")
        if matched:
            print("\nSample matches (first 10):")
            for bid, cid, name in matched[:10]:
                print(f"  {name} -> {cid[:16]}...")
    else:
        print(f"DONE. {len(matched)} buyers linked to XLeads contacts.")
    print("=" * 60)


if __name__ == "__main__":
    run()
