"""
Seed outreach_contacts table from all Columbus email draft files.
Run from ODIN/ root: python seed_outreach_contacts.py [--apply]

Dry-run by default — shows what would be inserted.
Pass --apply to write to DB.
"""

import os
import sys
import re
import psycopg2
import psycopg2.extras
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / 'api' / '.env')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ─── Contact data keyed by email (parsed from draft files) ───────────────────
# Format: (name, business_name, niche, email, website, tier, source, hook_type)

CONTACTS = [
    # ── PLUMBING (21) ──────────────────────────────────────────────────────────
    ('Dick',      'Beckley PHC',                    'plumbing', 'beckleyphc@frontier.com',                   '', 0, 'leads_plumbing', 'after_hours_coverage'),
    ('Bret',      'Jim Speiser Electric',            'plumbing', 'bret@jimspeiserelectric.com',              '', 0, 'leads_plumbing', 'high_ticket_miss'),
    ('Paul',      'Joe Ganzel Plumbing',             'plumbing', 'ganzelplumbing@gmail.com',                 '', 0, 'leads_plumbing', 'missed_calls'),
    ('Scott',     'Johnson Plumbing',                'plumbing', 'scottjohnson7213@gmail.com',               '', 0, 'leads_plumbing', 'visibility_gap'),
    ('Rebecca',   'Lippincott Plumbing',             'plumbing', 'becky@lippincottplumbing.com',             '', 0, 'leads_plumbing', 'reputation_inbound'),
    ('Jeremy',    'Main & Sons',                     'plumbing', 'mainsons1@gmail.com',                      '', 0, 'leads_plumbing', 'owner_operated_gap'),
    ('Shaun',     'Mancini Plumbing',                'plumbing', 'plushaun78@gmail.com',                     '', 0, 'leads_plumbing', 'drain_urgency'),
    ('Rodney',    'Northwest Septic',                'plumbing', 'northwestseptic5@gmail.com',               '', 0, 'leads_plumbing', 'emergency_urgency'),
    ('Paul',      'Paul Fox & Sons',                 'plumbing', 'paulfoxsons@gmail.com',                    '', 0, 'leads_plumbing', 'high_ticket_miss'),
    ('Mike',      'Petrie Plumbing',                 'plumbing', 'mikedpetrie@gmail.com',                    '', 0, 'leads_plumbing', 'phone_coverage'),
    ('Dave',      'Hall Plumbing and Heating',       'plumbing', 'hallplumbingandheating@yahoo.com',         '', 0, 'leads_plumbing', 'reputation_inbound'),
    ('Jamie',     'T&J Plumbing',                    'plumbing', 'tandjplumbing@att.net',                    '', 0, 'leads_plumbing', 'drain_urgency'),
    ('Doug',      'Tressler Plumbing',               'plumbing', 'dougtressler@yahoo.com',                   '', 0, 'leads_plumbing', 'call_answer_rate'),
    (None,        'Action Sewer Cleaning Plumbing',  'plumbing', 'actionsewercleaningplumbingllc@gmail.com', '', 0, 'leads_plumbing', 'missed_calls'),
    (None,        'Alternative Plumbing Plus',        'plumbing', 'alternativeplumbing@bex.net',              '', 0, 'leads_plumbing', 'missed_calls'),
    (None,        'Joshua Plumbing LLC',              'plumbing', 'joshuaplumbingllc@yahoo.com',              '', 0, 'leads_plumbing', 'missed_calls'),
    (None,        'Knueve & Sons',                   'plumbing', 'service@knueve.com',                       '', 0, 'leads_plumbing', 'voicemail_followup'),
    (None,        'Reliable Sewer Cleaning',         'plumbing', 'reliablesewercleaning@gmail.com',          '', 0, 'leads_plumbing', 'after_hours_coverage'),
    (None,        'Rooter Brothers',                 'plumbing', 'rooterbros1@gmail.com',                    '', 0, 'leads_plumbing', 'partner_phone_gap'),
    ('Doug',      'Dewese Services',                 'plumbing', 'dendew@hotmail.com',                       '', 0, 'leads_plumbing', 'high_ticket_miss'),
    (None,        'Interior Channel',                'plumbing', 'smiller_interiorchannel@yahoo.com',        '', 0, 'leads_plumbing', 'missed_calls'),

    # ── ROOFING (5) ────────────────────────────────────────────────────────────
    ('Nico',      'Hendo Roofing',                   'roofing',  'nico@hendoroofing.com',                    'https://www.hendoroofing.com',                  0, 'leads_007_roofing', 'storm_surge'),
    ('Chad',      'Muth & Company Roofing',          'roofing',  'chad@muthandco.com',                       'https://muthroofing.com',                       0, 'leads_007_roofing', 'reputation_inbound'),
    ('Justin',    'Able Roofing',                    'roofing',  'smile@ableroof.com',                       'https://www.ableroof.com',                      0, 'leads_007_roofing', 'call_conversion_tracking'),
    ('Joe',       'Columbus Roofing Rescue',         'roofing',  'office@roofingrescue.com',                 'https://www.columbusroofingrescue.com',          0, 'leads_007_roofing', 'emergency_urgency'),
    ('William',   'Brothers Roofing & Construction', 'roofing',  'william@brothersroofingconstruction.com',  'https://www.brothersroofingconstruction.com',   0, 'leads_007_roofing', 'high_volume_inbound'),

    # ── AUTO REPAIR (5) ────────────────────────────────────────────────────────
    ('Brian',     'A.B.S. Auto Service',             'auto_repair', 'service@autoserviceabs.com',            'https://autoserviceabs.com',             0, 'leads_004_auto-repair', 'estimate_followup'),
    ('Todd',      'Buckeye Complete Auto Care',      'auto_repair', 'todd@buckeyecompleteauto.com',          'https://www.buckeyecompleteauto.com',    0, 'leads_004_auto-repair', 'lapsed_customer_reactivation'),
    ('Luke',      "Luke's Auto",                     'auto_repair', 'contact@lukesautoservice.com',          'https://lukesautoservice.com',           0, 'leads_004_auto-repair', 'owner_operated_gap'),
    ('Jerry',     "Tom & Jerry's Auto Service",      'auto_repair', 'info@tomandjerrysauto.com',             'https://www.tomandjerrysauto.com',       0, 'leads_004_auto-repair', 'reputation_inbound'),
    ('Chris',     'Alternative Auto Care',           'auto_repair', 'customerservice@alternativeautocare.com','https://alternativeautocare.com',       0, 'leads_004_auto-repair', 'speed_to_lead'),

    # ── DENTAL (5) ─────────────────────────────────────────────────────────────
    ('Dr. Kevin Albert',    'Clintonville Dental Group',     'dental', 'info@clintonvilledentalgroup.com',    'https://www.clintonvilledentalgroup.com',  0, 'leads_005_dental', 'labor_cost'),
    ('Dr. Erin Whittaker',  'Whittaker Dental Group',        'dental', 'scheduling@flossyourteeth.com',       'http://www.flossyourteeth.com',            0, 'leads_005_dental', 'patient_reactivation'),
    ('Dr. Mark Raisch',     'Advanced Dental Wellness',      'dental', 'office@adwcolumbus.com',              'https://www.advanceddentalwellness.com',   0, 'leads_005_dental', 'no_show_reduction'),
    ('Dr. Matthew Paulson', 'Bauer Dental Group',            'dental', 'info@columbusdentists.net',           'https://columbusdentists.net',             0, 'leads_005_dental', 'new_patient_followup_speed'),
    ('Dr. David Dixon',     'Columbus Family Dental Care',   'dental', 'info@columbusfamilydentalcare.com',   'https://www.columbusfamilydentalcare.com', 0, 'leads_005_dental', 'review_generation'),

    # ── LAW (5) ────────────────────────────────────────────────────────────────
    ('David Van Slyke',  'Plunkett Cooney',                       'law', 'dvanslyke@plunkettcooney.com', 'https://www.plunkettcooney.com', 0, 'leads_006_law', 'intake_conversion'),
    ('Steven Harris',    'Harris & Engler',                       'law', 'contactus@harrisengler.com',   'https://www.harrisengler.com',   0, 'leads_006_law', 'intake_conversion'),
    ('Mike Anthony',     'Anthony Law LLC',                       'law', 'admin@anthonylawllc.com',      'https://anthonylawllc.com',      0, 'leads_006_law', 'small_firm_intake'),
    ('Beatrice Wolper',  'Emens Wolper Jacobs & Jasin Law',       'law', 'info@ewjjlaw.com',             'https://ewjjlaw.com',            0, 'leads_006_law', 'after_hours_intake'),
    ('Theran Selph',     'Selph Law',                             'law', 'info@selphlaw.com',            'https://www.selphlaw.com',       0, 'leads_006_law', 'boutique_intake_speed'),
]


def run(apply: bool = False):
    if not DATABASE_URL:
        print('ERROR: DATABASE_URL not set. Export it or add to api/.env')
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    inserted = 0
    skipped  = 0

    for (name, biz, niche, email, website, tier, source, hook_type) in CONTACTS:
        cur.execute("SELECT id FROM outreach_contacts WHERE LOWER(email)=LOWER(%s)", (email,))
        existing = cur.fetchone()
        if existing:
            print(f'  SKIP (exists): {biz} <{email}>')
            skipped += 1
            continue

        if apply:
            cur.execute("""
                INSERT INTO outreach_contacts
                    (name, business_name, niche, email, website, tier, source, spoke, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'ai_audit', 'cold')
            """, (name, biz, niche, email, website, tier, source))
            print(f'  INSERT: {biz} <{email}> [{niche}] hook={hook_type}')
        else:
            print(f'  DRY RUN: {biz} <{email}> [{niche}] hook={hook_type}')
        inserted += 1

    if apply:
        conn.commit()
        print(f'\n✅ Inserted {inserted} contacts, skipped {skipped} existing.')
    else:
        print(f'\nDry run: {inserted} would be inserted, {skipped} already exist.')
        print('Run with --apply to write to DB.')

    cur.close()
    conn.close()


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    run(apply=apply)
