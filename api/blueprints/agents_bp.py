"""ODIN Blueprint — Business agent endpoints (/api/agents/*)."""
from flask import Blueprint, request, jsonify, g

from core.db   import get_db, log_action
from core.auth import require_auth
import utils.business_scout   as scout_agent
import utils.income_calculator as income_agent
import utils.business_builder  as builder_agent
import utils.automation_auditor as auditor_agent
import utils.it_agent          as it_agent

bp = Blueprint('agents', __name__)


@bp.route('/api/agents/scout', methods=['POST'])
@require_auth
def agent_scout():
    data = request.get_json() or {}
    try:
        result = scout_agent.run(
            keywords=data.get('keywords', ''),
            skills=data.get('skills', ''),
            budget_usd=int(data.get('budget', 500)),
            hours_per_week=int(data.get('hours_per_week', 10)),
        )
        log_action(g.user['user_id'], 'agent_scout', data={'keywords': data.get('keywords')})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/agents/income', methods=['POST'])
@require_auth
def agent_income():
    data     = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = income_agent.run(
            business_name=business,
            hours_per_week=int(data.get('hours_per_week', 10)),
            starting_budget=int(data.get('starting_budget', 200)),
            platform=data.get('platform', ''),
        )
        log_action(g.user['user_id'], 'agent_income', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/agents/build', methods=['POST'])
@require_auth
def agent_build():
    data     = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = builder_agent.run(business_name=business)
        log_action(g.user['user_id'], 'agent_build', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/agents/audit', methods=['POST'])
@require_auth
def agent_audit():
    data     = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400
    try:
        result = auditor_agent.run(business_name=business)
        log_action(g.user['user_id'], 'agent_audit', data={'business': business})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/agents/debug', methods=['POST'])
@require_auth
def agent_debug():
    data    = request.get_json() or {}
    problem = (data.get('problem') or '').strip()
    if not problem:
        return jsonify({'error': 'problem is required'}), 400
    try:
        result = it_agent.run(
            problem=problem,
            include_health_check=data.get('health_check', True),
        )
        log_action(g.user['user_id'], 'agent_debug', data={'problem': problem[:200]})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/agents/plan', methods=['POST'])
@require_auth
def agent_plan():
    data     = request.get_json() or {}
    business = (data.get('business') or '').strip()
    if not business:
        return jsonify({'error': 'business is required'}), 400

    results, errors = {}, {}
    for name, fn, kwargs in [
        ('scout',  scout_agent.run,   {'keywords': business, 'budget_usd': int(data.get('budget', 500)), 'hours_per_week': int(data.get('hours_per_week', 10))}),
        ('income', income_agent.run,  {'business_name': business, 'hours_per_week': int(data.get('hours_per_week', 10)), 'starting_budget': int(data.get('budget', 200))}),
        ('build',  builder_agent.run, {'business_name': business}),
        ('audit',  auditor_agent.run, {'business_name': business}),
    ]:
        try:
            results[name] = fn(**kwargs)
        except Exception as e:
            errors[name] = str(e)

    log_action(g.user['user_id'], 'agent_plan', data={'business': business})
    return jsonify({'business': business, 'results': results, 'errors': errors})


@bp.route('/api/agents/spawn', methods=['POST'])
@require_auth
def agent_spawn():
    """Dynamically spawn sub-agents for a complex task."""
    import utils.agent_spawner as agent_spawner
    import utils.xleads as xleads
    data = request.get_json() or {}
    task = (data.get('task') or '').strip()
    if not task:
        return jsonify({'error': 'task is required'}), 400
    try:
        result = agent_spawner.spawn(
            task=task,
            get_db=get_db,
            xleads_mod=xleads,
            triggered_by=g.user.get('name', 'api'),
        )
        log_action(g.user['user_id'], 'agent_spawn', data={'task': task[:200]})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502
