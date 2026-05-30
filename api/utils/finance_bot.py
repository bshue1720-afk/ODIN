"""
ODIN Finance Bot — Teller.io integration
Monitors all bank accounts + credit cards, detects subscriptions,
tracks spending, and advises on business card ROI allocation.

Required env vars (set in Railway):
  TELLER_ACCESS_TOKEN   — from Teller Connect enrollment (covers all linked accounts)
  TELLER_CERT_B64       — base64-encoded TLS certificate (from Teller dashboard)
  TELLER_KEY_B64        — base64-encoded private key (from Teller dashboard)

Optional:
  TELLER_BIZ_CARD_ACCOUNT_ID — Teller account ID for the $5k business card
                                 (auto-detected if not set: finds credit card ~$5k limit)
"""

import os
import base64
import logging
import json
import tempfile
from datetime import datetime, timedelta
from collections import defaultdict

import anthropic
import psycopg2
import psycopg2.extras
import requests

log = logging.getLogger('odin.finance_bot')

TELLER_BASE = 'https://api.teller.io'
BIZ_CARD_LIMIT = 5000  # used for auto-detection if TELLER_BIZ_CARD_ACCOUNT_ID not set

# ─── TLS CERT SETUP ───────────────────────────────────────────────────────────

_cert_path = None
_key_path  = None


def _init_certs():
    """Decode base64 cert + key from env vars and write to temp files once per process."""
    global _cert_path, _key_path
    if _cert_path and _key_path and os.path.exists(_cert_path) and os.path.exists(_key_path):
        return
    cert_b64 = os.environ.get('TELLER_CERT_B64', '')
    key_b64  = os.environ.get('TELLER_KEY_B64', '')
    if not cert_b64 or not key_b64:
        raise RuntimeError('TELLER_CERT_B64 and TELLER_KEY_B64 must be set in Railway env vars')
    tmp = tempfile.gettempdir()
    _cert_path = os.path.join(tmp, 'teller_cert.pem')
    _key_path  = os.path.join(tmp, 'teller_key.pem')
    with open(_cert_path, 'wb') as f:
        f.write(base64.b64decode(cert_b64))
    with open(_key_path, 'wb') as f:
        f.write(base64.b64decode(key_b64))


def _get_tokens() -> list[str]:
    """
    Returns list of all Teller access tokens.
    Supports TELLER_ACCESS_TOKENS (comma-separated) or legacy TELLER_ACCESS_TOKEN.
    """
    multi = os.environ.get('TELLER_ACCESS_TOKENS', '')
    if multi:
        return [t.strip() for t in multi.split(',') if t.strip()]
    single = os.environ.get('TELLER_ACCESS_TOKEN', '')
    if single:
        return [single]
    raise RuntimeError('TELLER_ACCESS_TOKENS must be set in Railway env vars')


def _teller_get(path: str, token: str = None) -> dict | list:
    """Authenticated GET to Teller API (mTLS + HTTP Basic)."""
    _init_certs()
    if not token:
        tokens = _get_tokens()
        token = tokens[0]
    resp = requests.get(
        f'{TELLER_BASE}{path}',
        cert=(_cert_path, _key_path),
        auth=(token, ''),
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


# ─── DATABASE ─────────────────────────────────────────────────────────────────

def _db():
    url  = os.environ.get('DATABASE_URL', '')
    conn = psycopg2.connect(url, sslmode='require')
    return conn


# ─── ACCOUNT SYNC ─────────────────────────────────────────────────────────────

def sync_accounts() -> list[dict]:
    """
    Fetch all accounts from all Teller tokens and upsert into bank_accounts table.
    Returns list of account dicts with current balances.
    """
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        tokens = _get_tokens()
        all_accounts = []
        for token in tokens:
            try:
                accts = _teller_get('/accounts', token=token)
                if not isinstance(accts, list):
                    accts = accts.get('data', [])
                # Tag each account with its token for transaction syncing
                for a in accts:
                    a['_token'] = token
                all_accounts.extend(accts)
            except Exception as te:
                log.warning(f'Account sync failed for token ...{token[-6:]}: {te}')
        accounts = all_accounts

        synced = []
        biz_card_env = os.environ.get('TELLER_BIZ_CARD_ACCOUNT_ID', '')

        for acct in accounts:
            acct_id   = acct.get('id', '')
            inst_name = (acct.get('institution') or {}).get('name', '') or acct.get('institution_name', '')
            acct_name = acct.get('name', '')
            acct_type = acct.get('type', '')
            subtype   = acct.get('subtype', '')
            currency  = acct.get('currency', 'USD')

            # Fetch balance (use this account's token)
            acct_token = acct.get('_token')
            balance_data = {}
            try:
                balance_data = _teller_get(f'/accounts/{acct_id}/balances', token=acct_token)
            except Exception as be:
                log.warning(f'Balance fetch failed for {acct_id}: {be}')

            ledger_bal   = _to_float(balance_data.get('ledger'))
            avail_bal    = _to_float(balance_data.get('available'))

            # Detect credit limit from links or metadata
            credit_limit = _to_float(acct.get('credit_limit')) or None

            # Mark biz card: env var match OR first credit card with limit near $5k
            is_biz = bool(biz_card_env and acct_id == biz_card_env)
            if not is_biz and acct_type == 'credit':
                if not biz_card_env:
                    # Auto-detect: mark first credit card as biz card (user can override later)
                    cur.execute("SELECT COUNT(*) as cnt FROM bank_accounts WHERE is_biz_card = TRUE")
                    already = cur.fetchone()['cnt']
                    if already == 0:
                        is_biz = True

            cur.execute("""
                INSERT INTO bank_accounts
                    (teller_account_id, institution_name, account_name, account_type,
                     subtype, last_balance, available_balance, currency, is_biz_card,
                     credit_limit, last_synced)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (teller_account_id) DO UPDATE SET
                    institution_name  = EXCLUDED.institution_name,
                    account_name      = EXCLUDED.account_name,
                    last_balance      = COALESCE(EXCLUDED.last_balance, bank_accounts.last_balance),
                    available_balance = COALESCE(EXCLUDED.available_balance, bank_accounts.available_balance),
                    credit_limit      = COALESCE(EXCLUDED.credit_limit, bank_accounts.credit_limit),
                    last_synced       = NOW()
            """, (
                acct_id, inst_name, acct_name, acct_type,
                subtype, ledger_bal, avail_bal, currency, is_biz,
                credit_limit,
            ))

            synced.append({
                'id':           acct_id,
                'institution':  inst_name,
                'name':         acct_name,
                'type':         acct_type,
                'subtype':      subtype,
                'balance':      ledger_bal,
                'available':    avail_bal,
                'is_biz_card':  is_biz,
                'credit_limit': credit_limit,
            })

        conn.commit()
        return synced

    finally:
        conn.close()


# ─── TRANSACTION SYNC ─────────────────────────────────────────────────────────

def sync_transactions(days: int = 30) -> int:
    """
    Fetch recent transactions for all accounts and upsert into transactions table.
    Returns number of new transactions inserted.
    """
    conn    = _db()
    cur     = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    new_cnt = 0

    try:
        # Build token map: re-fetch accounts per token to know which token owns which account
        tokens = _get_tokens()
        token_map = {}  # acct_id -> token
        for token in tokens:
            try:
                accts = _teller_get('/accounts', token=token)
                if not isinstance(accts, list):
                    accts = accts.get('data', [])
                for a in accts:
                    token_map[a['id']] = token
            except Exception:
                pass

        cur.execute("SELECT teller_account_id FROM bank_accounts")
        acct_ids = [r['teller_account_id'] for r in cur.fetchall()]

        from_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

        for acct_id in acct_ids:
            acct_token = token_map.get(acct_id)
            if not acct_token:
                log.warning(f'No token found for account {acct_id} — skipping')
                continue
            try:
                txs = _teller_get(f'/accounts/{acct_id}/transactions?from_date={from_date}', token=acct_token)
                if not isinstance(txs, list):
                    txs = txs.get('data', [])

                for tx in txs:
                    tx_id    = tx.get('id', '')
                    amount   = _to_float(tx.get('amount'))
                    tx_date  = tx.get('date', '')
                    desc     = tx.get('description', '')
                    merchant = (tx.get('details') or {}).get('counterparty', {}).get('name', '') or desc
                    category = (tx.get('details') or {}).get('category', '')
                    subcat   = (tx.get('details') or {}).get('subcategory', '')
                    tx_type  = tx.get('type', '')
                    status   = tx.get('status', 'posted')

                    if not tx_id or not tx_date:
                        continue

                    cur.execute("""
                        INSERT INTO transactions
                            (teller_tx_id, teller_account_id, amount, tx_date,
                             description, merchant_name, category, subcategory, tx_type, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (teller_tx_id) DO NOTHING
                    """, (tx_id, acct_id, amount, tx_date, desc, merchant,
                          category, subcat, tx_type, status))

                    if cur.rowcount:
                        new_cnt += 1

            except Exception as te:
                log.warning(f'Transaction sync failed for {acct_id}: {te}')

        conn.commit()
        return new_cnt

    finally:
        conn.close()


# ─── SUBSCRIPTION DETECTION ───────────────────────────────────────────────────

def detect_subscriptions() -> list[dict]:
    """
    Analyze last 90 days of transactions to find recurring charges.
    Upserts results into subscriptions table.
    Returns list of detected subscriptions.
    """
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT teller_account_id, merchant_name, amount, tx_date
            FROM transactions
            WHERE tx_date >= CURRENT_DATE - INTERVAL '90 days'
              AND amount > 0
              AND merchant_name IS NOT NULL AND merchant_name != ''
              AND status = 'posted'
            ORDER BY merchant_name, teller_account_id, tx_date
        """)
        rows = cur.fetchall() or []

        # Group by (merchant, account, amount) — find recurring patterns
        groups = defaultdict(list)
        for row in rows:
            key = (row['merchant_name'], row['teller_account_id'], float(row['amount']))
            groups[key].append(row['tx_date'])

        detected = []

        for (merchant, acct_id, amount), dates in groups.items():
            if len(dates) < 2:
                continue  # need at least 2 occurrences

            dates_sorted = sorted(dates)
            gaps = []
            for i in range(1, len(dates_sorted)):
                delta = (dates_sorted[i] - dates_sorted[i-1]).days
                gaps.append(delta)

            avg_gap = sum(gaps) / len(gaps) if gaps else 0

            if 25 <= avg_gap <= 35:
                freq = 'monthly'
            elif 6 <= avg_gap <= 8:
                freq = 'weekly'
            elif 340 <= avg_gap <= 390:
                freq = 'annual'
            elif avg_gap > 0:
                freq = 'irregular'
            else:
                continue

            last_date    = dates_sorted[-1]
            next_exp     = last_date + timedelta(days=int(avg_gap))
            total_spent  = round(amount * len(dates), 2)

            cur.execute("""
                INSERT INTO subscriptions
                    (merchant_name, teller_account_id, amount, frequency,
                     last_charged, next_expected, charge_count, total_spent, status, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW())
                ON CONFLICT (merchant_name, teller_account_id, amount) DO UPDATE SET
                    frequency     = EXCLUDED.frequency,
                    last_charged  = EXCLUDED.last_charged,
                    next_expected = EXCLUDED.next_expected,
                    charge_count  = EXCLUDED.charge_count,
                    total_spent   = EXCLUDED.total_spent,
                    status        = 'active',
                    updated_at    = NOW()
            """, (merchant, acct_id, amount, freq, last_date, next_exp,
                  len(dates), total_spent))

            detected.append({
                'merchant':     merchant,
                'amount':       amount,
                'frequency':    freq,
                'last_charged': str(last_date),
                'next_expected':str(next_exp),
                'count':        len(dates),
                'total_spent':  total_spent,
            })

        conn.commit()
        return detected

    finally:
        conn.close()


# ─── BALANCES ─────────────────────────────────────────────────────────────────

def get_balances() -> dict:
    """
    Read current balances from bank_accounts table.
    Returns summary dict: deposits, credit_used, credit_available, biz_card.
    """
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT teller_account_id, institution_name, account_name,
                   account_type, subtype, last_balance, available_balance,
                   is_biz_card, credit_limit, last_synced
            FROM bank_accounts
            ORDER BY account_type, institution_name
        """)
        accounts = cur.fetchall() or []

        deposits        = []
        credit_accounts = []
        biz_card        = None
        total_deposits  = 0.0
        total_credit    = 0.0
        total_available = 0.0

        for a in accounts:
            bal   = float(a['last_balance'] or 0)
            avail = float(a['available_balance'] or 0)
            lim   = float(a['credit_limit'] or 0)

            if a['account_type'] == 'depository':
                deposits.append(a)
                total_deposits += bal
            elif a['account_type'] == 'credit':
                credit_accounts.append(a)
                total_credit    += bal
                total_available += avail
                if a['is_biz_card']:
                    biz_card = a

        reserved       = float(os.environ.get('FINANCE_RESERVED_FUNDS', '0'))
        reserved_label = os.environ.get('FINANCE_RESERVED_LABEL', 'Reserved funds')
        true_liquid    = round(total_deposits - total_credit - reserved, 2)

        return {
            'accounts':        accounts,
            'deposits':        deposits,
            'credit':          credit_accounts,
            'biz_card':        biz_card,
            'total_deposits':  round(total_deposits, 2),
            'total_credit':    round(total_credit, 2),
            'total_available': round(total_available, 2),
            'net_liquid':      round(total_deposits - total_credit, 2),
            'reserved':        reserved,
            'reserved_label':  reserved_label,
            'true_liquid':     true_liquid,
            'last_synced':     accounts[0]['last_synced'] if accounts else None,
        }

    finally:
        conn.close()


# ─── SPENDING REPORT ──────────────────────────────────────────────────────────

def get_spending_report(period: str = 'month') -> dict:
    """
    Aggregate transactions by category and merchant for the given period.
    period: 'week' | 'month' | 'ytd'
    Returns dict with totals, by_category, top_merchants, daily_avg.
    """
    if period == 'week':
        interval = '7 days'
        label    = 'Last 7 Days'
    elif period == 'ytd':
        interval = '365 days'
        label    = 'Year to Date'
    else:
        interval = '30 days'
        label    = 'Last 30 Days'

    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Total spend
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM transactions
            WHERE tx_date >= CURRENT_DATE - INTERVAL %s
              AND amount > 0
              AND status = 'posted'
        """, (interval,))
        total_spend = float(cur.fetchone()['total'])

        # By category
        cur.execute("""
            SELECT COALESCE(category, 'Uncategorized') AS cat,
                   COUNT(*) AS tx_count,
                   SUM(amount) AS total
            FROM transactions
            WHERE tx_date >= CURRENT_DATE - INTERVAL %s
              AND amount > 0 AND status = 'posted'
            GROUP BY cat
            ORDER BY total DESC
        """, (interval,))
        by_category = cur.fetchall() or []

        # Top merchants by spend
        cur.execute("""
            SELECT COALESCE(merchant_name, description, 'Unknown') AS merchant,
                   COUNT(*) AS tx_count,
                   SUM(amount) AS total
            FROM transactions
            WHERE tx_date >= CURRENT_DATE - INTERVAL %s
              AND amount > 0 AND status = 'posted'
            GROUP BY merchant
            ORDER BY total DESC
            LIMIT 10
        """, (interval,))
        top_merchants = cur.fetchall() or []

        # Biz card only spend
        cur.execute("""
            SELECT COALESCE(SUM(t.amount), 0) AS total
            FROM transactions t
            JOIN bank_accounts b ON b.teller_account_id = t.teller_account_id
            WHERE b.is_biz_card = TRUE
              AND t.tx_date >= CURRENT_DATE - INTERVAL %s
              AND t.amount > 0 AND t.status = 'posted'
        """, (interval,))
        biz_card_spend = float(cur.fetchone()['total'])

        days = 7 if period == 'week' else (365 if period == 'ytd' else 30)

        return {
            'period':         label,
            'total_spend':    round(total_spend, 2),
            'daily_avg':      round(total_spend / days, 2),
            'by_category':    [dict(r) for r in by_category],
            'top_merchants':  [dict(r) for r in top_merchants],
            'biz_card_spend': round(biz_card_spend, 2),
        }

    finally:
        conn.close()


# ─── SUBSCRIPTIONS SUMMARY ────────────────────────────────────────────────────

def get_subscriptions() -> list[dict]:
    """Read detected subscriptions from DB, ordered by amount desc."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT s.merchant_name, s.amount, s.frequency, s.last_charged,
                   s.next_expected, s.charge_count, s.total_spent, s.status,
                   b.account_name, b.institution_name
            FROM subscriptions s
            LEFT JOIN bank_accounts b ON b.teller_account_id = s.teller_account_id
            WHERE s.status = 'active'
            ORDER BY s.amount DESC
        """)
        return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


# ─── BIZ CARD STATUS ──────────────────────────────────────────────────────────

def get_biz_card() -> dict | None:
    """Return business card account details with available balance."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT teller_account_id, institution_name, account_name,
                   last_balance, available_balance, credit_limit, last_synced
            FROM bank_accounts
            WHERE is_biz_card = TRUE
            LIMIT 1
        """)
        row = cur.fetchone()
        if not row:
            return None
        r = dict(row)
        r['balance']   = float(r.get('last_balance') or 0)
        r['available'] = float(r.get('available_balance') or 0)
        r['limit']     = float(r.get('credit_limit') or BIZ_CARD_LIMIT)
        r['used_pct']  = round((r['balance'] / r['limit']) * 100, 1) if r['limit'] else 0
        return r
    finally:
        conn.close()


# ─── BIZ CARD INVESTMENT ADVISOR ──────────────────────────────────────────────

def analyze_biz_card_investment() -> str:
    """
    Claude-powered ROI analysis: where should Brock invest available biz card balance?
    Returns formatted Slack message.
    """
    biz = get_biz_card()
    if not biz:
        return '⚠️ No business card found. Run `finance sync` first, then `mark biz card <account name>`.'

    available  = biz['available']
    used       = biz['balance']
    limit      = biz['limit']
    used_pct   = biz['used_pct']
    acct_label = f"{biz['institution_name']} — {biz['account_name']}"

    # Real liquid position
    bal_data       = get_balances()
    reserved       = bal_data['reserved']
    reserved_label = bal_data['reserved_label']
    true_liquid    = bal_data['true_liquid']

    # Pull MTD revenue from revenue_events
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT spoke, COALESCE(SUM(amount), 0) AS total
            FROM revenue_events
            WHERE type = 'income'
              AND DATE_TRUNC('month', event_date) = DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY spoke
        """)
        revenue_rows = cur.fetchall() or []
        cur.execute("SELECT COUNT(*) AS cnt FROM leads WHERE status NOT IN ('closed','dead')")
        active_leads = cur.fetchone()['cnt']
        cur.execute("SELECT COUNT(*) AS cnt FROM leads WHERE mctp_total >= 8 AND status NOT IN ('closed','dead')")
        hot_leads = cur.fetchone()['cnt']
    finally:
        conn.close()

    mtd_revenue = {r['spoke']: float(r['total']) for r in revenue_rows}
    total_mtd   = sum(mtd_revenue.values())

    revenue_summary = ', '.join(
        [f'{s}: ${v:,.0f}' for s, v in mtd_revenue.items()]
    ) if mtd_revenue else 'No revenue logged yet'

    prompt = f"""You are ODIN, Brock's AI business OS and financial advisor.

BROCK'S BUSINESS CARD SNAPSHOT:
  Card: {acct_label}
  Limit: ${limit:,.0f} | Used: ${used:,.0f} ({used_pct}%) | AVAILABLE: ${available:,.0f}
  This card is exclusively for getting businesses off the ground and maximizing ROI.

REAL CASH POSITION:
  PNC checking: ${bal_data["total_deposits"]:,.2f} gross
  Reserved ({reserved_label}): -${reserved:,.2f}
  TRUE available liquid (after reserve): ${true_liquid:,.2f}
  ⚠️ Do NOT recommend anything that risks the ${reserved:,.0f} house down payment reserve.

CURRENT BUSINESS SITUATION:
  MTD Revenue: {revenue_summary} (Total: ${total_mtd:,.0f})
  Active RE Pipeline: {active_leads} leads | {hot_leads} hot (score 8+/10)

BUSINESSES:
  1. Real Estate Wholesaling (Memphis TN, virtual) — No deals closed yet.
     1,900 XLeads contacts, Eddie cold calling, 8,655 buyers in DB.
     Closest to revenue: convert hot leads into first closed deal ($10-20k fee).
  2. Valdr Ops — Missed call AI text-back SaaS. NO active leads — needs cold outreach to generate pipeline.
     $750-2k setup fee + $150-300/mo recurring. Infrastructure fully built. Target: local service businesses.
  3. PartSync Pro — On hold. HVAC contractors ($1.5-4.5k/mo retainer). No active outreach happening.

INVESTMENT OPTIONS TO EVALUATE for ${available:,.0f} available:

A. VIRTUAL ASSISTANTS ($200-400/mo/VA)
   - Cold callers for RE leads (1,900 XLeads contacts sitting untouched)
   - Valdr Ops outreach VA (cold outreach to local service businesses — no existing pipeline)
   - Best for: scaling outreach without Brock's time

B. PPL - PAY PER LEAD for RE ($30-80/lead, motivated sellers, Memphis)
   - Skip the cold list, buy pre-qualified motivated sellers
   - Better conversion rate than bulk SMS
   - Best for: accelerating first deal close

C. Valdr Ops Cold Outreach (Facebook/Google ads, LinkedIn, cold email to local businesses)
   - Target: plumbers, electricians, dentists, auto shops — businesses that miss calls
   - Must build pipeline from scratch — no existing warm leads
   - Best for: building recurring monthly revenue if Brock can close the first few clients

D. PPL for Valdr Ops (buy local biz lead lists)
   - Buy targeted local business lists to feed into ODIN outreach system
   - Best for: jumpstarting Valdr Ops pipeline without ad spend

E. PartSync Pro Relaunch (HVAC contractors, $1.5-4.5k/mo)
   - Brock transitioning from job soon — was on hold
   - Would need to restart outreach from scratch

F. Hold for RE Earnest Money ($500-2,000 per deal)
   - Need liquid reserves for deal deposits when under contract

For each option:
- Estimated ROI on ${available:,.0f}
- Weeks/months to first return
- Risk: low/medium/high
- Exact action to take with this budget

Then: *TOP RECOMMENDATION* — one specific action Brock should execute THIS WEEK.

Be direct. Think like a startup investor. No fluff."""

    try:
        key    = os.environ.get('ANTHROPIC_API_KEY', '')
        client = anthropic.Anthropic(api_key=key)
        resp   = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}],
        )
        advice = resp.content[0].text.strip()
    except Exception as e:
        advice = f'(AI analysis failed: {e})'

    lines = [
        f'*💳 Business Card ROI Advisor*',
        f'*Card:* {acct_label}',
        f'*Limit:* ${limit:,.0f} | *Used:* ${used:,.0f} ({used_pct}%) | *Available to Deploy:* *${available:,.0f}*',
        f'*MTD Revenue:* ${total_mtd:,.0f}',
        '',
        advice,
        '',
        '_Run `invest` anytime for a fresh analysis. `finance sync` updates balances._',
    ]
    return '\n'.join(lines)


# ─── FULL FINANCE DASHBOARD ───────────────────────────────────────────────────

def finance_dashboard() -> str:
    """
    Full finance dashboard: balances + MTD spend + subscriptions summary + biz card.
    Returns formatted Slack message.
    """
    try:
        bal  = get_balances()
        spend = get_spending_report('month')
        subs  = get_subscriptions()
        biz   = get_biz_card()

        now = datetime.now().strftime('%Y-%m-%d %I:%M %p ET')
        lines = [f'*💰 ODIN Finance Dashboard — {now}*', '']

        # Balances
        lines.append('*ACCOUNTS*')
        for a in bal['accounts']:
            t = a['account_type']
            b = float(a['last_balance'] or 0)
            v = float(a['available_balance'] or 0)
            tag = ' ⭐ BIZ' if a['is_biz_card'] else ''
            if t == 'depository':
                lines.append(f'  {a["institution_name"]} — {a["account_name"]}: *${b:,.2f}*{tag}')
            elif t == 'credit':
                lines.append(f'  {a["institution_name"]} — {a["account_name"]}: ${b:,.2f} used | *${v:,.2f} avail*{tag}')

        lines.append(f'\n  Net liquid (deposits − credit): ${bal["net_liquid"]:,.2f}')
        if bal['reserved'] > 0:
            lines.append(f'  Reserved ({bal["reserved_label"]}): -${bal["reserved"]:,.2f}')
            lines.append(f'  *True available liquid: ${bal["true_liquid"]:,.2f}*')
        lines.append('')

        # MTD Spend
        lines.append(f'*SPENDING — {spend["period"]}*')
        lines.append(f'  Total spend: *${spend["total_spend"]:,.2f}* | Daily avg: ${spend["daily_avg"]:,.2f}')
        if biz:
            lines.append(f'  Biz card spend: ${spend["biz_card_spend"]:,.2f} of ${biz["limit"]:,.0f} limit')
        if spend['by_category']:
            top3 = spend['by_category'][:4]
            cats = ' | '.join([f'{r["cat"]}: ${float(r["total"]):,.0f}' for r in top3])
            lines.append(f'  Top categories: {cats}')
        lines.append('')

        # Subscriptions
        sub_total = sum(float(s['amount']) for s in subs)
        lines.append(f'*SUBSCRIPTIONS ({len(subs)} active — ${sub_total:,.2f}/mo)*')
        if subs:
            for s in subs[:8]:
                freq_tag = f' ({s["frequency"]})' if s['frequency'] != 'monthly' else ''
                lines.append(f'  • {s["merchant_name"]}: *${float(s["amount"]):,.2f}/mo*{freq_tag}')
            if len(subs) > 8:
                lines.append(f'  _…and {len(subs) - 8} more. `subscriptions` for full list._')
        else:
            lines.append('  No recurring charges detected yet. Run `finance sync` first.')
        lines.append('')

        # Biz Card Quick Summary
        if biz:
            lines.append(f'*BIZ CARD — {biz["institution_name"]}*')
            lines.append(f'  ${biz["available"]:,.2f} available of ${biz["limit"]:,.0f} limit ({biz["used_pct"]}% used)')
            lines.append(f'  `invest` for AI deployment recommendation')

        lines.append('')
        lines.append('`finance sync` — refresh from Teller | `spending week` | `subscriptions` | `invest`')

        return '\n'.join(lines)

    except Exception as e:
        log.error(f'Finance dashboard failed: {e}')
        return f'❌ Finance dashboard error: {type(e).__name__}: {e}'


# ─── FULL SYNC (accounts + transactions + subscriptions + snapshot) ────────────

def full_sync() -> str:
    """Run complete sync: accounts → transactions → subscriptions → snapshot. Returns status string."""
    try:
        accounts = sync_accounts()
        new_txs  = sync_transactions(days=30)
        subs     = detect_subscriptions()
        _save_snapshot()

        lines = [
            f'*✅ Finance Sync Complete*',
            f'  Accounts synced: {len(accounts)}',
            f'  New transactions: {new_txs}',
            f'  Subscriptions detected: {len(subs)}',
        ]
        return '\n'.join(lines)
    except RuntimeError as re:
        return f'⚠️ Finance sync not ready: {re}\nSet TELLER_ACCESS_TOKEN, TELLER_CERT_B64, TELLER_KEY_B64 in Railway.'
    except Exception as e:
        log.error(f'Full sync failed: {e}')
        return f'❌ Sync failed: {type(e).__name__}: {e}'


def _save_snapshot():
    """Write today's finance snapshot to finance_snapshots table."""
    try:
        bal   = get_balances()
        spend = get_spending_report('month')
        biz   = get_biz_card()

        conn = _db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO finance_snapshots
                (snapshot_date, total_deposits, total_credit_used, total_credit_available,
                 biz_card_balance, biz_card_available, net_liquid, mtd_spend, snapshot_data)
            VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (snapshot_date) DO UPDATE SET
                total_deposits         = EXCLUDED.total_deposits,
                total_credit_used      = EXCLUDED.total_credit_used,
                total_credit_available = EXCLUDED.total_credit_available,
                biz_card_balance       = EXCLUDED.biz_card_balance,
                biz_card_available     = EXCLUDED.biz_card_available,
                net_liquid             = EXCLUDED.net_liquid,
                mtd_spend              = EXCLUDED.mtd_spend,
                snapshot_data          = EXCLUDED.snapshot_data
        """, (
            bal['total_deposits'],
            bal['total_credit'],
            bal['total_available'],
            biz['balance'] if biz else 0,
            biz['available'] if biz else 0,
            bal['net_liquid'],
            spend['total_spend'],
            json.dumps({'accounts': len(bal['accounts'])}),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f'Snapshot save failed: {e}')


# ─── MARK BIZ CARD ────────────────────────────────────────────────────────────

def mark_biz_card(search_name: str) -> str:
    """Mark an account as the biz card by partial name match. Returns status string."""
    conn = _db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("""
            SELECT teller_account_id, institution_name, account_name
            FROM bank_accounts
            WHERE LOWER(account_name) LIKE LOWER(%s)
               OR LOWER(institution_name) LIKE LOWER(%s)
            LIMIT 5
        """, (f'%{search_name}%', f'%{search_name}%'))
        matches = cur.fetchall() or []

        if not matches:
            return f'⚠️ No account matching `{search_name}` found. Run `balances` to see account names.'
        if len(matches) > 1:
            names = ', '.join([f'{m["institution_name"]} — {m["account_name"]}' for m in matches])
            return f'⚠️ Multiple matches: {names}. Be more specific.'

        acct = matches[0]
        cur.execute("UPDATE bank_accounts SET is_biz_card = FALSE")
        cur.execute("UPDATE bank_accounts SET is_biz_card = TRUE WHERE teller_account_id = %s",
                    (acct['teller_account_id'],))
        conn.commit()
        return f'✅ *{acct["institution_name"]} — {acct["account_name"]}* marked as biz card.\nRun `biz card` to see its status.'
    finally:
        conn.close()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return None
