"""CAN health report for one DBC file: one bus, load, messages, worst-case latency."""
from __future__ import annotations

from dataclasses import dataclass, field

from .parser import DbcBus, parse_dbc
from ..estimators.registry import get_coefficients

CLASSIC = "can"
CAN_FD = "canfd"
CLASSIC_MAX_KBPS = 1000.0
FD_ARB_MAX_KBPS = 1000.0
FD_DATA_MAX_KBPS = 8000.0
FD_LENGTHS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64}
FRAME_OVERHEAD_BITS = 47
YELLOW = 0.6
RED = 0.8


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
    standard: str = CLASSIC
    arbitration_kbps: float = 0.0
    data_kbps: float = 0.0
    illegal_count: int = 0


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
                    "standard": b.standard,
                    "arbitration_kbps": b.arbitration_kbps,
                    "data_kbps": b.data_kbps,
                    "illegal_count": b.illegal_count,
                }
                for b in self.buses
            ],
        }


def allowed_length(standard: str, length: int) -> bool:
    if standard == CAN_FD:
        return length in FD_LENGTHS
    return 0 <= length <= 8


def resolve_can_rates(
    bitrate_kbps: float | None = None,
    standard: str | None = None,
    arbitration_kbps: float | None = None,
    data_kbps: float | None = None,
) -> tuple[str, float, float]:
    """Return standard, arbitration kbps, and the rate the payload is measured against."""
    if standard not in (CLASSIC, CAN_FD):
        rate = float(bitrate_kbps) if bitrate_kbps else float(get_coefficients()["can"]["default_bitrate_kbps"])
        if rate > CLASSIC_MAX_KBPS:
            data = min(rate, FD_DATA_MAX_KBPS)
            arbitration = min(500.0, data)
            return CAN_FD, arbitration, data
        rate = min(max(rate, 1.0), CLASSIC_MAX_KBPS)
        return CLASSIC, rate, rate

    if standard == CLASSIC:
        rate = data_kbps if data_kbps is not None else bitrate_kbps
        if rate is None:
            rate = float(get_coefficients()["can"]["default_bitrate_kbps"])
        rate = min(max(float(rate), 1.0), CLASSIC_MAX_KBPS)
        return CLASSIC, rate, rate

    data = data_kbps if data_kbps is not None else bitrate_kbps
    if data is None:
        data = 2000.0
    data = min(max(float(data), 1.0), FD_DATA_MAX_KBPS)
    arbitration = 500.0 if arbitration_kbps is None else float(arbitration_kbps)
    arbitration = min(max(arbitration, 1.0), FD_ARB_MAX_KBPS, data)
    if data < arbitration:
        data = arbitration
    return CAN_FD, arbitration, data


def build_health_report(
    dbc_path: str,
    bitrate_kbps: float | None = None,
    *,
    standard: str | None = None,
    arbitration_kbps: float | None = None,
    data_kbps: float | None = None,
) -> HealthReport:
    chosen, arbitration, data = resolve_can_rates(bitrate_kbps, standard, arbitration_kbps, data_kbps)
    buses = parse_dbc(dbc_path, bitrate_kbps=data)
    out_buses: list[BusHealth] = []

    for b in buses:
        b.bitrate_kbps = data
        illegal = sum(1 for message in b.messages if not allowed_length(chosen, message.dlc))
        top = _message_rows(b)
        if illegal:
            out_buses.append(
                BusHealth(
                    name=b.name,
                    bitrate_kbps=data,
                    total_kbps=b.total_kbps,
                    load_pct=0.0,
                    verdict="CRITICAL",
                    top_messages=top,
                    worst_case_latency_ms=0.0,
                    suggestions=[_fit_suggestion(chosen, illegal)],
                    standard=chosen,
                    arbitration_kbps=arbitration,
                    data_kbps=data,
                    illegal_count=illegal,
                )
            )
            continue

        load = b.load_pct
        if load >= RED:
            verdict = "CRITICAL"
        elif load >= YELLOW:
            verdict = "WARN"
        else:
            verdict = "OK"
        out_buses.append(
            BusHealth(
                name=b.name,
                bitrate_kbps=data,
                total_kbps=b.total_kbps,
                load_pct=load,
                verdict=verdict,
                top_messages=top,
                worst_case_latency_ms=_worst_case_latency(b),
                suggestions=_suggestions(b, load),
                standard=chosen,
                arbitration_kbps=arbitration,
                data_kbps=data,
                illegal_count=0,
            )
        )

    return HealthReport(dbc_path=dbc_path, buses=out_buses)


def _message_rows(bus: DbcBus) -> list[dict]:
    return [
        {
            "name": message.name,
            "id": message.frame_id,
            "dlc": message.dlc,
            "cycle_ms": message.cycle_ms,
            "bps": round(message.bps, 1),
            "share_pct": round((message.bps / bus.total_bps * 100) if bus.total_bps else 0.0, 2),
        }
        for message in bus.messages
    ]


def _fit_suggestion(standard: str, count: int) -> str:
    if standard == CLASSIC:
        return f"{count} messages are longer than 8 bytes and do not fit classic CAN."
    return f"{count} messages have a length CAN FD does not allow."


def frame_latency(payload_bytes: int, bitrate_kbps: float, load: float) -> tuple[int, float, float, float]:
    """Bits on the wire, transmission ms, wait ms, and worst-case latency ms."""
    bits = payload_bytes * 8 + FRAME_OVERHEAD_BITS
    if bitrate_kbps <= 0:
        return bits, 0.0, 0.0, 0.0
    tx_ms = bits / bitrate_kbps
    if load >= 1.0:
        return bits, tx_ms, float("inf"), float("inf")
    wait_ms = tx_ms * 2.0 * load
    latency_ms = (wait_ms + tx_ms) / (1.0 - load)
    return bits, tx_ms, wait_ms, latency_ms


def latency_text(
    payload_bytes: int,
    bitrate_kbps: float,
    load: float,
    payload_kbps: float,
    *,
    standard: str,
) -> list[str]:
    from ..gui.i18n import t

    bits, tx_ms, wait_ms, latency_ms = frame_latency(payload_bytes, bitrate_kbps, load)
    fields = {
        "nbytes": payload_bytes,
        "overhead": FRAME_OVERHEAD_BITS,
        "bits": bits,
        "rate": bitrate_kbps,
        "rate_name": t("can_lat_rate_fd" if standard == CAN_FD else "can_lat_rate_can"),
        "payload": payload_kbps,
        "tx": tx_ms,
        "wait": wait_ms,
        "load": load * 100,
        "latency": latency_ms,
    }
    return [
        t(key).format(**fields)
        for key in ("can_lat_bits", "can_lat_tx", "can_lat_load", "can_lat_wait", "can_lat_total")
    ]


def _worst_case_latency(bus: DbcBus) -> float:
    if not bus.messages:
        return 0.0
    longest = max(message.dlc for message in bus.messages)
    return frame_latency(longest, bus.bitrate_kbps, bus.load_pct)[3]


def _suggestions(bus: DbcBus, load: float) -> list[str]:
    if load >= RED:
        return [f"Overloaded ({load:.0%}) at {bus.bitrate_kbps:.0f} kbps."]
    if load >= YELLOW:
        return [f"Near limit ({load:.0%}). Review periodic message cadences."]
    return []
