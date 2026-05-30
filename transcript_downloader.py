"""
ODIN YouTube Transcript Downloader v2.0
----------------------------------------
Professional bulk transcript downloader for entire YouTube channels.

Architecture:
  - PRIMARY:  youtube-transcript-api (fast, direct, no subprocess)
  - FALLBACK: yt-dlp VTT subtitle extraction (handles edge cases)
  - QUEUE:    SQLite job database (survives crashes, true resume)
  - SAFETY:   Jittered delays, exponential backoff, batch resting

Requirements:
    pip install yt-dlp youtube-transcript-api

Usage:
    python transcript_downloader.py
"""

import os
import re
import sys
import time
import random
import sqlite3
import logging
import subprocess
import shutil
import tempfile
import glob as _glob
from datetime import datetime

# ── UTF-8 output on Windows ───────────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('downloader.log', encoding='utf-8'),
    ]
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — edit these
# ═══════════════════════════════════════════════════════════════════════════════

CHANNELS = [
    "https://www.youtube.com/@danmartell",
    "https://www.youtube.com/@FlipWithRick",
]

OUTPUT_ROOT  = "transcripts"          # folder where .txt files are saved
DB_PATH      = "transcript_jobs.db"  # SQLite queue — survives crashes

# Cookies file (Netscape format) — exported from Firefox while on VPN
# To export: install "cookies.txt" Firefox extension → youtube.com → export
COOKIES_PATH = r"C:\Users\Brock\Downloads\5.27 yt cookies.txt"

# yt-dlp binary path
YTDLP = (
    shutil.which("yt-dlp")
    or r"C:\Users\Brock\OneDrive\Desktop\Master Folder\ODIN\Scripts\yt-dlp.exe"
)

# Language preference for transcripts
LANG_PREFERENCE = ["en", "en-US", "en-GB"]

# ── Rate limiting ──────────────────────────────────────────────────────────────
DELAY_MIN   = 8     # min seconds between videos
DELAY_MAX   = 18    # max seconds between videos (randomized)

# ── Batch safety ───────────────────────────────────────────────────────────────
BATCH_SIZE  = 75    # downloads per batch before resting
BATCH_REST  = 5400  # seconds to rest between batches (90 min)

# ── Retry / backoff ────────────────────────────────────────────────────────────
MAX_ATTEMPTS    = 5    # max tries per video before marking failed
BACKOFF_BASE    = 60   # initial backoff seconds on block (doubles each retry)
BACKOFF_MAX     = 900  # cap backoff at 15 minutes

# ═══════════════════════════════════════════════════════════════════════════════


# ── Import youtube-transcript-api ─────────────────────────────────────────────
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from youtube_transcript_api._errors import VideoUnavailable

_BLOCK_EXCEPTIONS = ()
for _name in ('RequestBlocked', 'IpBlocked'):
    try:
        import importlib
        _mod = importlib.import_module('youtube_transcript_api._errors')
        _exc = getattr(_mod, _name, None)
        if _exc:
            _BLOCK_EXCEPTIONS = _BLOCK_EXCEPTIONS + (_exc,)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE — SQLite job queue
# ═══════════════════════════════════════════════════════════════════════════════

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id    TEXT PRIMARY KEY,
            channel     TEXT,
            title       TEXT,
            upload_date TEXT,
            status      TEXT DEFAULT 'pending',
            attempts    INTEGER DEFAULT 0,
            error       TEXT,
            filepath    TEXT,
            word_count  INTEGER,
            updated_at  TEXT
        )
    """)
    conn.commit()
    return conn


def upsert_video(conn, video_id, channel, title, upload_date):
    """Insert a video into the queue (skip if already exists)."""
    conn.execute("""
        INSERT OR IGNORE INTO videos (video_id, channel, title, upload_date, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (video_id, channel, title, upload_date, datetime.now().isoformat()))
    conn.commit()


def mark_done(conn, video_id, filepath, word_count):
    conn.execute("""
        UPDATE videos SET status='done', filepath=?, word_count=?,
        attempts=attempts+1, updated_at=? WHERE video_id=?
    """, (filepath, word_count, datetime.now().isoformat(), video_id))
    conn.commit()


def mark_skipped(conn, video_id, reason):
    conn.execute("""
        UPDATE videos SET status='skipped', error=?,
        attempts=attempts+1, updated_at=? WHERE video_id=?
    """, (reason, datetime.now().isoformat(), video_id))
    conn.commit()


def mark_failed(conn, video_id, error):
    conn.execute("""
        UPDATE videos SET status='failed', error=?,
        attempts=attempts+1, updated_at=? WHERE video_id=?
    """, (error, datetime.now().isoformat(), video_id))
    conn.commit()


def increment_attempt(conn, video_id, error):
    conn.execute("""
        UPDATE videos SET error=?, attempts=attempts+1, updated_at=?
        WHERE video_id=?
    """, (error, datetime.now().isoformat(), video_id))
    conn.commit()


def get_pending(conn):
    """Return pending videos that haven't exceeded max attempts."""
    return conn.execute("""
        SELECT * FROM videos
        WHERE status = 'pending' AND attempts < ?
        ORDER BY channel, upload_date DESC
    """, (MAX_ATTEMPTS,)).fetchall()


def get_stats(conn):
    rows = conn.execute("""
        SELECT status, COUNT(*) as cnt FROM videos GROUP BY status
    """).fetchall()
    return {r['status']: r['cnt'] for r in rows}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:120]


def jitter_sleep(label: str = ""):
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    if label:
        log.info(f"  [wait] {delay:.1f}s {label}")
    time.sleep(delay)


def backoff_sleep(attempt: int):
    wait = min(BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 30), BACKOFF_MAX)
    log.warning(f"  [backoff] Attempt {attempt+1} — sleeping {wait:.0f}s")
    time.sleep(wait)


# ═══════════════════════════════════════════════════════════════════════════════
# CHANNEL ENUMERATION — yt-dlp flat-playlist
# ═══════════════════════════════════════════════════════════════════════════════

def enumerate_channel(channel_url: str) -> tuple[str, list[dict]]:
    """
    Use yt-dlp --flat-playlist to get all video IDs, titles, dates.
    Returns (channel_name, [{'id', 'title', 'date'}, ...])
    """
    log.info(f"\n[>] Enumerating: {channel_url}")
    cmd = [
        YTDLP,
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(upload_date)s",
        "--no-warnings",
        "--quiet",
        channel_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300)

    if result.returncode != 0 or not result.stdout.strip():
        log.error(f"  [err] yt-dlp enumeration failed: {result.stderr[:200]}")
        return "unknown_channel", []

    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 1:
            continue
        vid_id   = parts[0].strip()
        title    = parts[1].strip() if len(parts) > 1 else vid_id
        raw_date = parts[2].strip() if len(parts) > 2 else ""
        if raw_date and len(raw_date) == 8 and raw_date.isdigit():
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        else:
            date_str = datetime.today().strftime("%Y-%m-%d")
        videos.append({"id": vid_id, "title": title, "date": date_str})

    # Get channel name
    channel_name = "Unknown_Channel"
    for field in ["%(uploader)s", "%(playlist_title)s"]:
        name_cmd = [YTDLP, "--flat-playlist", "--print", field,
                    "--playlist-items", "1", "--no-warnings", "--quiet", channel_url]
        name_res = subprocess.run(name_cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=30)
        cand = (name_res.stdout or "").strip().splitlines()
        cand = cand[0] if cand else ""
        if cand and cand not in ("NA", "None", ""):
            channel_name = cand
            break

    log.info(f"  [OK] {channel_name} — {len(videos)} videos found")
    return channel_name, videos


# ═══════════════════════════════════════════════════════════════════════════════
# PRIMARY: youtube-transcript-api (fast, direct)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_via_api(video_id: str) -> tuple[str | None, str | None]:
    """
    Returns (transcript_text, error_code).
    error_code: None=success, 'BLOCKED', 'DISABLED', 'NOT_FOUND', 'UNAVAILABLE', 'ERROR:<msg>'
    """
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        transcript = None
        # Try manual captions first (higher quality)
        try:
            transcript = transcript_list.find_manually_created_transcript(LANG_PREFERENCE)
        except Exception:
            pass

        # Fall back to auto-generated
        if transcript is None:
            try:
                transcript = transcript_list.find_generated_transcript(LANG_PREFERENCE)
            except Exception:
                pass

        # Fall back to any available
        if transcript is None:
            available = list(transcript_list)
            if available:
                transcript = available[0]

        if transcript is None:
            return None, "NOT_FOUND"

        segments = transcript.fetch()
        raw = " ".join(
            (seg.text if hasattr(seg, 'text') else seg.get('text', '')).strip()
            for seg in segments
        )
        raw = re.sub(r'\[.*?\]', '', raw)
        raw = re.sub(r'\s{2,}', ' ', raw).strip()
        return (raw if raw else None), ("NOT_FOUND" if not raw else None)

    except TranscriptsDisabled:
        return None, "DISABLED"
    except NoTranscriptFound:
        return None, "NOT_FOUND"
    except VideoUnavailable:
        return None, "UNAVAILABLE"
    except Exception as e:
        if _BLOCK_EXCEPTIONS and isinstance(e, _BLOCK_EXCEPTIONS):
            return None, "BLOCKED"
        msg = str(e)
        if any(k in msg.lower() for k in ("429", "too many", "blocked", "ip")):
            return None, "BLOCKED"
        return None, f"ERROR:{msg[:120]}"


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK: yt-dlp VTT extraction
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_via_ytdlp(video_id: str) -> tuple[str | None, str | None]:
    """
    Returns (transcript_text, error_code).
    Uses yt-dlp to download VTT subtitle file, parses it, deletes it.
    """
    tmp_dir  = tempfile.mkdtemp()
    out_tmpl = os.path.join(tmp_dir, "%(id)s.%(ext)s")

    cmd = [
        YTDLP,
        "--write-auto-subs",
        "--write-subs",
        "--skip-download",
        "--sub-lang", "en.*",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-warnings",
        "--sleep-interval", "3",
        "--sleep-requests", "1",
        "-o", out_tmpl,
    ]
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        cmd += ["--cookies", COOKIES_PATH]
    cmd.append(f"https://www.youtube.com/watch?v={video_id}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None, "ERROR:yt-dlp timeout"

    vtt_files = _glob.glob(os.path.join(tmp_dir, "*.vtt")) or \
                _glob.glob(os.path.join(tmp_dir, "*.*"))

    if not vtt_files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        stderr = (result.stderr or "").lower()
        stdout = (result.stdout or "").lower()
        combined = stderr + stdout
        if "429" in combined or "too many requests" in combined or "ratelimit" in combined:
            return None, "BLOCKED"
        if "n challenge" in combined or "no video formats" in combined:
            return None, "BLOCKED"  # IP/challenge issue — worth retrying
        if "video unavailable" in combined or "sign in" in combined or "private" in combined:
            return None, "UNAVAILABLE"  # permanently gone — skip, don't retry
        if "no subtitles" in combined or "subtitles are disabled" in combined:
            return None, "DISABLED"
        return None, "NOT_FOUND"

    try:
        with open(vtt_files[0], encoding="utf-8", errors="replace") as f:
            raw = f.read()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Parse VTT → clean text
    lines, text_lines = raw.splitlines(), []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(line.startswith(p) for p in ("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if re.match(r'^\d{2}:\d{2}', line):
            continue
        if re.match(r'^\d+$', line):
            continue
        line = re.sub(r'<[^>]+>', '', line)
        line = re.sub(r'\[.*?\]', '', line).strip()
        if line:
            text_lines.append(line)

    # Deduplicate consecutive identical lines
    deduped = []
    for line in text_lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    text = re.sub(r'\s{2,}', ' ', " ".join(deduped)).strip()
    return (text if text else None), ("NOT_FOUND" if not text else None)


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED FETCH — primary → fallback → backoff retry
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_transcript(video_id: str) -> tuple[str | None, str | None]:
    """
    Try youtube-transcript-api first, fall back to yt-dlp.
    Returns (text, error_code).
    """
    for attempt in range(MAX_ATTEMPTS):
        # ── PRIMARY ──────────────────────────────────────────────────────────
        text, err = fetch_via_api(video_id)
        if text:
            return text, None
        if err in ("DISABLED", "NOT_FOUND", "UNAVAILABLE"):
            return None, err  # definitive — don't retry

        if err == "BLOCKED":
            log.warning(f"  [block] API blocked on attempt {attempt+1} — trying yt-dlp fallback")
            text, err2 = fetch_via_ytdlp(video_id)
            if text:
                return text, None
            if err2 in ("NOT_FOUND", "UNAVAILABLE", "DISABLED"):
                return None, err2
            if err2 == "BLOCKED":
                backoff_sleep(attempt)
                continue
            if err2 and err2.startswith("ERROR:"):
                log.warning(f"  [ytdlp err] {err2}")
                backoff_sleep(attempt)
                continue
            return None, err2

        # ── FALLBACK (API returned error but not block) ───────────────────────
        if err and err.startswith("ERROR:"):
            log.warning(f"  [api err] {err} — trying yt-dlp fallback")
            text, err2 = fetch_via_ytdlp(video_id)
            if text:
                return text, None
            backoff_sleep(attempt)
            continue

        # No text, no clear error — try ytdlp anyway
        text, err2 = fetch_via_ytdlp(video_id)
        if text:
            return text, None
        if err2 in ("NOT_FOUND", "UNAVAILABLE", "DISABLED"):
            return None, err2
        backoff_sleep(attempt)

    return None, "MAX_RETRIES"


# ═══════════════════════════════════════════════════════════════════════════════
# SAVE
# ═══════════════════════════════════════════════════════════════════════════════

def save_transcript(channel_dir: str, date_str: str, title: str, text: str) -> str:
    safe_title = sanitize_filename(title)
    filename   = f"{date_str}_{safe_title}.txt"
    filepath   = os.path.join(channel_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Title: {title}\n")
        f.write(f"Date:  {date_str}\n")
        f.write("-" * 60 + "\n\n")
        f.write(text)
        f.write("\n")
    return filepath


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    conn = init_db()

    log.info("=" * 60)
    log.info("  ODIN Transcript Downloader v2.0")
    log.info(f"  Channels: {len(CHANNELS)}")
    log.info(f"  Output:   ./{OUTPUT_ROOT}/")
    log.info(f"  DB:       {DB_PATH}")
    log.info(f"  Cookies:  {COOKIES_PATH}")
    log.info("=" * 60)

    # ── PHASE 1: Enumerate all channels → populate DB ─────────────────────────
    for channel_url in CHANNELS:
        channel_name, videos = enumerate_channel(channel_url)
        safe_channel = sanitize_filename(channel_name)
        channel_dir  = os.path.join(OUTPUT_ROOT, safe_channel)
        os.makedirs(channel_dir, exist_ok=True)

        new_count = 0
        for v in videos:
            # Check if file already exists on disk (resume from old runs)
            safe_title = sanitize_filename(v['title'])
            existing   = os.path.join(channel_dir, f"{v['date']}_{safe_title}.txt")
            if os.path.exists(existing):
                # Mark as done in DB if not already
                conn.execute("""
                    INSERT OR IGNORE INTO videos
                        (video_id, channel, title, upload_date, status, filepath, updated_at)
                    VALUES (?, ?, ?, ?, 'done', ?, ?)
                """, (v['id'], safe_channel, v['title'], v['date'],
                      existing, datetime.now().isoformat()))
            else:
                upsert_video(conn, v['id'], safe_channel, v['title'], v['date'])
                new_count += 1
        conn.commit()
        log.info(f"  [db] {safe_channel}: {new_count} new videos queued")

    # ── PHASE 2: Process pending queue ────────────────────────────────────────
    batch_count = 0

    while True:
        pending = get_pending(conn)
        if not pending:
            stats = get_stats(conn)
            log.info(f"\n{'='*60}")
            log.info(f"  ALL DONE — Queue empty")
            for k, v in stats.items():
                log.info(f"  {k:12s}: {v}")
            log.info(f"{'='*60}")
            break

        stats = get_stats(conn)
        log.info(f"\n[queue] Pending: {len(pending)} | Done: {stats.get('done',0)} | "
                 f"Skipped: {stats.get('skipped',0)} | Failed: {stats.get('failed',0)}")

        progress_this_pass = 0  # track successful downloads + skips this iteration

        for row in pending:
            vid_id  = row['video_id']
            channel = row['channel']
            title   = row['title']
            date    = row['upload_date'] or datetime.today().strftime("%Y-%m-%d")
            label   = f"{title[:55]}"

            channel_dir = os.path.join(OUTPUT_ROOT, channel)
            os.makedirs(channel_dir, exist_ok=True)

            # Double-check file doesn't exist (another process may have saved it)
            safe_title = sanitize_filename(title)
            filepath   = os.path.join(channel_dir, f"{date}_{safe_title}.txt")
            if os.path.exists(filepath):
                mark_done(conn, vid_id, filepath, 0)
                log.info(f"  [skip] {label} — file exists")
                continue

            log.info(f"  [dl]   {label}")

            text, err = fetch_transcript(vid_id)

            if text:
                saved_path = save_transcript(channel_dir, date, title, text)
                word_count = len(text.split())
                mark_done(conn, vid_id, saved_path, word_count)
                log.info(f"  [OK]   {word_count:,} words → {os.path.basename(saved_path)}")
                batch_count += 1
                progress_this_pass += 1

                # ── Batch rest ────────────────────────────────────────────────
                if batch_count >= BATCH_SIZE:
                    log.info(f"\n  [batch] {BATCH_SIZE} downloads complete — "
                             f"resting {BATCH_REST//60} min...")
                    time.sleep(BATCH_REST)
                    batch_count = 0
                    log.info("  [batch] Resuming...\n")

            elif err in ("DISABLED", "NOT_FOUND", "UNAVAILABLE"):
                mark_skipped(conn, vid_id, err)
                log.info(f"  [skip] {err}")
                progress_this_pass += 1

            elif err == "MAX_RETRIES":
                mark_failed(conn, vid_id, "MAX_RETRIES")
                log.warning(f"  [fail] Max retries exceeded")
                progress_this_pass += 1

            else:
                increment_attempt(conn, vid_id, err or "unknown")
                log.warning(f"  [err]  {err}")

            # ── Jittered delay between every video ────────────────────────────
            jitter_sleep()

        # ── No-progress guard — if nothing resolved this pass, stop looping ──
        if progress_this_pass == 0:
            stats = get_stats(conn)
            log.warning(f"\n  [loop] No progress made this pass — breaking to avoid infinite loop")
            log.warning(f"  [loop] Remaining pending videos may need manual review")
            for k, v in stats.items():
                log.info(f"  {k:12s}: {v}")
            break

    conn.close()


if __name__ == "__main__":
    main()
