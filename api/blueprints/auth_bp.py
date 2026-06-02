"""ODIN Blueprint — Auth routes: login, set-password."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, request, jsonify

from core.db   import get_db, log_action
from core.auth import JWT_SECRET, JWT_EXPIRES

bp = Blueprint('auth', __name__)


@bp.route('/api/auth/login', methods=['POST'])
def login():
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT * FROM users WHERE email = %s AND active = true',
                (email,)
            )
            user = cur.fetchone()
    finally:
        conn.close()

    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401

    if not user['password_hash']:
        return jsonify({'error': 'Account not activated — check your invite link'}), 401

    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        log_action(user['id'], 'login_failed', result='fail')
        return jsonify({'error': 'Invalid credentials'}), 401

    conn = get_db()
    with conn.cursor() as cur:
        cur.execute('UPDATE users SET last_active = NOW() WHERE id = %s', (user['id'],))
        conn.commit()
    conn.close()

    token = jwt.encode({
        'user_id':   str(user['id']),
        'name':      user['name'],
        'email':     user['email'],
        'role':      user['role'],
        'namespace': user['namespace'],
        'exp':       datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRES),
    }, JWT_SECRET, algorithm='HS256')

    log_action(user['id'], 'login', result='ok')
    return jsonify({
        'token': token,
        'user': {
            'id':        str(user['id']),
            'name':      user['name'],
            'email':     user['email'],
            'role':      user['role'],
            'namespace': user['namespace'],
        }
    })


@bp.route('/api/auth/set-password', methods=['POST'])
def set_password():
    """Called when a new user clicks their invite link and sets a password."""
    data  = request.get_json() or {}
    token = (data.get('token') or '').strip()
    pw    = (data.get('password') or '').strip()

    if not token or not pw:
        return jsonify({'error': 'Token and password required'}), 400
    if len(pw) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT it.*, u.id as user_id, u.name
                   FROM invite_tokens it
                   JOIN users u ON u.id = it.user_id
                   WHERE it.token = %s AND it.used = false AND it.expires_at > NOW()""",
                (token,)
            )
            invite = cur.fetchone()

        if not invite:
            return jsonify({'error': 'Invalid or expired invite link'}), 400

        hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE users SET password_hash = %s, onboarded = true WHERE id = %s',
                (hashed, invite['user_id'])
            )
            cur.execute(
                'UPDATE invite_tokens SET used = true WHERE id = %s',
                (invite['id'],)
            )
            conn.commit()

        log_action(invite['user_id'], 'set_password', result='ok')
        return jsonify({'message': f'Password set for {invite["name"]}. You can now log in.'})
    finally:
        conn.close()
