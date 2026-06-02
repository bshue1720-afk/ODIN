"""ODIN Blueprint — Approval queue routes."""
from flask import Blueprint, request, jsonify, g

from core.db   import get_db, log_action
from core.auth import require_auth
from utils.approval_router import build_offer_script
from utils.slack_templates import send_slack_notification, approval_response_notification

bp = Blueprint('approval', __name__)


@bp.route('/api/approval', methods=['GET'])
@require_auth
def list_approvals():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if role == 'super_admin':
                cur.execute(
                    """SELECT aq.*, u.name as initiated_by_name
                       FROM approval_queue aq
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.status = 'pending'
                       ORDER BY aq.priority DESC, aq.created_at ASC"""
                )
            else:
                cur.execute(
                    """SELECT aq.*, u.name as initiated_by_name
                       FROM approval_queue aq
                       LEFT JOIN users u ON u.id = aq.initiated_by
                       WHERE aq.assigned_to = %s AND aq.status = 'pending'
                       ORDER BY aq.priority DESC, aq.created_at ASC""",
                    (user_id,)
                )
            items = []
            for r in cur.fetchall():
                row = dict(r)
                row['id'] = str(row['id'])
                if row.get('initiated_by'): row['initiated_by'] = str(row['initiated_by'])
                if row.get('assigned_to'):  row['assigned_to']  = str(row['assigned_to'])
                if row.get('resource_id'):  row['resource_id']  = str(row['resource_id'])
                if row.get('created_at'):   row['created_at']   = row['created_at'].isoformat()
                items.append(row)
    finally:
        conn.close()

    return jsonify({'approvals': items})


@bp.route('/api/approval/<approval_id>/approve', methods=['POST'])
@require_auth
def approve_item(approval_id):
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT aq.*, l.address, l.lao, l.arv_mid, u.namespace as caller_namespace
                   FROM approval_queue aq
                   LEFT JOIN leads l ON l.id = aq.resource_id
                   LEFT JOIN users u ON u.id = aq.initiated_by
                   WHERE aq.id = %s AND aq.status = 'pending'""",
                (approval_id,)
            )
            item = cur.fetchone()

        if not item:
            return jsonify({'error': 'Approval not found or already resolved'}), 404

        if role not in ('super_admin', 're_partner', 'acquisition_manager'):
            return jsonify({'error': 'Permission denied'}), 403

        data  = request.get_json() or {}
        notes = data.get('notes', '')

        offer_script = None
        if item['action_type'] == 'hot_lead_review' and item['lao']:
            offer_script = build_offer_script(item['address'], item['lao'])

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE approval_queue SET
                       status = 'approved', approver_notes = %s,
                       offer_script = %s, resolved_at = NOW()
                   WHERE id = %s""",
                (notes, offer_script, approval_id)
            )
            if item['resource_id']:
                cur.execute(
                    "UPDATE leads SET status = 'hot' WHERE id = %s",
                    (item['resource_id'],)
                )
            conn.commit()

        log_action(user_id, 'approve_item', 'approval_queue', approval_id,
                   {'action_type': item['action_type']})

        if item['initiated_by'] and item['address']:
            caller_msg = approval_response_notification(
                item.get('caller_namespace', 'caller'),
                item['address'],
                approved=True,
                offer_script=offer_script,
                notes=notes
            )
            send_slack_notification('caller', caller_msg, namespace=item.get('caller_namespace'))

    finally:
        conn.close()

    return jsonify({'message': 'Approved', 'offer_script': offer_script, 'address': item['address']})


@bp.route('/api/approval/<approval_id>/reject', methods=['POST'])
@require_auth
def reject_item(approval_id):
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    if role not in ('super_admin', 're_partner', 'acquisition_manager'):
        return jsonify({'error': 'Permission denied'}), 403

    data  = request.get_json() or {}
    notes = data.get('notes', 'Lead rejected.')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT aq.*, l.address, u.namespace as caller_namespace
                   FROM approval_queue aq
                   LEFT JOIN leads l ON l.id = aq.resource_id
                   LEFT JOIN users u ON u.id = aq.initiated_by
                   WHERE aq.id = %s AND aq.status = 'pending'""",
                (approval_id,)
            )
            item = cur.fetchone()
            if not item:
                return jsonify({'error': 'Approval not found'}), 404

            cur.execute(
                """UPDATE approval_queue SET
                       status = 'rejected', approver_notes = %s, resolved_at = NOW()
                   WHERE id = %s""",
                (notes, approval_id)
            )
            if item['resource_id']:
                cur.execute("UPDATE leads SET status = 'new' WHERE id = %s", (item['resource_id'],))
            conn.commit()

        caller_msg = approval_response_notification(
            item.get('caller_namespace', 'caller'),
            item['address'] or 'Lead',
            approved=False, offer_script=None, notes=notes
        )
        send_slack_notification('caller', caller_msg, namespace=item.get('caller_namespace'))
        log_action(user_id, 'reject_item', 'approval_queue', approval_id)
    finally:
        conn.close()

    return jsonify({'message': 'Rejected', 'notes': notes})
