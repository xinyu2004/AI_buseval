"""MIPI CSI / DSI estimator: resolution × fps × bpp, with lane capacity check."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("mipi_csi")
class MipiCsiEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        return _estimate(params, is_dsi=False)


@register("mipi_dsi")
class MipiDsiEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        return _estimate(params, is_dsi=True)


def _estimate(params: dict, is_dsi: bool) -> BandwidthEstimate:
    key = "mipi"
    coeffs = get_coefficients()[key]
    w = int(params["width"])
    h = int(params["height"])
    fps = float(params["fps"])
    bpp = float(params.get("bpp", 8))
    lanes = int(params.get("lanes", 1))
    count = int(params.get("count", 1))
    if count < 1:
        raise ValueError(f"mipi count must be >= 1, got {count}")

    frame_bytes = w * h * bpp / 8.0
    per_stream_mbps = frame_bytes * fps / 1e6  # MB/s per stream
    aggregate_mbps = per_stream_mbps * count   # total across all VC streams

    lane_cap_key = "dsi_lane_capacity_gbps" if is_dsi else "lane_capacity_gbps"
    lane_cap_gbps = coeffs[lane_cap_key]
    lane_cap_mbps = lanes * lane_cap_gbps * 1e9 / 8.0 / 1e6  # MB/s

    assumptions = []
    if aggregate_mbps > lane_cap_mbps:
        assumptions.append(
            f"aggregate {aggregate_mbps:.1f} MB/s ({count} streams) exceeds "
            f"{lanes}-lane capacity {lane_cap_mbps:.1f} MB/s"
        )

    # CSI = input to DDR (write); DSI = output from DDR (read)
    if is_dsi:
        read_bw, write_bw = aggregate_mbps, 0.0
        kind = "DSI"
    else:
        read_bw, write_bw = 0.0, aggregate_mbps
        kind = "CSI"

    if count > 1:
        dominant = f"{count}x {w}x{h}@{fps}fps×{bpp}bpp ({lanes} lanes, {count} streams)"
    else:
        dominant = f"{w}x{h}@{fps}fps×{bpp}bpp ({lanes} lanes)"

    return BandwidthEstimate(
        read_bw_mbps=round(read_bw, 4),
        write_bw_mbps=round(write_bw, 4),
        breakdown={
            "kind": kind,
            "width": w,
            "height": h,
            "fps": fps,
            "bpp": bpp,
            "lanes": lanes,
            "count": count,
            "frame_bytes": int(frame_bytes),
            "per_stream_mbps": round(per_stream_mbps, 4),
            "aggregate_mbps": round(aggregate_mbps, 4),
            "lane_capacity_mbps": round(lane_cap_mbps, 2),
        },
        dominant_factor=dominant,
        assumptions=assumptions,
    )
