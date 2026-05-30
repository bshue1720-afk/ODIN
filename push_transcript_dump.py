"""
ODIN Transcript Knowledge Builder — Step 2 (ONLINE)
Pushes the SQL dump file to Railway PostgreSQL.
Requires internet. Run after build_transcript_dump.py.

Usage:
    py push_transcript_dump.py

Resumable: ON CONFLICT DO UPDATE means safe to re-run.
"""

import psycopg2
from pathlib import Path
from datetime import datetime

BASE_DIR  = Path(r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN")
DUMP_FILE = BASE_DIR / "transcript_dump.sql"
LOG_FILE  = BASE_DIR / "transcript_dump.log"

DB_CONFIG = {
    "host":     "kodama.proxy.rlwy.net",
    "port":     55551,
    "user":     "postgres",
    "password": "yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC",
    "dbname":   "railway",
    "sslmode":  "require",
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def push():
    log("=" * 60)
    log("ODIN Transcript Dump Push starting")

    if not DUMP_FILE.exists():
        log(f"ERROR: Dump file not found: {DUMP_FILE}")
        log("Run build_transcript_dump.py first.")
        return

    size_mb = DUMP_FILE.stat().st_size / 1024 / 1024
    log(f"Dump file: {DUMP_FILE} ({size_mb:.1f} MB)")

    log("Connecting to Railway PostgreSQL...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        log("Connected.")
    except Exception as e:
        log(f"Connection failed: {e}")
        return

    log("Reading dump file...")
    sql = DUMP_FILE.read_text(encoding="utf-8")

    log("Executing... (this may take a few minutes)")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        log("Committed successfully.")
    except Exception as e:
        conn.rollback()
        log(f"ERROR during execution: {e}")
        log("Transaction rolled back. No changes made.")
        conn.close()
        return

    # Verify row count
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), SUM(char_count) FROM transcript_knowledge")
            row = cur.fetchone()
            count, chars = row[0], row[1] or 0
            log(f"Verified: {count} transcripts in DB ({chars/1024/1024:.1f} MB of content)")
    except Exception as e:
        log(f"Verification query failed: {e}")

    conn.close()
    log("Done! Transcripts are searchable in ODIN.")
    log("Slack: 'search knowledge <your query>'")
    log("=" * 60)


if __name__ == "__main__":
    push()
