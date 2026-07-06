"""CAN health report: per-bus load, Top-N messages, worst-case latency, suggestions."""
from __future__ import annotations

from dataclasses import dataclass, field

from .parser import DbcBus, parse_dbc
from ..estimators.registry import get_coefficients


@dataclass
class BusHealth:
    name: str
    bitrate_kbps: float
    total_kbps: float
    load_pct: float
    verdict: str  # OK | WARN | CRITICAL
    top_messages: list[dict]
    worst_case_latency_ms: float
    suggestions: list[str] = field(default_factory=list)


@dataclass
class HealthReport:
    dbc_path: str
    buses: list[BusHealth]

    def to_dict(self) -> dict:
        return {
            "dbc_path": self.dbc_path,
            "buses": [
                {
                    "name": b.name,
                    "bitrate_kbps": b.bitrate_kbps,
                    "total_kbps": round(b.total_kbps, 2),
                    "load_pct": round(b.load_pct, 4),
                    "verdict": b.verdict,
                    "top_messages": b.top_messages,
                    "worst_case_latency_ms": round(b.worst_case_latency_ms, 3),
                    "suggestions": b.suggestions,
                }
                for b in self.buses
            ],
        }


def build_health_report(dbc_path: str, bitrate_kbps: float | None = None) -> HealthReport:
    buses = parse_dbc(dbc_path, bitrate_kbps=bitrate_kbps)
    out_buses: list[BusHealth] = []

    for b in buses:
        load = b.load_pct
        if load >= 0.7:
            verdict = "CRITICAL"
        elif load >= 0.6:
            verdict = "WARN"
        else:
            verdict = "OK"

        top = [
            {
                "name": m.name,
                "id": m.frame_id,
                "dlc": m.dlc,
                "cycle_ms": m.cycle_ms,
                "bps": round(m.bps, 1),
                "share_pct": round((m.bps / b.total_bps * 100) if b.total_bps else 0.0, 2),
            }
            for m in b.messages[:10]
        ]

        latency = _worst_case_latency(b)
        suggestions = _suggestions(b, load)

        out_buses.append(
            BusHealth(
                name=b.name,
                bitrate_kbps=b.bitrate_kbps,
                total_kbps=b.total_kbps,
                load_pct=load,
                verdict=verdict,
                top_messages=top,
                worst_case_latency_ms=latency,
                suggestions=suggestions,
            )
        )

    return HealthReport(dbc_path=dbc_path, buses=out_buses)


def _worst_case_latency(bus: DbcBus) -> float:
    """Simplified worst-case frame latency (ms).

    latency ≈ (worst_arbitration + longest_frame_tx) / (1 - load)
    For load >= 1 the bus is over-saturated; cap to a large value.
    """
    if not bus.messages:
        return 0.0
    # longest frame transmission time (classic CAN, 8-byte max frame ≈ 135 bits on wire)
    longest_dlc = max(m.dlc for m in bus.messages)
    longest_bits = longest_dlc * 8 + 47  # 47 bits overhead (SOF+arb+CRC+ACK+IFS, approx)
    longest_tx_ms = (longest_bits / (bus.bitrate_kbps * 1000)) * 1000.0
    load = bus.load_pct
    if load >= 1.0:
        return float("inf")
    # worst-case arbitration backoff scales with bus occupancy
    arb_ms = longest_tx_ms * 2.0 * load
    return (arb_ms + longest_tx_ms) / (1.0 - load)


def _suggestions(bus: DbcBus, load: float) -> list[str]:
    out = []
    if load >= 0.9:
        out.append("Bus saturated — must restructure: split messages across buses or migrate to CAN-FD.")
    elif load >= 0.7:
        out.append(
            f"Overloaded ({load:.0%}). Consider upgrading {bus.bitrate_kbps:.0f}kbps → "
            "500kbps/1Mbps, or split the bus."
        )
    elif load >= 0.6:
        out.append(f"Near limit ({load:.0%}). Review periodic message cadences.")
    return out
