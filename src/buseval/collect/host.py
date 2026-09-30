"""Perf inventory on the machine that is running `buseval collect`."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .perf_text import bytes_from_metric_text
from .pmu import ddr_events_in, devices_in, pmu_family, select_ddr_events

SYSFS_DEVICES = Path("/sys/bus/event_source/devices")


def machine_arch() -> str:
    return os.uname().machine


def amd_cpu() -> bool:
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "AuthenticAMD" in text


def require_perf() -> None:
    if shutil.which("perf") is None:
        raise RuntimeError("perf is not installed or not on PATH")


def perf_list_text() -> str:
    require_perf()
    proc = subprocess.run(["perf", "list"], capture_output=True, text=True)
    return (proc.stdout or "") + (proc.stderr or "")


def ddr_events(listing: str | None = None) -> tuple[str, ...] | None:
    """Memory-controller read and write events on this machine."""
    if listing is None:
        listing = perf_list_text()
    return select_ddr_events(listing, ddr_events_in(SYSFS_DEVICES))


def device_names() -> list[str]:
    return devices_in(SYSFS_DEVICES)


def _event_token(spec: str) -> str:
    parts = spec.strip("/").split("/")
    return parts[1] if len(parts) > 1 else parts[0]


def _integer_bytes(text: str) -> int | None:
    token = text.strip().split()[0] if text.strip() else ""
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if value < 1:
        return None
    nearest = int(round(value))
    if abs(value - nearest) > 1e-6:
        return None
    return nearest


def _sysfs_bytes(sysfs: Path, spec: str) -> int | None:
    """Bytes per count from an event's scale file. A fractional unit scale is not bytes."""
    pmu = spec.strip("/").split("/", 1)[0]
    event = _event_token(spec)
    if not sysfs.is_dir():
        return None
    found: list[int] = []
    for device in sorted(sysfs.iterdir()):
        if pmu_family(device.name) != pmu and device.name != pmu:
            continue
        events = device / "events"
        if not events.is_dir():
            continue
        scale_file = events / f"{event}.scale"
        raw = ""
        if scale_file.is_file():
            raw = scale_file.read_text(encoding="utf-8", errors="replace")
        else:
            body = events / event
            if body.is_file():
                for line in body.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("scale="):
                        raw = line.split("=", 1)[1]
                        break
        size = _integer_bytes(raw)
        if size is not None:
            found.append(size)
    if not found or len(set(found)) != 1:
        return None
    return found[0]


def metric_details_text() -> str:
    """perf's own metric expressions, including the byte factor in a bandwidth formula."""
    require_perf()
    proc = subprocess.run(["perf", "list", "--details"], capture_output=True, text=True)
    return (proc.stdout or "") + (proc.stderr or "")


def event_bytes(
    specs: tuple[str, ...],
    *,
    sysfs: Path | None = None,
    metric_text: str | None = None,
) -> int | None:
    """Bytes in one selected count. Sysfs scale first, then a perf metric expression."""
    root = SYSFS_DEVICES if sysfs is None else sysfs
    scaled = [size for size in (_sysfs_bytes(root, spec) for spec in specs) if size is not None]
    if scaled:
        return scaled[0] if len(set(scaled)) == 1 else None
    if metric_text is None:
        metric_text = metric_details_text()
    return bytes_from_metric_text(metric_text, [_event_token(spec) for spec in specs])
