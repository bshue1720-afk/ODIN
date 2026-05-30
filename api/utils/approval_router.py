"""
ODIN Phase 3 — Component 3: Approval Routing Engine
route_approval(action_type, data, initiated_by_user_id, get_db) → dict
"""

# Fee thresholds
THRESHOLD_RE_PARTNER  = 15_000   # re_partner can approve under this
THRESHOLD_DUAL_REVIEW = 30_000   # re_partner reviews, super_admin final
# Above 30k → direct to super_admin

# Lead score thresholds
HOT_LEAD_SCORE  = 8   # score >= 8 → hot, routes up
WARM_LEAD_SCORE = 5   # score 5-7 → warm, AM can approve

MESSAGE_TEMPLATES = {
    'hot_lead_to_am': (
        "🔥 *HOT LEAD* — Score {score}/10\n"
        "*Address:* {address}\n"
        "*Motivation:* {motivation}\n"
        "*Timeline:* {timeline}\n"
        "*Asking:* ${asking_price:,.0f}\n"
        "*LAO:* ${lao:,.0f} | *Buyer Max:* ${buyer_max:,.0f}\n"
        "*Logged by:* {caller_name}\n"
        "Action needed: Review and approve or escalate."
    ),
    'hot_lead_to_partner': (
        "🔥 *HOT LEAD — Partner Review* — Score {score}/10\n"
        "*Address:* {address}\n"
        "*Motivation:* {motivation} | *Timeline:* {timeline}\n"
        "*Asking:* ${asking_price:,.0f}\n"
        "*LAO:* ${lao:,.0f} | *Walk-up:* ${walkup:,.0f} | "
        "*Buyer Max:* ${buyer_max:,.0f}\n"
        "*Fee at LAO:* ${fee_at_lao:,.0f} | *Fee at Walk-up:* ${fee_at_walkup:,.0f}\n"
        "*Logged by:* {caller_name}\n"
        "Action needed: Approve and generate offer script."
    ),
    'hot_lead_to_super_admin': (
        "🔥 *HOT LEAD — Brock Review* — Score {score}/10\n"
        "*Address:* {address}\n"
        "*Motivation:* {motivation} | *Timeline:* {timeline}\n"
        "*Asking:* ${asking_price:,.0f}\n"
        "*ARV:* ${arv_mid:,.0f} | *LAO:* ${lao:,.0f} | "
        "*Walk-up:* ${walkup:,.0f} | *Buyer Max:* ${buyer_max:,.0f}\n"
        "*Fee at LAO:* ${fee_at_lao:,.0f} | *Fee at Walk-up:* ${fee_at_walkup:,.0f}\n"
        "*Rec. Max Contract:* ${rec_max_contract:,.0f}\n"
        "*Logged by:* {caller_name}\n"
        "Action needed: Approve and generate offer script for caller."
    ),
    'deal_approval_partner': (
        "📋 *Deal Approval Request*\n"
        "*Address:* {address}\n"
        "*Contract Price:* ${contract_price:,.0f}\n"
        "*Assignment Fee:* ${assignment_fee:,.0f}\n"
        "Fee is under $15,000 — within your approval authority.\n"
        "super_admin notified."
    ),
    'deal_approval_dual': (
        "📋 *Deal Approval — Dual Review Required*\n"
        "*Address:* {address}\n"
        "*Contract Price:* ${contract_price:,.0f}\n"
        "*Assignment Fee:* ${assignment_fee:,.0f}\n"
        "Fee $15k–$30k — your review required, then super_admin final approval."
    ),
    'deal_approval_super_admin': (
        "📋 *Deal Approval — Direct to Brock*\n"
        "*Address:* {address}\n"
        "*Contract Price:* ${contract_price:,.0f}\n"
        "*Assignment Fee:* ${assignment_fee:,.0f}\n"
        "Fee over $30k — your direct approval required. re_partner notified."
    ),
    'add_user': (
        "👤 *New User Request*\n"
        "*Name:* {name}\n*Role:* {role}\n*Email:* {email}\n"
        "Awaiting super_admin approval."
    ),
    'buyer_list_update': (
        "📋 *Buyer List Update Request*\n"
        "*Action:* {action}\n*Buyer:* {buyer_name}\n"
        "Awaiting approval."
    ),
}

OFFER_SCRIPT_TEMPLATE = (
    "Hey [Seller Name], this is [Your Name] with Shue Box LLC. "
    "I've had a chance to look at the property at {address}. "
    "Based on its condition and what we're seeing in the market right now, "
    "I can make you an all-cash offer of ${offer_price:,.0f}. "
    "That means no repairs, no agent fees, and we can close in as little as "
    "two to three weeks — on your timeline. "
    "Does that work for your situation?"
)


def route_approval(action_type, data, initiated_by_user_id, get_db):
    """
    Determines who should approve an action and creates an approval_queue record.

    Args:
        action_type          (str): 'hot_lead_review' | 'deal_approval' |
                                    'add_user' | 'system_settings' | 'buyer_list_update'
        data                 (dict): action-specific payload
        initiated_by_user_id (str): UUID of the user who triggered this
        get_db               (callable): returns psycopg2 connection

    Returns:
        dict: { approver_user_id, approver_role, priority, message_template,
                approval_queue_id }
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Fetch initiating user's name for templates
            cur.execute('SELECT name, role FROM users WHERE id = %s',
                        (str(initiated_by_user_id),))
            initiator = cur.fetchone()
            initiator_name = initiator['name'] if initiator else 'Unknown'

            # Resolve approver based on action type
            result = _resolve_approver(action_type, data, cur, initiator_name)

            # Create approval queue record
            import json as _json
            cur.execute(
                """INSERT INTO approval_queue
                   (action_type, initiated_by, assigned_to, approver_role,
                    priority, resource_id, data, message_template)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    action_type,
                    str(initiated_by_user_id),
                    result.get('approver_user_id'),
                    result.get('approver_role'),
                    result.get('priority', 1),
                    data.get('resource_id'),
                    _json.dumps(data),
                    result.get('message_template'),
                )
            )
            queue_id = cur.fetchone()['id']
            conn.commit()

        result['approval_queue_id'] = str(queue_id)
        return result
    finally:
        conn.close()


def _resolve_approver(action_type, data, cur, initiator_name):
    """
    Core routing logic. Returns approver info dict.
    """
    if action_type == 'hot_lead_review':
        return _route_hot_lead(data, cur, initiator_name)

    if action_type == 'deal_approval':
        return _route_deal_approval(data, cur, initiator_name)

    if action_type in ('add_user', 'system_settings'):
        return _route_super_admin_only(action_type, data, cur, initiator_name)

    if action_type == 'buyer_list_update':
        return _route_buyer_update(data, cur, initiator_name)

    # Default: super_admin
    return _route_super_admin_only(action_type, data, cur, initiator_name)


def _route_hot_lead(data, cur, caller_name):
    score = data.get('mctp_total', 0)
    data['caller_name'] = caller_name

    # Step 1: Is there an active acquisition_manager?
    cur.execute(
        "SELECT id FROM users WHERE role = 'acquisition_manager' AND active = true LIMIT 1"
    )
    am = cur.fetchone()

    if am and score >= WARM_LEAD_SCORE and score < HOT_LEAD_SCORE:
        # Warm lead → AM
        msg = MESSAGE_TEMPLATES['hot_lead_to_am'].format(**{
            k: data.get(k, '') for k in MESSAGE_TEMPLATES['hot_lead_to_am'].split('{')[1:]
            if '}' in k
        })
        msg = _safe_format(MESSAGE_TEMPLATES['hot_lead_to_am'], data)
        return {
            'approver_user_id': str(am['id']),
            'approver_role': 'acquisition_manager',
            'priority': 2,
            'message_template': msg,
        }

    if am and score >= HOT_LEAD_SCORE:
        # Hot lead → AM first, will escalate to partner/super_admin
        msg = _safe_format(MESSAGE_TEMPLATES['hot_lead_to_am'], data)
        return {
            'approver_user_id': str(am['id']),
            'approver_role': 'acquisition_manager',
            'priority': 3,
            'message_template': msg,
        }

    # No AM — try re_partner
    cur.execute(
        "SELECT id FROM users WHERE role = 're_partner' AND active = true LIMIT 1"
    )
    partner = cur.fetchone()

    if partner and score >= HOT_LEAD_SCORE:
        msg = _safe_format(MESSAGE_TEMPLATES['hot_lead_to_partner'], data)
        return {
            'approver_user_id': str(partner['id']),
            'approver_role': 're_partner',
            'priority': 3,
            'message_template': msg,
        }

    # Fallback: super_admin (Brock)
    cur.execute(
        "SELECT id FROM users WHERE role = 'super_admin' AND active = true LIMIT 1"
    )
    admin = cur.fetchone()
    msg = _safe_format(MESSAGE_TEMPLATES['hot_lead_to_super_admin'], data)
    return {
        'approver_user_id': str(admin['id']) if admin else None,
        'approver_role': 'super_admin',
        'priority': 3,
        'message_template': msg,
    }


def _route_deal_approval(data, cur, initiator_name):
    fee = float(data.get('assignment_fee', 0))

    cur.execute(
        "SELECT id FROM users WHERE role = 're_partner' AND active = true LIMIT 1"
    )
    partner = cur.fetchone()

    cur.execute(
        "SELECT id FROM users WHERE role = 'super_admin' AND active = true LIMIT 1"
    )
    admin = cur.fetchone()

    if fee < THRESHOLD_RE_PARTNER and partner:
        msg = _safe_format(MESSAGE_TEMPLATES['deal_approval_partner'], data)
        return {
            'approver_user_id': str(partner['id']),
            'approver_role': 're_partner',
            'priority': 2,
            'message_template': msg,
        }

    if fee < THRESHOLD_DUAL_REVIEW and partner:
        msg = _safe_format(MESSAGE_TEMPLATES['deal_approval_dual'], data)
        return {
            'approver_user_id': str(partner['id']),
            'approver_role': 're_partner',
            'priority': 2,
            'message_template': msg,
        }

    # Over 30k or no partner — super_admin
    msg = _safe_format(MESSAGE_TEMPLATES['deal_approval_super_admin'], data)
    return {
        'approver_user_id': str(admin['id']) if admin else None,
        'approver_role': 'super_admin',
        'priority': 3,
        'message_template': msg,
    }


def _route_super_admin_only(action_type, data, cur, initiator_name):
    cur.execute(
        "SELECT id FROM users WHERE role = 'super_admin' AND active = true LIMIT 1"
    )
    admin = cur.fetchone()
    if action_type == 'add_user':
        msg = _safe_format(MESSAGE_TEMPLATES['add_user'], data)
    else:
        msg = f"Action {action_type} requires super_admin approval."
    return {
        'approver_user_id': str(admin['id']) if admin else None,
        'approver_role': 'super_admin',
        'priority': 1,
        'message_template': msg,
    }


def _route_buyer_update(data, cur, initiator_name):
    cur.execute(
        """SELECT id, role FROM users
           WHERE role IN ('disposition_agent', 're_partner', 'super_admin')
           AND active = true
           ORDER BY CASE role
               WHEN 'disposition_agent' THEN 1
               WHEN 're_partner' THEN 2
               WHEN 'super_admin' THEN 3
           END LIMIT 1"""
    )
    approver = cur.fetchone()
    msg = _safe_format(MESSAGE_TEMPLATES['buyer_list_update'], data)
    return {
        'approver_user_id': str(approver['id']) if approver else None,
        'approver_role': approver['role'] if approver else 'super_admin',
        'priority': 1,
        'message_template': msg,
    }


def _safe_format(template, data):
    """Format template with data dict, ignoring missing keys."""
    try:
        return template.format(**{k: (v if v is not None else '') for k, v in data.items()})
    except (KeyError, ValueError):
        return template


def build_offer_script(address, lao):
    """Generate the offer script sent back to caller after approval."""
    return OFFER_SCRIPT_TEMPLATE.format(
        address=address,
        offer_price=lao
    )
