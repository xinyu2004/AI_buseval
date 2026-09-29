"""DBC parser: extract messages and per-bus summary using cantools."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..estimators.registry import get_coefficients


@dataclass
class DbcMessage:
    name: str
    frame_id: str
    dlc: int
    cycle_ms: float
    bps: float  # bits per second contributed


@dataclass
class DbcBus:
    name: str
    bitrate_kbps: float
    messages: list[DbcMessage] = field(default_factory=list)

    @property
    def total_bps(self) -> float:
        return sum(m.bps for m in self.messages)

    @property
    def total_kbps(self) -> float:
        return self.total_bps / 1000.0

    @property
    def load_pct(self) -> float:
        if self.bitrate_kbps <= 0:
            return 0.0
        return self.total_kbps / self.bitrate_kbps


def parse_dbc(dbc_path: str, bitrate_kbps: float | None = None) -> list[DbcBus]:
    """Parse one DBC as one CAN bus. Bitrate comes from the caller; a DBC does not carry it reliably."""
    import cantools

    coeffs = get_coefficients()["can"]
    default_bitrate = bitrate_kbps or coeffs["default_bitrate_kbps"]
    bus = DbcBus(name="default", bitrate_kbps=float(default_bitrate))

    db = cantools.database.load_file(dbc_path)
    for msg in db.messages:
        cycle = msg.cycle_time or 0
        payload_bits = msg.length * 8
        bps = (payload_bits * (1000.0 / cycle)) if cycle and cycle > 0 else 0.0
        bus.messages.append(
            DbcMessage(
                name=msg.name,
                frame_id=f"0x{msg.frame_id:X}",
                dlc=msg.length,
                cycle_ms=float(cycle) if cycle else 0.0,
                bps=round(bps, 2),
            )
        )

    bus.messages.sort(key=lambda m: m.bps, reverse=True)
    return [bus]
