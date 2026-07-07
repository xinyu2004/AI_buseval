"""GPU and Display estimators."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("gpu")
class GpuEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["gpu"]
        w = int(params["width"])
        h = int(params["height"])
        fps = float(params["fps"])
        bpp = float(params.get("bpp", 32))
        overdraw = float(params.get("overdraw", coeffs["default_overdraw"]))

        bw = w * h * fps * bpp * overdraw / 8.0 / 1e6  # MB/s
        return BandwidthEstimate(
            read_bw_mbps=round(bw, 4),
            write_bw_mbps=round(bw, 4),
            breakdown={
                "width": w,
                "height": h,
                "fps": fps,
                "bpp": bpp,
                "overdraw": overdraw,
            },
            dominant_factor=f"{w}x{h}@{fps}×{bpp}bpp, overdraw {overdraw}",
            assumptions=[],
        )


@register("display")
class DisplayEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["display"]
        # Pipeline source (e.g. ISP→Display direct): use pre-computed source_input_mbps.
        if "source_input_mbps" in params:
            bw = float(params["source_input_mbps"])
            return BandwidthEstimate(
                read_bw_mbps=round(bw, 4),
                write_bw_mbps=0.0,
                breakdown={
                    "source_input_mbps": round(bw, 4),
                    "source": params.get("source"),
                },
                dominant_factor=f"Display {bw:.1f}MB/s from {params.get('source','?')}",
                assumptions=[],
            )
        w = int(params["width"])
        h = int(params["height"])
        fps = float(params["fps"])
        bpp = float(params.get("bpp", coeffs["default_bpp"]))

        bw = w * h * fps * bpp / 8.0 / 1e6  # MB/s
        return BandwidthEstimate(
            read_bw_mbps=round(bw, 4),
            write_bw_mbps=0.0,
            breakdown={
                "width": w,
                "height": h,
                "fps": fps,
                "bpp": bpp,
            },
            dominant_factor=f"{w}x{h}@{fps}×{bpp}bpp",
            assumptions=[],
        )
