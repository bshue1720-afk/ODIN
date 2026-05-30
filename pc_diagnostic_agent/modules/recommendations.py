"""
Recommendations Engine
Pure logic — takes all report dataclasses as input, emits Recommendation objects.
No side effects, no file I/O.
"""

from dataclasses import dataclass, field
from typing import List

from config import CPU_WARN_THRESHOLD, RAM_WARN_THRESHOLD, DISK_WARN_THRESHOLD, STARTUP_COUNT_WARN


@dataclass
class Recommendation:
    severity: str   # "INFO", "WARN", "CRITICAL"
    message: str
    action: str = ""


def generate(perf_report, startup_report, junk_report, defender_result) -> List[Recommendation]:
    recs: List[Recommendation] = []

    # ── Performance ──────────────────────────────────────────────────────────

    if perf_report.cpu_percent >= CPU_WARN_THRESHOLD:
        recs.append(Recommendation(
            severity="WARN",
            message=f"CPU usage is high at {perf_report.cpu_percent:.1f}%",
            action="Check Task Manager for runaway processes.",
        ))

    if perf_report.ram_percent >= RAM_WARN_THRESHOLD:
        used = perf_report.ram_used_gb
        total = perf_report.ram_total_gb
        recs.append(Recommendation(
            severity="WARN",
            message=f"RAM usage is {perf_report.ram_percent:.1f}% ({used:.1f} GB / {total:.1f} GB used)",
            action="Close unused applications or consider a RAM upgrade.",
        ))

    for disk in perf_report.disks:
        if disk.percent >= DISK_WARN_THRESHOLD:
            recs.append(Recommendation(
                severity="CRITICAL",
                message=f"Disk {disk.mountpoint} is at {disk.percent:.1f}% — only {disk.free_gb:.1f} GB free",
                action="Run junk cleaner or archive old files.",
            ))
        elif disk.percent >= 80:
            recs.append(Recommendation(
                severity="WARN",
                message=f"Disk {disk.mountpoint} is at {disk.percent:.1f}% ({disk.free_gb:.1f} GB free)",
                action="Consider cleaning up files soon.",
            ))
        if disk.health == "FAIL":
            recs.append(Recommendation(
                severity="CRITICAL",
                message=f"Disk {disk.mountpoint} SMART health check FAILED",
                action="Back up your data immediately and consider replacing this drive.",
            ))

    # ── Startup Audit ─────────────────────────────────────────────────────────

    total_startup = len(startup_report.entries)
    if total_startup > STARTUP_COUNT_WARN:
        recs.append(Recommendation(
            severity="INFO",
            message=f"{total_startup} startup items detected (recommended: <{STARTUP_COUNT_WARN})",
            action="Review and disable non-essential startup entries in Task Manager > Startup.",
        ))

    for entry in startup_report.flagged:
        if "ORPHANED" in entry.flags:
            recs.append(Recommendation(
                severity="WARN",
                message=f"Startup entry '{entry.name}' points to a missing file: {entry.path}",
                action="Safe to disable or remove this startup entry.",
            ))
        elif "UNSIGNED" in entry.flags:
            recs.append(Recommendation(
                severity="INFO",
                message=f"Startup entry '{entry.name}' is unsigned ({entry.path})",
                action="Verify this is a trusted application. If unknown, research before keeping.",
            ))

    # ── Junk Cleaner ─────────────────────────────────────────────────────────

    if junk_report.staged_size_bytes > 0:
        size_gb = junk_report.staged_size_bytes / (1024 ** 3)
        size_str = f"{size_gb:.2f} GB" if size_gb >= 1 else f"{junk_report.staged_size_bytes / (1024**2):.1f} MB"
        recs.append(Recommendation(
            severity="INFO",
            message=f"{junk_report.staged_count} junk files ({size_str}) staged in PotentialDelete",
            action="Review PotentialDelete folder, then run --confirm-delete to free space.",
        ))
    elif junk_report.files and junk_report.staged_count == 0:
        recs.append(Recommendation(
            severity="INFO",
            message=f"{len(junk_report.files)} junk files found but not staged (dry-run mode)",
            action="Run without --dry-run to stage files for review.",
        ))

    if junk_report.skipped_in_use > 0:
        recs.append(Recommendation(
            severity="INFO",
            message=f"{junk_report.skipped_in_use} files skipped (in use or permission denied)",
            action="Close open applications and re-run to capture more junk files.",
        ))

    # ── Defender ─────────────────────────────────────────────────────────────

    if defender_result is not None:
        if defender_result.skipped:
            recs.append(Recommendation(
                severity="INFO",
                message=f"Defender scan skipped: {defender_result.skip_reason}",
                action="Run as Administrator or enable Windows Defender to get scan results.",
            ))
        elif defender_result.threats_found:
            threat_list = ", ".join(defender_result.threats_found)
            recs.append(Recommendation(
                severity="CRITICAL",
                message=f"Windows Defender found {len(defender_result.threats_found)} threat(s): {threat_list}",
                action="Open Windows Security immediately and follow remediation steps.",
            ))
        elif defender_result.error:
            recs.append(Recommendation(
                severity="WARN",
                message=f"Defender scan encountered an error: {defender_result.error}",
                action="Check Windows Security app manually.",
            ))

    # Sort: CRITICAL first, then WARN, then INFO
    order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    recs.sort(key=lambda r: order.get(r.severity, 3))

    return recs
