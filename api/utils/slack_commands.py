"""
ODIN Slack Command Parser
Parses natural-ish text from Slack DMs / @mentions into structured actions.

Supported commands:
  help                                          — list commands
  blast tags:high-equity,tax-delinquent workflow:<id> limit:100
  text <contact_id> <message>
  email <contact_id> subject:<subj> body:<body>
  leads                                         — show hot + warm leads
  approvals                                     — show pending approvals
  approve <approval_id>
  reject <approval_id> [reason]
  ignore <contact_id>                           — tag Do-Not-Contact + remove from workflows
  workflows                                     — list workflow IDs + names
  contacts <search term>
  contact <contact_id>                          — get full contact detail
  contract <contact_id> template:<template_id>
  numbers                                       — list phone numbers
  buy number <area_code>                        — search available numbers
  confirm buy <+1XXXXXXXXXX>                    — purchase a specific number
  analyze <address> [beds:3] [baths:2] [sqft:1200] [zip:38111] [condition:medium]
  score <call notes>                            — MCTP score from notes
  script <seller name> <address>                — generate MCTP call script
  match <address> arv:<amount> assign:<amount>  — find best buyers for a deal
  draft email <seller|buyer> <context>          — draft an email
  content <deal summary or transcript>          — generate LinkedIn posts + email
  tags                                          — show all XLeads tags
  fields                                        — show all XLeads custom fields
  field sync                                    — re-sync custom fields from XLeads API
  lookup <address>                              — Shelby County Assessor property data
  mctp <seller> <address> M:X C:X T:X P:X [notes:<...>]  — log call (Eddie)
"""

import re

# Validated business spoke name lookup — resolves #N references in plan/income/build/audit commands
BUSINESS_SPOKE_NAMES = {
    1: 'ODIN Licensing',
    2: 'AI Appointment Setting Agency',
    3: 'AI Voice Agent Service',
    4: 'AI Business Plan Generator',
    5: 'AI Customer Support Setup',
    6: 'AI Podcast Production Service',
    7: 'Productized Content Engine',
    8: 'Local Business Reputation Management',
    9: 'AI Job Application Service',
    10: 'Shopify Digital Products Store',
    11: 'AI Newsletter Business',
}


def _resolve_business_name(text: str) -> str:
    """
    If text starts with '#N' or 'number N', resolve to the known business name.
    Returns the resolved name or the original text unchanged.
    """
    m = re.match(r'^#?(\d{1,2})\b', text.strip())
    if m:
        num = int(m.group(1))
        if num in BUSINESS_SPOKE_NAMES:
            return BUSINESS_SPOKE_NAMES[num]
    return text


# Keywords that trigger auto-ignore (negative replies from sellers)
NEGATIVE_KEYWORDS = [
    'stop', 'unsubscribe', 'remove me', 'not interested', 'wrong number',
    'do not contact', 'take me off', 'quit', 'cancel', 'no thanks',
    'no thank you', 'leave me alone', 'opt out', 'optout',
]

HELP_TEXT = """:robot_face: *ODIN Commands*

*Blasting*
`blast tags:high-equity,tax-delinquent workflow:<id> limit:100`
`blast query:"Memphis 38111" workflow:<id>`

*Messaging*
`text <contact_id> Hey, following up on your property at 123 Main...`
`email <contact_id> subject:Our Offer body:We'd like to offer $47k...`

*Leads & Pipeline*
`leads` — show hot + warm leads
`approvals` — show pending approvals
`approve <approval_id>`
`reject <approval_id> not motivated enough`

*Contacts*
`contacts high equity memphis`
`contact <contact_id>`
`ignore <contact_id>` — tag Do-Not-Contact + remove from all workflows

*Contracts*
`contract <contact_id> template:<template_id>`

*Buyers*
`buyers` — show buyer count + top Memphis flippers
`buyers flipper` — active flippers only
`buyers memphis` — Memphis metro buyers
`buyers <name or city>` — search buyers

*Workflows & Numbers*
`workflows` — list XLeads workflows + ODIN registry
`workflow add name:<n> id:<xleads_id> purpose:<desc>` — register a workflow
`workflow add name:<n> purpose:<desc>` — log a workflow (no GHL id yet)
`numbers` — list your phone numbers
`buy number 901` — search available 901 numbers
`confirm buy +19015551234` — purchase that number (charges apply)

*RE Skills*
`analyze 4314 Leatherwood Memphis beds:3 baths:2 sqft:1534 zip:38111 condition:medium`
`score Called seller, motivated divorce, needs out in 30 days, wants $80k`
`script John 4314 Leatherwood`
`match 4314 Leatherwood arv:165000 assign:90000`
`draft email seller John, called yesterday, interested in selling probate property`
`content Seller accepted $55k offer on a 3/2 in 38111, ARV $165k, fee $22k`
`lookup 4314 Leatherwood Ave Memphis` — Shelby County Assessor owner + value

*Call Logging (Eddie)*
`mctp John 4314 Leatherwood M:2 C:1 T:2 P:1 notes:Motivated divorce wants 90k`
  M=Motivation(0-3) C=Condition(0-2) T=Timeline(0-3) P=Price(0-2)

*Calendar & Email*
`schedule John 4314 Leatherwood tomorrow 2pm` — add seller appointment to Google Calendar
`schedule John 4314 Leatherwood Friday 10am notes:wants 85k`
`calendar today` / `calendar week` — view appointments
`gmail John 4314 Leatherwood Ave` — draft + send seller follow-up from shueboxllc@gmail.com
`gmail custom to:email@x.com subject:Offer body:We'd like to offer $47k`
`gmail inbox` — show recent inbox
`email triage` — autonomous inbox: classify + draft replies (auto-send low-risk if enabled)
`call me` / `brief me` — ODIN phones you and reads your briefing aloud (Twilio)

*XLeads Data*
`tags` — show all tags in your XLeads account
`fields` — show all custom fields
`field sync` — re-sync custom fields from XLeads API

*Business Agents* _(Brock + Katelyn)_
`ideas` — 5 highly automatable business ideas (ODIN picks for you)
`ideas <context>` — ideas filtered by your preference (e.g. `ideas low startup creative`)
`more ideas` — 5 different ideas (keeps going until you find one you like)
`plan <business>` — full analysis + PDF business plan (scout → income → builder → auditor)
`scout printables etsy` — score + rank business ideas
`income <business> hours:10 budget:200` — revenue model
`build <business>` — step-by-step task breakdown
`audit <business>` — automation map + ODIN build list

*Custom Agents*
`add agent <description>` — build a new agent from plain English
`add bot <description>` — same as add agent
`agents` — list all custom agents + their trigger keywords
`disable agent <name>` — turn off an agent

*Logs & Diagnostics*
`logs` — last 25 agent runs (all agents)
`logs errors` — only failed runs with error details
`logs <agent_name>` — runs for a specific agent (e.g. `logs scout`)

*IT Support* _(Brock + Katelyn)_
`debug <problem description>` — diagnose + fix any issue
`tech <problem>` — same as debug
Examples:
  `debug ODIN returns 502 when I submit a lead`
  `debug my Etsy shop got suspended`
  `debug XLeads SMS not sending`

*System*
`status` — pipeline snapshot, integration health, overdue follow-ups
`status 4314 Leatherwood` — one-line deal status for a specific lead
`follow up 4314 Leatherwood date:2026-06-01 action:Call back re: price cid:<xleads_id>` — update lead follow-up (cid links XLeads contact for auto-SMS)
`spokes` — list all active business spokes

*Task Delegation*
`tasks` — all pending/in-progress tasks with assignee (brock/eddie/odin)
`tasks eddie` / `tasks odin` / `tasks brock` — filter by assignee
`task done <title keywords>` — mark a task completed
`assign task <id or title> to <brock/eddie/odin>` — reassign a task

*Buyer Onboarding*
`buyers onboarding` — pipeline by stage (new/welcomed/qualified/active/inactive)
`onboard <name> cid:<xleads_contact_id>` — link buyer to XLeads + trigger welcome SMS

*CEO Tools*
`review` — full weekly CEO review (pipeline, Eddie activity, priorities, The One Thing)
`stats` / `stats week` / `stats month` / `stats spoke:real_estate` — full deal funnel
`resurrect` — dead/cold leads not contacted in 30+ days with re-engagement texts
`scorecard 4314 Leatherwood arv:165000 beds:3 baths:2 sqft:1400 zip:38111 condition:medium` — pre-offer due diligence

*Finance*
`finance` — full dashboard (balances, spend, subscriptions, biz card)
`finance sync` — pull fresh data from Teller.io + detect subscriptions
`balance` / `balances` — all account balances + net liquid
`subscriptions` — list all recurring charges detected
`spending` / `spending week` / `spending ytd` — spend breakdown by category
`biz card` — business card status (available balance, used %)
`invest` — AI analysis: best ROI use of available biz card balance
`mark biz card <name>` — designate which account is the $5k biz card

_Tip: you can @mention ODIN in a channel or DM directly._"""


def is_negative_reply(message: str) -> bool:
    """Return True if an inbound SMS looks like a negative/opt-out reply."""
    msg = message.lower().strip()
    return any(kw in msg for kw in NEGATIVE_KEYWORDS)


def parse_command(text: str) -> dict:
    """
    Parse a Slack message into { 'action': str, ...params }.
    Strips @mention prefix automatically.
    """
    # Strip @mention (e.g. <@U12345>)
    text = re.sub(r'<@\w+>', '', text).strip()
    text_lower = text.lower()

    # ── HELP ──────────────────────────────────────────────────────────────────
    if text_lower in ('help', '?', 'commands', 'odin help'):
        return {'action': 'help'}

    # ── EMAIL TRIAGE (autonomous inbox) ───────────────────────────────────────
    # Must come before the generic `email <id>` handler below.
    if text_lower in ('email triage', 'triage email', 'triage inbox', 'check inbox', 'inbox'):
        return {'action': 'email_triage'}

    # ── OUTBOUND VOICE BRIEFING (ODIN calls Brock) ────────────────────────────
    if text_lower in ('call me', 'brief me', 'voice briefing', 'phone briefing', 'call'):
        return {'action': 'call_me'}

    # ── DEBUG BLAST ───────────────────────────────────────────────────────────
    if text_lower.startswith('debug blast'):
        tags_m  = re.search(r'tags?:([\w,\-\.]+)', text_lower)
        return {
            'action': 'debug_blast',
            'tags':   tags_m.group(1).split(',') if tags_m else ['he', 'tax-delinquent'],
        }

    # ── BLAST STATS ───────────────────────────────────────────────────────────
    if text_lower in ('blast stats', 'blast results', 'campaign stats', 'sms stats'):
        return {'action': 'blast_stats', 'campaign_id': None}

    if text_lower.startswith('blast stats ') or text_lower.startswith('blast results '):
        parts = text.split(None, 2)
        return {'action': 'blast_stats', 'campaign_id': parts[2] if len(parts) > 2 else None}

    # ── BLAST ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('blast'):
        tags_m    = re.search(r'tags?:([\w,\-\.]+)', text_lower)
        wf_m      = re.search(r'workflow[:\s]+(\S+)', text, re.IGNORECASE)
        limit_m   = re.search(r'limit[:\s]+(\d+)', text_lower)
        query_m   = re.search(r'query[:\s]+"([^"]+)"', text, re.IGNORECASE)
        return {
            'action':      'blast',
            'tags':        tags_m.group(1).split(',') if tags_m else None,
            'workflow_id': wf_m.group(1) if wf_m else None,
            'limit':       int(limit_m.group(1)) if limit_m else 100,
            'query':       query_m.group(1) if query_m else None,
        }

    # ── TEXTBLAST (direct SMS, no workflow) ──────────────────────────────────
    if text_lower.startswith('textblast'):
        tags_m  = re.search(r'tags?:([\w,\-\.]+)', text_lower)
        limit_m = re.search(r'limit[:\s]+(\d+)', text_lower)
        msg_m   = re.search(r'msg[:\s]+"([^"]+)"', text, re.IGNORECASE)
        return {
            'action':  'textblast',
            'tags':    tags_m.group(1).split(',') if tags_m else None,
            'limit':   int(limit_m.group(1)) if limit_m else 100,
            'message': msg_m.group(1) if msg_m else None,
        }

    # ── TEXT (SMS) ────────────────────────────────────────────────────────────
    if text_lower.startswith('text '):
        parts = text.split(None, 2)
        if len(parts) >= 3:
            return {'action': 'sms', 'contact_id': parts[1], 'message': parts[2]}
        return {'action': 'error', 'msg': 'Usage: `text <contact_id> <message>`'}

    # ── EMAIL ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('email '):
        parts = text.split(None, 2)
        if len(parts) < 2:
            return {'action': 'error', 'msg': 'Usage: `email <contact_id> subject:... body:...`'}
        contact_id = parts[1]
        rest       = parts[2] if len(parts) > 2 else ''
        subj_m = re.search(r'subject[:\s]+(.+?)(?:\s+body[:\s]|$)', rest, re.IGNORECASE | re.DOTALL)
        body_m = re.search(r'body[:\s]+(.+)$', rest, re.IGNORECASE | re.DOTALL)
        return {
            'action':     'email',
            'contact_id': contact_id,
            'subject':    subj_m.group(1).strip() if subj_m else 'Message from Shue Box LLC',
            'body':       body_m.group(1).strip() if body_m else rest,
        }

    # ── DECISIONS ─────────────────────────────────────────────────────────────
    if text_lower in ('decisions', 'decision queue', 'decide', 'decision'):
        return {'action': 'decisions'}

    if text_lower.startswith('approve decision ') or text_lower.startswith('approve '):
        parts = text.split(None, 2)
        did = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else None)
        # strip leading "decision" word if present
        if did and did.lower().startswith('decision'):
            did = did[8:].strip()
        return {'action': 'approve_decision', 'decision_id': did}

    if text_lower.startswith('decline decision ') or text_lower.startswith('decline ') or text_lower.startswith('skip '):
        parts = text.split(None, 2)
        did = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else None)
        if did and did.lower().startswith('decision'):
            did = did[8:].strip()
        return {'action': 'decline_decision', 'decision_id': did}

    if text_lower.startswith('log decision ') or text_lower.startswith('add decision '):
        rest    = re.sub(r'^(log|add)\s+decision\s+', '', text, flags=re.IGNORECASE).strip()
        spoke_m = re.search(r'spoke[:\s]+([\w_]+)', rest, re.IGNORECASE)
        issue_m = re.search(r'issue[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        opt1_m  = re.search(r'option1?[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        opt2_m  = re.search(r'option2[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        opt3_m  = re.search(r'option3[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        rec_m   = re.search(r'rec(?:ommend)?[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        reason_m= re.search(r'reason[:\s]+"([^"]+)"', rest, re.IGNORECASE)
        # fallback: if no issue: tag, treat whole rest as the issue
        issue = issue_m.group(1) if issue_m else rest[:200]
        return {
            'action':      'log_decision',
            'spoke':       spoke_m.group(1) if spoke_m else 'general',
            'issue':       issue,
            'option_1':    opt1_m.group(1)  if opt1_m  else None,
            'option_2':    opt2_m.group(1)  if opt2_m  else None,
            'option_3':    opt3_m.group(1)  if opt3_m  else None,
            'recommended': rec_m.group(1)   if rec_m   else None,
            'reason':      reason_m.group(1) if reason_m else None,
        }

    # ── REVENUE ───────────────────────────────────────────────────────────────
    if text_lower in ('revenue', 'revenue mtd', 'p&l', 'income'):
        return {'action': 'revenue'}

    if text_lower in ('revenue sync', 'sync revenue', 'sync deals', 'pull deals'):
        return {'action': 'revenue_sync'}

    if text_lower.startswith('revenue log '):
        rest    = text[len('revenue log '):].strip()
        spoke_m  = re.search(r'spoke[:\s]+([\w_]+)', rest, re.IGNORECASE)
        amt_m    = re.search(r'amount[:\s]+([\d,\.]+)', rest, re.IGNORECASE)
        type_m   = re.search(r'type[:\s]+(\w+)', rest, re.IGNORECASE)
        desc_m   = re.search(r'desc(?:ription)?[:\s]+(.+?)(?:\s+\w+:|$)', rest, re.IGNORECASE | re.DOTALL)
        amt_str  = (amt_m.group(1) if amt_m else '0').replace(',', '')
        return {
            'action':      'revenue_log',
            'spoke':       spoke_m.group(1) if spoke_m else 'general',
            'amount':      float(amt_str),
            'type':        type_m.group(1).lower() if type_m else 'income',
            'description': desc_m.group(1).strip() if desc_m else rest[:100],
        }

    # ── OFFER GENERATION ──────────────────────────────────────────────────────
    if text_lower.startswith('draft offer ') or text_lower.startswith('offer draft '):
        address = re.sub(r'^(draft offer|offer draft)\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'draft_offer', 'address': address}

    if text_lower.startswith('offer status ') or text_lower.startswith('offer '):
        address = re.sub(r'^offer (status\s+)?', '', text, flags=re.IGNORECASE).strip()
        if address and not address.startswith('draft'):
            return {'action': 'offer_status', 'address': address}

    # ── SCORECARD (KPI targets) ────────────────────────────────────────────────
    if text_lower in ('scorecard', 'kpis', 'kpi status', 'targets'):
        return {'action': 'kpi_scorecard'}

    # ── LEADS ─────────────────────────────────────────────────────────────────
    if any(x in text_lower for x in ('hot leads', 'warm leads', 'leads', 'pipeline')):
        return {'action': 'leads'}

    # ── APPROVALS ─────────────────────────────────────────────────────────────
    if text_lower.startswith('approval'):
        return {'action': 'approvals'}

    # ── APPROVE ───────────────────────────────────────────────────────────────
    if text_lower.startswith('approve '):
        parts = text.split(None, 2)
        return {
            'action':      'approve',
            'approval_id': parts[1] if len(parts) > 1 else None,
            'notes':       parts[2] if len(parts) > 2 else '',
        }

    # ── REJECT ────────────────────────────────────────────────────────────────
    if text_lower.startswith('reject '):
        parts = text.split(None, 2)
        return {
            'action':      'reject',
            'approval_id': parts[1] if len(parts) > 1 else None,
            'notes':       parts[2] if len(parts) > 2 else '',
        }

    # ── IGNORE (do-not-contact) ───────────────────────────────────────────────
    if text_lower.startswith('ignore '):
        parts = text.split(None, 1)
        return {'action': 'ignore', 'contact_id': parts[1].strip() if len(parts) > 1 else None}

    # ── CONTRACT ──────────────────────────────────────────────────────────────
    if text_lower.startswith('contract'):
        parts      = text.split(None, 2)
        template_m = re.search(r'template[:\s]+(\S+)', text, re.IGNORECASE)
        return {
            'action':      'contract',
            'contact_id':  parts[1] if len(parts) > 1 else None,
            'template_id': template_m.group(1) if template_m else None,
        }

    # ── BUYERS ────────────────────────────────────────────────────────────────
    if text_lower.startswith('buyers'):
        rest = text[6:].strip()
        return {'action': 'buyers', 'query': rest or None}

    # ── WORKFLOW ADD ──────────────────────────────────────────────────────────
    if text_lower.startswith('workflow add'):
        rest       = text[len('workflow add'):].strip()
        name_m     = re.search(r'name[:\s]+(\S+)', rest, re.IGNORECASE)
        xid_m      = re.search(r'id[:\s]+(\S+)', rest, re.IGNORECASE)
        purpose_m  = re.search(r'purpose[:\s]+(.+?)(?:\s+(?:id|name|trigger|status)[:\s]|$)',
                                rest, re.IGNORECASE | re.DOTALL)
        trigger_m  = re.search(r'trigger[:\s]+(\S+)', rest, re.IGNORECASE)
        notes_m    = re.search(r'notes?[:\s]+(.+?)$', rest, re.IGNORECASE | re.DOTALL)
        return {
            'action':    'workflow_add',
            'name':      name_m.group(1).strip() if name_m else None,
            'xleads_id': xid_m.group(1).strip() if xid_m else None,
            'purpose':   purpose_m.group(1).strip() if purpose_m else None,
            'trigger':   trigger_m.group(1).strip() if trigger_m else 'manual',
            'notes':     notes_m.group(1).strip() if notes_m else None,
        }

    # ── WORKFLOWS LIST ────────────────────────────────────────────────────────
    if 'workflow' in text_lower:
        return {'action': 'workflows'}

    # ── STATUS (system health) / DEAL STATUS (address lookup) ────────────────
    if text_lower.startswith('status'):
        rest = text[6:].strip()
        if not rest:
            return {'action': 'status'}
        return {'action': 'deal_status', 'address': rest}

    # ── TASK DELEGATION ───────────────────────────────────────────────────────
    if text_lower.startswith('task done') or text_lower.startswith('tasks done'):
        rest = re.sub(r'^tasks?\s+done\s*', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'task_done', 'query': rest}

    if text_lower.startswith('assign task'):
        rest    = re.sub(r'^assign\s+task\s+', '', text, flags=re.IGNORECASE).strip()
        to_m    = re.search(r'\bto\s+(\S+)', rest, re.IGNORECASE)
        task_q  = re.sub(r'\s+to\s+\S+\s*$', '', rest, flags=re.IGNORECASE).strip()
        return {
            'action':    'assign_task',
            'query':     task_q,
            'assignee':  to_m.group(1).lower().strip() if to_m else None,
        }

    if text_lower.startswith('tasks') or text_lower.strip() == 'tasks':
        rest     = re.sub(r'^tasks\s*', '', text, flags=re.IGNORECASE).strip()
        filter_m = re.search(r'\b(brock|eddie|odin)\b', rest, re.IGNORECASE)
        return {
            'action':   'tasks',
            'assignee': filter_m.group(1).lower() if filter_m else None,
        }

    # ── SPOKES ────────────────────────────────────────────────────────────────
    if text_lower.strip() in ('spokes', 'spoke list', 'my spokes'):
        return {'action': 'spokes'}

    # ── BUYER ONBOARDING ─────────────────────────────────────────────────────
    if text_lower.startswith('onboard '):
        rest  = text[8:].strip()
        cid_m = re.search(r'cid[:\s]+(\S+)', rest, re.IGNORECASE)
        name  = re.sub(r'\s+cid[:\s]+\S+', '', rest, flags=re.IGNORECASE).strip()
        return {
            'action':            'onboard_buyer',
            'name':              name,
            'xleads_contact_id': cid_m.group(1) if cid_m else None,
        }

    if text_lower.strip() in ('buyers onboarding', 'onboarding', 'buyer onboarding'):
        return {'action': 'buyers_onboarding'}

    # ── REVIEW (weekly CEO review — on-demand) ────────────────────────────────
    if text_lower.strip() in ('review', 'ceo review', 'weekly review'):
        return {'action': 'review'}

    # ── STATS (funnel stats) ──────────────────────────────────────────────────
    if text_lower.startswith('stats'):
        rest    = text[5:].strip()
        period_m = re.search(r'\b(week|month|day)\b', rest, re.IGNORECASE)
        spoke_m  = re.search(r'spoke[:\s]+(\S+)', rest, re.IGNORECASE)
        return {
            'action': 'stats',
            'period': period_m.group(1).lower() if period_m else 'week',
            'spoke':  spoke_m.group(1) if spoke_m else 'real_estate',
        }

    # ── RESURRECT (dead lead re-engagement) ──────────────────────────────────
    if text_lower.strip().startswith('resurrect'):
        return {'action': 'resurrect'}

    # ── SCORECARD (pre-offer due diligence) ──────────────────────────────────
    if text_lower.startswith('scorecard '):
        rest    = text[10:].strip()
        arv_m   = re.search(r'arv[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        beds_m  = re.search(r'beds?[:\s]+(\d)', rest, re.IGNORECASE)
        baths_m = re.search(r'baths?[:\s]+([\d.]+)', rest, re.IGNORECASE)
        sqft_m  = re.search(r'sqft[:\s]+(\d+)', rest, re.IGNORECASE)
        zip_m   = re.search(r'zip[:\s]+(\d{5})', rest, re.IGNORECASE)
        cond_m  = re.search(r'condition[:\s]+(\w+)', rest, re.IGNORECASE)
        year_m  = re.search(r'year[:\s]+(\d{4})', rest, re.IGNORECASE)
        addr_raw = re.split(r'\s+(?:arv|beds?|baths?|sqft|zip|condition|year)[:\s]',
                            rest, flags=re.IGNORECASE)[0].strip()
        return {
            'action':    'scorecard',
            'address':   addr_raw,
            'arv':       int(arv_m.group(1).replace(',', '')) if arv_m else 0,
            'beds':      int(beds_m.group(1)) if beds_m else 3,
            'baths':     float(baths_m.group(1)) if baths_m else 2.0,
            'sqft':      int(sqft_m.group(1)) if sqft_m else 0,
            'zip_code':  zip_m.group(1) if zip_m else '',
            'condition': cond_m.group(1).lower() if cond_m else 'unknown',
            'year_built': int(year_m.group(1)) if year_m else 0,
        }

    # ── FOLLOW UP ─────────────────────────────────────────────────────────────
    if text_lower.startswith('follow up') or text_lower.startswith('followup'):
        rest      = re.sub(r'^follow\s*up\s*', '', text, flags=re.IGNORECASE).strip()
        date_m    = re.search(r'date[:\s]+([\d-]+)', rest, re.IGNORECASE)
        action_m  = re.search(r'action[:\s]+(.+?)(?:\s+(?:date|cid)[:\s]|$)', rest, re.IGNORECASE | re.DOTALL)
        cid_m     = re.search(r'cid[:\s]+(\S+)', rest, re.IGNORECASE)
        # Address = everything before the first known keyword
        addr_raw  = re.split(r'\s+(?:date|action|cid)[:\s]', rest, flags=re.IGNORECASE)[0].strip()
        return {
            'action':             'followup',
            'address':            addr_raw,
            'date':               date_m.group(1).strip() if date_m else None,
            'next_action':        action_m.group(1).strip() if action_m else None,
            'xleads_contact_id':  cid_m.group(1).strip() if cid_m else None,
        }

    # ── CONTACTS SEARCH ───────────────────────────────────────────────────────
    if text_lower.startswith(('contacts ', 'search ', 'find ')) and \
            not text_lower.startswith(('find knowledge ', 'knowledge find ')):
        query = re.sub(r'^(contacts|search|find)\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'contacts', 'query': query}

    # ── CONTACT DETAIL ────────────────────────────────────────────────────────
    if text_lower.startswith('contact '):
        parts = text.split(None, 1)
        return {'action': 'contact', 'contact_id': parts[1].strip() if len(parts) > 1 else None}

    # ── ANALYZE (full deal analysis) ─────────────────────────────────────────
    if text_lower.startswith('analyze '):
        rest    = text[8:].strip()
        beds_m  = re.search(r'beds?[:\s]+(\d)', rest, re.IGNORECASE)
        baths_m = re.search(r'baths?[:\s]+([\d.]+)', rest, re.IGNORECASE)
        sqft_m  = re.search(r'sqft[:\s]+(\d+)', rest, re.IGNORECASE)
        zip_m   = re.search(r'zip[:\s]+(\d{5})', rest, re.IGNORECASE)
        cond_m  = re.search(r'condition[:\s]+(\w+)', rest, re.IGNORECASE)
        rehab_m = re.search(r'rehab[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        fee_m   = re.search(r'fee[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        # Address = everything before the first param keyword
        addr_raw = re.split(r'\s+(?:beds?|baths?|sqft|zip|condition|rehab|fee)[:\s]',
                            rest, flags=re.IGNORECASE)[0].strip()
        return {
            'action':    'analyze',
            'address':   addr_raw,
            'beds':      int(beds_m.group(1)) if beds_m else 3,
            'baths':     float(baths_m.group(1)) if baths_m else 2.0,
            'sqft':      int(sqft_m.group(1)) if sqft_m else 0,
            'zip_code':  zip_m.group(1) if zip_m else '',
            'condition': cond_m.group(1).lower() if cond_m else 'unknown',
            'rehab':     float(rehab_m.group(1).replace(',','')) if rehab_m else None,
            'target_fee':float(fee_m.group(1).replace(',','')) if fee_m else 20000.0,
        }

    # ── SCORE (MCTP from notes) ───────────────────────────────────────────────
    if text_lower.startswith('score '):
        notes = text[6:].strip()
        addr_m = re.search(r'address[:\s]+([^,\n]+)', notes, re.IGNORECASE)
        name_m = re.search(r'(?:seller|name)[:\s]+([^,\n]+)', notes, re.IGNORECASE)
        return {
            'action':  'score',
            'notes':   notes,
            'address': addr_m.group(1).strip() if addr_m else '',
            'name':    name_m.group(1).strip() if name_m else '',
        }

    # ── SCRIPT (call script generator) ───────────────────────────────────────
    if text_lower.startswith('script '):
        parts   = text[7:].strip().split(None, 1)
        seller  = parts[0] if parts else ''
        address = parts[1] if len(parts) > 1 else ''
        return {'action': 'script', 'seller_name': seller, 'address': address}

    # ── MATCH (buyer matcher) ─────────────────────────────────────────────────
    if text_lower.startswith('match '):
        rest     = text[6:].strip()
        arv_m    = re.search(r'arv[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        assign_m = re.search(r'assign[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        fee_m    = re.search(r'fee[:\s]+\$?([\d,]+)', rest, re.IGNORECASE)
        zip_m    = re.search(r'zip[:\s]+(\d{5})', rest, re.IGNORECASE)
        addr_raw = re.split(r'\s+(?:arv|assign|fee|zip)[:\s]', rest, flags=re.IGNORECASE)[0].strip()
        return {
            'action':       'match',
            'address':      addr_raw,
            'arv_mid':      int(arv_m.group(1).replace(',','')) if arv_m else 0,
            'assign_price': int(assign_m.group(1).replace(',','')) if assign_m else 0,
            'fee_at_lao':   int(fee_m.group(1).replace(',','')) if fee_m else 0,
            'zip_code':     zip_m.group(1) if zip_m else '',
        }

    # ── DRAFT EMAIL ───────────────────────────────────────────────────────────
    if text_lower.startswith('draft email') or text_lower.startswith('draft seller') \
            or text_lower.startswith('draft buyer'):
        rest = re.sub(r'^draft\s+(email\s+)?', '', text, flags=re.IGNORECASE).strip()
        type_m = re.match(r'^(seller|buyer|partner)\s*', rest, re.IGNORECASE)
        rtype  = type_m.group(1).lower() if type_m else 'general'
        rtype_map = {'seller': 'seller_followup', 'buyer': 'buyer_outreach',
                     'partner': 'partner_outreach'}
        context = rest[type_m.end():].strip() if type_m else rest
        name_m  = re.match(r'^(\w+),?\s+', context)
        name    = name_m.group(1) if name_m else ''
        return {
            'action':         'draft_email',
            'recipient_type': rtype_map.get(rtype, 'general'),
            'recipient_name': name,
            'context':        context,
        }

    # ── CONTENT ENGINE ────────────────────────────────────────────────────────
    if text_lower.startswith('content '):
        source = text[8:].strip()
        return {'action': 'content', 'source': source}

    # ── PROPERTY LOOKUP ───────────────────────────────────────────────────────
    if text_lower.startswith('lookup ') or text_lower.startswith('property '):
        address = re.sub(r'^(lookup|property)\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'lookup', 'address': address}

    # ── SCHEDULE (Google Calendar) ────────────────────────────────────────────
    if text_lower.startswith('schedule '):
        rest     = text[9:].strip()
        notes_m  = re.search(r'notes?[:\s]+(.+)$', rest, re.IGNORECASE | re.DOTALL)
        notes    = notes_m.group(1).strip() if notes_m else ''
        rest_clean = re.sub(r'\s+notes?[:\s]+.+$', '', rest, flags=re.IGNORECASE).strip()
        # Time keywords: tomorrow, Monday, Friday, today + time like 2pm, 10:30am
        time_m   = re.search(
            r'(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
            r'\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm))',
            rest_clean, re.IGNORECASE
        )
        time_str = time_m.group(0) if time_m else ''
        rest_no_time = re.sub(
            r'\s*(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
            r'\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)', '',
            rest_clean, flags=re.IGNORECASE
        ).strip()
        # First word = seller name, rest = address
        parts   = rest_no_time.split(None, 1)
        seller  = parts[0] if parts else ''
        address = parts[1].strip() if len(parts) > 1 else ''
        return {
            'action':      'schedule',
            'seller_name': seller,
            'address':     address,
            'time_str':    time_str,
            'notes':       notes,
        }

    # ── CALENDAR ──────────────────────────────────────────────────────────────
    if text_lower.startswith('calendar') or text_lower in ('my calendar', 'appointments'):
        if 'week' in text_lower:
            return {'action': 'calendar', 'view': 'week'}
        return {'action': 'calendar', 'view': 'today'}

    # ── GMAIL ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('gmail ') or text_lower.startswith('send email '):
        rest = re.sub(r'^(gmail|send email)\s+', '', text, flags=re.IGNORECASE).strip()
        # Custom: gmail custom to:email subject:... body:...
        if rest.lower().startswith('custom'):
            rest2   = rest[6:].strip()
            to_m    = re.search(r'to[:\s]+([\w@.\-+]+)', rest2, re.IGNORECASE)
            subj_m  = re.search(r'subject[:\s]+(.+?)(?:\s+body[:\s]|$)', rest2, re.IGNORECASE|re.DOTALL)
            body_m  = re.search(r'body[:\s]+(.+)$', rest2, re.IGNORECASE|re.DOTALL)
            return {
                'action':  'gmail_send',
                'mode':    'custom',
                'to':      to_m.group(1) if to_m else '',
                'subject': subj_m.group(1).strip() if subj_m else '',
                'body':    body_m.group(1).strip() if body_m else rest2,
            }
        # Inbox: gmail inbox [query]
        if rest.lower().startswith('inbox'):
            query = rest[5:].strip()
            return {'action': 'gmail_inbox', 'query': query}
        # Default: gmail <seller name> <address> — draft seller follow-up
        parts   = rest.split(None, 1)
        seller  = parts[0] if parts else ''
        address = parts[1].strip() if len(parts) > 1 else ''
        return {'action': 'gmail_send', 'mode': 'seller', 'seller': seller, 'address': address}

    # ── TAGS ──────────────────────────────────────────────────────────────────
    if text_lower.rstrip() in ('tags', 'tag list', 'list tags', 'show tags'):
        return {'action': 'tags'}

    # ── FIELDS / CUSTOM FIELDS ────────────────────────────────────────────────
    if text_lower.startswith('field sync') or text_lower == 'sync fields':
        return {'action': 'field_sync'}

    if text_lower.rstrip() in ('fields', 'custom fields', 'field list', 'list fields'):
        return {'action': 'fields'}

    # ── MCTP LOG (Eddie / callers) ────────────────────────────────────────────
    # Format: mctp <seller_name> <address> M:X C:X T:X P:X [notes:<...>]
    if text_lower.startswith('mctp '):
        rest  = text[5:].strip()
        m_m   = re.search(r'\bM[:\s]+(\d)', rest, re.IGNORECASE)
        c_m   = re.search(r'\bC[:\s]+(\d)', rest, re.IGNORECASE)
        t_m   = re.search(r'\bT[:\s]+(\d)', rest, re.IGNORECASE)
        p_m   = re.search(r'\bP[:\s]+(\d)', rest, re.IGNORECASE)
        notes_m = re.search(r'notes?[:\s]+(.+)$', rest, re.IGNORECASE | re.DOTALL)
        # Strip scores + notes from rest to get seller + address
        stripped = re.sub(r'\s+[MCTP][:\s]+\d', '', rest, flags=re.IGNORECASE)
        stripped = re.sub(r'\s+notes?[:\s]+.+$', '', stripped, flags=re.IGNORECASE | re.DOTALL).strip()
        # First word = seller name, remainder = address
        parts = stripped.split(None, 1)
        seller  = parts[0] if parts else ''
        address = parts[1].strip() if len(parts) > 1 else ''
        return {
            'action':           'mctp_log',
            'seller_name':      seller,
            'address':          address,
            'motivation_score': int(m_m.group(1)) if m_m else 0,
            'condition_score':  int(c_m.group(1)) if c_m else 0,
            'timeline_score':   int(t_m.group(1)) if t_m else 0,
            'price_score':      int(p_m.group(1)) if p_m else 0,
            'notes':            notes_m.group(1).strip() if notes_m else '',
        }

    # ── CONFIRM BUY (must check before buy_number to avoid partial match) ────
    if text_lower.startswith('confirm buy') or text_lower.startswith('confirm purchase'):
        num_m = re.search(r'(\+1\d{10}|\+\d{10,15})', text)
        return {'action': 'confirm_buy', 'phone_number': num_m.group(1) if num_m else None}

    # ── BUY NUMBER ────────────────────────────────────────────────────────────
    if 'buy number' in text_lower or 'buy a number' in text_lower:
        ac_m = re.search(r'\b(\d{3})\b', text)
        return {'action': 'buy_number', 'area_code': ac_m.group(1) if ac_m else None}

    # ── LIST NUMBERS ──────────────────────────────────────────────────────────
    if text_lower in ('numbers', 'phone numbers', 'my numbers'):
        return {'action': 'numbers'}

    # ── MAYA ──────────────────────────────────────────────────────────────────
    if text_lower.startswith('maya'):
        # maya          → show current config
        # maya update   → push official prompt to XLeads
        if 'update' in text_lower or 'set' in text_lower or 'push' in text_lower:
            return {'action': 'maya_update'}
        return {'action': 'maya_status'}

    # ── DEBUG / IT SUPPORT ────────────────────────────────────────────────────
    if text_lower.startswith(('debug ', 'tech ', 'fix ', 'help me ', 'broken ', 'error ')):
        problem = re.sub(r'^(debug|tech|fix|help me|broken|error)\s+', '', text,
                         flags=re.IGNORECASE).strip()
        return {'action': 'debug', 'problem': problem}

    # ── SCOUT ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('scout'):
        keywords = re.sub(r'^scout\s*', '', text, flags=re.IGNORECASE).strip()
        budget_m = re.search(r'budget[:\s]+\$?(\d+)', text_lower)
        hours_m  = re.search(r'hours?[:\s]+(\d+)', text_lower)
        return {
            'action':   'scout',
            'keywords': keywords,
            'budget':   int(budget_m.group(1)) if budget_m else 500,
            'hours':    int(hours_m.group(1)) if hours_m else 10,
        }

    # ── INCOME ────────────────────────────────────────────────────────────────
    if text_lower.startswith('income '):
        business = re.sub(r'^income\s+', '', text, flags=re.IGNORECASE).strip()
        hours_m  = re.search(r'hours?[:\s]+(\d+)', business)
        budget_m = re.search(r'budget[:\s]+\$?(\d+)', business)
        # Strip params from business name
        business = re.sub(r'\s*(hours?|budget)[:\s]+\S+', '', business, flags=re.IGNORECASE).strip()
        business = _resolve_business_name(business)
        return {
            'action':   'income',
            'business': business,
            'hours':    int(hours_m.group(1)) if hours_m else 10,
            'budget':   int(budget_m.group(1)) if budget_m else 200,
        }

    # ── IDEAS (highly automatable business ideas for Brock or Katelyn) ──────────
    if text_lower == 'more ideas':
        return {'action': 'ideas', 'keywords': '', 'variation': True}
    if text_lower.startswith('more ideas '):
        kw = re.sub(r'^more ideas\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'ideas', 'keywords': kw, 'variation': True}
    if text_lower == 'ideas':
        return {'action': 'ideas', 'keywords': '', 'variation': False}
    if text_lower.startswith('ideas ') and not text_lower.startswith('ideas:'):
        kw = re.sub(r'^ideas\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'ideas', 'keywords': kw, 'variation': False}

    # ── AUDIT ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('audit '):
        business = re.sub(r'^audit\s+', '', text, flags=re.IGNORECASE).strip()
        business = _resolve_business_name(business)
        return {'action': 'audit', 'business': business}

    # ── BUILD ─────────────────────────────────────────────────────────────────
    if text_lower.startswith('build ') and not any(
        text_lower.startswith(p) for p in ('blast', 'build it', 'build plan')
    ):
        business = re.sub(r'^build\s+', '', text, flags=re.IGNORECASE).strip()
        business = _resolve_business_name(business)
        return {'action': 'build', 'business': business}

    # ── PLAN (full chain: scout → income → builder → auditor) ─────────────────
    if text_lower.startswith('plan '):
        business = re.sub(r'^plan\s+', '', text, flags=re.IGNORECASE).strip()
        business = _resolve_business_name(business)
        return {'action': 'plan', 'business': business}

    # ── AGENT LOGS ────────────────────────────────────────────────────────────
    if text_lower.startswith('logs'):
        rest     = text[4:].strip()
        # logs errors | logs <agent_name> | logs
        if rest.lower() == 'errors':
            return {'action': 'logs', 'filter': 'errors', 'agent': None}
        elif rest:
            return {'action': 'logs', 'filter': None, 'agent': rest.lower()}
        return {'action': 'logs', 'filter': None, 'agent': None}

    # ── ADD AGENT ─────────────────────────────────────────────────────────────
    if text_lower.startswith('add agent') or text_lower.startswith('add bot'):
        description = re.sub(r'^add\s+(agent|bot)\s*', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'add_agent', 'description': description}

    # ── AGENTS LIST ───────────────────────────────────────────────────────────
    if text_lower.strip() in ('agents', 'my agents', 'list agents', 'agent list'):
        return {'action': 'agents_list'}

    # ── DISABLE AGENT ─────────────────────────────────────────────────────────
    if text_lower.startswith('disable agent ') or text_lower.startswith('remove agent '):
        name = re.sub(r'^(disable|remove)\s+agent\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'agent_disable', 'name': name}

    # ── FINANCE ───────────────────────────────────────────────────────────────────
    if text_lower in ('finance', 'finances', 'finance dashboard', 'money', 'financial'):
        return {'action': 'finance_dashboard'}

    if text_lower.startswith('finance sync') or text_lower in ('sync finance', 'sync finances'):
        return {'action': 'finance_sync'}

    if text_lower in ('balance', 'balances', 'bank', 'accounts', 'bank balance', 'my accounts'):
        return {'action': 'finance_balances'}

    if text_lower in ('subscriptions', 'subs', 'recurring', 'recurring charges', 'my subscriptions'):
        return {'action': 'finance_subscriptions'}

    if text_lower.startswith('spending'):
        period = 'month'
        if 'week' in text_lower:
            period = 'week'
        elif 'year' in text_lower or 'ytd' in text_lower:
            period = 'ytd'
        return {'action': 'finance_spending', 'period': period}

    if text_lower in ('biz card', 'bizcard', 'business card', 'biz credit', 'business credit card'):
        return {'action': 'finance_biz_card'}

    if text_lower in ('invest', 'investment', 'where to invest', 'invest money', 'deploy capital'):
        return {'action': 'finance_invest'}

    if text_lower.startswith('mark biz card ') or text_lower.startswith('set biz card '):
        name = re.sub(r'^(mark|set)\s+biz\s+card\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'finance_mark_biz_card', 'name': name}

    # ── KNOWLEDGE SEARCH ─────────────────────────────────────────────────────
    if text_lower.startswith('find knowledge ') or text_lower.startswith('knowledge find '):
        query = re.sub(r'^(find knowledge|knowledge find)\s+', '', text, flags=re.IGNORECASE).strip()
        return {'action': 'knowledge_search', 'query': query}

    if text_lower.strip() in ('knowledge', 'find knowledge', 'knowledge find'):
        return {'action': 'knowledge_search', 'query': ''}

    # ── LEAD SNIPER (manual trigger) ─────────────────────────────────────────
    # snipe plumbing / snipe dental 10 / snipe roofing count:5
    if text_lower.startswith('snipe '):
        parts = text.split()
        niche = parts[1].lower() if len(parts) > 1 else ''
        count = 5
        for p in parts[2:]:
            if p.startswith('count:'):
                try:
                    count = int(p[6:])
                except ValueError:
                    pass
            elif p.isdigit():
                count = int(p)
        return {'action': 'snipe_leads', 'niche': niche, 'count': count}

    # ── OUTREACH QUEUE ───────────────────────────────────────────────────────
    if text_lower in ('outreach queue', 'send queue', 'email queue', 'queue status'):
        return {'action': 'outreach_queue'}

    # ── OUTREACH PIPELINE ────────────────────────────────────────────────────
    if text_lower in ('outreach', 'outreach pipeline', 'pipeline outreach'):
        return {'action': 'outreach', 'sub': ''}

    if text_lower == 'outreach stats':
        return {'action': 'outreach', 'sub': 'stats'}

    if text_lower.startswith('outreach '):
        sub = text_lower.split('outreach ', 1)[1].strip()
        return {'action': 'outreach', 'sub': sub}

    # log sent <email> [step:N] [hook:type]
    if text_lower.startswith('log sent '):
        rest  = text[9:].strip()
        parts = rest.split()
        email = parts[0] if parts else ''
        step  = 1
        hook  = None
        for p in parts[1:]:
            if p.startswith('step:'):
                try:
                    step = int(p[5:])
                except ValueError:
                    pass
            elif p.startswith('hook:'):
                hook = p[5:]
        return {'action': 'log_sent', 'email': email, 'step': step, 'hook': hook}

    # log reply <email> [status:call_booked]
    if text_lower.startswith('log reply '):
        rest   = text[10:].strip()
        parts  = rest.split()
        email  = parts[0] if parts else ''
        status = 'replied'
        for p in parts[1:]:
            if p.startswith('status:'):
                status = p[7:]
        return {'action': 'log_reply', 'email': email, 'status': status}

    # ── FALLBACK ──────────────────────────────────────────────────────────────
    return {'action': 'unknown', 'raw': text}
