"""
Insert 150 FWR entries from extract_stage_fwr2.txt into ODIN memories table.
Uses Brock's user_id. Safe to re-run (ON CONFLICT DO UPDATE).
"""

import psycopg2
from pathlib import Path

BASE_DIR  = Path(r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN")
INPUT     = BASE_DIR / "extract_stage_fwr2.txt"

DB_CONFIG = {
    "host":     "kodama.proxy.rlwy.net",
    "port":     55551,
    "user":     "postgres",
    "password": "yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC",
    "dbname":   "railway",
    "sslmode":  "require",
}

BROCK_USER_ID = "04120ba2-c76c-42ab-9c7e-f351025c5654"

def run():
    lines = INPUT.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        key, category, value = parts
        # Store as: key = "fwr_001", value = "[CATEGORY] value text"
        full_value = f"[{category}] {value}"
        entries.append((key.strip(), full_value.strip()))

    print(f"Parsed {len(entries)} entries from {INPUT.name}")

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    inserted = 0
    updated  = 0

    for key, value in entries:
        cur.execute("""
            INSERT INTO memories (user_id, key, value, source, importance)
            VALUES (%s, %s, %s, 'transcript_fwr', 3)
            ON CONFLICT (user_id, key) DO UPDATE
              SET value = EXCLUDED.value,
                  source = EXCLUDED.source,
                  updated_at = NOW()
        """, (BROCK_USER_ID, key, value))
        if cur.rowcount:
            inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done — {inserted} rows upserted into memories table.")

    # Verify
    conn2 = psycopg2.connect(**DB_CONFIG)
    cur2  = conn2.cursor()
    cur2.execute("SELECT COUNT(*) FROM memories WHERE source = 'transcript_fwr'")
    count = cur2.fetchone()[0]
    cur2.close()
    conn2.close()
    print(f"Verified: {count} FWR memories in DB.")

if __name__ == "__main__":
    run()
