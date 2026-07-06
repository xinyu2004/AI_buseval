"""ISP estimator: frame stream × per-stage read/write factors."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate, PipelineStage


@register("isp")
class IspEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["isp"]
        w = int(params["width"])
        h = int(params["height"])
        fps = float(params["fps"])
        in_format = params.get("in_format", "raw12")
        mode = params.get("mode", "serial")
        stages_raw = params.get("stages", [])

        bpp = coeffs["in_format_bpp"].get(in_format)
        if bpp is None:
            raise ValueError(
                f"Unknown in_format '{in_format}'. "
                f"Supported: {list(coeffs['in_format_bpp'])}"
            )
        frame_bytes = w * h * bpp / 8.0
        frame_mbps = frame_bytes * fps / 1e6  # MB/s

        stages = [PipelineStage(**s) if isinstance(s, dict) else s for s in stages_raw]
        if not stages:
            stages = [PipelineStage(name="default", read_factor=1.0, write_factor=1.0)]

        per_stage = []
        rs = []
        ws = []
        assumptions = []
        typical_max = coeffs["typical_stage_factor_max"]
        for s in stages:
            r = frame_mbps * s.read_factor
            ww = frame_mbps * s.write_factor
            rs.append(r)
            ws.append(ww)
            per_stage.append(
                {
                    "name": s.name,
                    "read_factor": s.read_factor,
                    "write_factor": s.write_factor,
                    "read_mbps": round(r, 4),
                    "write_mbps": round(ww, 4),
                }
            )
            if s.read_factor > typical_max or s.write_factor > typical_max:
                assumptions.append(f"non-typical stage '{s.name}' factor > {typical_max}")

        if mode == "serial":
            total_r = max(rs) if rs else 0.0
            total_w = max(ws) if ws else 0.0
            agg = "max (serial)"
        else:
            total_r = sum(rs)
            total_w = sum(ws)
            agg = "sum (parallel)"

        return BandwidthEstimate(
            read_bw_mbps=round(total_r, 4),
            write_bw_mbps=round(total_w, 4),
            breakdown={
                "width": w,
                "height": h,
                "fps": fps,
                "in_format": in_format,
                "bpp": bpp,
                "frame_stream_mbps": round(frame_mbps, 4),
                "mode": mode,
                "aggregation": agg,
                "stages": per_stage,
            },
            dominant_factor=f"{w}x{h}@{fps}×{in_format}, {len(stages)} stages, {agg}",
            assumptions=assumptions,
        )
