"""
Junk Cleaner Module
Discovers temp, cache, and junk files. Routes all moves through quarantine.stage_file().
Never deletes directly.
"""

import os
import winreg
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Tuple
import subprocess

from config import JUNK_SKIP_RECENT_HOURS


@dataclass
class JunkFile:
    path: Path
    category: str
    size_bytes: int


@dataclass
class JunkScanReport:
    files: List[JunkFile] = field(default_factory=list)
    staged_count: int = 0
    staged_size_bytes: int = 0
    skipped_in_use: int = 0
    skipped_recent: int = 0
    skipped_symlink: int = 0
    errors: List[str] = field(default_factory=list)


def _get_onedrive_paths() -> List[Path]:
    """Detect OneDrive sync roots via registry to exclude them from scanning."""
    paths = []
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts", 0, winreg.KEY_READ)
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, i)
                sub_key = winreg.OpenKey(key, sub_name)
                try:
                    val, _ = winreg.QueryValueEx(sub_key, "UserFolder")
                    paths.append(Path(val))
                except FileNotFoundError:
                    pass
                winreg.CloseKey(sub_key)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except Exception:
        pass
    return paths


def _is_under_onedrive(path: Path, onedrive_paths: List[Path]) -> bool:
    for od in onedrive_paths:
        try:
            path.relative_to(od)
            return True
        except ValueError:
            pass
    return False


def _is_windows_update_running() -> bool:
    """Return True if Windows Update service is currently RUNNING."""
    try:
        result = subprocess.run(
            ["sc", "query", "wuauserv"],
            capture_output=True, text=True, timeout=10
        )
        return "RUNNING" in result.stdout.upper()
    except Exception:
        return True  # fail safe: assume running if we can't check


def _browser_cache_paths() -> List[Tuple[Path, str]]:
    """Return (path, category) tuples for known browser cache locations."""
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    paths = []

    candidates = [
        (local_app / "Google" / "Chrome" / "User Data" / "Default" / "Cache" / "Cache_Data", "BrowserCache"),
        (local_app / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache",           "BrowserCache"),
        (local_app / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache" / "Cache_Data","BrowserCache"),
        (local_app / "Microsoft" / "Edge" / "User Data" / "Default" / "Code Cache",           "BrowserCache"),
        (appdata   / "Mozilla" / "Firefox" / "Profiles",                                      "BrowserCache"),
    ]

    for path, cat in candidates:
        if path.exists() and path.is_dir():
            # Firefox: profiles contain multiple cache dirs
            if "firefox" in str(path).lower():
                for profile in path.iterdir():
                    cache2 = profile / "cache2" / "entries"
                    if cache2.exists():
                        paths.append((cache2, cat))
            else:
                paths.append((path, cat))

    return paths


def _iter_files(directory: Path, onedrive_paths: List[Path], cutoff: datetime, max_files: int = 5000):
    """
    Yield (Path, skip_reason_or_None) for files in directory recursively.
    Uses os.scandir() iteratively to avoid loading all paths into memory at once.
    Caps at max_files per directory tree to prevent runaway scans.
    """
    import os
    count = 0
    stack = [str(directory)]

    while stack and count < max_files:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if count >= max_files:
                        break
                    try:
                        path = Path(entry.path)

                        if entry.is_symlink():
                            yield path, "symlink"
                            continue

                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue

                        if _is_under_onedrive(path, onedrive_paths):
                            continue

                        stat = entry.stat()
                        mtime = datetime.fromtimestamp(stat.st_mtime)
                        if mtime > cutoff:
                            yield path, "recent"
                            continue

                        count += 1
                        yield path, None

                    except (PermissionError, OSError):
                        yield Path(entry.path), "in_use"
                        continue
        except (PermissionError, OSError):
            continue


def scan(
    dry_run: bool = False,
    scan_id: str = "",
    include_prefetch: bool = False,
) -> JunkScanReport:
    from modules.quarantine import stage_file

    report = JunkScanReport()
    onedrive_paths = _get_onedrive_paths()
    cutoff = datetime.now() - timedelta(hours=JUNK_SKIP_RECENT_HOURS)

    # Build list of directories to scan
    scan_targets: List[Tuple[Path, str]] = []

    # Standard temp dirs
    for env_var, category in [("TEMP", "Temp"), ("TMP", "Temp")]:
        val = os.environ.get(env_var)
        if val:
            p = Path(val)
            if p.exists():
                scan_targets.append((p, category))

    scan_targets.append((Path(r"C:\Windows\Temp"), "WindowsTemp"))

    # Per-user temp dirs
    users_dir = Path(r"C:\Users")
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            user_temp = user_dir / "AppData" / "Local" / "Temp"
            if user_temp.exists():
                scan_targets.append((user_temp, "Temp"))

    # Windows Update cache — skip if wuauserv is RUNNING
    wu_path = Path(r"C:\Windows\SoftwareDistribution\Download")
    if wu_path.exists():
        if _is_windows_update_running():
            report.errors.append("Windows Update is running — skipping SoftwareDistribution\\Download")
        else:
            scan_targets.append((wu_path, "WindowsUpdate"))

    # Browser caches
    scan_targets.extend(_browser_cache_paths())

    # Prefetch (opt-in only)
    if include_prefetch:
        prefetch = Path(r"C:\Windows\Prefetch")
        if prefetch.exists():
            scan_targets.append((prefetch, "Prefetch"))

    # Deduplicate targets
    seen_targets = set()
    deduped = []
    for p, cat in scan_targets:
        key = str(p.resolve()).lower()
        if key not in seen_targets:
            seen_targets.add(key)
            deduped.append((p, cat))
    scan_targets = deduped

    # Scan each target
    from rich.console import Console as _Console
    _con = _Console()

    for directory, category in scan_targets:
        _con.print(f"  [dim]  Scanning: {directory}[/dim]")
        for file_path, skip_reason in _iter_files(directory, onedrive_paths, cutoff):
            if skip_reason == "symlink":
                report.skipped_symlink += 1
                continue
            if skip_reason == "recent":
                report.skipped_recent += 1
                continue
            if skip_reason == "in_use":
                report.skipped_in_use += 1
                continue
            if skip_reason == "onedrive":
                continue

            try:
                size = file_path.stat().st_size
            except (PermissionError, OSError):
                report.skipped_in_use += 1
                continue

            report.files.append(JunkFile(path=file_path, category=category, size_bytes=size))

            if scan_id:
                entry = stage_file(file_path, category, scan_id, dry_run=dry_run)
                if entry and entry.status in ("staged", "dry_run"):
                    report.staged_count += 1
                    report.staged_size_bytes += size
                elif entry and entry.status == "failed":
                    report.skipped_in_use += 1

    return report
