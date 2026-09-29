"""Shared DDR frame-stream size. GMSL link bandwidth is a different formula."""
from __future__ import annotations


def frame_stream_mbps(width: float, height: float, fps: float, bpp: float, count: float = 1) -> float:
    """Bytes per second of a raw frame stream, in MB/s.

    MB/s = width × height × fps × bpp × count / 8 / 1e6.
    """
    return float(width) * float(height) * float(fps) * float(bpp) * float(count) / 8.0 / 1e6
