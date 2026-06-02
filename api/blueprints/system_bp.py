"""ODIN Blueprint — System routes: dashboard serve, health check, set-password page."""
from flask import Blueprint, jsonify, send_from_directory, current_app

from core.db import get_db

bp = Blueprint('system', __name__)


@bp.route('/')
def serve_dashboard():
    return send_from_directory(current_app.static_folder, 'index.html')


@bp.route('/set-password')
def set_password_page():
    return send_from_directory(current_app.static_folder, 'index.html')


@bp.route('/api/health', methods=['GET'])
def health():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
        conn.close()
        db_status = 'ok'
    except Exception as e:
        db_status = str(e)
    return jsonify({'status': 'ok', 'db': db_status, 'version': 'odin-phase3'})
