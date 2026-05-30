"""
ODIN Layer 2 — Memory
Persistent user memory backed by Railway PostgreSQL.
No external services required.

Flow:
  1. load(user_id, get_db)   → fetch top memories before each advisor call
  2. format_for_prompt(rows) → inject as context block into system prompt
  3. extract_and_save(...)   → after each reply, Claude Haiku pulls new facts and upserts them
"""

import os
import json
import logging
import psycopg2
import psycopg2.extras
import anthropic

log = logging.getLogger('odin.memory')

_client = None

# Keys we track — Claude is instructed to only emit these
MEMORY_KEYS = [
    'current_business',       # what business Katelyn is actively building
    'income_goal',            # her monthly income target
    'available_hours',        # hours/week she can dedicate
    'skills',                 # skills she has mentioned
    'startup_budget',         # budget she mentioned
    'preferred_platform',     # etsy, shopify, gumroad, etc.
    'business_status',        # idea / building / launched / paused
    'last_business_discussed',# last business name she asked about
    'automation_preference',  # how much she wants automated
    'blockers',               # things she's said are stopping her
]

EXTRACT_SYSTEM = (
    "You extract key facts from a conversation snippet. "
    "Return ONLY a valid JSON object with keys from this list: "
    + str(MEMORY_KEYS) +
    ". Only include keys where the conversation contains clear, specific information. "
    "Values must be short strings (under 100 chars). "
    "If nothing new is learned, return {}."
)


def _get_client():
    global _client
    if _client is None:
        key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not key:
            raise RuntimeError('ANTHROPIC_API_KEY not set')
        _client = anthropic.Anthropic(api_key=key)
    return _client


# ─── LOAD ─────────────────────────────────────────────────────────────────────

def load(user_id: str, get_db) -> list:
    """Return up to 12 memory rows for this user, highest importance first."""
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT key, value, importance
            FROM memories
            WHERE user_id = %s
            ORDER BY importance DESC, updated_at DESC
            LIMIT 12
        """, (user_id,))
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error(f'memory.load failed: {e}')
        return []


# ─── FORMAT ───────────────────────────────────────────────────────────────────

def format_for_prompt(rows: list) -> str:
    """Convert memory rows into a compact context block for the system prompt."""
    if not rows:
        return ''
    lines = ['[What I know about Katelyn from past conversations]']
    for r in rows:
        lines.append(f'- {r["key"].replace("_", " ").title()}: {r["value"]}')
    return '\n'.join(lines)


# ─── SAVE ─────────────────────────────────────────────────────────────────────

def upsert(user_id: str, facts: dict, get_db, source: str = 'chat'):
    """Upsert a dict of {key: value} facts for this user."""
    if not facts:
        return
    try:
        conn = get_db()
        cur  = conn.cursor()
        for key, value in facts.items():
            if key not in MEMORY_KEYS:
                continue
            if not value or not str(value).strip():
                continue
            cur.execute("""
                INSERT INTO memories (user_id, key, value, source, importance)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, key)
                DO UPDATE SET value = EXCLUDED.value,
                              source = EXCLUDED.source,
                              updated_at = NOW()
            """, (user_id, key, str(value)[:200], source, _importance(key)))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f'memory.upsert failed: {e}')


def _importance(key: str) -> int:
    high = {'current_business', 'business_status', 'income_goal'}
    low  = {'last_business_discussed', 'blockers'}
    if key in high:
        return 3
    if key in low:
        return 1
    return 2


# ─── EXTRACT ──────────────────────────────────────────────────────────────────

def extract_and_save(user_id: str, user_msg: str, advisor_reply: str, get_db):
    """
    Use Claude Haiku to extract new facts from the latest exchange,
    then upsert them into the memories table.
    Runs after each advisor response — cheap (~200 tokens).
    """
    try:
        snippet = (
            f'User said: {user_msg[:400]}\n'
            f'Advisor replied: {advisor_reply[:400]}'
        )
        client = _get_client()
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            system=EXTRACT_SYSTEM,
            messages=[{'role': 'user', 'content': snippet}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        facts = json.loads(raw)
        if facts:
            upsert(user_id, facts, get_db, source='chat')
            log.info(f'memory.extract saved {len(facts)} fact(s) for {user_id}')
    except json.JSONDecodeError:
        pass  # Haiku returned non-JSON — skip silently
    except Exception as e:
        log.error(f'memory.extract_and_save failed: {e}')
