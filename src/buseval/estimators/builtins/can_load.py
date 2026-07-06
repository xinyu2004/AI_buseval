"""CAN estimator (load mode): bitrate × load% × payload efficiency."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("can")
class CanLoadEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["can"]
        bitrate_mbps = params.get("bitrate_mbps") or (coeffs["default_bitrate_kbps"] / 1000.0)
        load_pct = float(params.get("load_pct", 0.3))
        direction = params.get("direction", "both")

        bw = bitrate_mbps * load_pct * coeffs["payload_efficiency"]
        r, w = _split(bw, direction)

        assumptions = []
        if load_pct > get_coefficients()["alerts"]["aggressive_can_load_pct"]:
            assumptions.append(f"aggressive CAN load_pct={load_pct}")

        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "bitrate_mbps": bitrate_mbps,
                "load_pct": load_pct,
                "payload_efficiency": coeffs["payload_efficiency"],
            },
            dominant_factor=f"{bitrate_mbps}Mbps × load {load_pct:.0%}",
            assumptions=assumptions,
        )


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
