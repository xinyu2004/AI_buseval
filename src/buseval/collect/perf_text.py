"""Parse `perf stat` text into a DDR measurement.

One count is one memory-controller burst. The burst size is the cache-line
width from the cpu coefficients. CPU cache misses are not counted here.
"""
from __future__ import annotations

import re

from ..estimators.registry import get_coefficients
from .pmu import ddr_role

_COUNT = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s+(\S+)")
_TIME = re.compile(r"([\d.]+)\s+seconds time elapsed")
_ANSI = re.compile(r"\033\[[0-9;]*m")
_WINDOW = re.compile(r"^count window\s+(\d+(?:\.\d+)?) s$")
_READ_LINE = re.compile(r"^DDR read\s+(\d+) times$")
_WRITE_LINE = re.compile(r"^DDR write\s+(\d+) times$")
_SKIP_UNITS = {"seconds", "msec", "ms", "task-clock", "CPUs", "GHz", "insn", "duration_time"}
SOURCE = "ddr"


def parse_perf_stat(text: str) -> dict:
    events: dict[str, float] = {}
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
    return {"events": events, "duration_s": duration, "duration_text": duration_text}


def parse_report(text: str) -> dict | None:
    """The lines `pc_pmu.sh` prints. Missing lines mean this is not that report."""
    window = read = write = None
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        match = _WINDOW.match(line)
        if match:
            window = match.group(1)
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
    coeffs = get_coefficients().get("cpu", {})
    line_bytes = float(coeffs.get("cache_line_bytes", 64))
    duration = float(parsed.get("duration_s") or 0)
    counted = {name: count for name, count in (parsed.get("events") or {}).items() if count > 0}
    read_count, write_count = _burst_counts(counted)
    return {
        "kind": "ddr_bw",
        "source": source,
        "duration_s": duration,
        "duration_text": parsed.get("duration_text") or "",
        "items": [
            {
                "name": item_name,
                "read_bw_mbps": round(_bandwidth(read_count, duration, line_bytes), 4),
                "write_bw_mbps": round(_bandwidth(write_count, duration, line_bytes), 4),
                "raw": counted,
            }
        ],
    }


def _time_token(text: str) -> str:
    """Same elapsed seconds in the window line and both formulas."""
    return f"[magenta]{text} s[/magenta]"


def _formula_line(count: float, line_bytes: float, time_text: str, rate: float) -> str:
    return f"{count:.0f} × {line_bytes:.0f} B / {_time_token(time_text)} = {rate:.4f} MB/s"


def panel_lines(body: dict) -> list[str]:
    """Count window, then DDR read, then DDR write. The event name stays out of the box."""
    item = (body.get("items") or [{}])[0]
    coeffs = get_coefficients().get("cpu", {})
    line_bytes = float(coeffs.get("cache_line_bytes", 64))
    time_text = str(body.get("duration_text") or body.get("duration_s") or 0)
    read_count, write_count = _burst_counts(item.get("raw") or {})
    return [
        f"count window  {_time_token(time_text)}",
        "",
        f"DDR read  {read_count:.0f} times",
        _formula_line(read_count, line_bytes, time_text, float(item.get("read_bw_mbps") or 0)),
        "",
        f"DDR write  {write_count:.0f} times",
        _formula_line(write_count, line_bytes, time_text, float(item.get("write_bw_mbps") or 0)),
    ]
