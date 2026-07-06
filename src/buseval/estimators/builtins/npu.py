"""NPU estimator: weight load + activation, with TOPS sanity check."""
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
        read = weight_bw + act_bw * 0.5
        write = act_bw * 0.5

        assumptions = []
        if tops_peak > 0 and tops_used > 0:
            ratio = tops_used / tops_peak
            if ratio > coeffs["tops_safety_limit_pct"]:
                assumptions.append(
                    f"tops_used {tops_used} > {ratio:.0%} of peak {tops_peak}"
                )
        if tops_peak > 0 and not tops_used:
            assumptions.append("tops_peak set but tops_used not provided for sanity check")

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
                "tops_peak": tops_peak,
                "tops_used": tops_used,
                "mode": mode,
            },
            dominant_factor=f"params {params_mb}MB + act {act_mb}MB @ {fps}fps",
            assumptions=assumptions,
        )
