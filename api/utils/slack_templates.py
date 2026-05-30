"""
ODIN Phase 3 — Component 7: Slack Notification Templates
Per-role notification content + send_slack_notification()
"""
import os
import requests

# ─── Channel map (override via env vars) ───────────────────────
CHANNELS = {
    'super_admin':         os.environ.get('SLACK_CHANNEL_BROCK',   '#odin-brock'),
    're_partner':          os.environ.get('SLACK_CHANNEL_PARTNER',  '#odin-partner'),
    'acquisition_manager': os.environ.get('SLACK_CHANNEL_AM',       '#odin-am'),
    'disposition_agent':   os.environ.get('SLACK_CHANNEL_DISPO',    '#odin-dispo'),
    'business_standard':   os.environ.get('SLACK_CHANNEL_WIFE',     '#odin-wife'),
}

# Caller channels use their namespace: #odin-{namespace}
# Set SLACK_CHANNEL_CALLER_{NAMESPACE} to override.


def get_caller_channel(namespace):
    env_key = f'SLACK_CHANNEL_CALLER_{namespace.upper()}'
    return os.environ.get(env_key, f'#odin-{namespace}')


# ─── Template builders per notification type ────────────────────

def hot_lead_notification(lead_data, score, approver_role):
    """
    Builds Slack message blocks for a hot lead.
    Different content based on who receives it.
    """
    address      = lead_data.get('address', 'Unknown')
    motivation   = lead_data.get('motivation', '')
    timeline     = lead_data.get('timeline', '')
    asking       = lead_data.get('asking_price', 0) or 0
    arv          = lead_data.get('arv_mid', 0) or 0
    lao          = lead_data.get('lao', 0) or 0
    walkup       = lead_data.get('walkup', 0) or 0
    buyer_max    = lead_data.get('buyer_max', 0) or 0
    fee_lao      = lead_data.get('fee_at_lao', 0) or 0
    fee_walkup   = lead_data.get('fee_at_walkup', 0) or 0
    rec_max      = lead_data.get('rec_max_contract', 0) or 0
    caller_name  = lead_data.get('caller_name', 'Caller')

    if approver_role == 'super_admin':
        return {
            'text': f'🔥 HOT LEAD ({score}/10) — {address}',
            'blocks': [
                _header(f'🔥 HOT LEAD — {score}/10'),
                _section(f'*{address}*\n_{motivation}_'),
                _fields([
                    ('Timeline', timeline),
                    ('Asking',   f'${asking:,.0f}'),
                    ('ARV',      f'${arv:,.0f}'),
                    ('LAO',      f'${lao:,.0f}'),
                    ('Walk-up',  f'${walkup:,.0f}'),
                    ('Buyer Max',f'${buyer_max:,.0f}'),
                    ('Fee @ LAO',f'${fee_lao:,.0f}'),
                    ('Fee @ WU', f'${fee_walkup:,.0f}'),
                    ('Rec. Max', f'${rec_max:,.0f}'),
                    ('Logged by',caller_name),
                ]),
                _divider(),
                _section('*Action needed:* Approve + generate offer script for caller.'),
                _actions(lead_data.get('approval_queue_id', ''))
            ]
        }

    if approver_role == 're_partner':
        return {
            'text': f'🔥 HOT LEAD ({score}/10) — {address}',
            'blocks': [
                _header(f'🔥 HOT LEAD — Partner Review — {score}/10'),
                _section(f'*{address}*\n_{motivation}_'),
                _fields([
                    ('Timeline', timeline),
                    ('Asking',   f'${asking:,.0f}'),
                    ('LAO',      f'${lao:,.0f}'),
                    ('Walk-up',  f'${walkup:,.0f}'),
                    ('Buyer Max',f'${buyer_max:,.0f}'),
                    ('Fee @ LAO',f'${fee_lao:,.0f}'),
                    ('Fee @ WU', f'${fee_walkup:,.0f}'),
                    ('Logged by',caller_name),
                ]),
                _divider(),
                _section('*Action needed:* Approve and generate offer script.'),
                _actions(lead_data.get('approval_queue_id', ''))
            ]
        }

    # acquisition_manager
    return {
        'text': f'🔥 HOT LEAD ({score}/10) — {address}',
        'blocks': [
            _header(f'🔥 HOT LEAD — {score}/10'),
            _section(f'*{address}*\n_{motivation}_'),
            _fields([
                ('Timeline', timeline),
                ('Asking',   f'${asking:,.0f}'),
                ('LAO',      f'${lao:,.0f}'),
                ('Logged by',caller_name),
            ]),
            _divider(),
            _section('*Action needed:* Review and approve or escalate.'),
            _actions(lead_data.get('approval_queue_id', ''))
        ]
    }


def warm_lead_notification(lead_data, score):
    address = lead_data.get('address', 'Unknown')
    caller  = lead_data.get('caller_name', 'Caller')
    return {
        'text': f'♨️ Warm Lead ({score}/10) — {address}',
        'blocks': [
            _header(f'♨️ Warm Lead — {score}/10'),
            _section(f'*{address}*\nLogged by: {caller}'),
            _section('Review MCTP details in the dashboard.'),
        ]
    }


def lead_assigned_notification(caller_name, address, follow_up_date):
    return {
        'text': f'📋 New lead assigned: {address}',
        'blocks': [
            _header('📋 New Lead Assigned'),
            _section(f'*{address}*\nFollow-up: {follow_up_date}'),
            _section('Log your MCTP call when you reach the seller.'),
        ]
    }


def approval_response_notification(caller_name, address, approved, offer_script, notes=''):
    emoji = '✅' if approved else '❌'
    action = 'APPROVED' if approved else 'REJECTED'
    blocks = [
        _header(f'{emoji} Lead {action}: {address}'),
    ]
    if approved and offer_script:
        blocks.append(_section(f'*Offer Script:*\n```{offer_script}```'))
    if notes:
        blocks.append(_section(f'*Notes:* {notes}'))
    return {
        'text': f'{emoji} Lead {action}: {address}',
        'blocks': blocks
    }


def daily_briefing_notification(role, summary_data):
    """Role-specific daily briefing content."""
    if role == 'super_admin':
        return {
            'text': '☀️ ODIN Daily Briefing',
            'blocks': [
                _header('☀️ ODIN Daily Briefing — All Spokes'),
                _section(
                    f"*Active Leads:* {summary_data.get('active_leads', 0)}\n"
                    f"*Hot (8+):* {summary_data.get('hot_leads', 0)}\n"
                    f"*Warm (5-7):* {summary_data.get('warm_leads', 0)}\n"
                    f"*Pending Approvals:* {summary_data.get('pending_approvals', 0)}\n"
                    f"*Deals Closed MTD:* {summary_data.get('deals_closed', 0)}"
                )
            ]
        }
    if role == 're_partner':
        return {
            'text': '☀️ RE Daily Briefing',
            'blocks': [
                _header('☀️ RE Daily Briefing'),
                _section(
                    f"*Active Pipeline:* {summary_data.get('active_leads', 0)} leads\n"
                    f"*Hot Leads:* {summary_data.get('hot_leads', 0)}\n"
                    f"*Pending Your Approval:* {summary_data.get('pending_approvals', 0)}"
                )
            ]
        }
    if role == 'acquisition_manager':
        return {
            'text': '☀️ AM Daily Briefing',
            'blocks': [
                _header('☀️ Acquisition Manager Briefing'),
                _section(
                    f"*Callers Active:* {summary_data.get('active_callers', 0)}\n"
                    f"*Calls Today:* {summary_data.get('calls_today', 0)}\n"
                    f"*Warm Leads to Review:* {summary_data.get('warm_leads', 0)}"
                )
            ]
        }
    return {'text': '☀️ Daily Briefing', 'blocks': [_header('☀️ Daily Briefing')]}


def new_user_invite_notification(invitee_name, role, invite_url):
    return {
        'text': f'👤 You\'ve been added to ODIN as {role}',
        'blocks': [
            _header('Welcome to ODIN'),
            _section(
                f"Hi {invitee_name}, you've been added as *{role}*.\n"
                f"Set your password here: {invite_url}\n"
                f"Link expires in 48 hours."
            )
        ]
    }


# ─── Slack API sender ────────────────────────────────────────────

def send_slack_notification(channel_or_role, message_payload, namespace=None):
    """
    Sends a Slack message.

    Args:
        channel_or_role: Role name (looks up channel) or explicit '#channel'
        message_payload: dict from template builders above
        namespace: caller's namespace (for per-caller channels)

    Returns:
        bool: True if sent, False if failed/not configured
    """
    token = os.environ.get('SLACK_BOT_TOKEN', '')
    if not token:
        print(f'[slack] No bot token configured — skipping notification')
        return False

    # Resolve channel
    if channel_or_role.startswith('#'):
        channel = channel_or_role
    elif channel_or_role == 'caller' and namespace:
        channel = get_caller_channel(namespace)
    else:
        channel = CHANNELS.get(channel_or_role, f'#{channel_or_role}')

    payload = {
        'channel': channel,
        'text': message_payload.get('text', 'ODIN notification'),
    }
    if 'blocks' in message_payload:
        payload['blocks'] = message_payload['blocks']

    try:
        resp = requests.post(
            'https://slack.com/api/chat.postMessage',
            json=payload,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            timeout=10
        )
        data = resp.json()
        if not data.get('ok'):
            print(f'[slack] Error: {data.get("error", "unknown")} → channel={channel}')
            return False
        return True
    except Exception as e:
        print(f'[slack] Exception: {e}')
        return False


# ─── Block helpers ───────────────────────────────────────────────

def _header(text):
    return {'type': 'header', 'text': {'type': 'plain_text', 'text': text[:150]}}


def _section(text):
    return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}


def _divider():
    return {'type': 'divider'}


def _fields(pairs):
    fields = [
        {'type': 'mrkdwn', 'text': f'*{k}*\n{v}'}
        for k, v in pairs
    ]
    # Slack limits 10 fields per section block
    return {'type': 'section', 'fields': fields[:10]}


def _actions(approval_queue_id):
    if not approval_queue_id:
        return _section('Open ODIN dashboard to review.')
    return {
        'type': 'section',
        'text': {
            'type': 'mrkdwn',
            'text': f'<odin-dashboard/approval/{approval_queue_id}|Open in ODIN Dashboard>'
        }
    }
