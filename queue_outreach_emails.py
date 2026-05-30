"""
Parse all email draft files and seed outreach_touches with status='pending'.
Run once from ODIN/ root: python queue_outreach_emails.py [--apply]

Dry-run by default. Pass --apply to write to DB.
Safe to re-run — skips contacts that already have a step-1 touch.
"""

import os
import re
import sys
import psycopg2
import psycopg2.extras
from pathlib import Path

DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://postgres:yBtFmogbIuHGfNhHFhzrmlhANWfgrzxC@kodama.proxy.rlwy.net:55551/railway'
)

DRAFT_FILES = [
    'projects/ai-audit/email-drafts-columbus-plumbing.txt',
    'projects/ai-audit/email-drafts-columbus-roofing.txt',
    'projects/ai-audit/email-drafts-columbus-auto-repair.txt',
    'projects/ai-audit/email-drafts-columbus-dental.txt',
    'projects/ai-audit/email-drafts-columbus-law.txt',
]

# Hook type by niche (used for stats tracking)
NICHE_HOOK_DEFAULTS = {
    'plumbing':   'missed_calls',
    'roofing':    'storm_surge',
    'auto_repair':'estimate_followup',
    'dental':     'labor_cost',
    'law':        'intake_conversion',
}


def parse_emails(filepath: str) -> list[dict]:
    """Parse a draft file into a list of {to, subject, body} dicts."""
    text = Path(filepath).read_text(encoding='utf-8', errors='replace')
    emails = []

    # Split on '---' separator lines
    blocks = re.split(r'\n---\n', text)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        to_match      = re.search(r'^To:\s*(.+)$',      block, re.MULTILINE)
        subject_match = re.search(r'^Subject:\s*(.+)$', block, re.MULTILINE)

        if not to_match or not subject_match:
            continue

        to_email = to_match.group(1).strip()
        subject  = subject_match.group(1).strip()

        # Body is everything after the blank line following Subject:
        after_subject = block[subject_match.end():]
        body_parts    = after_subject.split('\n', 1)
        body          = body_parts[1].strip() if len(body_parts) > 1 else ''

        # Remove the trailing signature if duplicated (keep it — it's in the draft)
        if to_email and subject and body:
            emails.append({'to': to_email, 'subject': subject, 'body': body})

    return emails


def run(apply: bool = False):
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    inserted = 0
    skipped  = 0

    for filepath in DRAFT_FILES:
        full_path = Path(__file__).parent / filepath
        if not full_path.exists():
            print(f'  MISSING: {filepath}')
            continue

        emails = parse_emails(str(full_path))
        print(f'\n[{filepath}] — {len(emails)} emails found')

        for e in emails:
            # Look up contact
            cur.execute("""
                SELECT id, name, business_name, niche, status
                FROM outreach_contacts
                WHERE LOWER(email) = LOWER(%s)
            """, (e['to'],))
            contact = cur.fetchone()

            if not contact:
                print(f'  SKIP (no contact): {e["to"]}')
                skipped += 1
                continue

            # Skip if already has a step-1 touch
            cur.execute("""
                SELECT id FROM outreach_touches
                WHERE contact_id = %s AND sequence_step = 1
            """, (contact['id'],))
            if cur.fetchone():
                print(f'  SKIP (already queued): {contact["business_name"]} <{e["to"]}>')
                skipped += 1
                continue

            hook = NICHE_HOOK_DEFAULTS.get(contact['niche'], 'missed_calls')

            if apply:
                cur.execute("""
                    INSERT INTO outreach_touches
                        (contact_id, channel, status, subject, body, sequence_step, hook_type)
                    VALUES (%s, 'email', 'pending', %s, %s, 1, %s)
                """, (contact['id'], e['subject'], e['body'], hook))
                print(f'  QUEUE: {contact["business_name"]} <{e["to"]}> | {e["subject"][:50]}')
            else:
                print(f'  DRY RUN: {contact["business_name"]} <{e["to"]}> | {e["subject"][:50]}')

            inserted += 1

    if apply:
        conn.commit()
        print(f'\nQueued {inserted} emails, skipped {skipped}.')
        print('Set OUTREACH_ENABLED=true in Railway when ready to start sending.')
    else:
        print(f'\nDry run: {inserted} would be queued, {skipped} skipped.')
        print('Run with --apply to write to DB.')

    cur.close()
    conn.close()


if __name__ == '__main__':
    run(apply='--apply' in sys.argv)
