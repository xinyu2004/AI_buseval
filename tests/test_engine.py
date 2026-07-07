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
    """Pipeline with source inherits the master's width/height/fps/bpp/count and
    recomputes the frame stream from those (no pre-computed bandwidth field)."""
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
    # NPU read should include the 4-cam frame stream: 1920*1080*30*12*4/8/1e6 = 373.248 MB/s
    assert npu.breakdown["source"] == "CSI0"
    assert abs(npu.breakdown["input_frame_mbps"] - 373.248) < 1e-3


def test_predict_assumptions_one_row_per_item_with_source():
    """Predictor-level assumptions: each item appears at most once, with source
    wiring + verify notes joined into a single message."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology

    topo = Topology(
        masters=[
            Master(name="CSI0", type="mipi_csi", verify=True,
                   params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4}),
            Master(name="CSI1", type="mipi_csi", verify=True,
                   params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2}),
        ],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source="CSI1", mode="serial", verify=True,
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
            Pipeline(name="NPU0", type="npu", source="CSI0", mode="parallel", verify=True,
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 30, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    result = predict(topo)
    assumptions = result.assumptions

    # each item appears at most once
    from collections import Counter
    counts = Counter(a["item"] for a in assumptions)
    assert all(c == 1 for c in counts.values()), f"duplicate items: {counts}"

    # ISP0 row mentions source CSI1
    isp_row = next(a for a in assumptions if a["item"] == "ISP0")
    assert "CSI1" in isp_row["message"]
    assert "uses unverified default value" in isp_row["message"]

    # NPU0 row mentions source CSI0 + input bandwidth
    npu_row = next(a for a in assumptions if a["item"] == "NPU0")
    assert "CSI0" in npu_row["message"]
    assert "373.2" in npu_row["message"]
    assert "uses unverified default value" in npu_row["message"]


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


def test_lint_source_p2p_unsupported():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        pipelines=[
            Pipeline(name="ISP0", type="isp", mode="serial",
                     params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12},
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 1.0}]),
            Pipeline(name="NPU0", type="npu", source="ISP0",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 10, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "source-pipeline" for i in issues)


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


def test_lint_npu_fps_exceeds_source():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4, "count": 4})],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI0", mode="parallel",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 50, "tops_peak": 0}),
        ],
        ddr_channels=[DDRChannel(name="DDR0", theoretical_peak_mbps=100000, efficiency=0.7)],
    )
    issues = lint(topo)
    assert any(i.rule == "npu-fps-exceeds-source" for i in issues)


def test_lint_npu_fps_within_source_ok():
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
    assert not any(i.rule == "npu-fps-exceeds-source" for i in issues)
