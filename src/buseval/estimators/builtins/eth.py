"""ETH estimator: link × utilization, accounting for frame overhead."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate, note


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
        capacity = link_gbps * 1000 * eff / 8.0  # MB/s the link can carry

        # A connected upstream (VENC bitstream, or another picture) is the DDR
        # read. Do not add link × util on top of that stream.
        if "source_input_mbps" in params:
            bw = float(params["source_input_mbps"])
            assumptions = []
            if bw > capacity:
                assumptions.append(note(
                    f"stream {bw:.1f} MB/s exceeds {link_gbps:g}G link {capacity:.1f} MB/s",
                    "red",
                ))
            return BandwidthEstimate(
                read_bw_mbps=round(bw, 4),
                write_bw_mbps=0.0,
                breakdown={
                    "link_gbps": link_gbps,
                    "mtu": mtu,
                    "frame_efficiency": round(eff, 4),
                    "link_capacity_mbps": round(capacity, 4),
                    "source_input_mbps": round(bw, 4),
                    "source": params.get("source"),
                },
                dominant_factor=f"bitstream {bw:.1f} MB/s",
                assumptions=assumptions,
            )

        bw = capacity * util_pct
        r, w = _split(bw, direction)

        assumptions = []
        if util_pct > get_coefficients()["alerts"]["aggressive_util_pct"]:
            assumptions.append(note(f"aggressive ETH util_pct={util_pct}", "red"))

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
