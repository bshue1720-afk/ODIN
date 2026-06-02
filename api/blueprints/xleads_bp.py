"""ODIN Blueprint — XLeads gateway routes (54 passthrough routes + inbound webhook)."""
import os
import json
import re

import psycopg2.extras
from flask import Blueprint, request, jsonify, current_app, g

from core.db   import get_db, log_action
from core.auth import require_auth, require_role
from core.helpers import _slack_post, _haiku
from utils.slack_commands import is_negative_reply
import utils.xleads       as xleads
import utils.lead_scorer  as lead_scorer
import utils.discord_notify as discord_notify

bp = Blueprint('xleads', __name__)


# ─── DLQ helper ──────────────────────────────────────────────────────

def _dlq_write(payload: dict, error_msg: str, endpoint: str = '/api/xleads/inbound'):
    """Write a failed webhook payload to the dead letter queue for later retry."""
    try:
        _db  = get_db()
        _cur = _db.cursor()
        _cur.execute("""
            INSERT INTO webhook_dead_letters
              (source, endpoint, payload, error_msg, retry_count, next_retry, created_at, updated_at)
            VALUES ('xleads', %s, %s, %s, 0, NOW() + INTERVAL '15 minutes', NOW(), NOW())
        """, (endpoint, json.dumps(payload), error_msg[:500]))
        _db.commit()
        _db.close()
    except Exception as _dlq_err:
        print(f'[dlq_write] Failed to write dead letter: {_dlq_err}')


# ─── Contacts ────────────────────────────────────────────────────────

@bp.route('/api/xleads/contacts', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_search_contacts():
    query  = request.args.get('q')
    tags   = request.args.get('tags', '').split(',') if request.args.get('tags') else None
    limit  = min(int(request.args.get('limit', 20)), 100)
    skip   = int(request.args.get('skip', 0))
    try:
        contacts = xleads.search_contacts(query=query, tags=tags, limit=limit, skip=skip)
        log_action(g.user['user_id'], 'xl_search_contacts', data={'query': query, 'tags': tags})
        return jsonify({'contacts': contacts, 'count': len(contacts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_create_contact():
    data       = request.get_json() or {}
    first_name = (data.get('first_name') or '').strip()
    if not first_name:
        return jsonify({'error': 'first_name is required'}), 400
    try:
        contact = xleads.create_contact(
            first_name=first_name,
            last_name=data.get('last_name'),
            phone=data.get('phone'),
            email=data.get('email'),
            address=data.get('address'),
            city=data.get('city'),
            state=data.get('state'),
            zip_code=data.get('zip_code'),
            tags=data.get('tags'),
        )
        log_action(g.user['user_id'], 'xl_create_contact', 'xleads', contact.get('id'), {'name': first_name})
        return jsonify({'contact': contact}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact(contact_id):
    try:
        contact = xleads.get_contact(contact_id)
        return jsonify({'contact': contact})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_contact(contact_id):
    data = request.get_json() or {}
    if not data:
        return jsonify({'error': 'No fields to update'}), 400
    try:
        contact = xleads.update_contact(contact_id, **data)
        log_action(g.user['user_id'], 'xl_update_contact', 'xleads', contact_id, data)
        return jsonify({'contact': contact})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_contact(contact_id):
    try:
        result = xleads.delete_contact(contact_id)
        log_action(g.user['user_id'], 'xl_delete_contact', 'xleads', contact_id)
        return jsonify({'message': 'Contact deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/tag', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_tag_contact(contact_id):
    data = request.get_json() or {}
    try:
        result = {}
        if data.get('add'):
            result['add'] = xleads.add_contact_tags(contact_id, data['add'])
        if data.get('remove'):
            result['remove'] = xleads.remove_contact_tags(contact_id, data['remove'])
        log_action(g.user['user_id'], 'xl_tag_contact', 'xleads', contact_id, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/sms', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_send_sms(contact_id):
    data    = request.get_json() or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'message is required'}), 400
    try:
        result = xleads.send_sms(contact_id, message, data.get('from_number'))
        log_action(g.user['user_id'], 'xl_send_sms', 'xleads', contact_id, {'message_length': len(message)})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/workflow', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_trigger_workflow(contact_id):
    data        = request.get_json() or {}
    workflow_id = (data.get('workflow_id') or '').strip()
    if not workflow_id:
        return jsonify({'error': 'workflow_id is required'}), 400
    try:
        result = xleads.trigger_workflow(contact_id, workflow_id)
        log_action(g.user['user_id'], 'xl_trigger_workflow', 'xleads', contact_id, {'workflow_id': workflow_id})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/notes', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact_notes(contact_id):
    try:
        notes = xleads.get_contact_notes(contact_id)
        return jsonify({'notes': notes, 'count': len(notes)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/notes', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_add_contact_note(contact_id):
    data = request.get_json() or {}
    body = (data.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'body is required'}), 400
    try:
        result = xleads.add_contact_note(contact_id, body)
        log_action(g.user['user_id'], 'xl_add_note', 'xleads', contact_id, {'note_length': len(body)})
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/tasks', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_contact_tasks(contact_id):
    try:
        tasks = xleads.get_contact_tasks(contact_id)
        return jsonify({'tasks': tasks, 'count': len(tasks)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/tasks', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_add_contact_task(contact_id):
    data  = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400
    try:
        result = xleads.add_contact_task(contact_id, title, due_date=data.get('due_date'), description=data.get('description'))
        log_action(g.user['user_id'], 'xl_add_task', 'xleads', contact_id, {'title': title})
        return jsonify(result), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contacts/<contact_id>/email', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_send_email(contact_id):
    data    = request.get_json() or {}
    subject = (data.get('subject') or '').strip()
    body    = (data.get('body') or '').strip()
    if not subject or not body:
        return jsonify({'error': 'subject and body are required'}), 400
    try:
        result = xleads.send_email(contact_id, subject, body, from_name=data.get('from_name'), from_email=data.get('from_email'))
        log_action(g.user['user_id'], 'xl_send_email', 'xleads', contact_id, {'subject': subject})
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Conversations ───────────────────────────────────────────────────

@bp.route('/api/xleads/conversations', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_conversations():
    contact_id = request.args.get('contact_id')
    limit      = min(int(request.args.get('limit', 20)), 100)
    try:
        convos = xleads.get_conversations(contact_id=contact_id, limit=limit)
        return jsonify({'conversations': convos, 'count': len(convos)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/conversations/<conversation_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_conversation(conversation_id):
    try:
        convo = xleads.get_conversation(conversation_id)
        return jsonify({'conversation': convo})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/conversations/<conversation_id>/messages', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_messages(conversation_id):
    limit = min(int(request.args.get('limit', 20)), 100)
    try:
        messages = xleads.get_messages(conversation_id, limit=limit)
        return jsonify({'messages': messages, 'count': len(messages)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Pipeline ────────────────────────────────────────────────────────

@bp.route('/api/xleads/pipeline', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'disposition_agent')
def xl_get_pipeline():
    try:
        deals = xleads.get_opportunities(
            pipeline_id=request.args.get('pipeline_id'),
            stage_id=request.args.get('stage_id'),
            limit=min(int(request.args.get('limit', 20)), 100),
        )
        return jsonify({'opportunities': deals, 'count': len(deals)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/pipeline/<opportunity_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'disposition_agent')
def xl_get_opportunity(opportunity_id):
    try:
        opp = xleads.get_opportunity(opportunity_id)
        return jsonify({'opportunity': opp})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/pipeline', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_create_opportunity():
    data     = request.get_json() or {}
    required = ('contact_id', 'pipeline_id', 'stage_id', 'name')
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Required: {", ".join(missing)}'}), 400
    try:
        opp = xleads.create_opportunity(data['contact_id'], data['pipeline_id'], data['stage_id'], data['name'], monetary_value=data.get('monetary_value'))
        log_action(g.user['user_id'], 'xl_create_opportunity', 'xleads', opp.get('id'), {'name': data['name']})
        return jsonify({'opportunity': opp}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/pipeline/<opportunity_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_opportunity(opportunity_id):
    data = request.get_json() or {}
    if not data:
        return jsonify({'error': 'No fields to update'}), 400
    try:
        opp = xleads.update_opportunity(opportunity_id, **data)
        log_action(g.user['user_id'], 'xl_update_opportunity', 'xleads', opportunity_id, data)
        return jsonify({'opportunity': opp})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/pipeline/<opportunity_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_delete_opportunity(opportunity_id):
    try:
        result = xleads.delete_opportunity(opportunity_id)
        log_action(g.user['user_id'], 'xl_delete_opportunity', 'xleads', opportunity_id)
        return jsonify({'message': 'Opportunity deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/bulk-workflow', methods=['POST'])
@require_auth
@require_role('super_admin', 'acquisition_manager')
def xl_bulk_trigger_workflow():
    data        = request.get_json() or {}
    workflow_id = (data.get('workflow_id') or '').strip()
    tags        = data.get('tags')
    query       = data.get('query')
    limit       = min(int(data.get('limit', 50)), 200)
    if not workflow_id:
        return jsonify({'error': 'workflow_id is required'}), 400
    if not tags and not query:
        return jsonify({'error': 'tags or query required'}), 400
    try:
        contacts  = xleads.search_contacts(query=query, tags=tags, limit=limit)
        triggered, failed = [], []
        for c in contacts:
            cid = c.get('id')
            try:
                xleads.trigger_workflow(cid, workflow_id)
                triggered.append(cid)
            except Exception as e:
                failed.append({'id': cid, 'error': str(e)})
        log_action(g.user['user_id'], 'xl_bulk_workflow', 'xleads', None, {'workflow_id': workflow_id, 'tags': tags, 'triggered': len(triggered), 'failed': len(failed)})
        return jsonify({'triggered': len(triggered), 'failed': len(failed), 'failed_details': failed, 'message': f'{len(triggered)} contacts enrolled in workflow {workflow_id}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Contracts ───────────────────────────────────────────────────────

@bp.route('/api/xleads/contracts/templates', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_contract_templates():
    try:
        templates = xleads.list_contract_templates()
        return jsonify({'templates': templates, 'count': len(templates)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contracts', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'disposition_agent')
def xl_list_contracts():
    try:
        contracts = xleads.list_contracts()
        return jsonify({'contracts': contracts, 'count': len(contracts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/contracts/send', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_send_contract():
    data        = request.get_json() or {}
    template_id = (data.get('template_id') or '').strip()
    contact_id  = (data.get('contact_id') or '').strip()
    if not template_id or not contact_id:
        return jsonify({'error': 'template_id and contact_id are required'}), 400
    try:
        result = xleads.send_contract_from_template(template_id, contact_id, data.get('signers'))
        log_action(g.user['user_id'], 'xl_send_contract', 'xleads', contact_id, {'template_id': template_id})
        return jsonify({'message': 'Contract sent', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Workflows ───────────────────────────────────────────────────────

@bp.route('/api/xleads/workflows', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_workflows():
    try:
        workflows = xleads.list_workflows()
        summary   = [{'id': w.get('id'), 'name': w.get('name'), 'status': w.get('status')} for w in workflows]
        return jsonify({'workflows': summary, 'count': len(summary)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Calendars ───────────────────────────────────────────────────────

@bp.route('/api/xleads/calendars', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_list_calendars():
    try:
        calendars = xleads.list_calendars()
        return jsonify({'calendars': calendars, 'count': len(calendars)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/calendars/events', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_get_calendar_events():
    try:
        events = xleads.get_calendar_events(calendar_id=request.args.get('calendar_id'), start_time=request.args.get('start_time'), end_time=request.args.get('end_time'))
        return jsonify({'events': events, 'count': len(events)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/calendars/events', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager', 'caller')
def xl_book_appointment():
    data     = request.get_json() or {}
    required = ('contact_id', 'calendar_id', 'start_time', 'end_time')
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Required: {", ".join(missing)}'}), 400
    try:
        event = xleads.book_appointment(data['contact_id'], data['calendar_id'], data['start_time'], data['end_time'], title=data.get('title'))
        log_action(g.user['user_id'], 'xl_book_appointment', 'xleads', data['contact_id'], {'start': data['start_time']})
        return jsonify({'event': event}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/calendars/events/<event_id>', methods=['PUT'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_update_appointment(event_id):
    data = request.get_json() or {}
    try:
        result = xleads.update_appointment(event_id, **data)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/calendars/events/<event_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_cancel_appointment(event_id):
    try:
        result = xleads.cancel_appointment(event_id)
        log_action(g.user['user_id'], 'xl_cancel_appointment', 'xleads', event_id)
        return jsonify({'message': 'Appointment cancelled', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Phone Numbers ────────────────────────────────────────────────────

@bp.route('/api/xleads/phone-numbers', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_phone_numbers():
    try:
        numbers = xleads.list_phone_numbers()
        return jsonify({'phone_numbers': numbers, 'count': len(numbers)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/phone-numbers/search', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_search_available_numbers():
    area_code = request.args.get('area_code', '').strip()
    if not area_code:
        return jsonify({'error': 'area_code is required'}), 400
    try:
        numbers = xleads.search_available_numbers(area_code, limit=min(int(request.args.get('limit', 5)), 20), country=request.args.get('country', 'US'))
        return jsonify({'available_numbers': numbers, 'count': len(numbers)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/phone-numbers/buy', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_buy_phone_number():
    data         = request.get_json() or {}
    phone_number = (data.get('phone_number') or '').strip()
    if not phone_number:
        return jsonify({'error': 'phone_number is required (E.164 format)'}), 400
    try:
        result = xleads.buy_phone_number(phone_number, area_code=data.get('area_code'))
        log_action(g.user['user_id'], 'xl_buy_phone_number', 'xleads', None, {'phone_number': phone_number})
        return jsonify({'message': f'Number {phone_number} purchased', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/phone-numbers/<number_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_phone_number(number_id):
    try:
        result = xleads.delete_phone_number(number_id)
        log_action(g.user['user_id'], 'xl_delete_phone_number', 'xleads', number_id)
        return jsonify({'message': 'Number released', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/phone-numbers/pools', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_number_pools():
    try:
        pools = xleads.get_number_pools()
        return jsonify({'pools': pools})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/twilio', methods=['GET'])
@require_auth
@require_role('super_admin')
def xl_get_twilio_account():
    try:
        info = xleads.get_twilio_account()
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Voice AI (Maya) ─────────────────────────────────────────────────

@bp.route('/api/xleads/voice-ai/agents', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_voice_agents():
    try:
        agents = xleads.list_voice_agents()
        return jsonify({'agents': agents, 'count': len(agents)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/voice-ai/agents/<agent_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_voice_agent(agent_id):
    try:
        agent = xleads.get_voice_agent(agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/voice-ai/agents/<agent_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_voice_agent(agent_id):
    data = request.get_json() or {}
    try:
        agent = xleads.update_voice_agent(agent_id, **data)
        log_action(g.user['user_id'], 'xl_update_voice_agent', 'xleads', agent_id, data)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/voice-ai/agents/<agent_id>/goals', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_voice_agent_goals(agent_id):
    try:
        goals = xleads.get_voice_agent_goals(agent_id)
        return jsonify({'goals': goals})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/voice-ai/agents/<agent_id>/goals', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_voice_agent_goals(agent_id):
    data  = request.get_json() or {}
    goals = data.get('goals')
    if not isinstance(goals, list):
        return jsonify({'error': 'goals must be a list'}), 400
    try:
        result = xleads.update_voice_agent_goals(agent_id, goals)
        log_action(g.user['user_id'], 'xl_update_voice_goals', 'xleads', agent_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Conversation AI ─────────────────────────────────────────────────

@bp.route('/api/xleads/conversation-ai/bots', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_conversation_bots():
    try:
        bots = xleads.list_conversation_bots()
        return jsonify({'bots': bots, 'count': len(bots)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/conversation-ai/bots/<bot_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_conversation_bot(bot_id):
    try:
        bot = xleads.get_conversation_bot(bot_id)
        return jsonify({'bot': bot})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/conversation-ai/bots/<bot_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_conversation_bot(bot_id):
    data = request.get_json() or {}
    try:
        bot = xleads.update_conversation_bot(bot_id, **data)
        log_action(g.user['user_id'], 'xl_update_convo_bot', 'xleads', bot_id, data)
        return jsonify({'bot': bot})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Agent Studio ────────────────────────────────────────────────────

@bp.route('/api/xleads/agent-studio', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_studio_agents():
    try:
        agents = xleads.list_studio_agents()
        return jsonify({'agents': agents, 'count': len(agents)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/agent-studio/<agent_id>', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_get_studio_agent(agent_id):
    try:
        agent = xleads.get_studio_agent(agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/agent-studio', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_create_studio_agent():
    data   = request.get_json() or {}
    name   = (data.pop('name', '') or '').strip()
    prompt = (data.pop('prompt', '') or '').strip()
    if not name or not prompt:
        return jsonify({'error': 'name and prompt are required'}), 400
    try:
        agent = xleads.create_studio_agent(name, prompt, **data)
        log_action(g.user['user_id'], 'xl_create_studio_agent', 'xleads', agent.get('id'), {'name': name})
        return jsonify({'agent': agent}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/agent-studio/<agent_id>', methods=['PUT'])
@require_auth
@require_role('super_admin')
def xl_update_studio_agent(agent_id):
    data = request.get_json() or {}
    try:
        agent = xleads.update_studio_agent(agent_id, **data)
        log_action(g.user['user_id'], 'xl_update_studio_agent', 'xleads', agent_id)
        return jsonify({'agent': agent})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Tags ────────────────────────────────────────────────────────────

@bp.route('/api/xleads/tags', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_tags():
    try:
        tags = xleads.list_tags()
        return jsonify({'tags': tags, 'count': len(tags)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/tags', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_create_tag():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    try:
        tag = xleads.create_tag(name)
        log_action(g.user['user_id'], 'xl_create_tag', 'xleads', None, {'name': name})
        return jsonify({'tag': tag}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/tags/<tag_id>', methods=['DELETE'])
@require_auth
@require_role('super_admin')
def xl_delete_tag(tag_id):
    try:
        result = xleads.delete_tag(tag_id)
        log_action(g.user['user_id'], 'xl_delete_tag', 'xleads', tag_id)
        return jsonify({'message': 'Tag deleted', 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Custom Fields ────────────────────────────────────────────────────

@bp.route('/api/xleads/custom-fields', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_custom_fields():
    try:
        fields = xleads.list_custom_fields()
        return jsonify({'custom_fields': fields, 'count': len(fields)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/custom-fields', methods=['POST'])
@require_auth
@require_role('super_admin')
def xl_create_custom_field():
    data      = request.get_json() or {}
    name      = (data.get('name') or '').strip()
    data_type = (data.get('data_type') or '').strip().upper()
    if not name or not data_type:
        return jsonify({'error': 'name and data_type are required'}), 400
    try:
        field = xleads.create_custom_field(name, data_type, placeholder=data.get('placeholder'))
        log_action(g.user['user_id'], 'xl_create_custom_field', 'xleads', None, {'name': name, 'type': data_type})
        return jsonify({'custom_field': field}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Campaigns ───────────────────────────────────────────────────────

@bp.route('/api/xleads/campaigns', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner', 'acquisition_manager')
def xl_list_campaigns():
    try:
        campaigns = xleads.list_campaigns()
        return jsonify({'campaigns': campaigns, 'count': len(campaigns)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Social Planner ───────────────────────────────────────────────────

@bp.route('/api/xleads/social', methods=['GET'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_list_social_posts():
    limit = min(int(request.args.get('limit', 20)), 100)
    try:
        posts = xleads.list_social_posts(limit=limit)
        return jsonify({'posts': posts, 'count': len(posts)})
    except Exception as e:
        return jsonify({'error': str(e)}), 502


@bp.route('/api/xleads/social', methods=['POST'])
@require_auth
@require_role('super_admin', 're_partner')
def xl_create_social_post():
    data      = request.get_json() or {}
    content   = (data.get('content') or '').strip()
    platforms = data.get('platforms')
    if not content or not platforms:
        return jsonify({'error': 'content and platforms are required'}), 400
    try:
        post = xleads.create_social_post(content, platforms, scheduled_at=data.get('scheduled_at'), media_urls=data.get('media_urls'))
        log_action(g.user['user_id'], 'xl_create_social_post', 'xleads', None, {'platforms': platforms})
        return jsonify({'post': post}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 502


# ─── Inbound Webhook ────────────────────────────────────────────────

@bp.route('/api/xleads/inbound', methods=['POST'])
def xleads_inbound():
    """
    Receives inbound SMS/email events from XLeads (GoHighLevel).
    GHL fires this when a seller replies → MCTP score → Slack/Discord alert.
    """
    _raw_payload = request.get_json(silent=True) or {}
    data = _raw_payload

    _msg_raw   = data.get('message')
    _msg_obj   = _msg_raw if isinstance(_msg_raw, dict) else {}
    event_type = data.get('type', '') or _msg_obj.get('type', '')
    contact_id = data.get('contactId', '') or data.get('contact_id', '')
    _body_str  = data.get('body') or (_msg_raw if isinstance(_msg_raw, str) else None) or _msg_obj.get('body', '') or _msg_obj.get('text', '')
    body       = str(_body_str).strip() if _body_str else ''
    first_name = data.get('firstName', 'Unknown')
    last_name  = data.get('lastName', '')
    phone      = data.get('phone', '—')
    msg_type   = data.get('messageType', '') or _msg_obj.get('type', 'SMS')
    _direction = data.get('direction', '') or _msg_obj.get('direction', '')

    if 'inbound' not in event_type.lower() and _direction != 'inbound':
        return jsonify({'ok': True})
    if not body:
        return jsonify({'ok': True})

    sender_name   = f'{first_name} {last_name}'.strip()
    brock_channel = os.environ.get('SLACK_CHANNEL_BROCK', '')

    log_action(None, 'xleads_inbound_message', 'xleads', contact_id, {'from': sender_name, 'message_preview': body[:100]})

    # ── Buyer criteria capture ─────────────────────────────────────────
    if contact_id and not is_negative_reply(body):
        try:
            _bq_db  = get_db()
            _bq_cur = _bq_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            _bq_cur.execute("""
                SELECT b.id AS buyer_id, b.name,
                       bc.id AS criteria_id, bc.notes AS criteria_notes
                FROM buyers b
                LEFT JOIN buyer_criteria bc ON bc.buyer_id = b.id
                WHERE b.xleads_contact_id = %s
                  AND b.is_dnc = FALSE
                  AND b.onboarding_stage IN ('welcomed', 'qualified')
                LIMIT 1
            """, (contact_id,))
            _buyer = _bq_cur.fetchone()
            if _buyer:
                _criteria_raw = _haiku(
                    f'Extract buyer criteria from this SMS reply. Return JSON only: '
                    f'{{"min_price": number or null, "max_price": number or null, '
                    f'"zip_codes": ["list"], "deal_types": ["flip","rental"], '
                    f'"max_rehab": number or null, "notes": "one-line summary"}}\n\nSMS: "{body}"',
                    max_tokens=200,
                )
                try:
                    _criteria = json.loads(_criteria_raw.strip().strip('`').replace('json\n', '').replace('json', ''))
                except Exception:
                    _criteria = {}

                _bq_cur.execute("""
                    INSERT INTO buyer_criteria
                        (buyer_id, min_price, max_price, zip_codes, deal_types, max_rehab, notes, raw_reply)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (buyer_id) DO UPDATE SET
                        min_price  = COALESCE(EXCLUDED.min_price,  buyer_criteria.min_price),
                        max_price  = COALESCE(EXCLUDED.max_price,  buyer_criteria.max_price),
                        zip_codes  = COALESCE(EXCLUDED.zip_codes,  buyer_criteria.zip_codes),
                        deal_types = COALESCE(EXCLUDED.deal_types, buyer_criteria.deal_types),
                        max_rehab  = COALESCE(EXCLUDED.max_rehab,  buyer_criteria.max_rehab),
                        notes      = EXCLUDED.notes,
                        raw_reply  = EXCLUDED.raw_reply,
                        updated_at = NOW()
                """, (
                    str(_buyer['buyer_id']),
                    _criteria.get('min_price'), _criteria.get('max_price'),
                    _criteria.get('zip_codes') or [], _criteria.get('deal_types') or [],
                    _criteria.get('max_rehab'), _criteria.get('notes', '')[:300], body[:500],
                ))
                _bq_cur.execute("""
                    UPDATE buyers SET onboarding_stage = 'qualified', updated_at = NOW()
                    WHERE id = %s AND onboarding_stage != 'active'
                """, (str(_buyer['buyer_id']),))
                _bq_db.commit()
                _bq_cur.close()
                _bq_db.close()
                _buyer_name = _buyer['name'] or sender_name
                _slack_post(brock_channel,
                    f'✅ *Buyer criteria captured — {_buyer_name}*\n'
                    f'Reply: _{body}_\n'
                    f'{_criteria.get("notes", "")}\n'
                    f'Price: ${_criteria.get("min_price") or "?"} – ${_criteria.get("max_price") or "?"} | '
                    f'Zips: {", ".join(_criteria.get("zip_codes") or []) or "—"} | '
                    f'Types: {", ".join(_criteria.get("deal_types") or []) or "—"}\n'
                    f'_Buyer advanced to Qualified stage._'
                )
                return jsonify({'ok': True})
            else:
                _bq_cur.close()
                _bq_db.close()
        except Exception as _bq_err:
            print(f'[xleads_inbound] Buyer criteria capture failed: {_bq_err}')

    # ── Negative / opt-out ────────────────────────────────────────────
    if is_negative_reply(body):
        try:
            xleads.add_contact_tags(contact_id, ['Do-Not-Contact', 'Opted-Out'])
            xleads.remove_contact_tags(contact_id, ['Hot', 'Warm', 'Cold'])
            workflows = xleads.list_workflows()
            for wf in workflows:
                try:
                    xleads.remove_from_workflow(contact_id, wf['id'])
                except Exception:
                    pass
        except Exception as e:
            print(f'[xleads_inbound] Auto-ignore failed: {e}')

        try:
            _oo_db  = get_db()
            _oo_cur = _oo_db.cursor()
            _oo_cur.execute("""
                UPDATE leads SET opted_out_at = NOW(), opt_out_reason = %s, updated_at = NOW()
                WHERE xleads_contact_id = %s
            """, (body[:200], contact_id))
            _oo_db.commit()
            _oo_cur.close()
            _oo_db.close()
        except Exception:
            pass

        opt_out_msg = (
            f'🚫 *Auto-ignored* — {sender_name} ({phone})\n'
            f'Message: _{body}_\n'
            f'Tagged Do-Not-Contact and removed from all workflows.'
        )
        if brock_channel:
            _slack_post(brock_channel, opt_out_msg)
        eddie_ch = os.environ.get('SLACK_CHANNEL_EDDIE', '')
        if eddie_ch and eddie_ch != brock_channel:
            _slack_post(eddie_ch, opt_out_msg)
        return jsonify({'ok': True})

    # ── Positive / neutral — MCTP score + alert ───────────────────────
    score_block  = ''
    sentiment_tag = ''
    score_result  = {}
    try:
        score_result = lead_scorer.score(notes=body, address=data.get('address', ''), caller_name=sender_name)
        total        = score_result.get('total', 0)
        tier         = score_result.get('tier', '')
        tier_emoji   = '🔥' if tier == 'Hot' else ('⚡' if tier == 'Warm' else '🧊')

        try:
            import anthropic as _anthropic
            _client = _anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
            _s_resp = _client.messages.create(
                model='claude-haiku-4-5-20251001', max_tokens=10,
                messages=[{'role': 'user', 'content':
                    f'Classify this seller SMS reply into ONE word: curious, interested, stalling, angry, negotiating, or neutral.\nReply text: "{body[:300]}"\nRespond with only the single classification word.'}]
            )
            sentiment_tag = _s_resp.content[0].text.strip().lower()
        except Exception:
            sentiment_tag = ''

        sentiment_emoji = {'curious': '🤔', 'interested': '👍', 'stalling': '⏳', 'angry': '😠', 'negotiating': '🤝', 'neutral': '😐'}.get(sentiment_tag, '')
        sentiment_line  = f'\n_{sentiment_emoji} Sentiment: {sentiment_tag}_' if sentiment_tag else ''

        score_block = (
            f'\n*{tier_emoji} Auto-MCTP: {total}/10 ({tier})*\n'
            f'M:{score_result.get("motivation",0)} C:{score_result.get("condition",0)} '
            f'T:{score_result.get("timeline",0)} P:{score_result.get("price",0)}'
            f'{sentiment_line}'
        )

        if total >= 8:
            try:
                xleads.add_contact_tags(contact_id, ['Hot'])
                xleads.remove_contact_tags(contact_id, ['Warm', 'Cold'])
            except Exception:
                pass
            try:
                _hr_db  = get_db()
                _hr_cur = _hr_db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                _hr_cur.execute("""
                    SELECT xleads_id FROM workflow_registry
                    WHERE name ILIKE '%customer%replied%' OR name ILIKE '%customer-replied%' LIMIT 1
                """)
                _wf_row = _hr_cur.fetchone()
                _hr_cur.close()
                _hr_db.close()
                if _wf_row and _wf_row.get('xleads_id') and contact_id:
                    xleads.trigger_workflow(contact_id, _wf_row['xleads_id'])
            except Exception:
                pass
        elif total >= 5:
            try:
                xleads.add_contact_tags(contact_id, ['Warm'])
                xleads.remove_contact_tags(contact_id, ['Hot', 'Cold'])
            except Exception:
                pass
    except Exception:
        pass

    # ── Upsert lead + stamp blast_campaign_id ─────────────────────────
    try:
        _db  = get_db()
        _cur = _db.cursor()
        _cur.execute("""
            SELECT id FROM blast_campaigns
            WHERE created_at >= NOW() - INTERVAL '7 days' AND sent_count > 0 AND health_status != 'flagged'
            ORDER BY created_at DESC LIMIT 1
        """)
        _camp    = _cur.fetchone()
        _camp_id = str(_camp['id']) if _camp else None  # RealDictCursor — use key, not index
        _mctp_total = score_result.get('total', 0)

        _cur.execute("""
            INSERT INTO leads (user_id, address, xleads_contact_id, mctp_total, blast_campaign_id,
                               motivation_score, condition_score, timeline_score, price_score,
                               reply_sentiment, last_reply_body, last_reply_at,
                               spoke, status)
            VALUES ((SELECT id FROM users WHERE role = 'super_admin' LIMIT 1), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), 'real_estate', 'new')
            ON CONFLICT (xleads_contact_id) DO UPDATE SET
                mctp_total        = COALESCE(EXCLUDED.mctp_total, leads.mctp_total),
                blast_campaign_id = COALESCE(leads.blast_campaign_id, EXCLUDED.blast_campaign_id),
                motivation_score  = COALESCE(EXCLUDED.motivation_score, leads.motivation_score),
                condition_score   = COALESCE(EXCLUDED.condition_score, leads.condition_score),
                timeline_score    = COALESCE(EXCLUDED.timeline_score, leads.timeline_score),
                price_score       = COALESCE(EXCLUDED.price_score, leads.price_score),
                reply_sentiment   = COALESCE(EXCLUDED.reply_sentiment, leads.reply_sentiment),
                last_reply_body   = EXCLUDED.last_reply_body,
                last_reply_at     = NOW(),
                updated_at        = NOW()
        """, (
            data.get('address') or f'{first_name} {last_name}'.strip() or 'Unknown',
            contact_id, _mctp_total, _camp_id,
            score_result.get('motivation', 0), score_result.get('condition', 0),
            score_result.get('timeline', 0),   score_result.get('price', 0),
            sentiment_tag or None, body[:500] if body else None,
        ))
        _db.commit()
        _cur.close()
        _db.close()
    except Exception as _le:
        print(f'[xleads_inbound] Lead upsert failed: {_le}')

    # ── Clear follow_up_date + stamp last_contact_date ────────────────
    if contact_id:
        try:
            _fu_db  = get_db()
            _fu_cur = _fu_db.cursor()
            _fu_cur.execute("""
                UPDATE leads
                SET follow_up_date = NULL, next_action = NULL,
                    followup_step = 1, last_contact_date = CURRENT_DATE, updated_at = NOW()
                WHERE xleads_contact_id = %s
            """, (contact_id,))
            _fu_db.commit()
            _fu_cur.close()
            _fu_db.close()
        except Exception:
            pass

    # ── Pull last 5 conversation messages ─────────────────────────────
    history_block = ''
    try:
        convos = xleads.get_conversations(contact_id=contact_id, limit=1)
        if convos:
            convo_id = convos[0].get('id', '')
            if convo_id:
                messages = xleads.get_messages(convo_id, limit=6)
                prior    = [m for m in messages if (m.get('body') or m.get('message', '')) != body][-5:]
                if prior:
                    history_lines = ['*📜 Prior messages:*']
                    for m in prior:
                        direction = m.get('direction', 'unknown')
                        arrow     = '→' if direction == 'outbound' else '←'
                        msg_body  = (m.get('body') or m.get('message', ''))[:120]
                        history_lines.append(f'  {arrow} _{msg_body}_')
                    history_block = '\n' + '\n'.join(history_lines)
    except Exception:
        pass

    alert_text = (
        f'📩 *{msg_type} Reply — {sender_name}* ({phone})\n'
        f'Message: _{body}_'
        f'{score_block}'
        f'{history_block}\n\n'
        f'Contact ID: `{contact_id}`\n'
        f'Reply: `text {contact_id} your message here`\n'
        f'Score full call: `score {body[:80]}`\n'
        f'Lookup property: `lookup {data.get("address", sender_name)}`\n'
        f'Ignore: `ignore {contact_id}`'
    )
    if brock_channel:
        _slack_post(brock_channel, alert_text)
    eddie_ch = os.environ.get('SLACK_CHANNEL_EDDIE', '')
    if eddie_ch and eddie_ch != brock_channel:
        _slack_post(eddie_ch, alert_text)

        try:
            total_score = 0
            try:
                total_score = int(re.search(r'Auto-MCTP: (\d+)/10', score_block).group(1))
            except Exception:
                pass

            if total_score >= 8:
                tier_str, color, emoji = 'Hot',   0xFF4444, '🔥'
            elif total_score >= 5:
                tier_str, color, emoji = 'Warm',  0xFFAA00, '⚡'
            else:
                tier_str, color, emoji = 'Reply', 0x4A90D9, '📩'

            score_line   = f'**Score:** {total_score}/10 ({tier_str})\n' if total_score > 0 else ''
            history_plain = ('\n' + history_block.replace('*', '**').replace('_', '*')) if history_block else ''
            desc = (
                f'**Message:** {body[:400]}\n\n'
                f'{score_line}'
                f'**Phone:** {phone}\n'
                f'**Contact ID:** `{contact_id}`'
                f'{history_plain}'
            )
            discord_notify.post_embed(
                title=f'{emoji} Seller Reply — {sender_name}',
                description=desc, color=color, target='brock',
            )
        except Exception:
            pass

    return jsonify({'ok': True})


@bp.route('/api/xleads/inbound/retry', methods=['POST'])
def xleads_inbound_retry():
    """Internal: re-process a dead letter entry by ID."""
    data   = request.get_json(silent=True) or {}
    dlq_id = data.get('id')
    if not dlq_id:
        return jsonify({'error': 'id required'}), 400
    try:
        db  = get_db()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT payload FROM webhook_dead_letters WHERE id = %s AND resolved = FALSE", (dlq_id,))
        row = cur.fetchone()
        db.close()
        if not row:
            return jsonify({'error': 'not found or already resolved'}), 404

        with current_app.test_request_context(
            '/api/xleads/inbound', method='POST',
            data=json.dumps(row['payload']), content_type='application/json'
        ):
            resp = xleads_inbound()

        db2 = get_db()
        cur2 = db2.cursor()
        cur2.execute("UPDATE webhook_dead_letters SET resolved = TRUE, updated_at = NOW() WHERE id = %s", (dlq_id,))
        db2.commit()
        db2.close()
        return jsonify({'ok': True, 'retried': dlq_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
