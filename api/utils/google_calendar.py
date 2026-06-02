"""
ODIN — Google Calendar Integration
Schedule seller appointments from Slack.

Setup (one-time):
  1. Go to console.cloud.google.com → New Project → "ODIN"
  2. Enable "Google Calendar API"
  3. OAuth consent screen → External → add shueboxllc@gmail.com as test user
  4. Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON
  5. Save as ODIN/api/credentials/google_oauth.json
  6. Run: python api/utils/google_calendar.py --setup
     → Opens browser, you approve, tokens saved to ODIN/api/credentials/google_token.json
  7. Set GOOGLE_CALENDAR_ID in Railway (use 'primary' for your main calendar)

Slack commands (after setup):
  `schedule John 4314 Leatherwood tomorrow 2pm`
  `schedule Mary 123 Oak St Friday 10am notes:she wants 85k`
  `calendar today`  — show today's appointments
  `calendar week`   — show this week

No cost. Google Calendar API free tier = 1M requests/day.
"""

import os
import base64
import json
import datetime
from pathlib import Path

CREDENTIALS_FILE = Path(__file__).parent.parent / 'credentials' / 'google_oauth.json'
TOKEN_FILE       = Path(__file__).parent.parent / 'credentials' / 'google_token.json'
CALENDAR_ID      = os.environ.get('GOOGLE_CALENDAR_ID', 'primary')
SCOPES           = ['https://www.googleapis.com/auth/calendar']


def _ensure_token_file():
    """Write token from GOOGLE_TOKEN_JSON env var (Railway) if file is absent."""
    if TOKEN_FILE.exists():
        return
    raw = os.environ.get('GOOGLE_TOKEN_JSON', '')
    if not raw:
        return
    try:
        try:
            decoded = base64.b64decode(raw).decode('utf-8')
            json.loads(decoded)
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(decoded)
        except Exception:
            json.loads(raw)
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(raw)
    except Exception:
        pass


def _get_service():
    """Get authenticated Google Calendar service. Raises if not set up."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            'Google API libraries not installed. '
            'Run: pip install google-auth google-auth-oauthlib google-api-python-client'
        )

    _ensure_token_file()

    if not TOKEN_FILE.exists():
        raise RuntimeError(
            'Google Calendar not authorized. '
            'Run setup: python api/utils/google_calendar.py --setup\n'
            'Then set GOOGLE_TOKEN_JSON in Railway (base64-encode the token file).'
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Refresh token if expired
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise RuntimeError('Google token expired. Re-run setup.')

    return build('calendar', 'v3', credentials=creds)


def schedule_appointment(
    seller_name: str,
    address: str,
    start_dt: datetime.datetime,
    duration_minutes: int = 30,
    notes: str = '',
    phone: str = '',
) -> dict:
    """
    Create a calendar event for a seller appointment.
    Returns dict with event id, link, formatted time.
    """
    service = _get_service()

    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

    description = f'Seller: {seller_name}\nAddress: {address}'
    if phone:
        description += f'\nPhone: {phone}'
    if notes:
        description += f'\nNotes: {notes}'
    description += '\n\nScheduled via ODIN'

    event = {
        'summary':     f'Seller Appt — {seller_name} | {address}',
        'description': description,
        'start':       {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Chicago'},
        'end':         {'dateTime': end_dt.isoformat(),   'timeZone': 'America/Chicago'},
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup',  'minutes': 60},
                {'method': 'popup',  'minutes': 15},
            ],
        },
    }

    created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    return {
        'id':        created['id'],
        'link':      created.get('htmlLink', ''),
        'title':     created['summary'],
        'start':     start_dt.strftime('%A %b %d at %-I:%M %p CT'),
        'seller':    seller_name,
        'address':   address,
    }


def get_today_events() -> list:
    """Return today's calendar events."""
    service  = _get_service()
    now      = datetime.datetime.now(datetime.timezone.utc)
    day_end  = now.replace(hour=23, minute=59, second=59)

    result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=day_end.isoformat(),
        singleEvents=True,
        orderBy='startTime',
    ).execute()

    return result.get('items', [])


def get_week_events() -> list:
    """Return this week's calendar events."""
    service  = _get_service()
    now      = datetime.datetime.now(datetime.timezone.utc)
    week_end = now + datetime.timedelta(days=7)

    result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now.isoformat(),
        timeMax=week_end.isoformat(),
        singleEvents=True,
        orderBy='startTime',
        maxResults=20,
    ).execute()

    return result.get('items', [])


def format_event_slack(event: dict) -> str:
    """Format a single calendar event for Slack."""
    title  = event.get('summary', 'Unnamed')
    start  = event.get('start', {}).get('dateTime', '')
    link   = event.get('htmlLink', '')

    time_str = ''
    if start:
        try:
            import dateutil.parser
            dt = dateutil.parser.parse(start).astimezone(
                datetime.timezone(datetime.timedelta(hours=-5))  # CT
            )
            time_str = dt.strftime('%-I:%M %p')
        except Exception:
            time_str = start

    link_part = f' <{link}|view>' if link else ''
    return f'• {time_str} — *{title}*{link_part}'


def parse_appointment_time(text: str) -> datetime.datetime:
    """
    Parse natural-language time from Slack commands.
    Examples: "tomorrow 2pm", "Friday 10am", "Monday at 3:30pm"
    Returns a timezone-aware datetime in US/Central.
    """
    import re
    from datetime import date, timedelta

    now    = datetime.datetime.now()
    target = now.date()

    text_l = text.lower()

    # Day keywords
    if 'today' in text_l:
        target = now.date()
    elif 'tomorrow' in text_l:
        target = now.date() + timedelta(days=1)
    else:
        days = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                'friday': 4, 'saturday': 5, 'sunday': 6}
        for day_name, day_num in days.items():
            if day_name in text_l:
                today_num = now.weekday()
                delta = (day_num - today_num) % 7
                if delta == 0:
                    delta = 7  # next week if today
                target = now.date() + timedelta(days=delta)
                break

    # Time parsing
    time_m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', text_l)
    hour, minute = 9, 0  # default 9am
    if time_m:
        hour   = int(time_m.group(1))
        minute = int(time_m.group(2)) if time_m.group(2) else 0
        if time_m.group(3) == 'pm' and hour != 12:
            hour += 12
        elif time_m.group(3) == 'am' and hour == 12:
            hour = 0

    # Build aware datetime in Central time (UTC-5 standard / UTC-6 DST)
    # Using fixed offset for simplicity — Railway server is UTC
    dt = datetime.datetime.combine(target, datetime.time(hour, minute))
    # Add 6 hours to convert 9am CT → 15:00 UTC (CST offset; CDT would be 5)
    dt_utc = dt + datetime.timedelta(hours=6)
    return dt_utc.replace(tzinfo=datetime.timezone.utc)


def is_available() -> bool:
    """Check if Google Calendar is set up and credentials are valid."""
    _ensure_token_file()
    if not TOKEN_FILE.exists():
        return False
    try:
        _get_service()
        return True
    except Exception:
        return False


# ─── One-time setup CLI ──────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    if '--setup' in sys.argv:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            print('Install: pip install google-auth-oauthlib google-api-python-client')
            sys.exit(1)

        if not CREDENTIALS_FILE.exists():
            print(f'ERROR: OAuth credentials file not found at {CREDENTIALS_FILE}')
            print('Download it from console.cloud.google.com → Credentials → OAuth 2.0 Client IDs')
            sys.exit(1)

        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        flow   = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds  = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        print(f'✅ Google Calendar authorized! Token saved to {TOKEN_FILE}')
        print('Deploy to Railway — ODIN can now schedule appointments from Slack.')
