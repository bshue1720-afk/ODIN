"""
Defender Scan Module
Runs Windows Defender CLI (MpCmdRun.exe) with -DisableRemediation.
Defender reports findings — it takes NO automatic action.
We surface results as recommendations only.
"""

import subprocess
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from config import DEFENDER_TIMEOUT


@dataclass
class DefenderScanResult:
    scan_target: str
    threats_found: List[str] = field(default_factory=list)
    exit_code: int = 0
    raw_output: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error: Optional[str] = None


def _find_mpcmdrun() -> Optional[Path]:
    """Locate MpCmdRun.exe — check fixed path first, then versioned platform path."""
    fixed = Path(r"C:\Program Files\Windows Defender\MpCmdRun.exe")
    if fixed.exists():
        return fixed

    # Versioned path: C:\ProgramData\Microsoft\Windows Defender\Platform\<version>\MpCmdRun.exe
    platform_base = Path(r"C:\ProgramData\Microsoft\Windows Defender\Platform")
    if platform_base.exists():
        candidates = sorted(platform_base.glob("*/MpCmdRun.exe"), reverse=True)
        if candidates:
            return candidates[0]

    return None


def _is_defender_service_running() -> bool:
    """Check if the WinDefend service is active."""
    try:
        result = subprocess.run(
            ["sc", "query", "WinDefend"],
            capture_output=True, text=True, timeout=10
        )
        return "RUNNING" in result.stdout.upper()
    except Exception:
        return False


def _parse_threats(output: str) -> List[str]:
    """Extract threat names from MpCmdRun.exe stdout."""
    threats = []
    for line in output.splitlines():
        # Defender outputs lines like: "Threat         : TrojanDropper:Win32/..."
        match = re.search(r"(?:Threat|ThreatName)\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            threat = match.group(1).strip()
            if threat and threat not in threats:
                threats.append(threat)
    return threats


def scan(target_path: str) -> DefenderScanResult:
    """
    Run a Defender custom-path scan on target_path.
    -ScanType 3 = custom path scan
    -DisableRemediation = report only, take no automatic action
    """
    mpcmdrun = _find_mpcmdrun()

    if mpcmdrun is None:
        return DefenderScanResult(
            scan_target=target_path,
            skipped=True,
            skip_reason="MpCmdRun.exe not found on this system",
        )

    if not _is_defender_service_running():
        return DefenderScanResult(
            scan_target=target_path,
            skipped=True,
            skip_reason="Windows Defender service (WinDefend) is not running",
        )

    target = Path(target_path)
    if not target.exists():
        return DefenderScanResult(
            scan_target=target_path,
            skipped=True,
            skip_reason=f"Target path does not exist: {target_path}",
        )

    cmd = [
        str(mpcmdrun),
        "-Scan",
        "-ScanType", "3",
        "-File", str(target),
        "-DisableRemediation",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DEFENDER_TIMEOUT,
        )
        raw_output = result.stdout + result.stderr
        threats = _parse_threats(raw_output)

        return DefenderScanResult(
            scan_target=target_path,
            threats_found=threats,
            exit_code=result.returncode,
            raw_output=raw_output,
        )

    except subprocess.TimeoutExpired:
        return DefenderScanResult(
            scan_target=target_path,
            skipped=True,
            skip_reason=f"Defender scan timed out after {DEFENDER_TIMEOUT}s",
        )
    except Exception as e:
        return DefenderScanResult(
            scan_target=target_path,
            error=str(e),
        )


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else r"C:\Windows\Temp"
    r = scan(target)
    print(f"Target:   {r.scan_target}")
    print(f"Skipped:  {r.skipped}  ({r.skip_reason})" if r.skipped else f"Exit code: {r.exit_code}")
    if r.threats_found:
        print(f"THREATS:  {r.threats_found}")
    else:
        print("Result:   CLEAN")
    if r.error:
        print(f"Error:    {r.error}")
