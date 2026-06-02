"""ODIN Blueprint — Leads and Buyers routes."""
import os
import psycopg2.extras
from flask import Blueprint, request, jsonify, g

from core.db      import get_db, log_action
from core.auth    import require_auth
from core.helpers import _slack_post
from utils.permissions import check_permission_role
from utils.approval_router import route_approval, build_offer_script
from utils.slack_templates import (
    send_slack_notification, hot_lead_notification, warm_lead_notification, approval_response_notification,
)
import utils.xleads as xleads
import utils.offer_calculator as offer_calc

bp = Blueprint('leads', __name__)


# ─── VA INTAKE WEBHOOK ───────────────────────────────────────────────

@bp.route('/api/leads/va-intake', methods=['POST'])
def va_intake():
    """
    Webhook called by Google Apps Script when a VA submits a lead via Google Form.
    Secured by VA_INTAKE_TOKEN env var.
    Creates lead in ODIN DB + XLeads contact + Slack alert.
    """
    token = os.environ.get('VA_INTAKE_TOKEN', '')
    auth  = request.headers.get('Authorization', '')
    if token and auth != f'Bearer {token}':
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}

    seller_name = (data.get('seller_name') or '').strip()
    phone       = (data.get('phone')       or '').strip()
    address     = (data.get('address')     or '').strip()
    city        = (data.get('city')        or 'Memphis').strip()
    state       = (data.get('state')       or 'TN').strip()
    zip_code    = (data.get('zip')         or '').strip()
    notes       = (data.get('notes')       or '').strip()
    va_name     = (data.get('va_name')     or 'VA').strip()

    if not address:
        return jsonify({'error': 'address is required'}), 400

    m_score = min(int(data.get('m_score', 0)), 3)
    c_score = min(int(data.get('c_score', 0)), 2)
    t_score = min(int(data.get('t_score', 0)), 3)
    p_score = min(int(data.get('p_score', 0)), 2)
    mctp_total = m_score + c_score + t_score + p_score
    status = 'hot' if mctp_total >= 8 else ('warm' if mctp_total >= 5 else 'new')
    tier   = '🔥 HOT' if status == 'hot' else ('🌡 WARM' if status == 'warm' else '🧊 COLD')

    # Upsert lead in ODIN DB
    conn = get_db()
    lead_id = None
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute('SELECT id FROM users WHERE role = %s LIMIT 1', ('super_admin',))
            admin = cur.fetchone()
            user_id = admin['id'] if admin else None

            cur.execute('SELECT id FROM leads WHERE address ILIKE %s', (address,))
            existing = cur.fetchone()
            if existing:
                lead_id = existing['id']
                cur.execute("""
                    UPDATE leads SET
                        motivation_score = %s, condition_score = %s,
                        timeline_score = %s, price_score = %s,
                        status = %s, caller_notes = %s,
                        lead_source = 'va_cold_call', updated_at = NOW()
                    WHERE id = %s
                """, (m_score, c_score, t_score, p_score, status, notes, lead_id))
            else:
                cur.execute("""
                    INSERT INTO leads (
                        user_id, address, city, state, zip,
                        motivation_score, condition_score, timeline_score, price_score,
                        status, caller_notes, spoke, lead_source, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, 'real_estate', 'va_cold_call', NOW(), NOW()
                    ) RETURNING id
                """, (user_id, address, city, state, zip_code,
                      m_score, c_score, t_score, p_score,
                      status, notes))
                row = cur.fetchone()
                lead_id = row['id'] if row else None
        conn.commit()
    finally:
        conn.close()

    # Create XLeads contact
    xleads_contact_id = None
    try:
        name_parts = seller_name.split(' ', 1)
        first = name_parts[0]
        last  = name_parts[1] if len(name_parts) > 1 else ''
        result = xleads.create_contact(
            first_name=first, last_name=last,
            phone=phone, address=address,
            city=city, state=state, zip_code=zip_code,
            tags=['va-lead']
        )
        xleads_contact_id = (result.get('contact') or {}).get('id')
    except Exception as e:
        print(f'[va_intake] XLeads contact creation failed: {e}')

    # Slack alert to #odin-brock
    try:
        brock_ch = os.environ.get('SLACK_CHANNEL_BROCK', '')
        if brock_ch:
            msg = (
                f"{tier} *VA Lead Submitted* — {seller_name or 'Unknown Seller'}\n"
                f">*Address:* {address}, {city}, {state} {zip_code}\n"
                f">*Phone:* {phone or 'N/A'}\n"
                f">*MCTP:* {mctp_total}/10 (M:{m_score} C:{c_score} T:{t_score} P:{p_score})\n"
                f">*Notes:* {notes or '—'}\n"
                f">*Submitted by:* {va_name}"
            )
            _slack_post(brock_ch, msg)
    except Exception as e:
        print(f'[va_intake] Slack alert failed: {e}')

    log_action(None, 'va_intake', 'leads', lead_id,
               {'address': address, 'mctp_total': mctp_total, 'status': status, 'va': va_name})

    return jsonify({
        'ok':                True,
        'lead_id':           str(lead_id) if lead_id else None,
        'xleads_contact_id': xleads_contact_id,
        'mctp_total':        mctp_total,
        'status':            status,
    })


# ─── BUYERS ─────────────────────────────────────────────────────────

@bp.route('/api/buyers', methods=['GET'])
@require_auth
def list_buyers():
    role = g.user.get('role')
    if not check_permission_role(role, 'read', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    market   = request.args.get('market', '')
    active   = request.args.get('active', '')
    search   = request.args.get('search', '')
    page     = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    offset   = (page - 1) * per_page

    conn = get_db()
    try:
        with conn.cursor() as cur:
            conditions, params = ['1=1'], []
            if market:
                conditions.append("(market ILIKE %s OR state = %s OR city ILIKE %s)")
                params += [f'%{market}%', market.upper(), f'%{market}%']
            if active == 'true':
                conditions.append("active = true AND is_dnc = false")
            elif active == 'false':
                conditions.append("active = false")
            if search:
                conditions.append("(name ILIKE %s OR email ILIKE %s OR phone ILIKE %s OR company ILIKE %s)")
                params += [f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%']

            where = ' AND '.join(conditions)
            cur.execute("SELECT COUNT(*) as cnt FROM buyers WHERE " + where, params)
            total = cur.fetchone()['cnt']

            cur.execute(
                "SELECT * FROM buyers WHERE " + where +
                " ORDER BY active DESC, is_flipper DESC, portfolio_owned DESC LIMIT %s OFFSET %s",
                params + [per_page, offset]
            )
            buyers = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('created_at'): row['created_at'] = row['created_at'].isoformat()
                if row.get('updated_at'): row['updated_at'] = row['updated_at'].isoformat()
                buyers.append(row)
    finally:
        conn.close()

    return jsonify({'buyers': buyers, 'total': total, 'page': page, 'per_page': per_page})


# ─── LEADS ──────────────────────────────────────────────────────────

@bp.route('/api/leads/sync-xleads', methods=['POST'])
@require_auth
def sync_xleads_leads():
    role = g.user.get('role')
    if role not in ('super_admin', 're_partner', 'acquisition_manager'):
        return jsonify({'error': 'Permission denied'}), 403

    limit = int(request.json.get('limit', 100)) if request.json else 100
    tags  = request.json.get('tags', []) if request.json else []

    try:
        contacts = xleads.search_contacts(tags=tags, limit=limit) if tags else xleads.get_contacts(limit=limit)
    except Exception as e:
        return jsonify({'error': f'XLeads fetch failed: {str(e)}'}), 500

    if not contacts:
        return jsonify({'synced': 0, 'message': 'No contacts returned from XLeads'})

    conn = get_db()
    synced, skipped = 0, 0
    try:
        with conn.cursor() as cur:
            for c in contacts:
                address_parts = [
                    c.get('address1') or c.get('address', ''),
                    c.get('city', ''),
                    c.get('state', ''),
                    c.get('postalCode') or c.get('zip', ''),
                ]
                address = ', '.join(p for p in address_parts if p).strip(', ')
                if not address:
                    skipped += 1
                    continue
                cur.execute("SELECT id FROM leads WHERE address = %s", (address,))
                if cur.fetchone():
                    skipped += 1
                    continue
                city  = c.get('city', '')
                state = c.get('state', '')
                zip_  = c.get('postalCode') or c.get('zip', '')
                cur.execute("""
                    INSERT INTO leads (id, address, city, state, zip, status, spoke, created_at, updated_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, 'new', 'real_estate', NOW(), NOW())
                """, (address, city, state, zip_))
                synced += 1
        conn.commit()
    finally:
        conn.close()

    return jsonify({'synced': synced, 'skipped': skipped, 'total_fetched': len(contacts)})


@bp.route('/api/leads', methods=['GET'])
@require_auth
def list_leads():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')
    status  = request.args.get('status')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            base = """
                SELECT l.*, u.name as assigned_to_name
                FROM leads l
                LEFT JOIN users u ON u.id = l.assigned_to
            """
            if role == 'caller':
                query  = base + " WHERE (l.assigned_to = %s OR l.assigned_to IS NULL)"
                params = [user_id]
                if status:
                    query += ' AND l.status = %s'
                    params.append(status)
                query += ' ORDER BY l.created_at DESC'
                cur.execute(query, params)
            elif role == 'disposition_agent':
                cur.execute(base + " WHERE l.status = 'contracted' ORDER BY l.updated_at DESC")
            else:
                if not check_permission_role(role, 'read', 'leads'):
                    return jsonify({'error': 'Permission denied'}), 403
                params = []
                query  = base + ' WHERE 1=1'
                if status:
                    query += ' AND l.status = %s'
                    params.append(status)
                query += ' ORDER BY l.mctp_total DESC NULLS LAST, l.created_at DESC'
                cur.execute(query, params)

            leads = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('user_id'):      row['user_id']     = str(row['user_id'])
                if row.get('assigned_to'):  row['assigned_to'] = str(row['assigned_to'])
                if row.get('created_at'):   row['created_at']  = row['created_at'].isoformat()
                if row.get('updated_at'):   row['updated_at']  = row['updated_at'].isoformat()
                leads.append(row)
    finally:
        conn.close()

    return jsonify({'leads': leads})


@bp.route('/api/leads/mctp', methods=['POST'])
@require_auth
def log_mctp():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    if not check_permission_role(role, 'write', 'assigned_leads') and \
       not check_permission_role(role, 'write', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    data    = request.get_json() or {}
    address = (data.get('address') or '').strip()
    if not address:
        return jsonify({'error': 'address is required'}), 400

    mot_score      = int(data.get('motivation_score', 0))
    cond_score     = int(data.get('condition_score', 0))
    timeline_score = int(data.get('timeline_score', 0))
    price_score    = int(data.get('price_score', 0))
    mctp_total     = mot_score + cond_score + timeline_score + price_score
    status         = 'hot' if mctp_total >= 8 else ('warm' if mctp_total >= 5 else 'new')

    if not data.get('arv_mid') and address:
        try:
            deal_result = offer_calc.analyze_deal(
                address=address,
                beds=int(data.get('beds', 3)),
                baths=float(data.get('baths', 2.0)),
                sqft=int(data.get('sqft', 0)),
                condition=data.get('condition', 'unknown'),
                zip_code=data.get('zip', ''),
            )
            arv  = deal_result['arv']
            deal = deal_result['deal']
            data.setdefault('arv_mid',         arv['arv_mid'])
            data.setdefault('rehab_mid',        deal_result['rehab_used'])
            data.setdefault('lao',              deal['lao'])
            data.setdefault('walkup',           deal['walk_up_max'])
            data.setdefault('assign_price',     deal['assign_price'])
            data.setdefault('fee_at_lao',       deal['fee_at_lao'])
            data.setdefault('fee_at_walkup',    deal['fee_at_walkup'])
            data.setdefault('rec_max_contract', deal['contract_for_target'])
        except Exception as e:
            print(f'[log_mctp] auto deal math failed: {e}')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM leads WHERE address = %s AND user_id = %s', (address, user_id))
            existing = cur.fetchone()

            if existing:
                lead_id = existing['id']
                cur.execute(
                    """UPDATE leads SET
                        motivation = %s, motivation_score = %s,
                        condition_score = %s, timeline = %s, timeline_score = %s,
                        asking_price = %s, price_score = %s,
                        condition = %s, caller_notes = %s,
                        next_action = %s, follow_up_date = %s,
                        status = %s, beds = %s, baths = %s, sqft = %s,
                        arv_mid = %s, rehab_mid = %s, lao = %s, walkup = %s,
                        buyer_max = %s, assign_price = %s, fee_at_lao = %s,
                        fee_at_walkup = %s, rec_max_contract = %s
                       WHERE id = %s""",
                    (
                        data.get('motivation'), mot_score, cond_score,
                        data.get('timeline'), timeline_score,
                        data.get('asking_price'), price_score,
                        data.get('condition'), data.get('caller_notes'),
                        data.get('next_action'), data.get('follow_up_date'),
                        status,
                        data.get('beds'), data.get('baths'), data.get('sqft'),
                        data.get('arv_mid'), data.get('rehab_mid'),
                        data.get('lao'), data.get('walkup'), data.get('buyer_max'),
                        data.get('assign_price'), data.get('fee_at_lao'),
                        data.get('fee_at_walkup'), data.get('rec_max_contract'),
                        lead_id
                    )
                )
            else:
                cur.execute(
                    """INSERT INTO leads (
                        user_id, assigned_to, address, city, state, zip,
                        beds, baths, sqft, condition,
                        motivation, motivation_score, condition_score,
                        timeline, timeline_score, asking_price, price_score,
                        arv_mid, rehab_mid, lao, walkup, buyer_max,
                        assign_price, fee_at_lao, fee_at_walkup, rec_max_contract,
                        status, caller_notes, next_action, follow_up_date
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id""",
                    (
                        user_id, user_id,
                        address, data.get('city', 'Memphis'), data.get('state', 'TN'), data.get('zip'),
                        data.get('beds'), data.get('baths'), data.get('sqft'), data.get('condition'),
                        data.get('motivation'), mot_score, cond_score,
                        data.get('timeline'), timeline_score,
                        data.get('asking_price'), price_score,
                        data.get('arv_mid'), data.get('rehab_mid'),
                        data.get('lao'), data.get('walkup'), data.get('buyer_max'),
                        data.get('assign_price'), data.get('fee_at_lao'),
                        data.get('fee_at_walkup'), data.get('rec_max_contract'),
                        status, data.get('caller_notes'), data.get('next_action'), data.get('follow_up_date'),
                    )
                )
                lead_id = cur.fetchone()['id']
            conn.commit()
    finally:
        conn.close()

    log_action(user_id, 'log_mctp', 'leads', lead_id,
               {'address': address, 'mctp_total': mctp_total, 'status': status})

    response = {
        'lead_id':    str(lead_id),
        'mctp_total': mctp_total,
        'status':     status,
        'message':    f'MCTP logged. Score: {mctp_total}/10 — {status.upper()}',
    }

    xleads_contact_id = data.get('xleads_contact_id')
    if xleads_contact_id and mctp_total >= 5:
        try:
            xl_result = xleads.sync_mctp_to_xleads(
                xleads_contact_id, mctp_total, address, data.get('xleads_workflow_id')
            )
            response['xleads_sync'] = xl_result
        except Exception as e:
            response['xleads_sync'] = {'error': str(e)}

    if mctp_total >= 5:
        lead_payload = {**data, 'mctp_total': mctp_total,
                        'caller_name': g.user.get('name', 'Caller'),
                        'resource_id': str(lead_id)}
        try:
            approval_result = route_approval('hot_lead_review', lead_payload, user_id, get_db)
            response['approval_queue_id'] = approval_result.get('approval_queue_id')
            response['routed_to']         = approval_result.get('approver_role')

            if mctp_total >= 8:
                lead_payload['approval_queue_id'] = approval_result.get('approval_queue_id')
                slack_msg = hot_lead_notification(lead_payload, mctp_total, approval_result.get('approver_role'))
            else:
                slack_msg = warm_lead_notification(lead_payload, mctp_total)

            send_slack_notification(approval_result.get('approver_role', 'super_admin'), slack_msg)
        except Exception as e:
            print(f'[route_approval] Error: {e}')

    return jsonify(response), 201


@bp.route('/api/leads/<lead_id>/assign', methods=['POST'])
@require_auth
def assign_lead(lead_id):
    role = g.user.get('role')
    if not check_permission_role(role, 'write', 'leads'):
        return jsonify({'error': 'Permission denied'}), 403

    data        = request.get_json() or {}
    assignee_id = data.get('assignee_id')
    if not assignee_id:
        return jsonify({'error': 'assignee_id required'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE leads SET assigned_to = %s WHERE id = %s RETURNING address',
                (assignee_id, lead_id)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Lead not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'assign_lead', 'leads', lead_id, {'assignee_id': assignee_id})
    finally:
        conn.close()

    return jsonify({'message': 'Lead assigned', 'address': row['address']})
