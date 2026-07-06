"""SPI estimator: min(clock limit, transfer demand)."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("spi")
class SpiEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["spi"]
        clock_mhz = float(params.get("clock_mhz", coeffs["default_clock_mhz"]))
        xfer_bytes = float(params.get("xfer_bytes", 0))
        xfer_hz = float(params.get("xfer_hz", 0))
        direction = params.get("direction", "both")  # SPI full-duplex default

        clock_bw = clock_mhz * 1e6 / 8.0 / 1e6  # MB/s
        demand_bw = (xfer_bytes * xfer_hz) / 1e6  # MB/s
        bw = min(clock_bw, demand_bw) if demand_bw > 0 else clock_bw
        r, w = _split(bw, direction)

        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "clock_mhz": clock_mhz,
                "clock_bw_mbps": round(clock_bw, 4),
                "demand_bw_mbps": round(demand_bw, 4),
                "chosen": "demand" if demand_bw > 0 and demand_bw < clock_bw else "clock",
            },
            dominant_factor=f"clock {clock_mhz}MHz, xfer {xfer_bytes}B×{xfer_hz}Hz",
            assumptions=[],
        )


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
