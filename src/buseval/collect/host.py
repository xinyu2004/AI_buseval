"""Perf inventory on the machine that is running `buseval collect`."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .pmu import ddr_events_in, devices_in, select_ddr_events

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
