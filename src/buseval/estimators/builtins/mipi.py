"""MIPI CSI / DSI estimator: resolution × fps × bpp, with lane capacity check.

Three modes:
  1. Master (standalone): compute frame stream from width/height/fps/bpp/count.
     CSI writes to DDR; DSI reads from DDR.
  2. Pipeline with master source: inherit image dims from the source master,
     compute frame stream, same DDR direction as master mode.
  3. Pipeline with pipeline source (e.g. DSI source: DISP0): receive
     source_input_mbps (upstream's carried bandwidth). Use it ONLY for lane
     capacity validation — DDR traffic is NOT added (the upstream pipeline
     already counts the DDR access). This avoids double-counting: Display reads
     the framebuffer from DDR, DSI carries that data to the panel via p2p.
"""
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
    kind = "DSI" if is_dsi else "CSI"
    source = params.get("source")

    # --- Mode 3: pipeline source (e.g. DSI sourced from Display) ---
    # source_input_mbps is the upstream's carried bandwidth. We use it for lane
    # capacity validation only — DDR traffic is 0 (upstream already counts it).
    if "source_input_mbps" in params:
        carried_mbps = float(params["source_input_mbps"])
        lanes = int(params.get("lanes", 1))
        lane_cap_key = "dsi_lane_capacity_gbps" if is_dsi else "lane_capacity_gbps"
        lane_cap_mbps = lanes * coeffs[lane_cap_key] * 1e9 / 8.0 / 1e6
        # DSI p2p: data comes from upstream (Display), but DSI controller still
        # reads descriptors/config from DDR (~2% of carried bandwidth).
        ctrl_overhead = float(coeffs.get("control_overhead_pct", 0.02))
        ddr_read = carried_mbps * ctrl_overhead if is_dsi else 0.0
        ddr_write = carried_mbps * ctrl_overhead if not is_dsi else 0.0

        assumptions = []
        if carried_mbps > lane_cap_mbps:
            assumptions.append(
                f"carried {carried_mbps:.1f} MB/s exceeds {lanes}-lane capacity "
                f"{lane_cap_mbps:.1f} MB/s"
            )

        return BandwidthEstimate(
            read_bw_mbps=round(ddr_read, 4),   # small descriptor/config read from DDR
            write_bw_mbps=round(ddr_write, 4),  # small descriptor/config write to DDR
            breakdown={
                "kind": kind,
                "mode": "p2p",
                "source": source,
                "carried_mbps": round(carried_mbps, 4),
                "lanes": lanes,
                "lane_capacity_mbps": round(lane_cap_mbps, 2),
            },
            dominant_factor=f"{kind} {carried_mbps:.1f}MB/s ({lanes} lanes) from {source}",
            assumptions=assumptions,
        )

    # --- Mode 1 & 2: standalone or master-source pipeline ---
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

    # CSI = input to DDR (write dominant); DSI = output from DDR (read dominant)
    # Both also have a small control/descriptor overhead in the opposite direction (~2%).
    ctrl_overhead = float(coeffs.get("control_overhead_pct", 0.02))
    if is_dsi:
        read_bw, write_bw = aggregate_mbps, aggregate_mbps * ctrl_overhead
    else:
        read_bw, write_bw = aggregate_mbps * ctrl_overhead, aggregate_mbps

    if count > 1:
        dominant = f"{count}x {w}x{h}@{fps}fps×{bpp}bpp ({lanes} lanes, {count} streams)"
    else:
        dominant = f"{w}x{h}@{fps}fps×{bpp}bpp ({lanes} lanes)"
    if source:
        dominant += f" from {source}"

    return BandwidthEstimate(
        read_bw_mbps=round(read_bw, 4),
        write_bw_mbps=round(write_bw, 4),
        breakdown={
            "kind": kind,
            "mode": "standalone",
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
            "source": source,
        },
        dominant_factor=dominant,
        assumptions=assumptions,
    )
