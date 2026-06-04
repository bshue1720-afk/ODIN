"""
ODIN Voice Blueprint
Routes:
  POST /api/voice/call-me   — trigger ODIN to call Brock's phone
  GET  /api/voice/status    — is ODIN on a live call?
"""
import os
import uuid
import logging
import asyncio

from flask import Blueprint, jsonify, request
from core.auth import require_auth

bp = Blueprint('voice', __name__, url_prefix='/api/voice')
log = logging.getLogger(__name__)


@bp.route('/call-me', methods=['POST'])
@require_auth
def call_me():
    """Trigger ODIN to call BROCK_PHONE_NUMBER via SIP."""
    trunk_id = os.environ.get('LIVEKIT_SIP_TRUNK_ID', '')
    if not trunk_id:
        return jsonify({
            'error': 'LIVEKIT_SIP_TRUNK_ID not configured. '
                     'Set up a SIP trunk in LiveKit Cloud → Phone Numbers, then add the trunk ID to Railway.'
        }), 503

    phone = os.environ.get('BROCK_PHONE_NUMBER', '')
    if not phone:
        return jsonify({'error': 'BROCK_PHONE_NUMBER not set in Railway env vars.'}), 503

    livekit_url    = os.environ.get('LIVEKIT_URL', '')
    livekit_key    = os.environ.get('LIVEKIT_API_KEY', '')
    livekit_secret = os.environ.get('LIVEKIT_API_SECRET', '')

    if not all([livekit_url, livekit_key, livekit_secret]):
        return jsonify({'error': 'LiveKit env vars not configured.'}), 503

    room_name = f"odin-call-{uuid.uuid4().hex[:8]}"

    try:
        import livekit.api as lk_api

        async def _dispatch():
            lk = lk_api.LiveKitAPI(
                url=livekit_url,
                api_key=livekit_key,
                api_secret=livekit_secret,
            )
            await lk.sip.create_sip_participant(
                lk_api.CreateSIPParticipantRequest(
                    sip_trunk_id=trunk_id,
                    sip_call_to=f"tel:{phone}",
                    room_name=room_name,
                    participant_identity="brock",
                    participant_name="Brock",
                )
            )
            await lk.aclose()

        asyncio.run(_dispatch())
        log.info(f"[voice_bp] Outbound call dispatched to {phone}, room={room_name}")
        return jsonify({'status': 'calling', 'room': room_name, 'phone': phone})

    except Exception as e:
        log.error(f"[voice_bp] Outbound call failed: {e}")
        return jsonify({'error': str(e)}), 500


@bp.route('/status', methods=['GET'])
@require_auth
def status():
    """Check if there is an active voice session."""
    livekit_url    = os.environ.get('LIVEKIT_URL', '')
    livekit_key    = os.environ.get('LIVEKIT_API_KEY', '')
    livekit_secret = os.environ.get('LIVEKIT_API_SECRET', '')

    if not all([livekit_url, livekit_key, livekit_secret]):
        return jsonify({'active': False, 'error': 'LiveKit not configured'})

    try:
        import livekit.api as lk_api

        async def _list_rooms():
            lk = lk_api.LiveKitAPI(
                url=livekit_url,
                api_key=livekit_key,
                api_secret=livekit_secret,
            )
            rooms = await lk.room.list_rooms(lk_api.ListRoomsRequest())
            await lk.aclose()
            return [r for r in rooms.rooms if r.name.startswith('odin-call-')]

        active_rooms = asyncio.run(_list_rooms())
        return jsonify({
            'active': len(active_rooms) > 0,
            'sessions': [{'room': r.name, 'participants': r.num_participants} for r in active_rooms],
        })

    except Exception as e:
        return jsonify({'active': False, 'error': str(e)})
