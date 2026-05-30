"""
ODIN — Lead Sniper
Auto-discovers new outreach leads when the initial batch is exhausted.
Uses Claude API + web_search tool to find decision makers and emails.
Then generates a personalized cold email per lead and queues it.

Triggered automatically by email_sender.py when all step-1 touches are sent.
Can also be triggered manually via Slack: `snipe <niche> [count:5]`

Supported niches: plumbing, roofing, auto_repair, dental, law
"""

import os
import re
import json
import logging

import psycopg2
import psycopg2.extras

log = logging.getLogger('odin.lead_sniper')

_slack_post_fn = None
_get_db_fn     = None
_channels      = None

CITY = 'Columbus, Ohio'

NICHE_HOOKS = {
    'plumbing':    'missed calls and after-hours coverage — plumbers lose avg $67K/year to unanswered calls that go straight to competitors',
    'roofing':     'call capture during storm surges — most roofing shops miss their highest-value jobs when phones are overwhelmed',
    'auto_repair': 'estimate follow-up — most shops quote a job, customer leaves, nobody calls, job goes to whoever calls first',
    'dental':      'front desk labor cost — insurance verification, scheduling, and reactivation burn 3+ hrs/day of staff time ($3-6K/mo)',
    'law':         'intake conversion speed — 70% of potential clients retain the first firm that calls them back',
}

HOOK_TYPE_MAP = {
    'plumbing':    'missed_calls',
    'roofing':     'storm_surge',
    'auto_repair': 'estimate_followup',
    'dental':      'labor_cost',
    'law':         'intake_conversion',
}


def init(slack_post_fn, get_db_fn, channels: dict):
    global _slack_post_fn, _get_db_fn, _channels
    _slack_post_fn = slack_post_fn
    _get_db_fn     = get_db_fn
    _channels      = channels


def _post(msg: str):
    if _slack_post_fn and _channels:
        _slack_post_fn(_channels.get('brock', ''), msg)


def _db():
    if _get_db_fn:
        return _get_db_fn()
    return psycopg2.connect(os.environ['DATABASE_URL'])


# ─── LEAD DISCOVERY ───────────────────────────────────────────────────────────

def snipe(niche: str, count: int = 5) -> str:
    """
    Find `count` new leads for `niche` in Columbus OH.
    Inserts into outreach_contacts + queues personalized emails.
    Returns a Slack-ready summary string.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

    _post(f'🔍 Sniping {count} new *{niche}* leads in {CITY}…')

    # Step 1: Find leads via web search
    leads = _find_leads(client, niche, count)
    if not leads:
        msg = f'⚠️ Sniper found no leads for {niche} — check API or try again'
        _post(msg)
        return msg

    # Step 2: For each lead, generate email + insert into DB
    conn   = _db()
    cur    = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    queued = 0
    skipped = 0

    for lead in leads:
        email = (lead.get('email') or '').strip().lower()
        biz   = (lead.get('business_name') or '').strip()
        name  = (lead.get('name') or '').strip()

        if not email or not biz or '@' not in email:
            skipped += 1
            continue

        # Skip if contact already exists
        cur.execute(
            "SELECT id FROM outreach_contacts WHERE LOWER(email) = %s", (email,)
        )
        if cur.fetchone():
            skipped += 1
            continue

        # Insert contact
        cur.execute("""
            INSERT INTO outreach_contacts
                (name, business_name, niche, email, phone, website, tier, source, spoke, status)
            VALUES (%s, %s, %s, %s, %s, %s, 0, 'auto_sniper', 'ai_audit', 'cold')
            RETURNING id
        """, (name, biz, niche, email,
              lead.get('phone', ''), lead.get('website', '')))
        contact_id = cur.fetchone()['id']

        # Generate personalized email
        subject, body = _generate_email(client, name, biz, niche, lead.get('website', ''))
        if not subject or not body:
            log.warning(f'Email generation failed for {biz} — skipping queue')
            skipped += 1
            continue

        # Queue the touch
        cur.execute("""
            INSERT INTO outreach_touches
                (contact_id, channel, status, subject, body, sequence_step, hook_type)
            VALUES (%s, 'email', 'pending', %s, %s, 1, %s)
        """, (contact_id, subject, body, HOOK_TYPE_MAP.get(niche, 'missed_calls')))
        queued += 1

    conn.commit()
    cur.close()
    conn.close()

    summary = f'✅ *Snipe complete — {niche}*: {queued} new leads queued, {skipped} skipped'
    _post(summary)
    log.info(summary)
    return summary


def _find_leads(client, niche: str, count: int) -> list:
    """
    Use Claude + web_search to find business leads with real email addresses.
    Returns a list of dicts: name, business_name, email, phone, website.
    """
    prompt = f"""Search for {count} independent {niche} businesses in {CITY} that I can cold email about AI automation.

For each business find:
- Business name
- Owner or decision-maker's first and last name
- Their direct email address (owner's email preferred over info@ or contact@)
- Website URL
- Phone number

Target criteria:
- Independent owner-operated (NOT chains, franchises, or large corporations)
- 3–20 employees
- Has a website with contact info
- Looks like a small team handling their own phones

After searching, return ONLY a valid JSON array. Each object must have these exact keys:
name, business_name, email, website, phone

Example format:
[
  {{"name": "John Smith", "business_name": "Smith Plumbing LLC", "email": "john@smithplumbing.com", "website": "https://smithplumbing.com", "phone": "(614) 555-1234"}},
  ...
]

Only include businesses where you found a real, specific email address. No placeholder or guessed emails."""

    try:
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=4096,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as e:
        log.error(f'_find_leads API error ({niche}): {e}')
        return []

    # Extract text content from response
    full_text = ''
    for block in response.content:
        if hasattr(block, 'text'):
            full_text += block.text

    # Parse JSON array from response
    json_match = re.search(r'\[[\s\S]*?\]', full_text)
    if not json_match:
        log.warning(f'_find_leads: no JSON array in response for {niche}')
        return []

    try:
        leads = json.loads(json_match.group())
        return [l for l in leads if isinstance(l, dict)]
    except json.JSONDecodeError as e:
        log.error(f'_find_leads JSON parse error: {e}')
        return []


# ─── EMAIL GENERATION ─────────────────────────────────────────────────────────

def _generate_email(client, name: str, biz: str, niche: str, website: str) -> tuple:
    """
    Generate a unique personalized cold email for this lead using Claude Haiku.
    Returns (subject, body) tuple. Returns (None, None) on failure.
    """
    first    = (name or '').split()[0]
    greeting = f'Hey {first},' if first else 'Hey,'
    hook     = NICHE_HOOKS.get(niche, 'automation gaps that cost revenue')

    prompt = f"""Write a short cold email to {name or 'the owner'} at {biz}, a {niche} business{(' — ' + website) if website else ''}.

Sender: Brock Shue, Shue Box LLC (shueboxllc@gmail.com)
Product: $497 AI automation audit — finds revenue leaks, delivered in 5 days as a scored PDF + 30-min debrief call.

Core angle for {niche} businesses: {hook}

Rules:
- Start with "{greeting}"
- 3–5 sentences MAXIMUM
- Be specific to their business type — no generic "I help businesses with AI"
- No fabricated anecdotes, no "I found you while mapping"
- One clear, low-friction question or ask at the end
- Mention the $497 price
- End with: — Brock Shue\\nshueboxllc@gmail.com

Return ONLY in this format (no extra text):
Subject: [subject line here]

[email body here]"""

    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=400,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()

        subject_match = re.match(r'^Subject:\s*(.+)$', text, re.MULTILINE)
        if not subject_match:
            return None, None

        subject = subject_match.group(1).strip()
        body    = text[subject_match.end():].strip()

        # Remove stray "Body:" header if present
        body = re.sub(r'^Body:\s*\n?', '', body, flags=re.IGNORECASE).strip()

        return subject, body

    except Exception as e:
        log.error(f'_generate_email error for {biz}: {e}')
        return None, None


# ─── BATCH EXHAUSTION CHECK ───────────────────────────────────────────────────

def check_and_snipe_bottom_niches(get_db_fn) -> str:
    """
    Called by email_sender when last step-1 touch is sent.
    Pulls reply rates per niche, snipes 5 new leads for the bottom 2.
    """
    conn = get_db_fn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            oc.niche,
            COUNT(ot.id) FILTER (WHERE ot.status = 'sent')    AS sent,
            COUNT(ot.id) FILTER (WHERE ot.status = 'replied') AS replied,
            ROUND(
                COUNT(ot.id) FILTER (WHERE ot.status = 'replied')::numeric /
                NULLIF(COUNT(ot.id) FILTER (WHERE ot.status = 'sent'), 0) * 100, 1
            ) AS reply_rate
        FROM outreach_contacts oc
        LEFT JOIN outreach_touches ot ON ot.contact_id = oc.id AND ot.channel = 'email'
        WHERE oc.spoke = 'ai_audit'
        GROUP BY oc.niche
        HAVING COUNT(ot.id) FILTER (WHERE ot.status = 'sent') > 0
        ORDER BY reply_rate ASC NULLS FIRST
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        return 'No niche stats yet'

    # Alert with full niche performance breakdown
    lines = ['*📊 Batch 1 Complete — Niche Performance*', '']
    for r in rows:
        rate = r['reply_rate'] or 0
        bar  = '█' * int(rate / 10)
        lines.append(f"{r['niche'].ljust(12)} {r['sent']} sent / {r['replied']} replied — {rate}% {bar}")

    bottom = [r['niche'] for r in rows[:2]]
    lines += ['', f'Auto-sniping 5 new leads each for lowest niches: *{", ".join(bottom)}*']
    _post('\n'.join(lines))

    # Snipe bottom 2 niches
    results = []
    for niche in bottom:
        result = snipe(niche, count=5)
        results.append(result)

    return ' | '.join(results)
