"""
PC Diagnostic Agent — Central Configuration
All tunable constants live here.
"""

from pathlib import Path
import os

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
POTENTIAL_DELETE_DIR = Path("C:/PotentialDelete")
REPORTS_DIR = BASE_DIR / "reports"

# ── Thresholds ────────────────────────────────────────────────────────────────

CPU_WARN_THRESHOLD    = 80   # % average usage (1-second sample)
RAM_WARN_THRESHOLD    = 85   # % of total RAM used
DISK_WARN_THRESHOLD   = 90   # % of any single disk used
JUNK_MIN_AGE_HOURS    = 24   # skip files modified more recently than this

# ── Defender ─────────────────────────────────────────────────────────────────

DEFENDER_TIMEOUT = 300  # seconds before scan is considered hung

DEFENDER_PATHS = [
    Path(r"C:\Program Files\Windows Defender\MpCmdRun.exe"),
    # versioned platform path discovered at runtime via glob
]

# ── Junk Scan Targets ─────────────────────────────────────────────────────────

# Each entry: (glob_pattern_or_path, category_label)
# Paths using env vars are resolved at runtime in junk_cleaner.py
JUNK_TARGETS = [
    ("%TEMP%",                          "Temp"),
    ("%TMP%",                           "Temp"),
    (r"C:\Windows\Temp",               "WindowsTemp"),
    (r"C:\Windows\SoftwareDistribution\Download", "WindowsUpdate"),
    # Browser caches — resolved per-user at runtime
    ("CHROME_CACHE",                    "BrowserCache"),
    ("EDGE_CACHE",                      "BrowserCache"),
    ("FIREFOX_CACHE",                   "BrowserCache"),
]

# Files modified within this many hours are skipped (safety buffer)
JUNK_SKIP_RECENT_HOURS = 24

# ── Startup Audit ─────────────────────────────────────────────────────────────

# Common safe startup entries (name substrings, case-insensitive)
STARTUP_KNOWN_SAFE = [
    "windows security",
    "windows defender",
    "microsoft onedrive",
    "realtek",
    "nvidia",
    "amd",
    "intel",
    "discord",
    "steam",
    "dropbox",
    "google drive",
    "teams",
    "zoom",
    "slack",
    "spotify",
    "chrome",
    "edge",
    "firefox",
    "cortana",
    "ctfmon",
    "sihost",
    "taskhostw",
    "explorer",
    "searchui",
    "runtimebroker",
    "backgroundtaskhost",
]

# ── Recommendations ───────────────────────────────────────────────────────────

STARTUP_COUNT_WARN = 15   # warn if more than this many startup entries
