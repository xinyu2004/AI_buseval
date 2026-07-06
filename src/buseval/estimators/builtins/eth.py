"""ETH estimator: link × utilization, accounting for frame overhead."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("eth")
class EthEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["eth"]
        link_gbps = float(params.get("link_gbps", 1))
        util_pct = float(params.get("util_pct", 0.4))
        mtu = int(params.get("mtu", 1500))
        direction = params.get("direction", "both")

        overhead = coeffs["frame_overhead_bytes"]
        eff = mtu / (mtu + overhead)
        bw = link_gbps * 1000 * util_pct * eff / 8.0  # MB/s
        r, w = _split(bw, direction)

        assumptions = []
        if util_pct > get_coefficients()["alerts"]["aggressive_util_pct"]:
            assumptions.append(f"aggressive ETH util_pct={util_pct}")

        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "link_gbps": link_gbps,
                "util_pct": util_pct,
                "mtu": mtu,
                "frame_efficiency": round(eff, 4),
            },
            dominant_factor=f"{link_gbps}G × {util_pct:.0%} × eff {eff:.2f}",
            assumptions=assumptions,
        )


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
