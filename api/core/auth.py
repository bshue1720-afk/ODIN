"""
ODIN Core — Auth decorators and constants.
"""
import os
from functools import wraps

import jwt
from flask import request, jsonify, g

from utils.permissions import check_permission_role

JWT_SECRET  = os.environ.get('JWT_SECRET_KEY', 'odin-dev-secret-change-in-prod')
JWT_EXPIRES = int(os.environ.get('JWT_EXPIRES_HOURS', 24))
VALID_ROLES = (
    'super_admin', 're_partner', 'acquisition_manager',
    'caller', 'disposition_agent', 'business_standard', 'virtual_assistant'
)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        token = auth_header.replace('Bearer ', '').strip()
        if not token:
            return jsonify({'error': 'Authentication required'}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            g.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired — please log in again'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """Requires the authenticated user to have one of the specified roles."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if g.user.get('role') not in roles:
                return jsonify({'error': 'Permission denied'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_permission_for(action, resource):
    """Checks permission matrix for the authenticated user."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            role = g.user.get('role', '')
            if not check_permission_role(role, action, resource):
                return jsonify({'error': f'Permission denied: cannot {action} {resource}'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator
