"""
Quarantine Module — THE SAFETY LAYER
All file-touching operations route through here.

Flow:
  stage_file()      → shutil.move() to PotentialDelete staging folder + MANIFEST.json
  confirm_delete()  → rich table review + "YES" gate + Path.unlink()
  restore_file()    → moves file back to original_path
"""

import json
import shutil
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from config import POTENTIAL_DELETE_DIR


MANIFEST_FILENAME = "MANIFEST.json"


@dataclass
class ManifestEntry:
    original_path: str
    staged_path: str
    size_bytes: int
    moved_at: str
    category: str
    status: str  # "staged", "deleted", "restored", "failed"
    error: Optional[str] = None


def _load_manifest(scan_dir: Path) -> List[ManifestEntry]:
    manifest_path = scan_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return []
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [ManifestEntry(**entry) for entry in raw]


def _save_manifest(scan_dir: Path, entries: List[ManifestEntry]) -> None:
    manifest_path = scan_dir / MANIFEST_FILENAME
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, indent=2)


def get_scan_dir(scan_id: str) -> Path:
    return POTENTIAL_DELETE_DIR / scan_id


def create_scan_dir(scan_id: str) -> Path:
    scan_dir = get_scan_dir(scan_id)
    scan_dir.mkdir(parents=True, exist_ok=True)
    return scan_dir


def stage_file(
    source_path: Path,
    category: str,
    scan_id: str,
    dry_run: bool = False,
) -> Optional[ManifestEntry]:
    """
    Move a file to the PotentialDelete staging folder.
    Returns a ManifestEntry on success, None on skip/failure.
    Skips symlinks entirely.
    """
    source_path = Path(source_path)

    # Never follow or move symlinks
    if source_path.is_symlink():
        return None

    if not source_path.exists():
        return None

    try:
        size = source_path.stat().st_size
    except (PermissionError, OSError):
        return None

    scan_dir = get_scan_dir(scan_id)
    category_dir = scan_dir / category
    dest_name = source_path.name

    # Avoid filename collisions: append short hash of original path
    dest_path = category_dir / dest_name
    if dest_path.exists():
        path_hash = hashlib.md5(str(source_path).encode()).hexdigest()[:6]
        stem = source_path.stem
        suffix = source_path.suffix
        # Truncate stem if combined path would be too long
        max_stem = 200 - len(path_hash) - len(suffix)
        dest_path = category_dir / f"{stem[:max_stem]}_{path_hash}{suffix}"

    if dry_run:
        return ManifestEntry(
            original_path=str(source_path),
            staged_path=str(dest_path),
            size_bytes=size,
            moved_at=datetime.now().isoformat(),
            category=category,
            status="dry_run",
        )

    category_dir.mkdir(parents=True, exist_ok=True)

    try:
        shutil.move(str(source_path), str(dest_path))
        entry = ManifestEntry(
            original_path=str(source_path),
            staged_path=str(dest_path),
            size_bytes=size,
            moved_at=datetime.now().isoformat(),
            category=category,
            status="staged",
        )
    except PermissionError as e:
        entry = ManifestEntry(
            original_path=str(source_path),
            staged_path=str(dest_path),
            size_bytes=size,
            moved_at=datetime.now().isoformat(),
            category=category,
            status="failed",
            error=f"In use or permission denied: {e}",
        )
    except OSError as e:
        entry = ManifestEntry(
            original_path=str(source_path),
            staged_path=str(dest_path),
            size_bytes=size,
            moved_at=datetime.now().isoformat(),
            category=category,
            status="failed",
            error=str(e),
        )

    # Append to manifest
    entries = _load_manifest(scan_dir)
    entries.append(entry)
    _save_manifest(scan_dir, entries)

    return entry


def confirm_delete(scan_id: str) -> dict:
    """
    Permanently delete all staged files for a scan session.
    Requires user to type exactly "YES" at the prompt.
    Returns a summary dict.
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()
    scan_dir = get_scan_dir(scan_id)

    if not scan_dir.exists():
        console.print(f"[red]Scan folder not found: {scan_dir}[/red]")
        return {"deleted": 0, "failed": 0, "not_found": 0}

    entries = _load_manifest(scan_dir)
    staged = [e for e in entries if e.status == "staged"]

    if not staged:
        console.print("[yellow]No staged files found for this scan.[/yellow]")
        return {"deleted": 0, "failed": 0, "not_found": 0}

    total_size_mb = sum(e.size_bytes for e in staged) / (1024 ** 2)

    table = Table(title=f"Files staged for deletion — {scan_id}", show_lines=True)
    table.add_column("Original Path", style="cyan", no_wrap=False)
    table.add_column("Category", style="magenta")
    table.add_column("Size", justify="right")

    for e in staged:
        size_str = f"{e.size_bytes / 1024:.1f} KB" if e.size_bytes < 1024**2 else f"{e.size_bytes / (1024**2):.1f} MB"
        table.add_row(e.original_path, e.category, size_str)

    console.print(table)
    console.print(f"\n[bold]Total: {len(staged)} files  ({total_size_mb:.1f} MB)[/bold]")
    console.print("\n[bold red]This action is PERMANENT and cannot be undone.[/bold red]")

    response = input('\nType YES to permanently delete all files (anything else cancels): ')

    if response.strip() != "YES":
        console.print("[yellow]Cancelled. No files deleted.[/yellow]")
        return {"deleted": 0, "failed": 0, "not_found": 0}

    deleted = failed = not_found = 0

    for entry in entries:
        if entry.status != "staged":
            continue
        staged_path = Path(entry.staged_path)
        if not staged_path.exists():
            entry.status = "not_found"
            not_found += 1
            continue
        try:
            staged_path.unlink()
            entry.status = "deleted"
            deleted += 1
        except Exception as e:
            entry.status = "failed"
            entry.error = str(e)
            failed += 1

    _save_manifest(scan_dir, entries)

    console.print(f"\n[green]Deleted: {deleted}[/green]  [red]Failed: {failed}[/red]  [yellow]Not found: {not_found}[/yellow]")
    return {"deleted": deleted, "failed": failed, "not_found": not_found}


def restore_file(scan_id: str, index: int) -> bool:
    """
    Restore a single staged file back to its original path.
    index is 0-based position in the staged entries list.
    Returns True on success.
    """
    from rich.console import Console
    console = Console()

    scan_dir = get_scan_dir(scan_id)
    entries = _load_manifest(scan_dir)
    staged = [e for e in entries if e.status == "staged"]

    if index < 0 or index >= len(staged):
        console.print(f"[red]Invalid index {index}. Staged count: {len(staged)}[/red]")
        return False

    entry = staged[index]
    staged_path = Path(entry.staged_path)
    original_path = Path(entry.original_path)

    if not staged_path.exists():
        console.print(f"[red]Staged file not found: {staged_path}[/red]")
        return False

    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_path), str(original_path))
        entry.status = "restored"
        _save_manifest(scan_dir, entries)
        console.print(f"[green]Restored:[/green] {original_path}")
        return True
    except Exception as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        return False


def list_scans() -> List[str]:
    """Return scan IDs (folder names) in PotentialDelete, newest first."""
    if not POTENTIAL_DELETE_DIR.exists():
        return []
    return sorted(
        [d.name for d in POTENTIAL_DELETE_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )


def scan_summary(scan_id: str) -> dict:
    """Return a quick summary of a scan's manifest."""
    scan_dir = get_scan_dir(scan_id)
    entries = _load_manifest(scan_dir)
    total_size = sum(e.size_bytes for e in entries)
    by_status = {}
    for e in entries:
        by_status[e.status] = by_status.get(e.status, 0) + 1
    return {
        "scan_id": scan_id,
        "total_files": len(entries),
        "total_size_mb": round(total_size / (1024 ** 2), 2),
        "by_status": by_status,
    }
