"""USB estimator: nominal link rate × utilization × protocol efficiency."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("usb")
class UsbEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["usb"]
        version = str(params.get("version", "3"))
        util_pct = float(params.get("util_pct", 0.5))
        direction = params.get("direction", "both")

        nominal = coeffs["nominal_mbps"].get(version)
        if nominal is None:
            raise ValueError(
                f"Unknown USB version '{version}'. "
                f"Supported: {list(coeffs['nominal_mbps'])}"
            )
        eff = coeffs["protocol_efficiency"]
        bw = nominal * util_pct * eff / 8.0  # Mbps → MB/s
        r, w = _split(bw, direction)

        assumptions = []
        if util_pct > get_coefficients()["alerts"]["aggressive_util_pct"]:
            assumptions.append(f"aggressive USB util_pct={util_pct}")

        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "version": version,
                "nominal_mbps": nominal,
                "util_pct": util_pct,
                "protocol_efficiency": eff,
            },
            dominant_factor=f"USB{version} nominal {nominal}Mbps × {util_pct:.0%}",
            assumptions=assumptions,
        )


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
