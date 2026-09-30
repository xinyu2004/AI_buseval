"""Find memory-controller read and write events.

The inventory is `perf list` plus event files under
`/sys/bus/event_source/devices`. CPU cache misses are not DDR traffic.
A PMU qualifies by having a read burst and a write burst, not by a fixed name.
"""
from __future__ import annotations

import re
from pathlib import Path

SOFTWARE_DEVICES = frozenset({"software", "tracepoint", "kprobe", "uprobe", "breakpoint"})
_IGNORED_PMUS = SOFTWARE_DEVICES | {"cpu"}
_SPEC = re.compile(r"^ {0,3}([A-Za-z0-9_]+)/([A-Za-z0-9_.]+)/")
_INSTANCE = re.compile(r"_\d+$")
_NOT_A_BURST = ("clk", "cycle", "act", "pchg", "precharge", "ratio", "alloc", "slot", "page_tbl")
_READ_RANK = ("cas_cmd.rd", "cas_count_read", "data_read", ".rd")
_WRITE_RANK = ("cas_cmd.wr", "cas_count_write", "data_write", ".wr")


def hardware_devices(names: list[str]) -> list[str]:
    """Drop software PMUs. The rest are devices we may count."""
    return [name for name in names if name not in SOFTWARE_DEVICES]


def devices_in(sysfs: Path) -> list[str]:
    if not sysfs.is_dir():
        return []
    names = sorted(path.name for path in sysfs.iterdir() if path.is_dir() or path.is_symlink())
    return hardware_devices(names)


def pmu_family(name: str) -> str | None:
    """`amd_umc_0` and `amd_umc` are one controller. `cpu` is not a DDR counter."""
    if name in _IGNORED_PMUS:
        return None
    base = _INSTANCE.sub("", name)
    if base in _IGNORED_PMUS:
        return None
    return base


def perf_list_events(text: str) -> list[tuple[str, str]]:
    """`(pmu, event)` from `perf list` lines such as `amd_umc/umc_cas_cmd.rd/`."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        match = _SPEC.match(line)
        if not match:
            continue
        key = (match.group(1), match.group(2))
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def ddr_events_in(sysfs: Path) -> list[tuple[str, str]]:
    """Event files under every non-CPU PMU, as `(device, event)`."""
    found: list[tuple[str, str]] = []
    if not sysfs.is_dir():
        return found
    for device in sorted(sysfs.iterdir()):
        if pmu_family(device.name) is None:
            continue
        events = device / "events"
        if not events.is_dir():
            continue
        for path in sorted(events.iterdir()):
            if path.is_file():
                found.append((device.name, path.name))
    return found


def ddr_role(name: str) -> str | None:
    """`read` or `write` when this counter is one data burst. Clocks are neither."""
    folded = name.lower().rstrip("/")
    if any(skip in folded for skip in _NOT_A_BURST):
        return None
    if folded.endswith(".wr") or folded.endswith("_wr") or "write" in folded:
        return "write"
    if folded.endswith(".rd") or folded.endswith("_rd") or "read" in folded:
        return "read"
    return None


def burst_role(name: str) -> str | None:
    """Read or write burst. Page-table and I/O-only beats are not DDR."""
    role = ddr_role(name)
    if role is None:
        return None
    folded = name.lower()
    if "_io_" in folded:
        return None
    if any(token in folded for token in ("cas", "dram", "ddr", "data_read", "data_write")):
        return role
    return None


def _is_channel(event: str) -> bool:
    return bool(_INSTANCE.search(event.lower().rstrip("/").rsplit("/", 1)[-1]))


def _rank(event: str, role: str) -> tuple[int, str]:
    folded = event.lower()
    keys = _READ_RANK if role == "read" else _WRITE_RANK
    for index, key in enumerate(keys):
        if key in folded:
            return (index, event)
    return (len(keys), event)


def select_ddr_events(
    listing: str,
    extras: list[tuple[str, str]] | None = None,
) -> tuple[str, ...] | None:
    """Events to count, or nothing if no controller publishes a read and a write.

    One aggregate pair wins. Per-channel beats are used only when no PMU has
    a single read counter and a single write counter, and they are not added
    on top of that pair.
    """
    rows = perf_list_events(listing)
    if extras:
        rows.extend(extras)
    grouped: dict[str, list[str]] = {}
    for pmu, event in rows:
        family = pmu_family(pmu)
        if family is None or burst_role(event) is None:
            continue
        grouped.setdefault(family, []).append(event)

    aggregate: list[tuple[str, list[str], list[str]]] = []
    channels: list[tuple[str, list[str], list[str]]] = []
    for family, events in grouped.items():
        unique = list(dict.fromkeys(events))
        agg_read = [event for event in unique if burst_role(event) == "read" and not _is_channel(event)]
        agg_write = [event for event in unique if burst_role(event) == "write" and not _is_channel(event)]
        ch_read = [event for event in unique if burst_role(event) == "read" and _is_channel(event)]
        ch_write = [event for event in unique if burst_role(event) == "write" and _is_channel(event)]
        if agg_read and agg_write:
            aggregate.append((family, agg_read, agg_write))
        elif ch_read and ch_write:
            channels.append((family, ch_read, ch_write))

    if aggregate:
        family, reads, writes = min(
            aggregate,
            key=lambda item: (_rank(min(item[1], key=lambda event: _rank(event, "read")), "read"), item[0]),
        )
        best_read = min(reads, key=lambda event: _rank(event, "read"))
        best_write = min(writes, key=lambda event: _rank(event, "write"))
        return (f"{family}/{best_read}/", f"{family}/{best_write}/")
    if not channels:
        return None
    family, reads, writes = min(channels, key=lambda item: (_rank(min(item[1], key=lambda event: _rank(event, "read")), "read"), item[0]))
    return tuple(f"{family}/{event}/" for event in sorted(reads) + sorted(writes))
