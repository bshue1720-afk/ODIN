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
    # Katelyn keys
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
    # Brock financial advisor keys
    'fin_va_recommendation',  # advisor's current position on hiring VAs
    'fin_biz_card_guidance',  # current guidance on biz card deployment
    'fin_next_action',        # the specific next financial action recommended
    'fin_re_stage',           # current RE stage assessment (pre-deal, pipeline-building, etc.)
    'fin_risk_flag',          # active financial risk flags
    'fin_hold_off',           # things advisor said NOT to spend on right now
    # Tone profile keys (APEX "Agent Files" — per-channel writing style)
    'tone_email',             # how Brock writes cold/follow-up emails (e.g. "direct, 3 lines max, real-person feel")
    'tone_sms',               # how Brock writes SMS follow-ups (e.g. "casual, first name, no exclamation marks")
    'tone_slack',             # how Brock communicates in Slack (e.g. "short, commands-style, emoji ok")
]

EXTRACT_SYSTEM = (
    "You extract key facts from a conversation snippet. "
    "Return ONLY a valid JSON object with keys from this list: "
    + str(MEMORY_KEYS) +
    ". Only include keys where the conversation contains clear, specific information. "
    "Values must be short strings (under 100 chars). "
    "If nothing new is learned, return {}."
)

# Keys that map to Brock's financial advisor memory (source tag)
FINANCE_MEMORY_KEYS = {
    'fin_va_recommendation', 'fin_biz_card_guidance', 'fin_next_action',
    'fin_re_stage', 'fin_risk_flag', 'fin_hold_off',
}


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
    high = {'current_business', 'business_status', 'income_goal',
            'fin_va_recommendation', 'fin_biz_card_guidance', 'fin_next_action'}
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


_FINANCE_EXTRACT_SYSTEM = (
    "You extract the advisor's key POSITIONS and RECOMMENDATIONS from a financial advice exchange. "
    "Return ONLY a valid JSON object. Only use these exact keys:\n"
    "- fin_va_recommendation: advisor's position on hiring VAs (e.g. 'wait for blast results before hiring')\n"
    "- fin_biz_card_guidance: current biz card deployment guidance (e.g. 'hold — no proven ROI yet')\n"
    "- fin_next_action: the single most important next financial action recommended\n"
    "- fin_re_stage: current RE stage assessment (e.g. 'pre-first-deal, blast running, Eddie pending')\n"
    "- fin_risk_flag: active risk flagged (e.g. 'card interest risk if VA hired before first deal')\n"
    "- fin_hold_off: things explicitly advised against right now (e.g. 'no second VA, no ad spend')\n"
    "Only include keys where the advisor gave a CLEAR position. Values max 120 chars. "
    "If no clear position was taken, return {}."
)


def extract_finance_positions(user_id: str, question: str, advisor_reply: str, get_db):
    """
    Extract the financial advisor's key positions after each response.
    Stored in memories table — loaded next call to prevent contradictions.
    """
    try:
        client = _get_client()
        snippet = f'User asked: {question[:300]}\nAdvisor answered: {advisor_reply[:600]}'
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            system=_FINANCE_EXTRACT_SYSTEM,
            messages=[{'role': 'user', 'content': snippet}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        facts = json.loads(raw)
        if facts:
            upsert(user_id, facts, get_db, source='finance_advisor')
            log.info(f'finance memory saved {len(facts)} position(s) for {user_id}')
    except Exception as e:
        log.error(f'extract_finance_positions failed: {e}')


def load_finance_positions(user_id: str, get_db) -> str:
    """Load Brock's stored financial advisor positions as a context block."""
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT key, value FROM memories
            WHERE user_id = %s AND key LIKE 'fin\\_%' ESCAPE '\\'
            ORDER BY updated_at DESC
        """, (user_id,))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return ''
        lines = ['[My previous positions — I must stay consistent with these unless new data changes the picture]']
        labels = {
            'fin_va_recommendation': 'VA hiring position',
            'fin_biz_card_guidance': 'Biz card guidance',
            'fin_next_action':       'Recommended next action',
            'fin_re_stage':          'RE stage assessment',
            'fin_risk_flag':         'Active risk flag',
            'fin_hold_off':          'Currently advised against',
        }
        for r in rows:
            label = labels.get(r['key'], r['key'])
            lines.append(f'- {label}: {r["value"]}')
        return '\n'.join(lines)
    except Exception as e:
        log.error(f'load_finance_positions failed: {e}')
        return ''


# ─── TONE PROFILES ────────────────────────────────────────────────────────────

def load_tone_profile(user_id: str, channel: str, get_db) -> str:
    """
    Load Brock's stored writing style for a specific channel.
    channel: 'email' | 'sms' | 'slack'
    Returns a one-line tone instruction, or '' if not set.
    APEX 'Agent Files' parity — ODIN drafts in Brock's voice per channel.
    """
    key = f'tone_{channel}'
    if key not in MEMORY_KEYS:
        return ''
    try:
        conn = get_db()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT value FROM memories WHERE user_id = %s AND key = %s LIMIT 1",
            (user_id, key)
        )
        row = cur.fetchone()
        conn.close()
        return row['value'] if row else ''
    except Exception as e:
        log.error(f'load_tone_profile failed: {e}')
        return ''


def set_tone_profile(user_id: str, channel: str, description: str, get_db) -> bool:
    """
    Set/update Brock's writing style for a channel.
    channel: 'email' | 'sms' | 'slack'
    """
    key = f'tone_{channel}'
    if key not in MEMORY_KEYS:
        return False
    upsert(user_id, {key: description[:200]}, get_db, source='tone_profile')
    return True


def format_tone_profiles(user_id: str, get_db) -> str:
    """Return all tone profiles for display."""
    profiles = {}
    for channel in ('email', 'sms', 'slack'):
        val = load_tone_profile(user_id, channel, get_db)
        if val:
            profiles[channel] = val
    if not profiles:
        return '_No tone profiles set. Use `tone update email <description>` to teach ODIN your writing style._'
    lines = ['*✍️ Tone Profiles (Agent Files)*']
    for channel, desc in profiles.items():
        lines.append(f'• *{channel}*: {desc}')
    lines.append('\n_Update with: `tone update email|sms|slack <description>`_')
    return '\n'.join(lines)
