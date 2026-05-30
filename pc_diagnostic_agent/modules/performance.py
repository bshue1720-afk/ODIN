"""
Performance Module
Collects CPU, RAM, disk, and basic network metrics via psutil.
"""

import psutil
from dataclasses import dataclass, field
from typing import List
import sys

try:
    import wmi
    WMI_AVAILABLE = True
except ImportError:
    WMI_AVAILABLE = False


@dataclass
class DiskInfo:
    mountpoint: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float
    health: str  # "PASS", "FAIL", "UNKNOWN"


@dataclass
class PerformanceReport:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disks: List[DiskInfo]
    net_sent_mb: float
    net_recv_mb: float
    errors: List[str] = field(default_factory=list)


def _check_disk_health(mountpoint: str) -> str:
    """Query SMART-level disk health via WMI. Returns PASS/FAIL/UNKNOWN."""
    if not WMI_AVAILABLE:
        return "UNKNOWN"
    try:
        c = wmi.WMI()
        for disk in c.Win32_DiskDrive():
            status = (disk.Status or "").strip().upper()
            if status == "OK":
                return "PASS"
            elif status in ("PRED FAIL", "ERROR", "DEGRADED", "UNKNOWN"):
                return "FAIL"
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def collect() -> PerformanceReport:
    errors = []

    # CPU — 1-second blocking sample for accuracy
    try:
        cpu = psutil.cpu_percent(interval=1)
    except Exception as e:
        cpu = 0.0
        errors.append(f"CPU read error: {e}")

    # RAM
    try:
        ram = psutil.virtual_memory()
        ram_percent = ram.percent
        ram_used_gb = ram.used / (1024 ** 3)
        ram_total_gb = ram.total / (1024 ** 3)
    except Exception as e:
        ram_percent = ram_used_gb = ram_total_gb = 0.0
        errors.append(f"RAM read error: {e}")

    # Disks
    disks = []
    try:
        for part in psutil.disk_partitions(all=False):
            # Skip CD-ROM and network drives
            if "cdrom" in part.opts or part.fstype == "":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                health = _check_disk_health(part.mountpoint)
                disks.append(DiskInfo(
                    mountpoint=part.mountpoint,
                    total_gb=usage.total / (1024 ** 3),
                    used_gb=usage.used / (1024 ** 3),
                    free_gb=usage.free / (1024 ** 3),
                    percent=usage.percent,
                    health=health,
                ))
            except PermissionError:
                errors.append(f"Disk {part.mountpoint}: permission denied")
            except Exception as e:
                errors.append(f"Disk {part.mountpoint}: {e}")
    except Exception as e:
        errors.append(f"Disk partition read error: {e}")

    # Network I/O
    try:
        net = psutil.net_io_counters()
        net_sent_mb = net.bytes_sent / (1024 ** 2)
        net_recv_mb = net.bytes_recv / (1024 ** 2)
    except Exception as e:
        net_sent_mb = net_recv_mb = 0.0
        errors.append(f"Network read error: {e}")

    return PerformanceReport(
        cpu_percent=cpu,
        ram_percent=ram_percent,
        ram_used_gb=ram_used_gb,
        ram_total_gb=ram_total_gb,
        disks=disks,
        net_sent_mb=net_sent_mb,
        net_recv_mb=net_recv_mb,
        errors=errors,
    )


if __name__ == "__main__":
    r = collect()
    print(f"CPU:  {r.cpu_percent:.1f}%")
    print(f"RAM:  {r.ram_percent:.1f}%  ({r.ram_used_gb:.1f} GB / {r.ram_total_gb:.1f} GB)")
    for d in r.disks:
        print(f"Disk {d.mountpoint}: {d.percent:.1f}%  ({d.free_gb:.1f} GB free)  Health: {d.health}")
    print(f"Net:  Sent {r.net_sent_mb:.1f} MB  Recv {r.net_recv_mb:.1f} MB")
    if r.errors:
        print("Errors:", r.errors)
