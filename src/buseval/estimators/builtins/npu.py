"""NPU estimator: weight load + activation + optional camera-frame input.

Input frames (when `source` references a CSI, or width/height/fps/bpp are present)
are computed from image dimensions, not a pre-computed bandwidth number.
`activation` represents intermediate feature traffic between layers (distinct
from the input frame read).
"""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("npu")
class NpuEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["npu"]
        params_mb = float(params.get("params_mbytes", 0))
        act_mb = float(params.get("activation_mbytes", 0))
        fps = float(params.get("inference_fps", 0))
        tops_peak = float(params.get("tops_peak", 0))
        tops_used = float(params.get("tops_used", 0))
        mode = params.get("mode", "parallel")

        if fps <= 0:
            raise ValueError("npu.inference_fps must be > 0")

        latency_s = 1.0 / fps
        weight_bw = params_mb / latency_s  # MB/s
        act_bw = act_mb * coeffs["activation_read_write_factor"] / latency_s

        # Optional input frame stream (from source CSI dimensions, or declared directly).
        # NPU reads at most `inference_fps` frames/sec, capped by the source frame rate
        # (can't read frames that haven't arrived). input_frame uses min(inference_fps, source_fps).
        input_frame_mbps = 0.0
        source_fps = None
        effective_fps = None
        if "width" in params and "height" in params and "fps" in params:
            w = int(params["width"])
            h = int(params["height"])
            source_fps = float(params["fps"])
            bpp = float(params.get("bpp", 12))
            count = int(params.get("count", 1))
            effective_fps = min(fps, source_fps)
            input_frame_mbps = w * h * effective_fps * bpp * count / 8.0 / 1e6

        read = weight_bw + act_bw * 0.5 + input_frame_mbps
        write = act_bw * 0.5

        assumptions = []
        source = params.get("source")
        # Flag inconsistent fps: NPU inferring faster than frames arrive is impossible.
        if source_fps is not None and fps > source_fps:
            assumptions.append(
                f"inference_fps {fps} > source fps {source_fps} (capped to {source_fps})"
            )
        if tops_peak > 0 and tops_used > 0:
            ratio = tops_used / tops_peak
            if ratio > coeffs["tops_safety_limit_pct"]:
                assumptions.append(
                    f"tops_used {tops_used} > {ratio:.0%} of peak {tops_peak}"
                )
        if tops_peak > 0 and not tops_used:
            assumptions.append("tops_peak set but tops_used not provided for sanity check")

        dom_parts = [f"params {params_mb}MB", f"act {act_mb}MB", f"@ {fps}fps"]
        if input_frame_mbps > 0:
            dom_parts.append(f"input {input_frame_mbps:.1f}MB/s" + (f" from {source}" if source else ""))
        return BandwidthEstimate(
            read_bw_mbps=round(read, 4),
            write_bw_mbps=round(write, 4),
            breakdown={
                "params_mbytes": params_mb,
                "activation_mbytes": act_mb,
                "inference_fps": fps,
                "source_fps": source_fps,
                "effective_fps": effective_fps,
                "latency_s": round(latency_s, 6),
                "weight_bw_mbps": round(weight_bw, 4),
                "activation_bw_mbps": round(act_bw, 4),
                "input_frame_mbps": round(input_frame_mbps, 4),
                "source": source,
                "tops_peak": tops_peak,
                "tops_used": tops_used,
                "mode": mode,
            },
            dominant_factor=" + ".join(dom_parts),
            assumptions=assumptions,
        )
