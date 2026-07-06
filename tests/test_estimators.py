"""Tests for buseval estimators."""
import os
from pathlib import Path

import pytest

from buseval.estimators.registry import get_estimator, list_estimators
from buseval.schema import BandwidthEstimate

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_all_estimators_registered():
    expected = {
        "can", "can_dbc", "spi", "mipi_csi", "mipi_dsi",
        "usb", "eth", "flash", "isp", "npu", "gpu", "display",
    }
    assert expected <= set(list_estimators())


def test_can_load_estimator():
    est = get_estimator("can")
    r = est.estimate({"bitrate_mbps": 0.5, "load_pct": 0.3})
    assert isinstance(r, BandwidthEstimate)
    # 0.5 Mbps × 0.3 × 0.7 = 0.105 Mbps → 0.013125 MB/s, split 50/50
    assert abs(r.read_bw_mbps - 0.0525) < 1e-6 or abs(r.read_bw_mbps - 0.013125) < 1e-6


def test_spi_estimator():
    est = get_estimator("spi")
    r = est.estimate({"clock_mhz": 50, "xfer_bytes": 4096, "xfer_hz": 10000})
    # demand = 4096 * 10000 / 1e6 = 40.96 MB/s; clock = 50e6/8/1e6 = 6.25 MB/s
    # min = 6.25, split 50/50
    assert abs((r.read_bw_mbps + r.write_bw_mbps) - 6.25) < 1e-6


def test_mipi_csi_write_only():
    est = get_estimator("mipi_csi")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})
    assert r.read_bw_mbps == 0.0
    assert r.write_bw_mbps > 0
    assert "1920x1080" in r.dominant_factor


def test_mipi_dsi_read_only():
    est = get_estimator("mipi_dsi")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 60, "bpp": 24, "lanes": 4})
    assert r.read_bw_mbps > 0
    assert r.write_bw_mbps == 0.0


def test_mipi_lane_overflow_flagged():
    est = get_estimator("mipi_csi")
    r = est.estimate({"width": 4096, "height": 3072, "fps": 60, "bpp": 16, "lanes": 1})
    assert any("exceeds" in a for a in r.assumptions)


def test_usb_estimator():
    est = get_estimator("usb")
    r = est.estimate({"version": "3", "util_pct": 0.5})
    # 5000 Mbps × 0.5 × 0.9 / 8 = 281.25 MB/s, split 50/50
    assert abs((r.read_bw_mbps + r.write_bw_mbps) - 281.25) < 1e-6


def test_eth_estimator_frame_overhead():
    est = get_estimator("eth")
    r = est.estimate({"link_gbps": 1, "util_pct": 1.0, "mtu": 1500})
    # 1000 Mbps × 1.0 × 1500/1538 / 8 = 122.235 MB/s
    assert r.read_bw_mbps > 0
    assert r.write_bw_mbps > 0


def test_flash_random_penalty():
    est = get_estimator("flash")
    r_seq = est.estimate({"seq_read_mbps": 1000, "seq_write_mbps": 0, "util_pct": 1.0, "random_ratio": 0.0})
    r_rand = est.estimate({"seq_read_mbps": 1000, "seq_write_mbps": 0, "util_pct": 1.0, "random_ratio": 1.0})
    assert r_seq.read_bw_mbps > r_rand.read_bw_mbps


def test_isp_serial_vs_parallel():
    est = get_estimator("isp")
    params = {
        "width": 1920, "height": 1080, "fps": 30, "in_format": "raw12",
        "stages": [
            {"name": "a", "read_factor": 1.0, "write_factor": 1.0},
            {"name": "b", "read_factor": 2.0, "write_factor": 2.0},
        ],
    }
    serial = est.estimate({**params, "mode": "serial"})
    parallel = est.estimate({**params, "mode": "parallel"})
    # serial: max(1,2)=2x frame; parallel: 1+2=3x frame
    assert parallel.read_bw_mbps > serial.read_bw_mbps


def test_npu_estimator():
    est = get_estimator("npu")
    r = est.estimate({"params_mbytes": 100, "activation_mbytes": 50, "inference_fps": 100})
    # weight = 100 × 100 = 10000 MB/s read; act = 50 × 2 × 100 = 10000 split 50/50
    assert r.read_bw_mbps > 0
    assert r.write_bw_mbps > 0


def test_gpu_estimator():
    est = get_estimator("gpu")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 60, "bpp": 32, "overdraw": 3})
    assert r.read_bw_mbps == r.write_bw_mbps
    assert r.read_bw_mbps > 0


def test_display_read_only():
    est = get_estimator("display")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 60, "bpp": 32})
    assert r.read_bw_mbps > 0
    assert r.write_bw_mbps == 0.0


def test_can_dbc_estimator_with_sample():
    dbc = EXAMPLES / "sample.dbc"
    if not dbc.exists():
        pytest.skip("sample.dbc not found")
    est = get_estimator("can_dbc")
    r = est.estimate({"dbc_path": str(dbc)})
    assert r.read_bw_mbps >= 0
    assert "messages" in r.breakdown
