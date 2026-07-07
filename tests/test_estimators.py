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


def test_mipi_csi_count_aggregate():
    est = get_estimator("mipi_csi")
    single = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})
    multi = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})
    # count=4 → 4x aggregate write bandwidth
    assert abs(multi.write_bw_mbps - single.write_bw_mbps * 4) < 1e-6
    assert multi.read_bw_mbps == 0.0
    assert multi.breakdown["per_stream_mbps"] == single.write_bw_mbps
    assert multi.breakdown["count"] == 4
    assert "4x" in multi.dominant_factor


def test_mipi_csi_count_lane_check_uses_aggregate():
    est = get_estimator("mipi_csi")
    # 9 streams of 1080p@30 12bpp on 4-lane: 839 MB/s > 750 MB/s lane cap
    r = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 9})
    assert any("exceeds" in a for a in r.assumptions)
    # 4 streams: 373 MB/s < 750 MB/s, no overflow
    r4 = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})
    assert not any("exceeds" in a for a in r4.assumptions)


def test_mipi_csi_count_default_backward_compat():
    est = get_estimator("mipi_csi")
    r_no_count = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})
    r_count1 = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 1})
    assert r_no_count.write_bw_mbps == r_count1.write_bw_mbps
    assert "4x" not in r_count1.dominant_factor  # count=1 → no "Nx" prefix


def test_mipi_dsi_count_read_aggregate():
    est = get_estimator("mipi_dsi")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 60, "bpp": 24, "lanes": 4, "count": 2})
    single = est.estimate({"width": 1920, "height": 1080, "fps": 60, "bpp": 24, "lanes": 4})
    assert abs(r.read_bw_mbps - single.read_bw_mbps * 2) < 1e-6
    assert r.write_bw_mbps == 0.0


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


def test_isp_with_inherited_dimensions():
    """ISP computes frame_stream from width/height/fps/bpp/count (inherited from source)."""
    est = get_estimator("isp")
    r = est.estimate({
        "width": 1280, "height": 720, "fps": 60, "bpp": 12,
        "source": "CSI1",
        "mode": "serial",
        "stages": [{"name": "a", "read_factor": 1.5, "write_factor": 2.0}],
    })
    # frame_stream = 1280*720*60*12/8/1e6 = 82.944 MB/s
    # stage read = 82.944*1.5 = 124.416, write = 82.944*2.0 = 165.888
    assert abs(r.read_bw_mbps - 124.416) < 1e-3
    assert abs(r.write_bw_mbps - 165.888) < 1e-3
    assert r.breakdown["source"] == "CSI1"
    assert "from CSI1" in r.dominant_factor


def test_isp_count_multiplies_frame_stream():
    est = get_estimator("isp")
    single = est.estimate({
        "width": 1920, "height": 1080, "fps": 30, "bpp": 12,
        "mode": "serial",
        "stages": [{"name": "x", "read_factor": 1.0, "write_factor": 1.0}],
    })
    multi = est.estimate({
        "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4,
        "mode": "serial",
        "stages": [{"name": "x", "read_factor": 1.0, "write_factor": 1.0}],
    })
    assert abs(multi.read_bw_mbps - single.read_bw_mbps * 4) < 1e-6


def test_npu_inherits_input_frame_dimensions():
    """NPU with sources list: input = Σ per-source (w×h×src_fps×bpp×count/8), no cap."""
    est = get_estimator("npu")
    base = est.estimate({"params_mbytes": 100, "activation_mbytes": 50, "inference_fps": 100})
    with_input = est.estimate({
        "params_mbytes": 100, "activation_mbytes": 50, "inference_fps": 100,
        "sources": [{"name": "CSI0", "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4}],
    })
    # input = 1920*1080*30*12*4/8/1e6 = 373.248 MB/s (native fps, no cap)
    assert abs((with_input.read_bw_mbps - base.read_bw_mbps) - 373.248) < 1e-3
    assert with_input.write_bw_mbps == base.write_bw_mbps
    assert with_input.breakdown["sources"][0]["name"] == "CSI0"
    assert with_input.breakdown["source_names"] == ["CSI0"]
    assert "CSI0" in with_input.dominant_factor


def test_npu_multi_source_input_aggregated():
    """Multi-source: input = sum of per-source MB/s (not fps). Different resolutions."""
    est = get_estimator("npu")
    r = est.estimate({
        "params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30,
        "sources": [
            {"name": "CSI0", "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4},
            {"name": "CSI1", "width": 1280, "height": 720, "fps": 60, "bpp": 12, "count": 1},
        ],
    })
    # CSI0: 1920*1080*30*12*4/8/1e6 = 373.248
    # CSI1: 1280*720*60*12*1/8/1e6 = 82.944
    # total = 456.192
    assert abs(r.breakdown["input_frame_mbps"] - 456.192) < 1e-3
    assert len(r.breakdown["sources"]) == 2
    assert "CSI0+CSI1" in r.dominant_factor


def test_npu_multi_source_weight_not_doubled():
    """weight_bw computed once (not per source)."""
    est = get_estimator("npu")
    single = est.estimate({
        "params_mbytes": 100, "activation_mbytes": 0, "inference_fps": 30,
        "sources": [{"name": "CSI0", "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4}],
    })
    multi = est.estimate({
        "params_mbytes": 100, "activation_mbytes": 0, "inference_fps": 30,
        "sources": [
            {"name": "CSI0", "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4},
            {"name": "CSI1", "width": 1280, "height": 720, "fps": 60, "bpp": 12, "count": 1},
        ],
    })
    # weight_bw same (100MB * 30fps = 3000); only input_frame differs
    assert single.breakdown["weight_bw_mbps"] == multi.breakdown["weight_bw_mbps"]


def test_npu_no_cap_uses_native_fps():
    """No sync/cap: inference_fps=20 < source fps=30 → input still uses source's 30fps."""
    est = get_estimator("npu")
    r = est.estimate({
        "params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 20,
        "sources": [{"name": "CSI0", "width": 1920, "height": 1080, "fps": 30, "bpp": 12, "count": 4}],
    })
    # input uses native 30 (not capped to 20) = 373.248
    assert abs(r.breakdown["input_frame_mbps"] - 373.248) < 1e-3
    # soft warning: inference_fps < source fps
    assert any("async" in a for a in r.assumptions)


def test_npu_single_source_str_backward_compat():
    """source: CSI0 (str) via predictor equals source: [CSI0] (list)."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    from buseval.engine.predictor import predict
    topo_str = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})],
        pipelines=[Pipeline(name="NPU0", type="npu", source="CSI0",
                            params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0})],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    topo_list = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})],
        pipelines=[Pipeline(name="NPU0", type="npu", source=["CSI0"],
                            params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0})],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    r_str = predict(topo_str)
    r_list = predict(topo_list)
    npu_str = next(i for i in r_str.items if i.name == "NPU0")
    npu_list = next(i for i in r_list.items if i.name == "NPU0")
    assert npu_str.read_bw_mbps == npu_list.read_bw_mbps
    assert npu_str.breakdown["source_names"] == npu_list.breakdown["source_names"]
