"""Parse `perf stat` text into a cpu_bw measurement.

The shell adapter runs perf. This module turns that text into meas.json so
tests do not need a live PMU. Cache-line size comes from the cpu coefficients.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..estimators.registry import get_coefficients

_COUNT = re.compile(r"^\s*([\d,]+(?:\.\d+)?)\s+(\S+)")
_TIME = re.compile(r"([\d.]+)\s+seconds time elapsed")
_SKIP_UNITS = {"seconds", "msec", "ms", "task-clock", "CPUs", "GHz", "insn"}


def parse_perf_stat(text: str) -> dict:
    events: dict[str, float] = {}
    duration = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "not counted" in stripped or "not supported" in stripped or "<not supported>" in stripped:
            continue
        time_match = _TIME.search(stripped)
        if time_match:
            duration = float(time_match.group(1))
            continue
        count_match = _COUNT.match(stripped)
        if not count_match:
            continue
        name = count_match.group(2)
        if name in _SKIP_UNITS:
            continue
        events[name] = float(count_match.group(1).replace(",", ""))
    return {"events": events, "duration_s": duration}


def measurement_from_perf(parsed: dict, item_name: str = "CPU0") -> dict:
    coeffs = get_coefficients().get("cpu", {})
    line_bytes = float(coeffs.get("cache_line_bytes", 64))
    duration = float(parsed.get("duration_s") or 0)
    events = parsed.get("events") or {}

    def bandwidth(event: str) -> float | None:
        if duration <= 0 or event not in events:
            return None
        return events[event] * line_bytes / duration / 1e6

    read_bw = bandwidth("l2d_cache_refill")
    write_bw = bandwidth("l2d_cache_wb")
    if read_bw is None and write_bw is None and "bus_access" in events:
        read_bw = bandwidth("bus_access")
        write_bw = 0.0
    return {
        "kind": "cpu_bw",
        "source": "arm_pmu",
        "duration_s": duration,
        "items": [
            {
                "name": item_name,
                "read_bw_mbps": round(read_bw or 0.0, 4),
                "write_bw_mbps": round(write_bw or 0.0, 4),
                "raw": events,
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn perf stat text into meas.json")
    parser.add_argument("perf_text")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--name", default="CPU0")
    args = parser.parse_args(argv)
    text = Path(args.perf_text).read_text(encoding="utf-8", errors="replace")
    body = measurement_from_perf(parse_perf_stat(text), item_name=args.name)
    Path(args.output).write_text(json.dumps(body, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
