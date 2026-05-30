"""
Startup Audit Module
Reads registry Run/RunOnce keys and Task Scheduler entries.
READ-ONLY — never modifies registry or scheduled tasks.
"""

import winreg
import subprocess
import csv
import io
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from config import STARTUP_KNOWN_SAFE


@dataclass
class StartupEntry:
    name: str
    path: str            # resolved executable path (best effort)
    raw_value: str       # full registry/task value as-is
    source: str          # e.g. "HKLM\\Run", "TaskScheduler"
    flags: List[str]     # "ORPHANED", "UNSIGNED", "UNKNOWN_PUBLISHER", "KNOWN_SAFE"
    publisher: str = "Unknown"


@dataclass
class StartupAuditReport:
    entries: List[StartupEntry] = field(default_factory=list)
    flagged: List[StartupEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# Registry hives to check
_HIVES = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",      "HKLM\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",  "HKLM\\RunOnce"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",      "HKCU\\Run"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",  "HKCU\\RunOnce"),
]


def _extract_exe_path(raw_value: str) -> str:
    """Pull the executable path out of a registry value that may include args."""
    raw_value = raw_value.strip()

    # Handle quoted paths: "C:\path\to\app.exe" [args]
    if raw_value.startswith('"'):
        end_quote = raw_value.find('"', 1)
        if end_quote != -1:
            return raw_value[1:end_quote]

    # Handle rundll32 / cmd wrappers — return the wrapper itself
    lower = raw_value.lower()
    if "rundll32" in lower or "cmd.exe" in lower or "powershell" in lower:
        return raw_value.split()[0]

    # Unquoted: split on first space, take first token
    parts = raw_value.split(None, 1)
    return parts[0] if parts else raw_value


def _get_publisher(exe_path: str) -> str:
    """Use PowerShell Get-AuthenticodeSignature to get the publisher name."""
    if not Path(exe_path).exists():
        return "Unknown"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature '{exe_path}').SignerCertificate.Subject"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()
        if not output or "error" in output.lower():
            return "Unknown"
        # Extract CN= from certificate subject
        match = re.search(r'CN=([^,]+)', output)
        return match.group(1).strip() if match else output[:80]
    except Exception:
        return "Unknown"


def _is_signed(exe_path: str) -> bool:
    """Return True if the executable has a valid Authenticode signature."""
    if not Path(exe_path).exists():
        return False
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature '{exe_path}').Status"],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "Valid"
    except Exception:
        return False


def _read_registry_entries(errors: List[str]) -> List[StartupEntry]:
    entries = []
    for hive, subkey_path, source_label in _HIVES:
        try:
            key = winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            continue
        except PermissionError as e:
            errors.append(f"{source_label}: permission denied — {e}")
            continue

        i = 0
        while True:
            try:
                name, data, _ = winreg.EnumValue(key, i)
                raw_value = str(data)
                exe_path = _extract_exe_path(raw_value)

                flags = []
                exe = Path(exe_path)

                if not exe.exists():
                    flags.append("ORPHANED")
                else:
                    if not _is_signed(exe_path):
                        flags.append("UNSIGNED")

                name_lower = name.lower()
                if any(safe.lower() in name_lower or safe.lower() in exe_path.lower()
                       for safe in STARTUP_KNOWN_SAFE):
                    flags.append("KNOWN_SAFE")

                publisher = _get_publisher(exe_path) if exe.exists() else "Unknown"

                entries.append(StartupEntry(
                    name=name,
                    path=exe_path,
                    raw_value=raw_value,
                    source=source_label,
                    flags=flags,
                    publisher=publisher,
                ))
                i += 1
            except OSError:
                break

        winreg.CloseKey(key)
    return entries


def _read_task_scheduler_entries(errors: List[str]) -> List[StartupEntry]:
    entries = []
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/v"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            errors.append(f"schtasks failed: {result.stderr.strip()}")
            return entries

        reader = csv.DictReader(io.StringIO(result.stdout))
        for row in reader:
            try:
                status = row.get("Status", "").strip()
                logon_type = row.get("Logon Mode", "").strip()
                task_to_run = row.get("Task To Run", "").strip()
                task_name = row.get("TaskName", "").strip()

                # Only care about active startup-equivalent tasks
                if status not in ("Ready", "Running"):
                    continue
                if not task_name or task_name == "TaskName":
                    continue

                exe_path = _extract_exe_path(task_to_run) if task_to_run else "N/A"
                flags = []
                exe = Path(exe_path)

                if exe_path != "N/A" and not exe.exists():
                    flags.append("ORPHANED")
                elif exe_path != "N/A" and exe.exists():
                    if not _is_signed(exe_path):
                        flags.append("UNSIGNED")

                name_lower = task_name.lower()
                if any(safe.lower() in name_lower or safe.lower() in exe_path.lower()
                       for safe in STARTUP_KNOWN_SAFE):
                    flags.append("KNOWN_SAFE")

                publisher = _get_publisher(exe_path) if exe_path != "N/A" and exe.exists() else "Unknown"

                entries.append(StartupEntry(
                    name=task_name,
                    path=exe_path,
                    raw_value=task_to_run,
                    source="TaskScheduler",
                    flags=flags,
                    publisher=publisher,
                ))
            except Exception:
                continue

    except subprocess.TimeoutExpired:
        errors.append("schtasks query timed out")
    except Exception as e:
        errors.append(f"Task Scheduler read error: {e}")

    return entries


def audit() -> StartupAuditReport:
    report = StartupAuditReport()

    report.entries.extend(_read_registry_entries(report.errors))
    report.entries.extend(_read_task_scheduler_entries(report.errors))

    # Flagged = any entry with a flag that isn't ONLY "KNOWN_SAFE"
    for entry in report.entries:
        actionable_flags = [f for f in entry.flags if f != "KNOWN_SAFE"]
        if actionable_flags:
            report.flagged.append(entry)

    return report


if __name__ == "__main__":
    r = audit()
    print(f"Total startup entries: {len(r.entries)}")
    print(f"Flagged: {len(r.flagged)}")
    for e in r.flagged:
        print(f"  [{', '.join(e.flags)}] {e.name} — {e.path}")
    if r.errors:
        print("Errors:", r.errors)
