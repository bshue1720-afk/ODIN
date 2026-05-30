"""
Reporter Module
Rich terminal output + plain .txt report file saved to reports/
"""

import platform
import socket
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from config import REPORTS_DIR, POTENTIAL_DELETE_DIR


console = Console()


def _severity_color(severity: str) -> str:
    return {"CRITICAL": "bold red", "WARN": "yellow", "INFO": "cyan"}.get(severity, "white")


def _status_color(percent: float, warn: float, crit: float = None) -> str:
    if crit and percent >= crit:
        return "bold red"
    if percent >= warn:
        return "yellow"
    return "green"


def print_report(
    scan_id: str,
    perf,
    startup,
    junk,
    defender,
    recs,
    dry_run: bool = False,
):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_tag = "[DRY RUN] " if dry_run else ""

    console.print()
    console.rule(f"[bold cyan]PC Diagnostic Agent  |  {mode_tag}{now_str}[/bold cyan]")
    console.print()

    # ── System Info ──────────────────────────────────────────────────────────
    console.print(Panel(
        f"[bold]Host:[/bold] {socket.gethostname()}   "
        f"[bold]OS:[/bold] {platform.system()} {platform.release()} {platform.machine()}   "
        f"[bold]Scan ID:[/bold] {scan_id}",
        title="[bold]System Info[/bold]",
        border_style="dim",
    ))

    # ── Performance ──────────────────────────────────────────────────────────
    from config import CPU_WARN_THRESHOLD, RAM_WARN_THRESHOLD, DISK_WARN_THRESHOLD

    console.print("\n[bold underline]PERFORMANCE[/bold underline]")

    cpu_col = _status_color(perf.cpu_percent, CPU_WARN_THRESHOLD)
    console.print(f"  CPU Usage:   [{cpu_col}]{perf.cpu_percent:.1f}%[/{cpu_col}]")

    ram_col = _status_color(perf.ram_percent, RAM_WARN_THRESHOLD)
    console.print(f"  RAM Usage:   [{ram_col}]{perf.ram_percent:.1f}%[/{ram_col}]   "
                  f"({perf.ram_used_gb:.1f} GB / {perf.ram_total_gb:.1f} GB)")

    disk_table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1))
    disk_table.add_column("Drive")
    disk_table.add_column("Used %", justify="right")
    disk_table.add_column("Free", justify="right")
    disk_table.add_column("Total", justify="right")
    disk_table.add_column("Health")

    for d in perf.disks:
        col = _status_color(d.percent, 80, DISK_WARN_THRESHOLD)
        h_col = "green" if d.health == "PASS" else ("red" if d.health == "FAIL" else "dim")
        disk_table.add_row(
            d.mountpoint,
            f"[{col}]{d.percent:.1f}%[/{col}]",
            f"{d.free_gb:.1f} GB",
            f"{d.total_gb:.1f} GB",
            f"[{h_col}]{d.health}[/{h_col}]",
        )
    console.print(disk_table)

    if perf.errors:
        for err in perf.errors:
            console.print(f"  [dim]  Warning: {err}[/dim]")

    # ── Startup Audit ─────────────────────────────────────────────────────────
    console.print("\n[bold underline]STARTUP AUDIT[/bold underline]")
    console.print(f"  Total entries: {len(startup.entries)}   Flagged: [yellow]{len(startup.flagged)}[/yellow]")

    if startup.flagged:
        st = Table(show_header=True, box=box.SIMPLE, padding=(0, 1))
        st.add_column("Name", style="cyan")
        st.add_column("Source")
        st.add_column("Issue", style="yellow")
        st.add_column("Path", no_wrap=False)
        for e in startup.flagged:
            actionable = [f for f in e.flags if f != "KNOWN_SAFE"]
            st.add_row(e.name[:40], e.source, ", ".join(actionable), e.path[:60])
        console.print(st)

    if startup.errors:
        for err in startup.errors:
            console.print(f"  [dim]Warning: {err}[/dim]")

    # ── Junk Scan ─────────────────────────────────────────────────────────────
    console.print("\n[bold underline]JUNK SCAN[/bold underline]")
    size_str = _format_size(junk.staged_size_bytes)
    action_label = "Would stage" if dry_run else "Staged"
    console.print(f"  Files found:      {len(junk.files)}")
    console.print(f"  {action_label}:       [green]{junk.staged_count}[/green]  ({size_str})")
    console.print(f"  Skipped in-use:   {junk.skipped_in_use}")
    console.print(f"  Skipped recent:   {junk.skipped_recent}")
    if not dry_run and junk.staged_count > 0:
        console.print(f"  Staging folder:   [dim]{POTENTIAL_DELETE_DIR / scan_id}[/dim]")

    # ── Defender ─────────────────────────────────────────────────────────────
    console.print("\n[bold underline]DEFENDER SCAN[/bold underline]")
    if defender is None:
        console.print("  [dim]Skipped[/dim]")
    elif defender.skipped:
        console.print(f"  [yellow]Skipped:[/yellow] {defender.skip_reason}")
    elif defender.threats_found:
        console.print(f"  [bold red]THREATS FOUND ({len(defender.threats_found)}):[/bold red]")
        for t in defender.threats_found:
            console.print(f"    [red]• {t}[/red]")
    else:
        console.print(f"  [green]CLEAN[/green]  (exit code {defender.exit_code})")

    # ── Recommendations ───────────────────────────────────────────────────────
    console.print("\n[bold underline]RECOMMENDATIONS[/bold underline]")
    if not recs:
        console.print("  [green]No issues found. System looks healthy.[/green]")
    else:
        for rec in recs:
            col = _severity_color(rec.severity)
            console.print(f"  [{col}][{rec.severity}][/{col}]  {rec.message}")
            if rec.action:
                console.print(f"         [dim]-> {rec.action}[/dim]")

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    console.rule("[dim]Summary[/dim]")
    if not dry_run and junk.staged_count > 0:
        console.print(f"  [cyan]{junk.staged_count} files ({_format_size(junk.staged_size_bytes)}) staged for review.[/cyan]")
        console.print(f"  To permanently delete:  [bold]python main.py --confirm-delete {scan_id}[/bold]")
        console.print(f"  To restore a file:      [bold]python main.py --restore {scan_id} --index <N>[/bold]")
    elif dry_run:
        console.print(f"  [yellow]Dry-run complete. No files were moved.[/yellow]")
        console.print(f"  Run without --dry-run to stage files.")

    report_path = REPORTS_DIR / f"report_{scan_id}.txt"
    console.print(f"  Full report saved:      [dim]{report_path}[/dim]")
    console.print()


def write_report(
    scan_id: str,
    perf,
    startup,
    junk,
    defender,
    recs,
    dry_run: bool = False,
) -> Path:
    """Write a plain-text report file. Returns the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"report_{scan_id}.txt"

    lines = []
    sep = "=" * 70
    thin = "-" * 70

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines += [
        sep,
        f"  PC DIAGNOSTIC AGENT REPORT",
        f"  Scan ID : {scan_id}",
        f"  Date    : {now_str}",
        f"  Mode    : {'DRY RUN' if dry_run else 'LIVE'}",
        f"  Host    : {socket.gethostname()}",
        f"  OS      : {platform.system()} {platform.release()} {platform.machine()}",
        sep, "",
    ]

    lines += ["PERFORMANCE", thin]
    lines += [
        f"  CPU Usage  : {perf.cpu_percent:.1f}%",
        f"  RAM Usage  : {perf.ram_percent:.1f}%  ({perf.ram_used_gb:.1f} GB / {perf.ram_total_gb:.1f} GB)",
    ]
    for d in perf.disks:
        health = d.health
        lines.append(f"  Disk {d.mountpoint:<6}: {d.percent:.1f}%  ({d.free_gb:.1f} GB free of {d.total_gb:.1f} GB)  Health: {health}")
    if perf.errors:
        for err in perf.errors:
            lines.append(f"  [WARNING] {err}")
    lines.append("")

    lines += ["STARTUP AUDIT", thin]
    lines += [
        f"  Total entries : {len(startup.entries)}",
        f"  Flagged       : {len(startup.flagged)}",
    ]
    if startup.entries:
        lines.append("")
        lines.append(f"  {'Name':<40} {'Source':<20} {'Flags':<25} {'Publisher'}")
        lines.append(f"  {'-'*40} {'-'*20} {'-'*25} {'-'*30}")
        for e in startup.entries:
            flag_str = ", ".join(e.flags) if e.flags else "—"
            lines.append(f"  {e.name[:40]:<40} {e.source:<20} {flag_str:<25} {e.publisher[:30]}")
    lines.append("")

    lines += ["JUNK SCAN", thin]
    lines += [
        f"  Files found  : {len(junk.files)}",
        f"  Staged       : {junk.staged_count}  ({_format_size(junk.staged_size_bytes)})",
        f"  Skipped      : {junk.skipped_in_use} in-use, {junk.skipped_recent} recent, {junk.skipped_symlink} symlinks",
    ]
    if junk.files:
        lines.append("")
        lines.append(f"  {'Original Path':<70} {'Category':<16} {'Size'}")
        lines.append(f"  {'-'*70} {'-'*16} {'-'*12}")
        for f in junk.files[:500]:  # cap at 500 lines in report
            size_str = _format_size(f.size_bytes)
            lines.append(f"  {str(f.path)[:70]:<70} {f.category:<16} {size_str}")
        if len(junk.files) > 500:
            lines.append(f"  ... and {len(junk.files) - 500} more files (see MANIFEST.json)")
    lines.append("")

    lines += ["DEFENDER SCAN", thin]
    if defender is None:
        lines.append("  Skipped")
    elif defender.skipped:
        lines.append(f"  Skipped: {defender.skip_reason}")
    elif defender.threats_found:
        lines.append(f"  THREATS FOUND ({len(defender.threats_found)}):")
        for t in defender.threats_found:
            lines.append(f"    • {t}")
        lines.append("")
        lines.append("  Raw Defender output:")
        lines.append(thin)
        lines.append(defender.raw_output)
    else:
        lines.append(f"  CLEAN  (exit code {defender.exit_code})")
    lines.append("")

    lines += ["RECOMMENDATIONS", thin]
    if not recs:
        lines.append("  No issues found. System looks healthy.")
    else:
        for rec in recs:
            lines.append(f"  [{rec.severity}]  {rec.message}")
            if rec.action:
                lines.append(f"          -> {rec.action}")
    lines.append("")

    lines += [sep, "END OF REPORT", sep]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024**3):.2f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024**2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
