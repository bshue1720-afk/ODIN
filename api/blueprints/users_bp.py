"""ODIN Blueprint — User management routes."""
import secrets

import psycopg2.sql
import psycopg2.errors
from flask import Blueprint, request, jsonify, g

from core.db   import get_db, log_action, BASE_URL
from core.auth import require_auth, require_role, VALID_ROLES
from utils.slack_templates import send_slack_notification, new_user_invite_notification

bp = Blueprint('users', __name__)


@bp.route('/api/users', methods=['GET'])
@require_auth
def list_users():
    from core.auth import require_permission_for as _rpf  # noqa: avoid circular at module load
    from utils.permissions import check_permission_role
    role_filter      = request.args.get('role')
    requesting_role  = g.user.get('role')

    if not check_permission_role(requesting_role, 'read', 'users'):
        return jsonify({'error': 'Permission denied'}), 403

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if requesting_role == 'acquisition_manager':
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users WHERE role = 'caller' ORDER BY name"""
                )
            elif role_filter:
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users WHERE role = %s ORDER BY name""",
                    (role_filter,)
                )
            else:
                cur.execute(
                    """SELECT id, name, email, phone, namespace, role, active,
                              can_approve_deals, max_approvable_fee, onboarded,
                              last_active, created_at
                       FROM users ORDER BY
                           CASE role
                               WHEN 'super_admin' THEN 1
                               WHEN 're_partner' THEN 2
                               WHEN 'acquisition_manager' THEN 3
                               WHEN 'caller' THEN 4
                               WHEN 'disposition_agent' THEN 5
                               WHEN 'business_standard' THEN 6
                               WHEN 'virtual_assistant' THEN 7
                           END, name"""
                )
            users = [dict(r) for r in cur.fetchall()]
            for u in users:
                u['id'] = str(u['id'])
                if u.get('last_active'):
                    u['last_active'] = u['last_active'].isoformat()
                if u.get('created_at'):
                    u['created_at'] = u['created_at'].isoformat()
    finally:
        conn.close()

    return jsonify({'users': users})


@bp.route('/api/users', methods=['POST'])
@require_auth
@require_role('super_admin')
def create_user():
    data    = request.get_json() or {}
    name    = (data.get('name') or '').strip()
    email   = (data.get('email') or '').strip().lower()
    phone   = (data.get('phone') or '').strip()
    role    = (data.get('role') or '').strip()
    spend   = data.get('spend_limit')
    max_fee = data.get('max_approvable_fee')

    if not name or not email or not role:
        return jsonify({'error': 'name, email, and role are required'}), 400
    if role not in VALID_ROLES:
        return jsonify({'error': f'Invalid role. Must be one of: {", ".join(VALID_ROLES)}'}), 400
    if role == 'super_admin' and g.user.get('role') == 'super_admin':
        return jsonify({'error': 'Cannot create another super_admin'}), 403

    namespace    = name.lower().replace(' ', '_').replace('-', '_')[:50]
    can_approve  = role in ('super_admin', 're_partner')
    if not max_fee and role == 're_partner':
        max_fee = 15000

    conn = get_db()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """INSERT INTO users
                       (name, email, phone, namespace, role, spend_limit,
                        can_approve_deals, max_approvable_fee, created_by,
                        invited_at, active, onboarded)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), true, false)
                       RETURNING id, name, email, role, namespace""",
                    (name, email, phone or None, namespace, role,
                     spend, can_approve, max_fee, g.user.get('user_id'))
                )
                new_user = dict(cur.fetchone())
                new_user['id'] = str(new_user['id'])
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                return jsonify({'error': 'Email or namespace already exists'}), 409

            invite_token = secrets.token_urlsafe(32)
            cur.execute(
                """INSERT INTO invite_tokens (user_id, token, expires_at)
                   VALUES (%s, %s, NOW() + INTERVAL '48 hours')""",
                (new_user['id'], invite_token)
            )
            conn.commit()

        invite_url = f"{BASE_URL}/set-password?token={invite_token}"
        log_action(g.user['user_id'], 'create_user', 'users',
                   new_user['id'], {'role': role, 'name': name})

        if role == 'caller':
            msg = new_user_invite_notification(name, role, invite_url)
            send_slack_notification('caller', msg, namespace=namespace)

    finally:
        conn.close()

    return jsonify({
        'user': new_user,
        'invite_url': invite_url,
        'message': 'User created. Invite link expires in 48 hours.'
    }), 201


@bp.route('/api/users/<user_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def update_user(user_id):
    import psycopg2.sql as _sql
    data    = request.get_json() or {}
    allowed = ('role', 'spend_limit', 'max_approvable_fee', 'phone', 'name')
    updates = {k: v for k, v in data.items() if k in allowed}

    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400
    if 'role' in updates and updates['role'] not in VALID_ROLES:
        return jsonify({'error': 'Invalid role'}), 400
    if 'role' in updates and updates['role'] == 'super_admin':
        return jsonify({'error': 'Cannot assign super_admin role'}), 403

    values = list(updates.values()) + [user_id]
    conn   = get_db()
    try:
        with conn.cursor() as cur:
            set_clause = _sql.SQL(', ').join(
                _sql.SQL('{} = %s').format(_sql.Identifier(k))
                for k in updates
            )
            cur.execute(
                _sql.SQL('UPDATE users SET {} WHERE id = %s RETURNING id, name, role').format(set_clause),
                values
            )
            updated = cur.fetchone()
            if not updated:
                return jsonify({'error': 'User not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'update_user', 'users', user_id, updates)
    finally:
        conn.close()

    return jsonify({'user': dict(updated), 'message': 'User updated'})


@bp.route('/api/users/<user_id>/deactivate', methods=['POST'])
@require_auth
@require_role('super_admin')
def deactivate_user(user_id):
    if str(user_id) == str(g.user.get('user_id')):
        return jsonify({'error': 'Cannot deactivate your own account'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE users SET active = false WHERE id = %s RETURNING name',
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return jsonify({'error': 'User not found'}), 404
            conn.commit()
        log_action(g.user['user_id'], 'deactivate_user', 'users', user_id)
    finally:
        conn.close()

    return jsonify({'message': f'{row["name"]} deactivated'})


@bp.route('/api/users/<user_id>/invite', methods=['POST'])
@require_auth
@require_role('super_admin')
def resend_invite(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT name, email, namespace, role FROM users WHERE id = %s', (user_id,))
            user = cur.fetchone()
            if not user:
                return jsonify({'error': 'User not found'}), 404

            invite_token = secrets.token_urlsafe(32)
            cur.execute(
                """INSERT INTO invite_tokens (user_id, token, expires_at)
                   VALUES (%s, %s, NOW() + INTERVAL '48 hours')""",
                (user_id, invite_token)
            )
            conn.commit()
    finally:
        conn.close()

    invite_url = f"{BASE_URL}/set-password?token={invite_token}"
    return jsonify({'invite_url': invite_url, 'message': 'Invite link generated (48 hours)'})


@bp.route('/api/users/<user_id>/activity', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def user_activity(user_id):
    limit = min(int(request.args.get('limit', 50)), 200)
    conn  = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT action_type, resource, result, created_at
                   FROM agent_actions WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit)
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get('created_at'):
                    r['created_at'] = r['created_at'].isoformat()
    finally:
        conn.close()

    return jsonify({'activity': rows})
