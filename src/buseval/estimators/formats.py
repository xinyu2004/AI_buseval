"""One pixel-format table for every module.

Bandwidth uses the bpp of the format. ``custom`` is the exception: the user types bpp.
Names that store the same number of bits per pixel share one value.
"""
from __future__ import annotations

CUSTOM_FORMAT = "custom"

# Bits occupied in DDR per pixel, not the sensor's useful bit depth.
# P010 stores 10-bit samples in 16-bit words, 4:2:0, so 1.5 × 16 = 24.
FORMAT_BPP: dict[str, float] = {
    "raw8": 8,
    "raw10": 10,
    "raw12": 12,
    "raw14": 14,
    "raw16": 16,
    "yuv420": 12,
    "nv12": 12,
    "nv21": 12,
    "i420": 12,
    "yv12": 12,
    "yuv422": 16,
    "yuyv": 16,
    "uyvy": 16,
    "nv16": 16,
    "rgb565": 16,
    "yuv444": 24,
    "rgb888": 24,
    "bgr888": 24,
    "p010": 24,
    "rgba8888": 32,
    "bgra8888": 32,
    "argb8888": 32,
}


def format_names() -> list[str]:
    return list(FORMAT_BPP)


def format_choices() -> list[str]:
    return format_names() + [CUSTOM_FORMAT]


def bpp_for_format(name: str | None) -> float | None:
    if not name or str(name) == CUSTOM_FORMAT:
        return None
    return FORMAT_BPP.get(str(name))


def format_for_bpp(bpp: float) -> str | None:
    """First format in the table that stores this many bits. None means custom."""
    for name, bits in FORMAT_BPP.items():
        if float(bits) == float(bpp):
            return name
    return None


def format_for_link(spec: dict) -> tuple[str, float | None]:
    """Format control state for a stored link. The second value is custom bpp."""
    fmt = spec.get("format")
    if fmt == CUSTOM_FORMAT:
        return CUSTOM_FORMAT, float(spec.get("bpp") or 12)
    if bpp_for_format(fmt) is not None:
        return str(fmt), None
    if spec.get("bpp") not in (None, ""):
        matched = format_for_bpp(float(spec["bpp"]))
        if matched:
            return matched, None
        return CUSTOM_FORMAT, float(spec["bpp"])
    return "raw12", None


def resolve_bpp(params: dict, default: float | None = None) -> float:
    """A known format wins. ``custom`` and older YAML use the bpp number."""
    fmt = params.get("format")
    if fmt == CUSTOM_FORMAT:
        if params.get("bpp") not in (None, ""):
            return float(params["bpp"])
        raise ValueError("custom format needs bpp")
    derived = bpp_for_format(fmt)
    if derived is not None:
        return float(derived)
    if params.get("bpp") not in (None, ""):
        return float(params["bpp"])
    if default is not None:
        return float(default)
    raise ValueError("missing pixel format")
