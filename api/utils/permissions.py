"""
ODIN Phase 3 — Component 2: Permissions Engine
check_permission(user_id, action, resource, get_db) → bool
"""

# ─── Permission Matrix ───────────────────────────────────────────
# Structure: MATRIX[role][resource][action] = bool
# '*' means all resources or all actions
# Missing entries default to False

MATRIX = {
    'super_admin': {
        '*': {
            'read': True, 'write': True, 'approve': True,
            'delete': True, 'claim': True
        }
    },
    're_partner': {
        'wholesaling': {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'leads':       {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'buyers':      {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'deals':       {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'finances':    {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': False},
        'users':       {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': False},
        'settings':    {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'wife_spoke':  {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'other_spokes':{'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
    },
    'acquisition_manager': {
        'leads':    {'read': True,  'write': True,  'approve': False, 'delete': False, 'claim': False},
        'buyers':   {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': False},
        'deals':    {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': False},
        'finances': {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'users':    {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': False},
        'settings': {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
    },
    'caller': {
        'assigned_leads':  {'read': True,  'write': True,  'approve': False, 'delete': False, 'claim': False},
        'unassigned_pool': {'read': True,  'write': False, 'approve': False, 'delete': False, 'claim': True},
        'leads':           {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'buyers':          {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'finances':        {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'settings':        {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'deals':           {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
    },
    'disposition_agent': {
        'contracted_deals': {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'buyers':           {'read': True,  'write': True,  'approve': True,  'delete': False, 'claim': False},
        'leads':            {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'finances':         {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'settings':         {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
    },
    'business_standard': {
        'assigned_spoke': {'read': True,  'write': True,  'approve': False, 'delete': False, 'claim': False},
        'wholesaling':    {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'other_spokes':   {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
        'settings':       {'read': False, 'write': False, 'approve': False, 'delete': False, 'claim': False},
    },
    'virtual_assistant': {
        'assigned_tasks': {'read': True, 'write': True,  'approve': False, 'delete': False, 'claim': False},
        # everything else defaults to False
    },
}


def check_permission(user_id, action, resource, get_db):
    """
    Returns True if the user with user_id is permitted to perform
    `action` on `resource`. Looks up user role from DB.

    Args:
        user_id (str): UUID of the requesting user
        action  (str): 'read' | 'write' | 'approve' | 'delete' | 'claim'
        resource(str): 'leads' | 'deals' | 'buyers' | 'finances' |
                       'users' | 'settings' | 'wife_spoke' | 'wholesaling' |
                       'assigned_leads' | 'unassigned_pool' | 'assigned_tasks' |
                       'contracted_deals' | 'assigned_spoke' | 'other_spokes'
        get_db  (callable): function that returns a psycopg2 connection

    Returns:
        bool
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT role, active FROM users WHERE id = %s',
                (str(user_id),)
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row or not row['active']:
        return False

    role = row['role']
    return _matrix_lookup(role, action, resource)


def check_permission_role(role, action, resource):
    """
    Same as check_permission but accepts a role string directly.
    Used when the role is already known (e.g., from JWT payload).
    """
    return _matrix_lookup(role, action, resource)


def _matrix_lookup(role, action, resource):
    """Core matrix lookup with wildcard support."""
    role_perms = MATRIX.get(role, {})

    # super_admin wildcard
    if '*' in role_perms:
        return role_perms['*'].get(action, False)

    # Exact resource match
    if resource in role_perms:
        return role_perms[resource].get(action, False)

    # Wildcard resource fallback
    if '*' in role_perms:
        return role_perms['*'].get(action, False)

    return False


def get_role_permissions_summary(role):
    """Returns the full permission set for a role (for display in dashboard)."""
    return MATRIX.get(role, {})
