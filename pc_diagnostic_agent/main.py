"""
PC Diagnostic Agent
Entry point. Run this file.

Usage:
  python main.py                         — full scan (live mode)
  python main.py --dry-run               — scan, report only, no files moved
  python main.py --skip-defender         — skip Windows Defender scan
  python main.py --skip-junk             — skip junk file scan
  python main.py --skip-startup          — skip startup/registry audit
  python main.py --include-prefetch      — include Windows Prefetch files in junk scan
  python main.py --confirm-delete <id>   — permanently delete staged files from a scan
  python main.py --restore <id>          — restore all staged files from a scan
  python main.py --restore <id> --index <N>  — restore a single file (0-based index)
  python main.py --list-scans            — show all scans in PotentialDelete folder
  python main.py --report-only           — show reports for existing scans (no new scan)
"""

import argparse
import ctypes
import sys
import io
from datetime import datetime
from pathlib import Path

# Force UTF-8 output on Windows to avoid codec errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure site-packages and project root are on path
_SITE = r"C:\Users\Brock\OneDrive\Desktop\Master Folder\Lib\site-packages"
_ROOT = str(Path(__file__).parent)
for _p in [_SITE, _ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rich.console import Console

from config import POTENTIAL_DELETE_DIR, REPORTS_DIR
from modules import quarantine

console = Console()


def _check_elevation() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _make_scan_id() -> str:
    return datetime.now().strftime("scan_%Y-%m-%d_%H-%M-%S")


def run_scan(args) -> None:
    scan_id = _make_scan_id()
    dry_run = args.dry_run

    if not _check_elevation():
        console.print(
            "[yellow]WARNING: Not running as Administrator.[/yellow]\n"
            "Some paths (C:\\Windows\\Temp, registry HKLM keys, Windows Defender) "
            "may be inaccessible. Re-run as Admin for a complete scan.\n"
        )

    console.print(f"[bold cyan]Starting scan: {scan_id}[/bold cyan]")
    if dry_run:
        console.print("[yellow]DRY RUN — no files will be moved[/yellow]\n")

    # Create staging folder upfront (even for dry-run, so manifest has a home)
    if not dry_run:
        quarantine.create_scan_dir(scan_id)

    # ── Performance ──────────────────────────────────────────────────────────
    console.print("[dim]Collecting performance metrics...[/dim]")
    from modules.performance import collect as collect_perf
    perf = collect_perf()

    # ── Startup Audit ─────────────────────────────────────────────────────────
    startup = None
    if not args.skip_startup:
        console.print("[dim]Auditing startup entries...[/dim]")
        from modules.startup_audit import audit
        startup = audit()
    else:
        from modules.startup_audit import StartupAuditReport
        startup = StartupAuditReport()

    # ── Junk Cleaner ─────────────────────────────────────────────────────────
    junk = None
    if not args.skip_junk:
        console.print("[dim]Scanning for junk files...[/dim]")
        from modules.junk_cleaner import scan as scan_junk, JunkScanReport
        junk = scan_junk(
            dry_run=dry_run,
            scan_id=scan_id,
            include_prefetch=args.include_prefetch,
        )
    else:
        from modules.junk_cleaner import JunkScanReport
        junk = JunkScanReport()

    # ── Defender Scan ─────────────────────────────────────────────────────────
    defender = None
    if not args.skip_defender:
        # Scan the staging folder (confirming nothing malicious slipped in)
        # Falls back to scanning %TEMP% if staging folder is empty/not created
        scan_target = str(POTENTIAL_DELETE_DIR / scan_id) if (POTENTIAL_DELETE_DIR / scan_id).exists() else "C:\\Windows\\Temp"
        console.print(f"[dim]Running Defender scan on: {scan_target}[/dim]")
        from modules.defender_scan import scan as defender_scan
        defender = defender_scan(scan_target)

    # ── Recommendations ───────────────────────────────────────────────────────
    from modules.recommendations import generate
    recs = generate(perf, startup, junk, defender)

    # ── Output ────────────────────────────────────────────────────────────────
    from output.reporter import print_report, write_report
    print_report(scan_id, perf, startup, junk, defender, recs, dry_run=dry_run)
    write_report(scan_id, perf, startup, junk, defender, recs, dry_run=dry_run)


def cmd_confirm_delete(scan_id: str) -> None:
    # Run Defender on staging folder before offering deletion
    from modules.defender_scan import scan as defender_scan
    console.print(f"[dim]Running Defender scan on staging folder before deletion...[/dim]")
    scan_dir = quarantine.get_scan_dir(scan_id)
    if scan_dir.exists():
        result = defender_scan(str(scan_dir))
        if result.threats_found:
            console.print(f"[bold red]STOP: Defender found threats in the staging folder:[/bold red]")
            for t in result.threats_found:
                console.print(f"  [red]• {t}[/red]")
            console.print("[red]Deletion blocked. Open Windows Security to handle these threats first.[/red]")
            return
        elif not result.skipped:
            console.print(f"[green]Defender scan: CLEAN[/green]")

    quarantine.confirm_delete(scan_id)


def cmd_restore(scan_id: str, index: int = None) -> None:
    if index is not None:
        quarantine.restore_file(scan_id, index)
    else:
        # Restore ALL staged files
        scan_dir = quarantine.get_scan_dir(scan_id)
        entries = quarantine._load_manifest(scan_dir)
        staged = [e for e in entries if e.status == "staged"]
        if not staged:
            console.print("[yellow]No staged files to restore.[/yellow]")
            return
        console.print(f"Restoring {len(staged)} files...")
        for i in range(len(staged)):
            quarantine.restore_file(scan_id, i)


def cmd_list_scans() -> None:
    scans = quarantine.list_scans()
    if not scans:
        console.print("[dim]No scans found in PotentialDelete folder.[/dim]")
        return

    from rich.table import Table
    from rich import box
    t = Table(title="Scans in PotentialDelete", box=box.SIMPLE)
    t.add_column("Scan ID")
    t.add_column("Files", justify="right")
    t.add_column("Size", justify="right")
    t.add_column("Status Breakdown")

    for scan_id in scans:
        summary = quarantine.scan_summary(scan_id)
        status_str = "  ".join(f"{k}:{v}" for k, v in summary["by_status"].items())
        t.add_row(
            scan_id,
            str(summary["total_files"]),
            f"{summary['total_size_mb']:.1f} MB",
            status_str,
        )
    console.print(t)


def main():
    parser = argparse.ArgumentParser(
        description="PC Diagnostic Agent — scan, report, and safely stage junk for review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Scan modes
    parser.add_argument("--dry-run",          action="store_true", help="Report only — no files moved")
    parser.add_argument("--skip-defender",    action="store_true", help="Skip Windows Defender scan")
    parser.add_argument("--skip-junk",        action="store_true", help="Skip junk file scan")
    parser.add_argument("--skip-startup",     action="store_true", help="Skip startup/registry audit")
    parser.add_argument("--include-prefetch", action="store_true", help="Include Windows Prefetch files in junk scan")

    # Post-scan actions
    parser.add_argument("--confirm-delete",   metavar="SCAN_ID",   help="Permanently delete staged files from a scan")
    parser.add_argument("--restore",          metavar="SCAN_ID",   help="Restore staged files from a scan")
    parser.add_argument("--index",            type=int, default=None, help="File index for --restore (0-based). Omit to restore all.")
    parser.add_argument("--list-scans",       action="store_true", help="List all scans in PotentialDelete folder")

    args = parser.parse_args()

    if args.list_scans:
        cmd_list_scans()
    elif args.confirm_delete:
        cmd_confirm_delete(args.confirm_delete)
    elif args.restore:
        cmd_restore(args.restore, args.index)
    else:
        run_scan(args)


if __name__ == "__main__":
    main()
