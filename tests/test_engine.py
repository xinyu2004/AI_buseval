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
