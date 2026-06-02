"""
ODIN Core — Database helpers.
Imported by blueprints, commands, and utils. No Flask route logic here.
"""
import os
import json

import psycopg2
import psycopg2.extras
import psycopg2.sql
from flask import request

DATABASE_URL = os.environ.get('DATABASE_URL', '')
BASE_URL     = os.environ.get('BASE_URL', 'http://localhost:5000')


def get_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL not set. Add it to .env or Railway env vars.')
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def log_action(user_id, action_type, resource=None, resource_id=None, data=None, result='ok'):
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_actions
                   (user_id, action_type, resource, resource_id, data, result, ip_address)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(user_id) if user_id else None,
                    action_type, resource,
                    str(resource_id) if resource_id else None,
                    json.dumps(data) if data else None,
                    result,
                    request.remote_addr,
                )
            )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f'[log_action] Failed: {e}')
