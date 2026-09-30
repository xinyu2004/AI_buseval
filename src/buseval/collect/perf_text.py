"""Parse `perf stat` text into a DDR measurement.

One count is one memory-controller burst. How many bytes that count stands
for comes from the running machine: a sysfs scale, a perf metric expression,
or the MB/s printed beside the count. There is no built-in 64.
"""
from __future__ import annotations

import re

from .pmu import ddr_role

_COUNT = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s+(\S+)")
_TIME = re.compile(r"([\d.]+)\s+seconds time elapsed")
_RATE = re.compile(r"#\s*([\d.]+)\s*MB/s\b")
_ANSI = re.compile(r"\033\[[0-9;]*m")
_WINDOW = re.compile(r"^count window\s+(\d+(?:\.\d+)?) s$")
_SIZE = re.compile(r"^count size\s+(\d+) B$")
_READ_LINE = re.compile(r"^DDR read\s+(\d+) times$")
_WRITE_LINE = re.compile(r"^DDR write\s+(\d+) times$")
_SKIP_UNITS = {"seconds", "msec", "ms", "task-clock", "CPUs", "GHz", "insn", "duration_time"}
SOURCE = "ddr"


def _near_integer(value: float) -> int | None:
    """A byte count, not a rounded guess. More than 0.05 away from an integer is refused."""
    if value < 1:
        return None
    nearest = int(round(value))
    if abs(value - nearest) > 0.05:
        return None
    return nearest


def factor_on_line(line: str, event: str) -> int | None:
    """Bytes in `event * N` or `N * event` on a perf metric line that uses duration."""
    if "duration" not in line or event not in line:
        return None
    width = len(event)
    start = 0
    while start <= len(line) - width:
        at = line.find(event, start)
        if at < 0:
            return None
        before = line[at - 1] if at else ""
        if before.isalnum() or before == "_":
            start = at + 1
            continue
        rest = line[at + width:].lstrip()
        match = re.match(r"\*\s*(\d+)", rest)
        if match:
            return int(match.group(1))
        prefix = re.search(r"(\d+)\s*\*\s*$", line[:at])
        if prefix:
            return int(prefix.group(1))
        start = at + 1
    return None


def bytes_from_metric_text(text: str, events: list[str]) -> int | None:
    """One byte size when every mentioned event agrees. A mismatch is no size."""
    found: list[int] = []
    for event in events:
        values = {factor_on_line(line, event) for line in text.splitlines()}
        values.discard(None)
        if not values:
            continue
        if len(values) != 1:
            return None
        found.append(values.pop())
    if not found or len(set(found)) != 1:
        return None
    return found[0]


def bytes_from_rates(events: dict[str, float], rates: dict[str, float], duration: float) -> int | None:
    """Invert `count * bytes / time = MB/s` when perf already printed the rate."""
    if duration <= 0:
        return None
    found: list[int] = []
    for name, count in events.items():
        rate = rates.get(name)
        if rate is None or count <= 0:
            continue
        size = _near_integer(rate * 1e6 * duration / count)
        if size is None:
            return None
        found.append(size)
    if not found or len(set(found)) != 1:
        return None
    return found[0]


def parse_perf_stat(text: str) -> dict:
    events: dict[str, float] = {}
    rates: dict[str, float] = {}
    duration = None
    duration_text = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "not counted" in stripped or "not supported" in stripped or "<not supported>" in stripped:
            continue
        time_match = _TIME.search(stripped)
        if time_match:
            duration_text = time_match.group(1)
            duration = float(duration_text)
            continue
        count_match = _COUNT.match(stripped)
        if not count_match:
            continue
        name = count_match.group(2)
        if name in _SKIP_UNITS:
            continue
        events[name] = events.get(name, 0.0) + float(count_match.group(1).replace(",", ""))
        rate_match = _RATE.search(stripped)
        if rate_match:
            rates[name] = float(rate_match.group(1))
    parsed = {"events": events, "duration_s": duration, "duration_text": duration_text}
    if duration is not None:
        parsed["count_bytes"] = bytes_from_rates(events, rates, duration)
    return parsed


def parse_report(text: str) -> dict | None:
    """The lines `ddr_pmu.sh` prints. Missing count lines mean this is not that report."""
    window = read = write = size = None
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        match = _WINDOW.match(line)
        if match:
            window = match.group(1)
            continue
        match = _SIZE.match(line)
        if match:
            size = int(match.group(1))
            continue
        match = _READ_LINE.match(line)
        if match:
            read = match.group(1)
            continue
        match = _WRITE_LINE.match(line)
        if match:
            write = match.group(1)
    if window is None or read is None or write is None:
        return None
    return {
        "events": {"ddr_read": float(read), "ddr_write": float(write)},
        "duration_s": float(window),
        "duration_text": window,
        "count_bytes": size,
    }


def parse_collect_text(text: str) -> dict:
    """Human report first, then raw `perf stat` text."""
    report = parse_report(text)
    if report is not None:
        return report
    return parse_perf_stat(text)


def _burst_counts(counted: dict[str, float]) -> tuple[float, float]:
    read = 0.0
    write = 0.0
    for name, count in counted.items():
        role = ddr_role(name)
        if role == "read":
            read += count
        elif role == "write":
            write += count
    return read, write


def _bandwidth(count: float, duration: float, line_bytes: float) -> float:
    if duration <= 0:
        return 0.0
    return count * line_bytes / duration / 1e6


def measurement_from_perf(parsed: dict, item_name: str = "DDR", source: str = SOURCE) -> dict:
    raw_size = parsed.get("count_bytes")
    line_bytes = int(raw_size) if raw_size else None
    duration = float(parsed.get("duration_s") or 0)
    counted = {name: count for name, count in (parsed.get("events") or {}).items() if count > 0}
    read_count, write_count = _burst_counts(counted)
    read_bw = write_bw = None
    if line_bytes:
        read_bw = round(_bandwidth(read_count, duration, line_bytes), 4)
        write_bw = round(_bandwidth(write_count, duration, line_bytes), 4)
    return {
        "kind": "ddr_bw",
        "source": source,
        "duration_s": duration,
        "duration_text": parsed.get("duration_text") or "",
        "count_bytes": line_bytes,
        "items": [
            {
                "name": item_name,
                "read_bw_mbps": read_bw,
                "write_bw_mbps": write_bw,
                "raw": counted,
            }
        ],
    }


def _time_token(text: str) -> str:
    """Elapsed seconds. Magenta in the window line and both formulas."""
    return f"[magenta]{text} s[/magenta]"


def _size_token(line_bytes: float) -> str:
    """Bytes in one count. Blue, so it is not the same color as the window."""
    return f"[bright_blue]{line_bytes:.0f} B[/bright_blue]"


def _formula_line(count: float, line_bytes: float, time_text: str, rate: float) -> str:
    return f"{count:.0f} × {_size_token(line_bytes)} / {_time_token(time_text)} = {rate:.4f} MB/s"


def panel_lines(body: dict) -> list[str]:
    """Count window, then count size when the byte size is known. The event name stays out of the box."""
    item = (body.get("items") or [{}])[0]
    raw_size = body.get("count_bytes")
    line_bytes = int(raw_size) if raw_size else None
    time_text = str(body.get("duration_text") or body.get("duration_s") or 0)
    read_count, write_count = _burst_counts(item.get("raw") or {})
    lines = [f"count window  {_time_token(time_text)}"]
    if line_bytes:
        lines.append(f"count size  {_size_token(line_bytes)}")
    lines.append("")
    lines.append(f"DDR read  {read_count:.0f} times")
    if line_bytes:
        lines.append(_formula_line(read_count, line_bytes, time_text, float(item.get("read_bw_mbps") or 0)))
    lines.append("")
    lines.append(f"DDR write  {write_count:.0f} times")
    if line_bytes:
        lines.append(_formula_line(write_count, line_bytes, time_text, float(item.get("write_bw_mbps") or 0)))
    return lines
