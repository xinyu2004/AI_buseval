"""NPU estimator: weight load + activation + optional multi-source frame input.

Input frames are computed from each source's image dimensions (width/height/fps/
bpp/count) at the source's NATIVE fps — no synchronization, no cap. Each source's
MB/s is summed (not the fps, since resolutions differ). weight_bw and activation_bw
are computed once from inference_fps (the model is loaded once, shared across sources).
`activation` represents intermediate feature traffic between layers (distinct from
the input frame read).
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
        weight_bw = params_mb / latency_s  # MB/s (loaded once, shared across sources)
        act_bw = act_mb * coeffs["activation_read_write_factor"] / latency_s

        # Multi-source input frames: each source uses its own native fps (no cap/sync).
        # input_frame_mbps = Σ per source (w × h × src_fps × bpp × count / 8).
        sources_spec = params.get("sources", [])
        per_source = []
        input_frame_mbps = 0.0
        assumptions = []
        for s in sources_spec:
            w = int(s["width"])
            h = int(s["height"])
            src_fps = float(s["fps"])
            bpp = float(s.get("bpp", 12))
            count = int(s.get("count", 1))
            mbps = w * h * src_fps * bpp * count / 8.0 / 1e6
            input_frame_mbps += mbps
            per_source.append({
                "name": s.get("name"),
                "width": w, "height": h, "fps": src_fps,
                "bpp": bpp, "count": count,
                "input_mbps": round(mbps, 4),
            })
            # Soft warning: source fps faster than inference fps (async, not capped)
            if fps < src_fps:
                assumptions.append(
                    f"inference_fps {fps} < source '{s.get('name','?')}' fps {src_fps} "
                    f"(async; not capped)"
                )

        read = weight_bw + act_bw * 0.5 + input_frame_mbps
        write = act_bw * 0.5

        if tops_peak > 0 and tops_used > 0:
            ratio = tops_used / tops_peak
            if ratio > coeffs["tops_safety_limit_pct"]:
                assumptions.append(
                    f"tops_used {tops_used} > {ratio:.0%} of peak {tops_peak}"
                )
        if tops_peak > 0 and not tops_used:
            assumptions.append("tops_peak set but tops_used not provided for sanity check")

        source_names = [s.get("name") for s in sources_spec if s.get("name")]
        src_join = "+".join(source_names)
        dom_parts = [f"params {params_mb}MB", f"act {act_mb}MB", f"@ {fps}fps"]
        if input_frame_mbps > 0:
            tag = f" from {src_join}" if src_join else ""
            dom_parts.append(f"input {input_frame_mbps:.1f}MB/s{tag}")
        return BandwidthEstimate(
            read_bw_mbps=round(read, 4),
            write_bw_mbps=round(write, 4),
            breakdown={
                "params_mbytes": params_mb,
                "activation_mbytes": act_mb,
                "inference_fps": fps,
                "latency_s": round(latency_s, 6),
                "weight_bw_mbps": round(weight_bw, 4),
                "activation_bw_mbps": round(act_bw, 4),
                "input_frame_mbps": round(input_frame_mbps, 4),
                "sources": per_source,
                "source_names": source_names,
                "tops_peak": tops_peak,
                "tops_used": tops_used,
                "mode": mode,
            },
            dominant_factor=" + ".join(dom_parts),
            assumptions=assumptions,
        )
