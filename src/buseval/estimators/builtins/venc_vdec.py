"""VENC (video encoder) and VDEC (video decoder) estimators.

VENC reads a raw YUV frame stream from DDR and writes a compressed bitstream
(small). VDEC does the reverse. Both support H.264 / H.265 via the `codec`
parameter; compression_ratio can be overridden per instance.
"""
from __future__ import annotations

from ..formats import resolve_bpp
from ..frame import frame_stream_mbps
from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


def _output_frame(params: dict, coeffs: dict) -> tuple[float | None, dict]:
    """This block's own picture: width × height × fps × format bpp."""
    if not all(k in params and params[k] not in (None, "") for k in ("width", "height", "fps")):
        return None, {}
    bpp = resolve_bpp(params, float(coeffs["default_bpp"]))
    w = int(params["width"])
    h = int(params["height"])
    fps = float(params["fps"])
    count = int(params.get("count", 1))
    mbps = frame_stream_mbps(w, h, fps, bpp, count)
    dims = {"width": w, "height": h, "fps": fps, "bpp": bpp, "count": count}
    if params.get("format"):
        dims["format"] = params["format"]
    return mbps, dims


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
        picture, dims = _output_frame(params, coeffs)
        ratio, codec = _resolve_compression(params, coeffs)
        upstream = params.get("source_input_mbps")
        if upstream is not None:
            read = float(upstream)
            raw = picture if picture is not None else read
            dims = {**dims, "source_input_mbps": round(read, 4), "source": params.get("source")}
        else:
            if picture is None:
                raise ValueError("VENC needs width, height, and fps")
            read = picture
            raw = picture
        write = raw / ratio
        if upstream is not None:
            dom = f"VENC {codec} (1:{ratio:.0f}) from {params.get('source')}"
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
                "raw_frame_mbps": round(raw, 4),
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
        picture, dims = _output_frame(params, coeffs)
        ratio, codec = _resolve_compression(params, coeffs)
        upstream = params.get("source_input_mbps")
        if picture is None and upstream is None:
            raise ValueError("VDEC needs width, height, and fps")
        write = picture if picture is not None else float(upstream)
        if upstream is not None:
            read = float(upstream)
            dims = {**dims, "source_input_mbps": round(read, 4), "source": params.get("source")}
        else:
            read = write / ratio
        if upstream is not None:
            dom = f"VDEC {codec} (1:{ratio:.0f}) from {params.get('source')}"
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
                "raw_frame_mbps": round(write, 4),
                "bitstream_mbps": round(read, 4),
                "source": params.get("source"),
                "sources": params.get("sources"),
            },
            dominant_factor=dom,
            assumptions=[],
        )
