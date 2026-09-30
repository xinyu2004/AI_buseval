"""Count DDR bursts and turn the perf text into a measurement.

Discovery is `host`, the administrator step is `privilege`, and the
numbers are `perf_text`. This module only sequences those steps.
Scripts under `embedded/` are the copy you take to a board; they are not
started here.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .host import amd_cpu, ddr_events, device_names, event_bytes, machine_arch, perf_list_text, require_perf
from .perf_text import measurement_from_perf, parse_collect_text, parse_perf_stat
from .pmu import ddr_role
from .privilege import run_privileged

_MODPROBE_RISK = """\
This CPU is AMD and the memory-controller counters are not registered.
buseval collect will run: sudo modprobe amd_uncore

What changes: the in-tree AMD uncore driver is loaded until reboot.
It is not written to the boot configuration.

Effect: the kernel then exposes memory-controller perf counters.
Those counters do not modify memory contents. Counting them still
needs administrator rights, as in the notice above.
"""

_COUNT_RISK = """\
buseval collect will run: sudo perf stat -a
for about {duration} second(s).

What changes: nothing is written to boot configuration, and
perf_event_paranoid is not modified. The administrator password
is used only for this one command.

What it can see: read and write bursts at the memory controller
for the whole machine, including DMA from other programs, the
NIC, and the GPU. On a shared machine, a local user can thus
see the size of that traffic.

What it cannot see: file contents, and it does not change memory.
"""


def embedded_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "embedded"


def probe_text(listing: str | None = None, devices: list[str] | None = None) -> str:
    """Show hardware PMUs and the memory-controller events we would count."""
    if devices is None:
        devices = device_names()
    pair = ddr_events(listing if listing is not None else perf_list_text())
    events = list(pair) if pair else []
    size = event_bytes(tuple(events)) if events else None
    lines = [
        f"arch={machine_arch()}",
        "devices=" + " ".join(devices),
        "events=" + ",".join(events),
        "bytes=" if size is None else f"bytes={size}",
    ]
    if not events:
        raise RuntimeError("perf list has no memory-controller read and write events\n" + "\n".join(lines))
    return "\n".join(lines)


def _load_amd_uncore() -> bool:
    """Load the AMD uncore driver when this CPU is AMD and the PMU is missing."""
    if not amd_cpu() or shutil.which("modprobe") is None:
        return False
    proc = run_privileged(["modprobe", "amd_uncore"], notice=_MODPROBE_RISK)
    return proc.returncode == 0


def _perf_stat_text(events: tuple[str, ...], duration: float) -> str:
    """System-wide counts. Stats come back on stderr so root does not open /tmp."""
    cmd = ["perf", "stat", "-a", "-e", ",".join(events), "--", "sleep", str(duration)]
    proc = run_privileged(cmd, notice=_COUNT_RISK.format(duration=duration))
    text = proc.stderr or ""
    if proc.returncode != 0:
        detail = (text + (proc.stdout or "")).strip()
        raise RuntimeError(detail or "perf stat failed")
    return text


def _item_name(name: str) -> str:
    return "DDR" if name in {"CPU0", "DDR"} else name


def collect_machine(
    *,
    duration: float = 1.0,
    name: str = "DDR",
) -> dict:
    """Count memory-controller bursts for `duration` seconds."""
    require_perf()
    pair = ddr_events()
    if pair is None and _load_amd_uncore():
        pair = ddr_events()
    if pair is None:
        raise RuntimeError("perf list has no memory-controller read and write events")
    parsed = parse_perf_stat(_perf_stat_text(pair, duration))
    looked_up = event_bytes(pair)
    if looked_up is not None:
        parsed["count_bytes"] = looked_up
    body = measurement_from_perf(parsed, item_name=_item_name(name), source="ddr")
    raw = (body.get("items") or [{}])[0].get("raw") or {}
    if not any(ddr_role(event) for event in raw):
        raise RuntimeError("memory controller events did not count: " + " ".join(pair))
    return body


def measurement_from_text(text: str, name: str = "DDR") -> dict:
    """Parse perf text copied back from a board. Does not run perf."""
    parsed = parse_collect_text(text)
    return measurement_from_perf(parsed, item_name=_item_name(name), source="ddr")
