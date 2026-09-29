"""ISP estimator: one picture through per-stage read/write factors.

The picture is the first stage's width and height when those are set, otherwise
width/height from params (a CSI source fills those). Count is not a multiplier:
each ISP node is one use case. fps and the input format still come from params.
"""
from __future__ import annotations

from ..formats import bpp_for_format, resolve_bpp
from ..frame import frame_stream_mbps
from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate, PipelineStage, note


@register("isp")
class IspEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["isp"]
        mode = params.get("mode", "serial")
        stages_raw = params.get("stages", [])

        fps = float(params["fps"])
        if params.get("format"):
            bpp = resolve_bpp(params)
            in_format = str(params["format"])
        elif "bpp" in params:
            bpp = float(params["bpp"])
            in_format = params.get("in_format", f"bpp{int(bpp)}")
        else:
            in_format = params.get("in_format", "raw12")
            bpp = coeffs["in_format_bpp"].get(in_format)
            if bpp is None:
                raise ValueError(
                    f"Unknown in_format '{in_format}'. "
                    f"Supported: {list(coeffs['in_format_bpp'])} or set format."
                )

        stages = [PipelineStage(**s) if isinstance(s, dict) else s for s in stages_raw]
        if not stages:
            stages = [PipelineStage(name="default", read_factor=1.0, write_factor=1.0)]
        if stages[0].width and stages[0].height:
            w, h = int(stages[0].width), int(stages[0].height)
        else:
            w, h = int(params["width"]), int(params["height"])
        frame_mbps = frame_stream_mbps(w, h, fps, bpp)

        per_stage = []
        rs = []
        ws = []
        assumptions = []
        typical_max = coeffs["typical_stage_factor_max"]
        in_w, in_h, in_bpp = w, h, float(bpp)
        output_frame = frame_mbps
        output_format = in_format
        for s in stages:
            out_w = int(s.width) if s.width else in_w
            out_h = int(s.height) if s.height else in_h
            if s.format == "custom" and s.bpp:
                out_bpp = float(s.bpp)
            else:
                stage_bpp = bpp_for_format(s.format)
                out_bpp = float(stage_bpp) if stage_bpp is not None else in_bpp
            out_format = s.format or output_format
            in_frame = frame_stream_mbps(in_w, in_h, fps, in_bpp)
            out_frame = frame_stream_mbps(out_w, out_h, fps, out_bpp)
            r = in_frame * s.read_factor
            ww = out_frame * s.write_factor
            rs.append(r)
            ws.append(ww)
            per_stage.append(
                {
                    "name": s.name,
                    "read_factor": s.read_factor,
                    "write_factor": s.write_factor,
                    "format": out_format,
                    "width": out_w,
                    "height": out_h,
                    "bpp": out_bpp,
                    "read_mbps": round(r, 4),
                    "write_mbps": round(ww, 4),
                }
            )
            if s.read_factor > typical_max or s.write_factor > typical_max:
                assumptions.append(note(f"non-typical stage '{s.name}' factor > {typical_max}", "yellow"))
            in_w, in_h, in_bpp = out_w, out_h, out_bpp
            output_frame = out_frame
            output_format = out_format

        if mode == "serial":
            total_r = max(rs) if rs else 0.0
            total_w = max(ws) if ws else 0.0
            agg = "max (serial)"
        else:
            total_r = sum(rs)
            total_w = sum(ws)
            agg = "sum (parallel)"

        source = params.get("source")
        src_tag = f" from {source}" if source else ""
        dom = (
            f"{w}x{h}@{fps}×{in_format}, {len(stages)} stages, {agg}{src_tag}"
        )

        return BandwidthEstimate(
            read_bw_mbps=round(total_r, 4),
            write_bw_mbps=round(total_w, 4),
            breakdown={
                "width": w,
                "height": h,
                "fps": fps,
                "bpp": bpp,
                "in_format": in_format,
                "frame_stream_mbps": round(frame_mbps, 4),
                "output_mbps": round(output_frame, 4),
                "output_format": output_format,
                "output_width": in_w,
                "output_height": in_h,
                "output_bpp": in_bpp,
                "source": source,
                "mode": mode,
                "aggregation": agg,
                "stages": per_stage,
            },
            dominant_factor=dom,
            assumptions=assumptions,
        )
