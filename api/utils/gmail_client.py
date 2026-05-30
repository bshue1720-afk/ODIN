"""
ODIN — Gmail API Client
Send and read emails from shueboxllc@gmail.com directly through ODIN.

Setup (one-time — reuses same Google OAuth project as Calendar):
  1. In console.cloud.google.com → your ODIN project → Enable "Gmail API"
  2. Add Gmail scope: https://www.googleapis.com/auth/gmail.send
  3. Re-run setup: python api/utils/gmail_client.py --setup
     (will ask to re-authorize to include Gmail scope)
  4. Done — emails go out from shueboxllc@gmail.com

Slack commands (after setup):
  `gmail John 4314 Leatherwood Ave` — draft + send seller follow-up email
  `gmail buyer Sarah Johnson match:4314 Leatherwood` — buyer outreach email
  `gmail custom to:someone@email.com subject:Your subject body:Your message`

Why use Gmail over XLeads email:
  - shueboxllc@gmail.com has better deliverability for personal seller outreach
  - XLeads email is fine for bulk/templated — Gmail is for personal follow-ups
  - Sent emails logged in ODIN + visible in your Gmail Sent folder

No cost. Gmail API free tier = 500M quota units/day (more than enough).
"""

import os
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

CREDENTIALS_FILE = Path(__file__).parent.parent / 'credentials' / 'google_oauth.json'
TOKEN_FILE       = Path(__file__).parent.parent / 'credentials' / 'google_token.json'
GMAIL_FROM       = os.environ.get('GMAIL_FROM', 'shueboxllc@gmail.com')
GMAIL_FROM_NAME  = os.environ.get('GMAIL_FROM_NAME', 'Brock — Shue Box LLC')

# Gmail requires a broader scope than Calendar alone
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
]


def _get_service():
    """Get authenticated Gmail service. Raises if not set up."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            'Google API libraries not installed. '
            'Run: pip install google-auth google-auth-oauthlib google-api-python-client'
        )

    if not TOKEN_FILE.exists():
        raise RuntimeError(
            'Gmail not authorized. Run: python api/utils/gmail_client.py --setup'
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise RuntimeError('Google token expired. Re-run setup.')

    return build('gmail', 'v1', credentials=creds)


def send(to_email: str, subject: str, body: str,
         to_name: str = '', html: bool = False) -> dict:
    """
    Send an email from shueboxllc@gmail.com.
    Returns dict with message_id, thread_id.
    """
    service = _get_service()

    msg = MIMEMultipart('alternative') if html else MIMEText(body, 'plain')
    msg['to']      = f'{to_name} <{to_email}>' if to_name else to_email
    msg['from']    = f'{GMAIL_FROM_NAME} <{GMAIL_FROM}>'
    msg['subject'] = subject

    if html:
        msg.attach(MIMEText(body, 'html'))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId='me',
        body={'raw': raw},
    ).execute()

    return {
        'message_id': result.get('id'),
        'thread_id':  result.get('threadId'),
        'to':         to_email,
        'subject':    subject,
    }


def get_recent_threads(max_results: int = 10, query: str = '') -> list:
    """
    Get recent email threads from shueboxllc@gmail.com.
    query: Gmail search syntax e.g. 'from:seller@email.com' or 'subject:offer'
    """
    service = _get_service()
    results = service.users().messages().list(
        userId='me',
        maxResults=max_results,
        q=query or 'in:inbox',
    ).execute()

    messages = results.get('messages', [])
    threads  = []

    for m in messages[:max_results]:
        msg = service.users().messages().get(
            userId='me', id=m['id'], format='metadata',
            metadataHeaders=['From', 'Subject', 'Date'],
        ).execute()

        headers = {h['name']: h['value']
                   for h in msg.get('payload', {}).get('headers', [])}
        threads.append({
            'id':      m['id'],
            'from':    headers.get('From', '—'),
            'subject': headers.get('Subject', '(no subject)'),
            'date':    headers.get('Date', ''),
            'snippet': msg.get('snippet', '')[:100],
        })

    return threads


def format_slack_threads(threads: list) -> str:
    """Format recent email threads for Slack."""
    if not threads:
        return '_No recent emails._'
    lines = [f'*📧 Recent Emails ({len(threads)})*']
    for t in threads:
        lines.append(
            f'• *{t["subject"][:50]}*\n'
            f'  From: {t["from"][:40]} | _{t["snippet"]}_'
        )
    return '\n'.join(lines)


def is_available() -> bool:
    """Check if Gmail is authorized."""
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
            print(f'ERROR: {CREDENTIALS_FILE} not found')
            print('Download from console.cloud.google.com → Credentials → OAuth 2.0 Client IDs')
            sys.exit(1)

        CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        flow  = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        print(f'✅ Gmail authorized! Token saved to {TOKEN_FILE}')
        print('Now set GMAIL_FROM=shueboxllc@gmail.com in Railway and redeploy.')
