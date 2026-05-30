"""
ODIN Transcript Extractor
Runs via Windows Task Scheduler — processes 25 files/run, ~100 files/hour.
Writes extracted insights directly to ODIN memories DB.

Schedule: every hour via Task Scheduler
Checkpoint: extract_checkpoint.json (tracks progress)
Log: extract_log.txt
"""

import os
import json
import time
import psycopg2
import anthropic
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR       = Path(r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN")
TRANSCRIPT_DIRS = [
    BASE_DIR / "transcripts" / "Flip_With_Rick_-_Videos",
    BASE_DIR / "transcripts" / "Dan_Martell_-_Videos",
]
CHECKPOINT_FILE = BASE_DIR / "extract_checkpoint.json"
LOG_FILE        = BASE_DIR / "extract_log.txt"
FILES_PER_RUN   = 25
SOURCE_TAG      = "transcript_extractor_auto"

# DB connection (Railway PostgreSQL)
DB_CONFIG = {
    "host":     "kodama.proxy.rlwy.net",
    "port":     55551,
    "user":     "postgres",
    "password": "yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC",
    "dbname":   "railway",
    "sslmode":  "require",
}
USER_ID = "a4ede2f8-b3b1-4158-816f-e7503dab81a4"

# Anthropic (reads ANTHROPIC_API_KEY from env, or set directly here)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

EXTRACTION_PROMPT = """You are extracting actionable knowledge from a YouTube transcript for an ODIN business intelligence database.

Transcript filename: {filename}
Transcript content:
{content}

Extract ALL actionable insights from this transcript. Be thorough — aim for 3-10 entries per file.

EXTRACT these categories:
- RE_SCRIPT: Cold calling scripts, objection handlers, rebuttals, voicemail scripts, SMS templates (word-for-word)
- RE_LIST: List strategies, filter settings, free vs paid sources, list stacking methods
- RE_OFFER: ARV formulas, MAO/LAO calculations, repair cost blueprints, offer math
- RE_DISPO: Cash buyer tactics, JV structure, buyer scripts, presenting deals
- RE_DEAL_TYPE: Step-by-step processes for assignments, double close, wholetail, sub-to, creative finance, novation, short sale, land
- RE_MARKET: 2026 market intel, foreclosure data, regulation changes, iOS/carrier impacts, legal rulings
- RE_FOLLOWUP: Follow-up cadences with exact timing and channel sequences
- RE_KPI: Real numbers — calls per deal, conversion rates, cost per lead, VA benchmarks
- RE_TOOLS: Software with specific use cases, filter settings, workflows (PropStream, BatchLeads, XLeads, dialers)
- RE_LEGAL: State laws, contract language, compliance rules, what to avoid
- BIZ_SCALING: Scaling frameworks with specific revenue triggers and hiring sequences
- BIZ_AUTOMATION: AI automation opportunities with tool names and step-by-step workflows (any industry)
- BIZ_AI_TOOLS: Specific AI tools with exact use cases, prompts, and implementation steps
- BIZ_OPPORTUNITY: Business models with margin data, pricing structure, ideal customer
- BIZ_SYSTEMS: SOPs, delegation frameworks, meeting cadences, team structures
- PRODUCTIVITY: Daily/weekly systems with specific time blocks and measurable outputs
- FINANCE: Financial systems, pricing strategies, cash flow rules with specific formulas

SKIP: Pure motivation, vague advice without systems, generic "work hard" content.

Return ONLY a JSON array. Each object must have:
- "key": snake_case, max 55 chars, prefix fwr_ for Rick Ginn content or dm_ for Dan Martell content
- "category": one of the categories above
- "value": dense, specific, actionable — include exact scripts, numbers, tool names, step sequences. Minimum 2 sentences. Pack detail.

Example:
[
  {{"key": "fwr_guru_killer_opener", "category": "RE_SCRIPT", "value": "Guru Killer opener script (word-for-word): 'Hey [name], this is Zach. I'm not a realtor, I'm an investor and I was looking at your property at [address]. I don't know if you'd consider an offer but I wanted to reach out. Is that something you'd be open to?' Pause after last line. If they say not interested: 'I totally understand — would you mind if I called back in a few months just in case things change?' Works because it's non-threatening, positions as investor not agent, and sets up future contact on no."}}
]

Return valid JSON only. No explanation, no markdown fences."""


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"processed": [], "total_inserted": 0, "runs": 0}


def save_checkpoint(cp):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(cp, f, indent=2)


def get_all_files():
    files = []
    for d in TRANSCRIPT_DIRS:
        if d.exists():
            for f in sorted(d.glob("*.txt")):
                files.append(str(f))
    return files


def extract_insights(client, filepath):
    path = Path(filepath)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        log(f"  Read error {path.name}: {e}")
        return []

    # Truncate very long transcripts to ~50k chars to stay within token limits
    if len(content) > 50000:
        content = content[:50000] + "\n[TRUNCATED]"

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": EXTRACTION_PROMPT.format(
                    filename=path.name,
                    content=content
                )
            }]
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        insights = json.loads(raw)
        return insights if isinstance(insights, list) else []
    except json.JSONDecodeError as e:
        log(f"  JSON parse error for {path.name}: {e}")
        return []
    except Exception as e:
        log(f"  API error for {path.name}: {e}")
        return []


def insert_memories(conn, insights, source_file):
    inserted = 0
    skipped = 0
    with conn.cursor() as cur:
        for item in insights:
            key = item.get("key", "").strip()[:60]
            value = item.get("value", "").strip()
            category = item.get("category", "GENERAL").strip()

            if not key or not value:
                skipped += 1
                continue

            # Append category and source to value for context
            full_value = f"[{category}] {value} (source: {Path(source_file).name})"

            try:
                cur.execute(
                    """INSERT INTO memories (user_id, key, value, source)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (user_id, key) DO UPDATE
                       SET value = EXCLUDED.value, source = EXCLUDED.source""",
                    (USER_ID, key, full_value, SOURCE_TAG)
                )
                inserted += 1
            except Exception as e:
                log(f"    DB insert error for key '{key}': {e}")
                conn.rollback()
                skipped += 1

    conn.commit()
    return inserted, skipped


def main():
    log("=" * 60)
    log("Transcript extractor starting")

    # Load checkpoint
    cp = load_checkpoint()
    processed_set = set(cp["processed"])
    log(f"Checkpoint: {len(processed_set)} files already processed, {cp['total_inserted']} total memories inserted")

    # Get all files, filter already processed
    all_files = get_all_files()
    remaining = [f for f in all_files if f not in processed_set]
    log(f"Files remaining: {len(remaining)} of {len(all_files)} total")

    if not remaining:
        log("All files processed! Nothing to do.")
        return

    # Take next batch
    batch = remaining[:FILES_PER_RUN]
    log(f"Processing batch of {len(batch)} files")

    # Init clients
    if not ANTHROPIC_API_KEY:
        log("ERROR: ANTHROPIC_API_KEY not set. Set it as an environment variable.")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        log("DB connected")
    except Exception as e:
        log(f"DB connection failed: {e}")
        return

    # Process files
    run_inserted = 0
    run_skipped = 0

    for i, filepath in enumerate(batch):
        fname = Path(filepath).name
        log(f"  [{i+1}/{len(batch)}] {fname}")

        insights = extract_insights(client, filepath)
        log(f"    Extracted {len(insights)} insights")

        if insights:
            ins, skp = insert_memories(conn, insights, filepath)
            run_inserted += ins
            run_skipped += skp
            log(f"    Inserted {ins}, skipped {skp}")

        # Mark as processed
        cp["processed"].append(filepath)

        # Small delay between API calls to be gentle
        if i < len(batch) - 1:
            time.sleep(2)

    conn.close()

    # Update checkpoint
    cp["total_inserted"] += run_inserted
    cp["runs"] += 1
    save_checkpoint(cp)

    remaining_after = len(all_files) - len(cp["processed"])
    log(f"Run complete: {run_inserted} inserted, {run_skipped} skipped")
    log(f"Total inserted across all runs: {cp['total_inserted']}")
    log(f"Files remaining: {remaining_after}")
    log(f"Estimated runs to completion: {remaining_after // FILES_PER_RUN + 1}")
    log("=" * 60)


if __name__ == "__main__":
    main()
