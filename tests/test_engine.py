"""Tests for the predictor + margin engine and CLI-level flows."""
from pathlib import Path

import pytest

from buseval.loader import load_topology
from buseval.engine.predictor import predict
from buseval.engine.margin import evaluate_margin
from buseval.lint import lint
from buseval.cli import main as cli_main
from buseval.cli import _list_presets

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PRESETS = Path(__file__).resolve().parents[1] / "src" / "buseval" / "presets"


def test_predict_full_menu_disabled_all():
    topo = load_topology(EXAMPLES / "full_menu.yaml")
    result = predict(topo)
    assert result.total_read_mbps == 0.0
    assert result.total_write_mbps == 0.0
    assert result.items == []


def test_predict_simple_topology():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.5})],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=1000, efficiency=0.7)],
    )
    result = predict(topo)
    assert len(result.items) == 1
    assert result.total_read_mbps > 0
    margins = evaluate_margin(result)
    assert len(margins) == 1
    assert margins[0].verdict in {"OK", "WARN", "CRITICAL"}


def test_margin_critical():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="BIG", type="usb", params={"version": "3.2", "util_pct": 1.0})],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=10, efficiency=0.7)],
    )
    result = predict(topo)
    margins = evaluate_margin(result)
    assert margins[0].verdict == "CRITICAL"


def test_lint_no_ddr_errors():
    from buseval.schema import Master, Topology
    topo = Topology(masters=[Master(name="X", type="usb", params={"version": "3"})])
    issues = lint(topo)
    assert any(i.level == "error" and i.rule == "no-ddr" for i in issues)


def test_lint_csi_without_isp():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi", params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=10000)],
    )
    issues = lint(topo)
    assert any(i.rule == "csi-without-isp" for i in issues)


def test_all_presets_load_and_predict():
    for preset in _list_presets():
        topo = load_topology(PRESETS / f"{preset}.yaml")
        result = predict(topo)
        assert result.total_read_mbps >= 0
        margins = evaluate_margin(result)
        assert len(margins) >= 1


def test_cli_list_presets(capsys):
    rc = cli_main(["list", "presets"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rk3588" in out


def test_cli_predict_soc(capsys):
    rc = cli_main(["predict", "--soc", "s32g", "--format", "json"])
    assert rc in (0, 3)
    out = capsys.readouterr().out
    assert '"ddr_channels"' in out


def test_cli_predict_dbc_health(capsys):
    rc = cli_main(["predict", "--dbc", str(EXAMPLES / "sample.dbc"), "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "buses" in out


def test_cli_predict_soc_with_dbc(capsys):
    rc = cli_main(["predict", "--soc", "tda4vh", "--dbc", str(EXAMPLES / "sample.dbc"), "--format", "json"])
    assert rc in (0, 3)
    out = capsys.readouterr().out
    assert '"items"' in out
    assert "can_dbc" in out


def test_cli_lint(capsys):
    rc = cli_main(["lint", "-t", str(EXAMPLES / "full_menu.yaml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no-output" in out or "OK" in out


def test_cli_predict_can_dbc_multi_slot(capsys):
    rc = cli_main([
        "predict", "--soc", "tda4vh",
        "--can-dbc", f"CAN0={EXAMPLES / 'sample.dbc'}",
        "--can-dbc", f"CAN2={EXAMPLES / 'sample_heavy.dbc'}",
        "--format", "json",
    ])
    assert rc in (0, 3)
    out = capsys.readouterr().out
    assert "can_dbc" in out
    # both DBCs injected
    import json
    d = json.loads(out)
    can0 = next(i for i in d["items"] if i["name"] == "CAN0")
    can2 = next(i for i in d["items"] if i["name"] == "CAN2")
    assert can0["type"] == "can_dbc"
    assert can2["type"] == "can_dbc"
    # heavy DBC (17 msgs) should produce more bandwidth than light (10 msgs)
    assert (can2["read_bw_mbps"] + can2["write_bw_mbps"]) > (can0["read_bw_mbps"] + can0["write_bw_mbps"])


def test_cli_can_dbc_unknown_name_errors(capsys):
    rc = cli_main(["predict", "--soc", "tda4vh", "--can-dbc", "CAN9=x.dbc"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_can_dbc_non_can_target_errors(capsys):
    rc = cli_main(["predict", "--soc", "tda4vh", "--can-dbc", f"CSI0={EXAMPLES / 'sample.dbc'}"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "not a CAN master" in err


def test_cli_can_dbc_bad_format_errors(capsys):
    rc = cli_main(["predict", "--soc", "tda4vh", "--can-dbc", "CAN0"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "NAME=PATH" in err


def test_cli_can_dbc_and_dbc_mutually_exclusive(capsys):
    rc = cli_main([
        "predict", "--soc", "tda4vh",
        "--dbc", str(EXAMPLES / "sample.dbc"),
        "--can-dbc", f"CAN0={EXAMPLES / 'sample.dbc'}",
    ])
    assert rc != 0


def test_sample_heavy_dbc_loads():
    from buseval.dbc.health_report import build_health_report
    report = build_health_report(str(EXAMPLES / "sample_heavy.dbc"), bitrate_kbps=2000)
    assert len(report.buses) >= 1
    bus = report.buses[0]
    # 17 messages, 64-byte frames, total ~548 kbps on 2Mbps = ~27% load
    assert bus.load_pct > 0.1
    assert len(bus.top_messages) > 0


def test_predict_source_inherits_master_dimensions():
    """Pipeline with source (str or list) inherits master dims; NPU input uses
    native source fps (no cap)."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology

    topo = Topology(
        masters=[
            Master(name="CSI0", type="mipi_csi",
                   params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4}),
        ],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI0", mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    result = predict(topo)
    npu = next(i for i in result.items if i.name == "NPU0")
    # NPU read includes 4-cam stream at native 30fps: 1920*1080*30*12*4/8/1e6 = 373.248
    assert npu.breakdown["source_names"] == ["CSI0"]
    assert abs(npu.breakdown["input_frame_mbps"] - 373.248) < 1e-3


def test_lint_npu_fps_below_source():
    """lint warns per-source when inference_fps < source fps (async, not capped)."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI0", mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 20, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "npu-fps-below-source" for i in issues)


def test_lint_npu_fps_within_source_ok():
    """No warning when inference_fps >= source fps."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI0", mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert not any(i.rule == "npu-fps-below-source" for i in issues)


def test_lint_isp_multi_source_errors():
    """ISP with source list > 1 → error."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[
            Master(name="CSI0", type="mipi_csi",
                   params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4}),
            Master(name="CSI1", type="mipi_csi",
                   params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2}),
        ],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source=["CSI0", "CSI1"], mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "isp-multi-source" for i in issues)


def test_can_disabled_by_default_in_presets():
    """All 6 non-s32g presets: CAN masters default enabled=False."""
    from buseval.loader import load_topology
    from pathlib import Path
    presets_dir = Path(__file__).resolve().parents[1] / "src" / "buseval" / "presets"
    for soc in ("tda4vh", "orin_nx", "j5", "sa8155", "rk3588", "t527"):
        topo = load_topology(presets_dir / f"{soc}.yaml")
        for m in topo.masters:
            if m.type == "can":
                assert not m.enabled, f"{soc}.{m.name}: CAN should be disabled by default"


def test_s32g_can_still_enabled():
    """s32g (gateway) keeps CAN enabled — it's the SoC's primary function."""
    from buseval.loader import load_topology
    from pathlib import Path
    presets_dir = Path(__file__).resolve().parents[1] / "src" / "buseval" / "presets"
    topo = load_topology(presets_dir / "s32g.yaml")
    can_masters = [m for m in topo.masters if m.type == "can"]
    assert len(can_masters) > 0
    for m in can_masters:
        assert m.enabled, f"s32g.{m.name}: CAN should stay enabled (gateway SoC)"


def test_can_dbc_injection_enables_can():
    """--can-dbc forces enabled=True even if preset has CAN disabled."""
    from buseval.cli import _load_soc
    topo = _load_soc("tda4vh", dbc_path=None,
                     can_dbc_mappings=[("CAN0", "examples/sample.dbc")])
    can0 = next(m for m in topo.masters if m.name == "CAN0")
    assert can0.type == "can_dbc"
    assert can0.enabled is True


def test_predict_source_not_found_errors():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI9",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 10, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    with pytest.raises(ValueError, match="not found"):
        predict(topo)


def test_predict_p2p_isp_to_npu():
    """p2p: NPU sources ISP0 → NPU's input includes ISP0's write_bw (YUV output)."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI1", type="mipi_csi",
                        params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2})],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source="CSI1", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 2.0}]),
            Pipeline(name="NPU0", type="npu", source="ISP0", mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    result = predict(topo)
    isp = next(i for i in result.items if i.name == "ISP0")
    npu = next(i for i in result.items if i.name == "NPU0")
    # NPU's input_frame_mbps should equal ISP0's write_bw (p2p passes upstream output)
    assert abs(npu.breakdown["input_frame_mbps"] - isp.write_bw_mbps) < 1e-3


def test_predict_p2p_mixed_master_and_pipeline_sources():
    """NPU source=[CSI0, ISP0]: master contributes dims, pipeline contributes write_bw."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4}),
                 Master(name="CSI1", type="mipi_csi",
                        params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2})],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source="CSI1", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 2.0}]),
            Pipeline(name="NPU0", type="npu", source=["CSI0", "ISP0"], mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    result = predict(topo)
    npu = next(i for i in result.items if i.name == "NPU0")
    isp = next(i for i in result.items if i.name == "ISP0")
    # sources should contain both CSI0 (master, kind=master) and ISP0 (pipeline, kind=pipeline)
    kinds = {s["name"]: s.get("kind") for s in npu.breakdown["sources"]}
    assert kinds["CSI0"] == "master"
    assert kinds["ISP0"] == "pipeline"
    # CSI0 contributes 373.248 (master dims); ISP0 contributes its write_bw
    csi0_input = next(s["input_mbps"] for s in npu.breakdown["sources"] if s["name"] == "CSI0")
    isp0_input = next(s["input_mbps"] for s in npu.breakdown["sources"] if s["name"] == "ISP0")
    assert abs(csi0_input - 373.248) < 1e-3
    assert abs(isp0_input - isp.write_bw_mbps) < 1e-3


def test_predict_p2p_cyclic_dependency_errors():
    """A→B→A cycle raises ValueError."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        pipelines=[
            Pipeline(name="A", type="isp", source="B", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
            Pipeline(name="B", type="isp", source="A", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    with pytest.raises(ValueError, match="cyclic"):
        predict(topo)


def test_lint_source_cyclic():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        pipelines=[
            Pipeline(name="A", type="isp", source="B", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
            Pipeline(name="B", type="isp", source="A", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "source-cyclic" for i in issues)


def test_venc_estimator_h265():
    from buseval.estimators.registry import get_estimator
    est = get_estimator("venc")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 16, "codec": "h265"})
    # raw = 1920*1080*30*16/8/1e6 = 124.416 MB/s; write = 124.416/50 = 2.488
    assert abs(r.read_bw_mbps - 124.416) < 1e-3
    assert abs(r.write_bw_mbps - 124.416 / 50) < 1e-3
    assert "h265" in r.dominant_factor


def test_venc_estimator_h264_higher_bitrate():
    from buseval.estimators.registry import get_estimator
    est = get_estimator("venc")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 16, "codec": "h264"})
    # h264 ratio=30 → write = 124.416/30 = 4.147 (more than h265's 2.488)
    assert r.write_bw_mbps > 4.0


def test_vdec_estimator_reverse_of_venc():
    from buseval.estimators.registry import get_estimator
    est = get_estimator("vdec")
    r = est.estimate({"width": 1920, "height": 1080, "fps": 30, "bpp": 16, "codec": "h265"})
    # VDEC: read = bitstream (small) = 124.416/50; write = YUV (large) = 124.416
    assert abs(r.write_bw_mbps - 124.416) < 1e-3
    assert abs(r.read_bw_mbps - 124.416 / 50) < 1e-3


def test_venc_with_pipeline_source_input_stream():
    """VENC sourced from ISP0: uses upstream's write_bw as raw frame input."""
    from buseval.estimators.registry import get_estimator
    est = get_estimator("venc")
    r = est.estimate({
        "source_input_mbps": 165.89, "source": "ISP0",
        "codec": "h265",
    })
    assert abs(r.read_bw_mbps - 165.89) < 1e-3
    assert abs(r.write_bw_mbps - 165.89 / 50) < 1e-3
    assert "ISP0" in r.dominant_factor


def test_lint_source_override_warns():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI1", type="mipi_csi",
                        params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2})],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source="CSI1", mode="serial",
                     params={"width": 1920, "height": 1080, "fps": 30, "in_format": "raw12"},
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "source-override" for i in issues)
