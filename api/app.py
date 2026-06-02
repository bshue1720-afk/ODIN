"""
ODIN — Application Factory
Gunicorn entry point: app:app  (Railway Procfile unchanged)
Local dev:           python app.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, make_response

import utils.heartbeat        as heartbeat
import utils.outreach_tracker as outreach_tracker
import utils.email_sender     as email_sender
import utils.lead_sniper      as lead_sniper
import utils.xleads           as xleads
from utils.slack_templates    import CHANNELS
from core.helpers             import _slack_post
from core.db                  import get_db

from blueprints.system_bp       import bp as system_bp
from blueprints.auth_bp         import bp as auth_bp
from blueprints.users_bp        import bp as users_bp
from blueprints.leads_bp        import bp as leads_bp
from blueprints.approval_bp     import bp as approval_bp
from blueprints.dashboard_bp    import bp as dashboard_bp
from blueprints.agents_bp       import bp as agents_bp
from blueprints.integrations_bp import bp as integrations_bp
from blueprints.xleads_bp       import bp as xleads_bp


def create_app() -> Flask:
    application = Flask(__name__, static_folder='../dashboard', static_url_path='')

    # Manual CORS headers on every response (no flask-cors dependency)
    @application.after_request
    def _cors(response):
        response.headers['Access-Control-Allow-Origin']  = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        return response

    # Handle preflight OPTIONS for every route
    @application.before_request
    def _preflight():
        if request.method == 'OPTIONS':
            resp = make_response('', 204)
            resp.headers['Access-Control-Allow-Origin']  = '*'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
            return resp

    application.register_blueprint(system_bp)
    application.register_blueprint(auth_bp)
    application.register_blueprint(users_bp)
    application.register_blueprint(leads_bp)
    application.register_blueprint(approval_bp)
    application.register_blueprint(dashboard_bp)
    application.register_blueprint(agents_bp)
    application.register_blueprint(integrations_bp)
    application.register_blueprint(xleads_bp)

    return application


app = create_app()

# ─── HEARTBEAT + SERVICE STARTUP ─────────────────────────────────────────────
# APScheduler requires --workers 1 in gunicorn — never change.
heartbeat.init(
    slack_post_fn=_slack_post,
    get_db_fn=get_db,
    xleads_mod=xleads,
    channels=CHANNELS,
)
heartbeat.start_heartbeat()
outreach_tracker.init(_slack_post, get_db, CHANNELS)
email_sender.init(_slack_post, get_db, CHANNELS)
lead_sniper.init(_slack_post, get_db, CHANNELS)

# Discord bot (inbound command interface — no-op if DISCORD_BOT_TOKEN absent)
try:
    import utils.discord_bot as discord_bot
    discord_bot.start_bot()
except Exception as _dbot_err:
    print(f'[discord_bot] Failed to start: {_dbot_err}')


if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    print(f'ODIN API running on http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, debug=debug)
