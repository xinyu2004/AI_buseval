"""FLASH estimator (NAND / eMMC / UFS): sequential × util × random penalty."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("flash")
class FlashEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["flash"]
        seq_r = float(params.get("seq_read_mbps", 0))
        seq_w = float(params.get("seq_write_mbps", 0))
        util_pct = float(params.get("util_pct", coeffs["default_util_pct"]))
        random_ratio = float(params.get("random_ratio", 0.0))
        flash_type = params.get("type", "nand")

        eff = (1.0 - random_ratio) + coeffs["random_penalty"] * random_ratio
        read = seq_r * util_pct * eff
        write = seq_w * util_pct * eff

        assumptions = []
        if util_pct > get_coefficients()["alerts"]["aggressive_util_pct"]:
            assumptions.append(f"aggressive FLASH util_pct={util_pct}")

        return BandwidthEstimate(
            read_bw_mbps=round(read, 4),
            write_bw_mbps=round(write, 4),
            breakdown={
                "type": flash_type,
                "seq_read_mbps": seq_r,
                "seq_write_mbps": seq_w,
                "util_pct": util_pct,
                "random_ratio": random_ratio,
                "effective_factor": round(eff, 4),
            },
            dominant_factor=f"{flash_type} seq R/W {seq_r}/{seq_w} × util {util_pct:.0%}",
            assumptions=assumptions,
        )
