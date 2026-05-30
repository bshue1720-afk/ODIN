"""
ODIN Transcript Knowledge Builder — Step 1 (OFFLINE)
Reads all transcript files and writes a SQL dump file.
No internet required. Run this first.

Usage:
    py build_transcript_dump.py

Output:
    transcript_dump.sql  — push to Railway when online
    transcript_dump.log  — progress log
"""

import os
import re
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN")

TRANSCRIPT_DIRS = {
    "flip_with_rick": BASE_DIR / "transcripts" / "Flip_With_Rick_-_Videos",
    "dan_martell":    BASE_DIR / "transcripts" / "Dan_Martell_-_Videos",
}

OUTPUT_SQL = BASE_DIR / "transcript_dump.sql"
LOG_FILE   = BASE_DIR / "transcript_dump.log"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clean_title(filename):
    """Turn filename into readable title."""
    name = Path(filename).stem
    # Remove date prefix like 2026-05-25_
    name = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", name)
    # Replace underscores with spaces
    name = name.replace("_", " ")
    # Remove #shorts etc
    name = re.sub(r"\s*#\w+", "", name)
    return name.strip()


def escape_sql(text):
    """Escape text for safe PostgreSQL insertion."""
    return text.replace("'", "''")


def build_dump():
    log("=" * 60)
    log("ODIN Transcript Dump Builder starting")
    log(f"Output: {OUTPUT_SQL}")

    total_files = 0
    total_chars = 0
    errors = 0

    with open(OUTPUT_SQL, "w", encoding="utf-8") as out:
        # Header
        out.write("-- ODIN Transcript Knowledge Dump\n")
        out.write(f"-- Generated: {datetime.now().isoformat()}\n")
        out.write("-- Run migration 019 first, then push this file\n\n")

        # Run migration inline
        migration = Path(BASE_DIR / "api" / "migrations" / "019_transcript_knowledge.sql")
        if migration.exists():
            out.write(migration.read_text(encoding="utf-8"))
            out.write("\n\n")
            log("Migration 019 included in dump")

        out.write("BEGIN;\n\n")

        for source, directory in TRANSCRIPT_DIRS.items():
            if not directory.exists():
                log(f"WARNING: Directory not found: {directory}")
                continue

            files = sorted(directory.glob("*.txt"))
            log(f"Processing {len(files)} files from {source}")

            for i, filepath in enumerate(files):
                try:
                    content = filepath.read_text(encoding="utf-8", errors="replace")
                    title = clean_title(filepath.name)
                    char_count = len(content)

                    # Escape for SQL
                    safe_filename = escape_sql(filepath.name)
                    safe_title    = escape_sql(title)
                    safe_content  = escape_sql(content)

                    out.write(
                        f"INSERT INTO transcript_knowledge (filename, source, title, content, char_count)\n"
                        f"VALUES ('{safe_filename}', '{source}', '{safe_title}', '{safe_content}', {char_count})\n"
                        f"ON CONFLICT (filename) DO UPDATE SET\n"
                        f"    content = EXCLUDED.content,\n"
                        f"    char_count = EXCLUDED.char_count;\n\n"
                    )

                    total_files += 1
                    total_chars += char_count

                    if (i + 1) % 50 == 0:
                        log(f"  {source}: {i+1}/{len(files)} done")

                except Exception as e:
                    log(f"  ERROR reading {filepath.name}: {e}")
                    errors += 1

        out.write("COMMIT;\n\n")

        # Summary comment at end
        out.write(f"-- Summary: {total_files} files, {total_chars:,} chars, {errors} errors\n")
        out.write(f"-- Generated: {datetime.now().isoformat()}\n")

    size_mb = OUTPUT_SQL.stat().st_size / 1024 / 1024
    log("=" * 60)
    log(f"Done! {total_files} files written, {errors} errors")
    log(f"Total content: {total_chars:,} chars ({total_chars/1024/1024:.1f} MB)")
    log(f"SQL file size: {size_mb:.1f} MB")
    log(f"Output: {OUTPUT_SQL}")
    log("Next step: run push_transcript_dump.py when online")
    log("=" * 60)


if __name__ == "__main__":
    build_dump()
