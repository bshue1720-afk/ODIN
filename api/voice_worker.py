"""
ODIN Voice Worker
Runs as a subprocess spawned by app.py on Flask startup.
Uses sys.executable so it always uses the same Python as Flask.

Standalone:
    python voice_worker.py dev     ← local LiveKit server (zero cloud minutes)
    python voice_worker.py start   ← production LiveKit cloud
"""
import os
import sys
import logging
import subprocess

log = logging.getLogger(__name__)

# Absolute path to this file — works regardless of cwd
_THIS_FILE = os.path.abspath(__file__)


def _check_env() -> bool:
    """Return False if required env vars are missing."""
    required = ['LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET']
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.warning(f"[voice_worker] Missing env vars: {missing} — voice disabled")
        return False
    if not os.environ.get('DEEPGRAM_API_KEY'):
        log.warning("[voice_worker] DEEPGRAM_API_KEY not set — voice disabled")
        return False
    if not os.environ.get('CARTESIA_API_KEY'):
        log.warning("[voice_worker] CARTESIA_API_KEY not set — voice disabled")
        return False
    return True


def start_worker_thread():
    """Start the LiveKit worker as a child subprocess.
    Uses sys.executable so it uses the exact same Python as Flask.
    Called from app.py on startup.
    """
    if not _check_env():
        return

    proc = subprocess.Popen(
        [sys.executable, _THIS_FILE, 'start'],
        env=os.environ.copy(),
        cwd=os.path.dirname(_THIS_FILE),
    )
    log.info(f"[voice_worker] Started as subprocess PID {proc.pid}")


# ── Standalone / subprocess entry point ──────────────────────────────────────
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    if not _check_env():
        print("[voice_worker] Missing required env vars — exiting.")
        sys.exit(1)

    from livekit.agents import cli, WorkerOptions
    from utils.voice_agent import entrypoint

    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode == "dev":
        os.environ["LIVEKIT_URL"] = "ws://localhost:7880"
        print("[voice_worker] DEV mode — using local LiveKit server")

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="odin-voice"))
