"""VENC (video encoder) and VDEC (video decoder) estimators.

VENC reads a raw YUV frame stream from DDR and writes a compressed bitstream
(small). VDEC does the reverse. Both support H.264 / H.265 via the `codec`
parameter; compression_ratio can be overridden per instance.
"""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


def _frame_stream_mbps(params: dict, coeffs: dict) -> tuple[float, dict]:
    """Compute the raw YUV frame stream (MB/s). Either from explicit width/height/
    fps/bpp, or from a pre-computed source_input_mbps (when sourced from a pipeline
    whose output bandwidth is already known)."""
    if "source_input_mbps" in params:
        mbps = float(params["source_input_mbps"])
        return mbps, {
            "source_input_mbps": round(mbps, 4),
            "source": params.get("source"),
        }
    w = int(params["width"])
    h = int(params["height"])
    fps = float(params["fps"])
    bpp = float(params.get("bpp", coeffs["default_bpp"]))
    count = int(params.get("count", 1))
    mbps = w * h * fps * bpp * count / 8.0 / 1e6
    return mbps, {"width": w, "height": h, "fps": fps, "bpp": bpp, "count": count}


def _resolve_compression(params: dict, coeffs: dict) -> tuple[float, str]:
    """Return (compression_ratio, codec_name). Explicit params.compression_ratio
    wins; else look up by params.codec; else use codec default."""
    if "compression_ratio" in params:
        return float(params["compression_ratio"]), params.get("codec", "custom")
    codec = str(params.get("codec", coeffs["default_codec"]))
    ratios = coeffs.get("compression_ratios", {})
    if codec not in ratios:
        raise ValueError(
            f"Unknown codec '{codec}'. Supported: {list(ratios)} "
            f"or set compression_ratio directly."
        )
    return float(ratios[codec]), codec


@register("venc")
class VencEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["venc"]
        frame_mbps, dims = _frame_stream_mbps(params, coeffs)
        ratio, codec = _resolve_compression(params, coeffs)
        read = frame_mbps
        write = frame_mbps / ratio
        if "source_input_mbps" in dims:
            dom = f"VENC {codec} (1:{ratio:.0f}) from {dims['source']}"
        else:
            dom = f"VENC {dims['width']}x{dims['height']}@{dims['fps']} {codec} (1:{ratio:.0f})"
        return BandwidthEstimate(
            read_bw_mbps=round(read, 4),
            write_bw_mbps=round(write, 4),
            breakdown={
                "kind": "VENC",
                **dims,
                "codec": codec,
                "compression_ratio": ratio,
                "raw_frame_mbps": round(frame_mbps, 4),
                "bitstream_mbps": round(write, 4),
                "source": params.get("source"),
                "sources": params.get("sources"),
            },
            dominant_factor=dom,
            assumptions=[],
        )


@register("vdec")
class VdecEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["vdec"]
        frame_mbps, dims = _frame_stream_mbps(params, coeffs)
        ratio, codec = _resolve_compression(params, coeffs)
        read = frame_mbps / ratio
        write = frame_mbps
        if "source_input_mbps" in dims:
            dom = f"VDEC {codec} (1:{ratio:.0f}) from {dims['source']}"
        else:
            dom = f"VDEC {dims['width']}x{dims['height']}@{dims['fps']} {codec} (1:{ratio:.0f})"
        return BandwidthEstimate(
            read_bw_mbps=round(read, 4),
            write_bw_mbps=round(write, 4),
            breakdown={
                "kind": "VDEC",
                **dims,
                "codec": codec,
                "compression_ratio": ratio,
                "raw_frame_mbps": round(frame_mbps, 4),
                "bitstream_mbps": round(read, 4),
                "source": params.get("source"),
                "sources": params.get("sources"),
            },
            dominant_factor=dom,
            assumptions=[],
        )
