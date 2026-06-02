"""ODIN Blueprint — Dashboard summary and agent org chart."""
import psycopg2.extras
from flask import Blueprint, request, jsonify, g

from core.db   import get_db
from core.auth import require_auth

bp = Blueprint('dashboard', __name__)


@bp.route('/api/dashboard/agents', methods=['GET'])
@require_auth
def dashboard_agents():
    """Org chart: all heartbeat jobs + custom agents with last run and status."""
    HEARTBEAT_JOBS = [
        {'name': 'Morning Briefing',       'description': '6am daily CEO briefing',          'schedule': '6am daily'},
        {'name': 'Email Triage',           'description': '7am inbox classifier + drafter',  'schedule': '7:30am daily'},
        {'name': 'Finance Sync',           'description': '7am Teller.io bank sync',          'schedule': '7am daily'},
        {'name': 'Buyer Onboarding',       'description': '8am buyer welcome + qualify scan', 'schedule': '8am daily'},
        {'name': 'Revenue Sync',           'description': '9am XLeads won deals → revenue',   'schedule': '9am daily'},
        {'name': 'Lead Aging Scan',        'description': '9:05am stale lead alerts',         'schedule': '9:05am daily'},
        {'name': 'Offer Expiry Scan',      'description': '10am drafted offer alerts',        'schedule': '10am daily'},
        {'name': 'Pipeline Report',        'description': '9pm end-of-day summary',           'schedule': '9pm daily'},
        {'name': 'Session Memory Summary', 'description': '11pm Slack→memories (recall)',     'schedule': '11pm daily'},
        {'name': 'Reply Scanner',          'description': 'XLeads inbound check',             'schedule': 'Every 30min'},
        {'name': 'Follow-Up Queue',        'description': 'Auto-fire SMS follow-ups',         'schedule': 'Every 2hr'},
        {'name': 'Decision Queue Scan',    'description': 'Surface + auto-execute decisions', 'schedule': 'Every 2hr'},
        {'name': 'Opportunity Sync',       'description': 'XLeads pipeline snapshot',         'schedule': 'Every 4hr'},
        {'name': 'Task Delegation',        'description': 'Auto-assign + escalate tasks',     'schedule': 'Every 4hr'},
        {'name': 'KPI Auto-Update',        'description': 'Compute + traffic-light KPIs',     'schedule': 'Every 4hr'},
        {'name': 'SMS Health Monitor',     'description': 'Opt-out + deliverability check',   'schedule': 'Every hour'},
        {'name': 'Outreach Reply Scan',    'description': 'Gmail cold email replies',         'schedule': 'Every hour'},
        {'name': 'Webhook DLQ Retry',      'description': 'Retry failed webhook payloads',    'schedule': 'Every hour'},
        {'name': 'Outreach Auto-Send',     'description': 'Schedule email sender',            'schedule': 'Every 30min'},
        {'name': 'Weekly KPI',             'description': 'Monday 7am weekly summary',        'schedule': 'Mon 7am'},
        {'name': 'Weekly CEO Review',      'description': 'Sunday 8pm full review',           'schedule': 'Sun 8pm'},
        {'name': 'Resurrect Scan',         'description': 'Wednesday dead lead re-engage',    'schedule': 'Wed 10am'},
    ]

    db = get_db()
    try:
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Heartbeat jobs stamp 'heartbeat_<job_name>' into agent_actions after each run.
        # That's how we get real last_run times instead of always showing 'idle'.
        cur.execute("""
            SELECT action_type, MAX(created_at) AS last_run
            FROM agent_actions
            WHERE action_type LIKE 'heartbeat_%'
              AND created_at >= NOW() - INTERVAL '7 days'
            GROUP BY action_type
        """)
        action_rows = {r['action_type']: r for r in (cur.fetchall() or [])}

        cur.execute("SELECT name, description, created_at FROM custom_agents WHERE is_active = TRUE ORDER BY name")
        custom = cur.fetchall() or []
        db.close()
    except Exception:
        action_rows = {}
        custom = []

    agents = []
    for job in HEARTBEAT_JOBS:
        # Match the key format written by heartbeat._guarded()
        action_key = 'heartbeat_' + job['name'].lower().replace(' ', '_')
        row        = action_rows.get(action_key, {})
        last_run   = row.get('last_run')
        agents.append({
            'name':        job['name'],
            'description': job['description'],
            'schedule':    job['schedule'],
            'status':      'running' if last_run else 'idle',
            'last_run':    last_run.isoformat() if last_run else None,
            'last_error':  None,
        })

    for c in custom:
        agents.append({
            'name':        c['name'],
            'description': c.get('description', 'Custom agent'),
            'schedule':    'On-demand',
            'status':      'idle',
            'last_run':    None,
            'last_error':  None,
        })

    return jsonify({'agents': agents})


@bp.route('/api/dashboard/summary', methods=['GET'])
@require_auth
def dashboard_summary():
    role    = g.user.get('role')
    user_id = g.user.get('user_id')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if role == 'super_admin':
                cur.execute("SELECT COUNT(*) as total FROM users WHERE active = true")
                total_users  = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE status NOT IN ('dead', 'closed')")
                active_leads = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE mctp_total >= 8")
                hot_leads    = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE mctp_total BETWEEN 5 AND 7")
                warm_leads   = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM approval_queue WHERE status = 'pending'")
                pending      = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE status = 'closed'")
                closed       = cur.fetchone()['total']
                return jsonify({
                    'role': role,
                    'total_users': total_users,
                    'active_leads': active_leads,
                    'hot_leads': hot_leads,
                    'warm_leads': warm_leads,
                    'pending_approvals': pending,
                    'deals_closed': closed,
                })

            if role == 'caller':
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE assigned_to = %s", (user_id,))
                my_leads = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE assigned_to = %s AND mctp_total >= 8", (user_id,))
                hot  = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) as total FROM leads WHERE assigned_to IS NULL")
                pool = cur.fetchone()['total']
                return jsonify({'role': role, 'my_leads': my_leads, 'hot_leads_sent': hot, 'unassigned_pool': pool})

            return jsonify({'role': role, 'message': 'Dashboard active'})
    finally:
        conn.close()
