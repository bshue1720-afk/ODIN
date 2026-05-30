"""
ODIN — XLeads (GoHighLevel) API Client
Full coverage of all granted API v2.0 scopes.
Organized by category. All credentials from Railway env vars.

Categories:
  Contacts        — CRUD, tags, notes, tasks
  Conversations   — threads, messages, SMS
  Workflows       — list, trigger, remove
  Opportunities   — pipeline CRUD
  Calendars       — events, appointments
  Phone Numbers   — list, search, buy, delete
  Voice AI        — Maya prompt + goals
  Conversation AI — AI bot config
  Agent Studio    — AI agents
  Contracts       — templates, send, list
  Campaigns       — read
  Tags            — location-level tag management
  Custom Fields   — location-level custom fields
  Social Planner  — post scheduling
  Twilio          — account info
"""
import os
import requests

API_KEY     = os.environ.get('XLEADS_API_KEY', '')
LOCATION_ID = os.environ.get('XLEADS_LOCATION_ID', '')
BASE_URL    = os.environ.get('XLEADS_BASE_URL', 'https://services.leadconnectorhq.com')

HEADERS = {
    'Authorization': f'Bearer {API_KEY}',
    'Version':       '2021-07-28',
    'Content-Type':  'application/json',
}


# ─── HTTP Helpers ─────────────────────────────────────────────────────────────

def _get(path, params=None):
    r = requests.get(f'{BASE_URL}{path}', headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def _post(path, payload=None):
    r = requests.post(f'{BASE_URL}{path}', headers=HEADERS, json=payload or {}, timeout=15)
    r.raise_for_status()
    return r.json()

def _put(path, payload=None):
    r = requests.put(f'{BASE_URL}{path}', headers=HEADERS, json=payload or {}, timeout=15)
    r.raise_for_status()
    return r.json()

def _delete(path, payload=None):
    r = requests.delete(f'{BASE_URL}{path}', headers=HEADERS, json=payload or {}, timeout=15)
    r.raise_for_status()
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════════
# CONTACTS
# ═══════════════════════════════════════════════════════════════════════════════

def search_contacts(query=None, tags=None, limit=20, skip=0):
    """
    Search contacts by keyword or tags.

    GHL stores tags as exact strings. When multiple tags are passed (e.g. ['he', 'tax-delinquent']),
    they may exist as SEPARATE individual tags OR as a SINGLE compound tag ('he, tax delinquent').
    Both matching strategies are tried.

    Pagination uses GHL cursor (startAfter + startAfterId from meta).
    """
    if tags:
        def _norm(t):
            return (t['name'] if isinstance(t, dict) else t).lower().strip().replace('-', ' ')

        norm_tags    = [_norm(t) for t in tags]
        # Also build compound form: "he, tax delinquent" for single-tag storage
        compound_tag = ', '.join(norm_tags)

        matched  = []
        next_url = (
            f'{BASE_URL}/contacts/'
            f'?locationId={LOCATION_ID}&limit=100'
            + (f'&query={requests.utils.quote(query)}' if query else '')
        )

        while next_url and len(matched) < limit:
            try:
                r = requests.get(next_url, headers=HEADERS, timeout=20)
                r.raise_for_status()
                resp = r.json()
            except Exception as e:
                print(f'[search_contacts] GET error: {e}')
                break

            contacts = resp.get('contacts', [])
            if not contacts:
                break

            for c in contacts:
                ctags = {_norm(t) for t in (c.get('tags') or [])}
                # Strategy 1: all search tags are individual tags
                # Strategy 2: compound tag exists as one string
                if (all(nt in ctags for nt in norm_tags)) or (compound_tag in ctags):
                    matched.append(c)
                    if len(matched) >= limit:
                        break

            next_url = (resp.get('meta') or {}).get('nextPageUrl')

        return matched[:limit]

    # No tags — standard keyword search
    params = {'locationId': LOCATION_ID, 'limit': limit, 'skip': skip}
    if query:
        params['query'] = query
    return _get('/contacts/search', params=params).get('contacts', [])


def get_contact(contact_id):
    """Get a single contact by ID."""
    return _get(f'/contacts/{contact_id}').get('contact', {})


def create_contact(first_name, last_name=None, phone=None, email=None,
                   address=None, city=None, state=None, zip_code=None, tags=None):
    """Create a new contact in XLeads CRM."""
    payload = {
        'locationId': LOCATION_ID,
        'firstName':  first_name,
    }
    if last_name:   payload['lastName']  = last_name
    if phone:       payload['phone']     = phone
    if email:       payload['email']     = email
    if address:     payload['address1']  = address
    if city:        payload['city']      = city
    if state:       payload['state']     = state
    if zip_code:    payload['postalCode']= zip_code
    if tags:        payload['tags']      = tags
    return _post('/contacts/', payload).get('contact', {})


def update_contact(contact_id, **fields):
    """Update fields on an existing contact."""
    return _put(f'/contacts/{contact_id}', fields).get('contact', {})


def delete_contact(contact_id):
    """Delete a contact from XLeads CRM."""
    return _delete(f'/contacts/{contact_id}')


def add_contact_tags(contact_id, tags):
    """Add tags to a contact. tags: list of strings."""
    return _post(f'/contacts/{contact_id}/tags', {'tags': tags})


def remove_contact_tags(contact_id, tags):
    """Remove tags from a contact. Silently ignores tags that don't exist (422)."""
    try:
        return _delete(f'/contacts/{contact_id}/tags', {'tags': tags})
    except Exception:
        return {}


def get_contact_notes(contact_id):
    """Get all notes on a contact."""
    return _get(f'/contacts/{contact_id}/notes').get('notes', [])


def add_contact_note(contact_id, body, user_id=None):
    """Add a note to a contact."""
    payload = {'body': body}
    if user_id:
        payload['userId'] = user_id
    return _post(f'/contacts/{contact_id}/notes', payload)


def get_contact_tasks(contact_id):
    """Get tasks associated with a contact."""
    return _get(f'/contacts/{contact_id}/tasks').get('tasks', [])


def add_contact_task(contact_id, title, due_date=None, description=None):
    """Add a task to a contact."""
    payload = {'title': title, 'contactId': contact_id}
    if due_date:     payload['dueDate']     = due_date
    if description:  payload['description'] = description
    return _post(f'/contacts/{contact_id}/tasks', payload)


def sync_mctp_to_xleads(contact_id, mctp_total, address=None, workflow_id=None):
    """
    ODIN smart action: sync MCTP score → XLeads tag + optional workflow trigger.
    Called automatically when a caller logs a score in ODIN.
    """
    actions = {}
    try:
        remove_contact_tags(contact_id, ['Hot', 'Warm', 'Cold'])
    except Exception:
        pass

    tag = 'Hot' if mctp_total >= 8 else ('Warm' if mctp_total >= 5 else 'Cold')
    add_contact_tags(contact_id, [tag, f'MCTP-{mctp_total}'])
    actions['tagged'] = tag

    if mctp_total >= 8 and workflow_id:
        trigger_workflow(contact_id, workflow_id)
        actions['workflow_triggered'] = workflow_id

    return actions


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS & MESSAGING
# ═══════════════════════════════════════════════════════════════════════════════

def get_conversations(contact_id=None, limit=20):
    """Pull conversation threads, optionally filtered by contact."""
    params = {'locationId': LOCATION_ID, 'limit': limit}
    if contact_id:
        params['contactId'] = contact_id
    return _get('/conversations/search', params=params).get('conversations', [])


def get_conversation(conversation_id):
    """Get a single conversation thread."""
    return _get(f'/conversations/{conversation_id}').get('conversation', {})


def get_messages(conversation_id, limit=20):
    """Get all messages in a conversation thread."""
    return _get(f'/conversations/{conversation_id}/messages',
                params={'limit': limit}).get('messages', [])


def send_sms(contact_id, message, from_number=None):
    """
    Send an SMS to a contact. A2P 10DLC confirmed active.
    from_number: optional E.164 format e.g. '+19015551234'
    """
    payload = {'type': 'SMS', 'contactId': contact_id, 'message': message}
    if from_number:
        payload['fromNumber'] = from_number
    return _post('/conversations/messages', payload)


def send_email(contact_id, subject, body, from_name=None, from_email=None):
    """Send an email to a contact via XLeads."""
    payload = {
        'type':      'Email',
        'contactId': contact_id,
        'subject':   subject,
        'message':   body,
    }
    if from_name:   payload['fromName']  = from_name
    if from_email:  payload['fromEmail'] = from_email
    return _post('/conversations/messages', payload)


# ═══════════════════════════════════════════════════════════════════════════════
# WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════════

def list_workflows():
    """List all workflows with IDs, names, and status."""
    return _get('/workflows/', params={'locationId': LOCATION_ID}).get('workflows', [])


def trigger_workflow(contact_id, workflow_id):
    """Enroll a contact into a workflow (SMS sequence)."""
    from datetime import datetime, timezone
    payload = {'eventStartTime': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')}
    return _post(f'/contacts/{contact_id}/workflow/{workflow_id}', payload)


def remove_from_workflow(contact_id, workflow_id):
    """Remove a contact from a workflow."""
    return _delete(f'/contacts/{contact_id}/workflow/{workflow_id}')


def bulk_trigger_workflow(workflow_id, tags=None, query=None, limit=100):
    """
    Pull contacts matching criteria and enroll all into a workflow.
    Returns {'triggered': N, 'failed': [...]}
    """
    contacts  = search_contacts(query=query, tags=tags, limit=limit)
    triggered = []
    failed    = []
    for c in contacts:
        cid = c.get('id')
        try:
            trigger_workflow(cid, workflow_id)
            triggered.append(cid)
        except Exception as e:
            failed.append({'id': cid, 'error': str(e)})
    return {'triggered': len(triggered), 'failed': failed, 'total': len(contacts)}


# ═══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITIES / PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def get_opportunities(pipeline_id=None, stage_id=None, limit=20):
    """Pull deal pipeline from XLeads."""
    params = {'location_id': LOCATION_ID, 'limit': limit}
    if pipeline_id:  params['pipeline_id']       = pipeline_id
    if stage_id:     params['pipeline_stage_id'] = stage_id
    return _get('/opportunities/search', params=params).get('opportunities', [])


def get_opportunity(opportunity_id):
    """Get a single opportunity."""
    return _get(f'/opportunities/{opportunity_id}').get('opportunity', {})


def create_opportunity(contact_id, pipeline_id, stage_id, name, monetary_value=None):
    """Create a deal in the XLeads pipeline."""
    payload = {
        'pipelineId':      pipeline_id,
        'locationId':      LOCATION_ID,
        'name':            name,
        'pipelineStageId': stage_id,
        'contactId':       contact_id,
        'status':          'open',
    }
    if monetary_value:
        payload['monetaryValue'] = monetary_value
    return _post('/opportunities/', payload)


def update_opportunity(opportunity_id, **fields):
    """Update a deal — stage, value, status, etc."""
    return _put(f'/opportunities/{opportunity_id}', fields).get('opportunity', {})


def delete_opportunity(opportunity_id):
    """Remove a deal from the pipeline."""
    return _delete(f'/opportunities/{opportunity_id}')


# ═══════════════════════════════════════════════════════════════════════════════
# CALENDARS & APPOINTMENTS
# ═══════════════════════════════════════════════════════════════════════════════

def list_calendars():
    """List all calendars in the location."""
    return _get('/calendars/', params={'locationId': LOCATION_ID}).get('calendars', [])


def get_calendar_events(calendar_id, start_time, end_time):
    """
    Get scheduled events/appointments.
    All three params required by GHL API.
    start_time / end_time: Unix timestamps (ms) or ISO strings.
    """
    params = {
        'locationId': LOCATION_ID,
        'calendarId': calendar_id,
        'startTime':  start_time,
        'endTime':    end_time,
    }
    return _get('/calendars/events', params=params).get('events', [])


def book_appointment(contact_id, calendar_id, start_time, end_time, title=None):
    """Book a follow-up appointment with a seller."""
    payload = {
        'calendarId': calendar_id,
        'locationId': LOCATION_ID,
        'contactId':  contact_id,
        'startTime':  start_time,
        'endTime':    end_time,
        'title':      title or 'Follow-up Call — Shue Box LLC',
    }
    return _post('/calendars/events', payload)


def update_appointment(event_id, **fields):
    """Update an existing appointment."""
    return _put(f'/calendars/events/{event_id}', fields)


def cancel_appointment(event_id):
    """Cancel an appointment."""
    return _delete(f'/calendars/events/{event_id}')


# ═══════════════════════════════════════════════════════════════════════════════
# PHONE NUMBERS  — correct path: /phone-system/numbers/location/{locationId}
# Note: search/purchase/delete not exposed by GHL API — UI only for those.
# ═══════════════════════════════════════════════════════════════════════════════

def list_phone_numbers():
    """List all active phone numbers on the account."""
    data = _get(f'/phone-system/numbers/location/{LOCATION_ID}')
    return data.get('numbers', data.get('phoneNumbers', []))

def search_available_numbers(area_code, limit=5, country='US'):
    """Not exposed by GHL API — purchase numbers in XLeads UI → Settings → Phone Numbers."""
    raise NotImplementedError('Number search/purchase not available via GHL API. Use XLeads UI.')

def buy_phone_number(phone_number, area_code=None):
    raise NotImplementedError('Number purchase not available via GHL API. Use XLeads UI.')

def delete_phone_number(phone_number_id):
    raise NotImplementedError('Number deletion not available via GHL API. Use XLeads UI.')

def get_number_pools():
    raise NotImplementedError('Number pools not available via GHL API.')

def get_twilio_account():
    raise NotImplementedError('Twilio account not available via GHL API.')


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE AI (MAYA)
# ═══════════════════════════════════════════════════════════════════════════════

def list_voice_agents():
    """List all Voice AI agents (includes Maya)."""
    return _get('/voice-ai/agents', params={'locationId': LOCATION_ID}).get('agents', [])


def get_voice_agent(agent_id):
    """Get a specific Voice AI agent config."""
    return _get(f'/voice-ai/agents/{agent_id}').get('agent', {})


def update_voice_agent(agent_id, **fields):
    """
    Update Maya's prompt, name, goals, or settings.
    Common fields: prompt, name, voiceId, firstMessage
    """
    return _put(f'/voice-ai/agents/{agent_id}', fields).get('agent', {})


def get_voice_agent_goals(agent_id):
    """Get goals configured for a Voice AI agent."""
    return _get(f'/voice-ai/agents/{agent_id}/goals').get('goals', [])


def update_voice_agent_goals(agent_id, goals):
    """Update goals for a Voice AI agent. goals: list of goal dicts."""
    return _put(f'/voice-ai/agents/{agent_id}/goals', {'goals': goals})


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATION AI  — correct path: /conversation-ai/agents/ (no locationId param)
# ═══════════════════════════════════════════════════════════════════════════════

def list_conversation_bots():
    """List all Conversation AI agents (SMS bots). No locationId needed."""
    return _get('/conversation-ai/agents/search').get('agents', [])


def get_conversation_bot(bot_id):
    """Get a Conversation AI agent config."""
    return _get(f'/conversation-ai/agents/{bot_id}')


def update_conversation_bot(bot_id, personality, goal, instructions, **fields):
    """
    Update a Conversation AI agent.
    personality, goal, instructions are all required by GHL API.
    """
    payload = {'personality': personality, 'goal': goal, 'instructions': instructions, **fields}
    return _put(f'/conversation-ai/agents/{bot_id}', payload)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT STUDIO
# ═══════════════════════════════════════════════════════════════════════════════

def list_studio_agents():
    """List all agents in Agent Studio."""
    return _get('/agent-studio/agents', params={'locationId': LOCATION_ID}).get('agents', [])


def get_studio_agent(agent_id):
    """Get a specific Agent Studio agent."""
    return _get(f'/agent-studio/agents/{agent_id}').get('agent', {})


def create_studio_agent(name, prompt, **fields):
    """Create a new agent in Agent Studio."""
    payload = {'locationId': LOCATION_ID, 'name': name, 'prompt': prompt, **fields}
    return _post('/agent-studio/agents', payload).get('agent', {})


def update_studio_agent(agent_id, **fields):
    """Update an existing Agent Studio agent."""
    return _put(f'/agent-studio/agents/{agent_id}', fields).get('agent', {})


# ═══════════════════════════════════════════════════════════════════════════════
# DOCUMENTS & CONTRACTS  — correct path: /proposals/
# ═══════════════════════════════════════════════════════════════════════════════

def list_contract_templates():
    """List all contract/proposal templates."""
    return _get('/proposals/templates', params={'locationId': LOCATION_ID}).get('data', [])


def list_contracts():
    """List all sent documents and their e-signature status."""
    return _get('/proposals/document', params={'locationId': LOCATION_ID}).get('documents', [])


def send_contract(document_id, sent_by_user_id, contact_id=None):
    """
    Send an existing document for signature.
    document_id: the _id from list_contracts()
    sent_by_user_id: GHL user ID of the sender (required by API)
    """
    payload = {'documentId': document_id, 'sentBy': sent_by_user_id}
    if contact_id:
        payload['contactId'] = contact_id
    return _post('/proposals/document/send', payload)


def send_contract_from_template(template_id, contact_id, sent_by_user_id, signers=None):
    """
    Create and send a contract from a template.
    sent_by_user_id: GHL user ID of the sender (required).
    """
    payload = {
        'templateId':  template_id,
        'contactId':   contact_id,
        'sentBy':      sent_by_user_id,
        'locationId':  LOCATION_ID,
    }
    if signers:
        payload['signers'] = signers
    return _post('/proposals/document/send', payload)


# ═══════════════════════════════════════════════════════════════════════════════
# CAMPAIGNS
# ═══════════════════════════════════════════════════════════════════════════════

def list_campaigns():
    """List all campaigns and their status."""
    return _get('/campaigns/',
                params={'locationId': LOCATION_ID}).get('campaigns', [])


# ═══════════════════════════════════════════════════════════════════════════════
# TAGS  (location-level)
# ═══════════════════════════════════════════════════════════════════════════════

def list_tags():
    """List all tags defined in the location."""
    return _get(f'/locations/{LOCATION_ID}/tags').get('tags', [])


def create_tag(name):
    """Create a new tag in the location."""
    return _post(f'/locations/{LOCATION_ID}/tags', {'name': name}).get('tag', {})


def delete_tag(tag_id):
    """Delete a tag from the location."""
    return _delete(f'/locations/{LOCATION_ID}/tags/{tag_id}')


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM FIELDS  (location-level)
# ═══════════════════════════════════════════════════════════════════════════════

def list_custom_fields():
    """List all custom fields defined in the location."""
    return _get(f'/locations/{LOCATION_ID}/customFields').get('customFields', [])


def create_custom_field(name, data_type, placeholder=None):
    """
    Create a custom field.
    data_type: TEXT, LARGE_TEXT, NUMERICAL, PHONE, MONETARY, DATE, etc.
    """
    payload = {'name': name, 'dataType': data_type}
    if placeholder:
        payload['placeholder'] = placeholder
    return _post(f'/locations/{LOCATION_ID}/customFields', payload).get('customField', {})


# ═══════════════════════════════════════════════════════════════════════════════
# SOCIAL PLANNER  — correct path: /social-media-posting/{locationId}/
# ═══════════════════════════════════════════════════════════════════════════════

def list_social_accounts():
    """List connected social media accounts."""
    data = _get(f'/social-media-posting/{LOCATION_ID}/accounts')
    return (data.get('results') or {}).get('accounts', [])


def list_social_posts(limit=20):
    """List scheduled and published social media posts."""
    return _get(f'/social-media-posting/{LOCATION_ID}/posts',
                params={'limit': limit, 'skip': 0}).get('posts', [])


def create_social_post(content, account_ids, scheduled_at=None, media_urls=None):
    """
    Schedule a social media post.
    account_ids: list of connected account IDs from list_social_accounts()
    scheduled_at: ISO 8601 datetime string (omit to post immediately)
    """
    payload = {
        'accountIds': account_ids if isinstance(account_ids, list) else [account_ids],
        'post':       {'description': content},
    }
    if scheduled_at:  payload['scheduleDate'] = scheduled_at
    if media_urls:    payload['post']['mediaUrls'] = media_urls
    return _post(f'/social-media-posting/{LOCATION_ID}/posts', payload)
