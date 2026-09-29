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


def _ddr(peak, efficiency=0.7):
    """Both sides at `peak` MB/s: 32-bit, one group, so MT/s = peak / 4."""
    from buseval.schema import DDRChannel
    mt = peak / 4.0
    return DDRChannel(
        name="DDR0",
        controller_mt_s=mt,
        module_mt_s=mt,
        controller_width_bits=32,
        module_width_bits=32,
        efficiency=efficiency,
    )


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
        ddr_channels=[_ddr(1000)],
    )
    result = predict(topo)
    assert len(result.items) == 1
    assert result.total_read_mbps > 0
    margins = evaluate_margin(result)
    assert len(margins) == 1
    assert margins[0].verdict in {"OK", "WARN", "CRITICAL"}


def test_margin_verdict_follows_occupancy():
    """A read-heavy load under 60% of the whole bus stays OK."""
    from buseval.schema import Master, Topology
    topo = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 1.0})],
        ddr_channels=[_ddr(2000, efficiency=1.0)],
    )
    margins = evaluate_margin(predict(topo))
    margin = margins[0]
    demand = margin.read_demand_mbps + margin.write_demand_mbps
    assert margin.read_util == pytest.approx(margin.read_demand_mbps / margin.available_mbps, abs=1e-3)
    assert margin.write_util == pytest.approx(margin.write_demand_mbps / margin.available_mbps, abs=1e-3)
    assert margin.occupancy == pytest.approx(demand / margin.available_mbps, abs=1e-3)
    assert margin.occupancy < 0.6
    assert margin.verdict == "OK"


def test_share_colors_use_the_same_lines():
    from buseval.report.terminal import _share_style

    assert _share_style(0.177, 0.6, 0.8) == "green"
    assert _share_style(0.6, 0.6, 0.8) == "yellow"
    assert _share_style(0.8, 0.6, 0.8) == "red"


def test_cli_report_shows_occupancy_and_skips_empty_assumptions(capsys):
    rc = cli_main(["predict", "--soc", "tda4vh", "--no-color"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "17.7%" in out and "10.5%" in out and "28.2%" in out
    assert "No flagged assumptions" not in out


def test_ddr_table_colors_each_share():
    from io import StringIO

    from rich.console import Console

    from buseval.report.terminal import render_terminal

    topo = load_topology(PRESETS / "tda4vh.yaml")
    buf = StringIO()
    console = Console(file=buf, width=140, force_terminal=True, color_system="standard", no_color=False, highlight=False)
    render_terminal(predict(topo), console=console, use_color=True)
    text = buf.getvalue()
    import re
    assert "R-util" in text
    assert re.search(r"\x1b\[32m\s*17\.7%", text)
    assert re.search(r"\x1b\[32m\s*10\.5%", text)
    assert re.search(r"\x1b\[32m\s*28\.2%", text)
    assert re.search(r"\x1b\[32mOK", text)


def test_margin_critical():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="BIG", type="usb", params={"version": "3.2", "util_pct": 1.0})],
        ddr_channels=[_ddr(10)],
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
        ddr_channels=[_ddr(10000)],
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
    assert len(report.buses) == 1
    bus = report.buses[0]
    # 17 messages, 64-byte frames, total ~548 kbps on 2Mbps = ~27% load
    assert bus.standard == "canfd"
    assert bus.illegal_count == 0
    assert bus.load_pct > 0.1
    assert len(bus.top_messages) > 0
    classic = build_health_report(
        str(EXAMPLES / "sample_heavy.dbc"),
        standard="can",
        data_kbps=500,
    )
    assert classic.buses[0].illegal_count == 17
    assert classic.buses[0].load_pct == 0
    assert classic.buses[0].verdict == "CRITICAL"


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
        ddr_channels=[_ddr(100000)],
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
        ddr_channels=[_ddr(100000)],
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
        ddr_channels=[_ddr(100000)],
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
        ddr_channels=[_ddr(100000)],
    )
    issues = lint(topo)
    assert any(i.rule == "isp-multi-source" for i in issues)


def test_tda4vh_names_isp0_and_isp1_at_600_mpix():
    from buseval.session import load_preset
    topo = load_preset("tda4vh")
    slots = {slot.name: slot.mpix_s for slot in topo.isp_vpacs}
    assert slots == {"ISP0": 600, "ISP1": 600}
    issues = lint(topo)
    assert not any(i.rule in ("isp-spec-missing", "isp-vpac-over", "isp-unknown") for i in issues)


def test_isp_without_chip_spec_warns():
    from buseval.schema import Pipeline, Topology
    topo = Topology(
        pipelines=[Pipeline(
            name="ISP0", type="isp",
            params={"width": 1280, "height": 720, "fps": 30},
            stages=[{"name": "bayer", "width": 1280, "height": 720, "read_factor": 1, "write_factor": 1}],
        )],
        ddr_channels=[_ddr(10000)],
    )
    assert any(i.rule == "isp-spec-missing" for i in lint(topo))


def test_isp_pixels_over_its_own_rate_warns():
    from buseval.schema import IspVpac, Pipeline, Topology
    topo = Topology(
        isp_vpacs=[IspVpac(name="ISP0", mpix_s=10), IspVpac(name="ISP1", mpix_s=600)],
        pipelines=[Pipeline(
            name="ISP0", type="isp",
            params={"width": 1920, "height": 1080, "fps": 60},
            stages=[{"name": "bayer", "width": 1920, "height": 1080, "read_factor": 1, "write_factor": 1}],
        )],
        ddr_channels=[_ddr(10000)],
    )
    assert any(i.rule == "isp-vpac-over" and i.node == "ISP0" for i in lint(topo))


def test_isp_name_outside_the_chip_list_warns():
    from buseval.schema import IspVpac, Pipeline, Topology
    topo = Topology(
        isp_vpacs=[IspVpac(name="ISP0", mpix_s=600), IspVpac(name="ISP1", mpix_s=600)],
        pipelines=[Pipeline(
            name="ISP2", type="isp",
            params={"width": 1280, "height": 720, "fps": 30},
            stages=[{"name": "bayer", "width": 1280, "height": 720, "read_factor": 1, "write_factor": 1}],
        )],
        ddr_channels=[_ddr(10000)],
    )
    assert any(i.rule == "isp-unknown" and i.node == "ISP2" for i in lint(topo))


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


def test_assumptions_have_level_field():
    """Every assumption row carries a 'level' (red/yellow/info)."""
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="USB0", type="usb",
                        params={"version": "3", "util_pct": 0.4})],
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    for a in result.assumptions:
        assert "level" in a
        assert a["level"] in ("red", "yellow", "info")


def test_assumptions_ddr_near_full_is_red():
    """When DDR util >= 80%, a RED assumption is added for the DDR channel."""
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="BIG", type="usb",
                        params={"version": "3.2", "util_pct": 1.0})],
        ddr_channels=[_ddr(100)],
    )
    result = predict(topo)
    ddr_rows = [a for a in result.assumptions if a["item"] == "DDR0"]
    assert any(r["level"] == "red" and "near full" in r["message"] for r in ddr_rows)


def test_assumptions_aggressive_util_is_red():
    """USB util > 0.9 triggers a RED assumption."""
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="USB0", type="usb",
                        params={"version": "3", "util_pct": 0.95})],
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    usb_rows = [a for a in result.assumptions if a["item"] == "USB0"]
    assert any(r["level"] == "red" and "aggressive" in r["message"] for r in usb_rows)


def test_topology_hash_stable_for_same_topology():
    """Same topology → same hash."""
    from buseval.report.structured import build_structured
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.5})],
        ddr_channels=[_ddr(10000)],
    )
    h1 = build_structured(predict(topo))["topology_hash"]
    h2 = build_structured(predict(topo))["topology_hash"]
    assert h1 == h2
    assert len(h1) == 12


def test_topology_hash_differs_for_different_topology():
    from buseval.report.structured import build_structured
    from buseval.schema import Master, DDRChannel, Topology
    topo1 = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.5})],
        ddr_channels=[_ddr(10000)],
    )
    topo2 = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.9})],
        ddr_channels=[_ddr(10000)],
    )
    h1 = build_structured(predict(topo1))["topology_hash"]
    h2 = build_structured(predict(topo2))["topology_hash"]
    assert h1 != h2


def test_ddr_effective_peak_min_controller_and_module():
    """effective_peak = min(controller_peak, module_peak). MT/s already includes DDR."""
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="X", type="usb", params={"version": "3", "util_pct": 0.1})],
        ddr_channels=[DDRChannel(
            name="DDR0",
            controller_mt_s=4266, controller_width_bits=128,   # controller = 68256
            module_mt_s=3200, module_width_bits=128,            # module = 51200
            module_groups=1, efficiency=0.7,
        )],
    )
    result = predict(topo)
    margins = evaluate_margin(result)
    m = margins[0]
    assert m.controller_peak_mbps == 68256
    assert m.module_peak_mbps == 51200
    assert m.effective_peak_mbps == 51200  # min
    assert m.bottleneck == "module"


def test_ddr_bottleneck_controller_when_module_faster():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="X", type="usb", params={"version": "3", "util_pct": 0.1})],
        ddr_channels=[DDRChannel(
            name="DDR0",
            controller_mt_s=3200, controller_width_bits=32,   # controller = 12800
            module_mt_s=4266, module_width_bits=32,            # module = 17064
            module_groups=1, efficiency=0.7,
        )],
    )
    margins = evaluate_margin(predict(topo))
    assert margins[0].bottleneck == "controller"
    assert margins[0].effective_peak_mbps == 12800


def test_ddr_bottleneck_matched():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="X", type="usb", params={"version": "3", "util_pct": 0.1})],
        ddr_channels=[DDRChannel(
            name="DDR0",
            controller_mt_s=3200, controller_width_bits=32,
            module_mt_s=3200, module_width_bits=32,
            module_groups=1, efficiency=0.7,
        )],
    )
    margins = evaluate_margin(predict(topo))
    assert margins[0].bottleneck == "matched"


def test_ddr_module_groups_multiplies_bandwidth():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="X", type="usb", params={"version": "3", "util_pct": 0.1})],
        ddr_channels=[DDRChannel(
            name="DDR0",
            controller_mt_s=4266, controller_width_bits=64,   # 34128
            module_mt_s=4266, module_width_bits=32,            # 17064 per group
            module_groups=2,                                    # 17064 × 2 = 34128
            efficiency=0.7,
        )],
    )
    margins = evaluate_margin(predict(topo))
    assert margins[0].module_peak_mbps == 34128
    assert margins[0].effective_peak_mbps == 34128  # controller and module both 34128
    assert margins[0].bottleneck == "matched"


def test_ddr_without_both_rates_has_no_peak():
    from buseval.schema import Master, DDRChannel, Topology
    topo = Topology(
        masters=[Master(name="X", type="usb", params={"version": "3", "util_pct": 0.1})],
        ddr_channels=[DDRChannel(name="DDR0", efficiency=0.7)],
    )
    margins = evaluate_margin(predict(topo))
    assert margins[0].effective_peak_mbps == 0
    assert margins[0].bottleneck == "n/a"
    assert margins[0].available_mbps == 0


def test_predict_source_not_found_errors():
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI0", type="mipi_csi",
                        params={"width": 1920, "height": 1080, "fps": 30, "bpp": 12, "lanes": 4})],
        pipelines=[
            Pipeline(name="NPU0", type="npu", source="CSI9",
                     params={"params_mbytes": 10, "activation_mbytes": 5, "inference_fps": 10, "tops_peak": 0}),
        ],
        ddr_channels=[_ddr(100000)],
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
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    isp = next(i for i in result.items if i.name == "ISP0")
    npu = next(i for i in result.items if i.name == "NPU0")
    # The next block reads the output picture, not the ISP's internal peak write.
    assert abs(npu.breakdown["input_frame_mbps"] - isp.breakdown["output_mbps"]) < 1e-3


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
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    npu = next(i for i in result.items if i.name == "NPU0")
    isp = next(i for i in result.items if i.name == "ISP0")
    # sources should contain both CSI0 (master, kind=master) and ISP0 (pipeline, kind=pipeline)
    kinds = {s["name"]: s.get("kind") for s in npu.breakdown["sources"]}
    assert kinds["CSI0"] == "master"
    assert kinds["ISP0"] == "pipeline"
    # CSI0 contributes 373.248 (master dims); ISP0 contributes its output picture
    csi0_input = next(s["input_mbps"] for s in npu.breakdown["sources"] if s["name"] == "CSI0")
    isp0_input = next(s["input_mbps"] for s in npu.breakdown["sources"] if s["name"] == "ISP0")
    assert abs(csi0_input - 373.248) < 1e-3
    assert abs(isp0_input - isp.breakdown["output_mbps"]) < 1e-3


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
        ddr_channels=[_ddr(100000)],
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
        ddr_channels=[_ddr(100000)],
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
    """No local size: the bitstream falls back to the upstream picture."""
    from buseval.estimators.registry import get_estimator
    est = get_estimator("venc")
    r = est.estimate({
        "source_input_mbps": 165.89, "source": "ISP0",
        "codec": "h265",
    })
    assert abs(r.read_bw_mbps - 165.89) < 1e-3
    assert abs(r.write_bw_mbps - 165.89 / 50) < 1e-3
    assert "ISP0" in r.dominant_factor


def test_venc_bitstream_uses_local_picture():
    from buseval.estimators.registry import get_estimator
    est = get_estimator("venc")
    r = est.estimate({
        "source_input_mbps": 165.888, "source": "ISP0",
        "width": 1280, "height": 720, "fps": 60, "format": "yuv422",
        "codec": "h265",
    })
    picture = 1280 * 720 * 60 * 16 / 8 / 1e6
    assert abs(r.read_bw_mbps - 165.888) < 1e-3
    assert abs(r.write_bw_mbps - picture / 50) < 1e-3


def test_predict_dsi_sources_display_zero_ddr():
    """DSI p2p from Display: DSI carries Display's read_bw, DDR=0 (Display already counts)."""
    from buseval.schema import Master, DDRChannel, Pipeline, Topology
    topo = Topology(
        masters=[Master(name="CSI1", type="mipi_csi",
                        params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2})],
        pipelines=[
            Pipeline(name="ISP0", type="isp", source="CSI1", mode="serial",
                     stages=[{"name": "x", "read_factor": 1.0, "write_factor": 2.0}]),
            Pipeline(name="DISP0", type="display", source="ISP0"),
            Pipeline(name="DSI0", type="mipi_dsi", source="DISP0",
                     params={"lanes": 4}),
        ],
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    disp = next(i for i in result.items if i.name == "DISP0")
    dsi = next(i for i in result.items if i.name == "DSI0")
    assert abs(dsi.breakdown["carried_mbps"] - disp.read_bw_mbps) < 1e-3
    assert dsi.read_bw_mbps == 0.0
    assert dsi.write_bw_mbps == 0.0
    assert dsi.breakdown["lane_capacity_mbps"] == 750.0


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
        ddr_channels=[_ddr(100000)],
    )
    issues = lint(topo)
    assert any(i.rule == "source-override" for i in issues)


def test_assumptions_do_not_repeat_canvas_sources():
    from buseval.session import load_preset
    result = predict(load_preset("tda4vh"))
    for row in result.assumptions:
        assert "input from" not in row["message"]
        assert "MB/s from" not in row["message"]


def test_eth_reads_venc_bitstream_without_adding_link_util():
    from buseval.gui.document import edge_flows
    from buseval.schema import Master, Pipeline, Topology
    topo = Topology(
        masters=[
            Master(name="CSI1", type="mipi_csi",
                   params={"width": 1280, "height": 720, "fps": 60, "bpp": 12, "lanes": 2}),
            Master(name="ETH0", type="eth", source="VENC0",
                   params={"link_gbps": 1, "util_pct": 0.9, "mtu": 1500, "direction": "both"}),
        ],
        pipelines=[
            Pipeline(name="VENC0", type="venc", source="CSI1",
                     params={"width": 1280, "height": 720, "fps": 60, "format": "yuv422", "codec": "h265"}),
        ],
        ddr_channels=[_ddr(100000)],
    )
    result = predict(topo)
    venc = next(i for i in result.items if i.name == "VENC0")
    eth = next(i for i in result.items if i.name == "ETH0")
    assert eth.read_bw_mbps == pytest.approx(venc.write_bw_mbps)
    assert eth.read_bw_mbps == pytest.approx(110.592 / 50, abs=1e-3)
    assert eth.write_bw_mbps == 0
    assert edge_flows(topo, result)[("VENC0", "ETH0")] == pytest.approx(eth.read_bw_mbps)


def test_can_rates_stay_inside_the_standard():
    from buseval.dbc.health_report import resolve_can_rates
    assert resolve_can_rates(standard="can", data_kbps=2000) == ("can", 1000.0, 1000.0)
    assert resolve_can_rates(standard="canfd", arbitration_kbps=500, data_kbps=9000)[2] == 8000.0
    standard, arbitration, data = resolve_can_rates(standard="canfd", arbitration_kbps=2000, data_kbps=500)
    assert arbitration <= 1000
    assert data >= arbitration
    assert standard == "canfd"


def test_can_dialog_colors_one_file():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from buseval.gui.dialogs import CanDialog
    QApplication.instance() or QApplication([])
    dialog = CanDialog()
    assert dialog.standard.currentData() == "canfd"
    assert dialog.arbitration.maximum() == 1000
    assert dialog.data_rate.maximum() == 8000
    assert dialog.arbitration.value() == 500
    assert dialog.data_rate.value() == 2000
    dialog.path.setText(str(EXAMPLES / "sample_heavy.dbc"))
    dialog._run()
    text = dialog.conclusion.text()
    assert text == "负载 = 27.392%，最坏延迟 = 0.60 ms"
    lines = [label.text() for label in dialog.step_labels]
    assert lines[0] == "64（字节） × 8（比特/字节） + 47（帧开销） = 559 bit"
    assert "2000 kbps（数据码率）" in lines[1]
    assert lines[2].startswith('<span style="color:#1F8A4C">负载 =</span>')
    assert "kbps（报文）" in lines[2]
    assert lines[2].endswith('<span style="color:#1F8A4C">= 27.392%</span>')
    assert "2（已有一帧，再争一次）" in lines[3]
    assert lines[4].startswith('<span style="color:#1F8A4C">最坏延迟 =</span>')
    assert lines[4].endswith('<span style="color:#1F8A4C">= 0.60 ms</span>')
    assert "帧开销" in dialog.step_labels[0].toolTip()
    assert "47 bit" in dialog.step_labels[2].toolTip()
    assert all(label.styleSheet() == "" for label in dialog.step_labels)
    assert "#1F8A4C" in dialog.conclusion.styleSheet()
    assert dialog.messages.rowCount() == 17
    assert dialog.load.isHidden()
    dialog.standard.setCurrentIndex(dialog.standard.findData("can"))
    assert dialog.conclusion.text() == ""
    assert dialog.data_rate.maximum() == 1000
    assert dialog.data_rate.value() == 1000
    dialog.data_rate.setValue(500)
    dialog._run()
    assert "17" in dialog.conclusion.text() and "装不下" in dialog.conclusion.text()
    assert all(label.text() == "" for label in dialog.step_labels)
    assert "#DC2626" in dialog.conclusion.styleSheet()
    assert dialog.messages.rowCount() == 17


def test_can_cli_shows_the_calculation_inside_the_panel():
    import io
    from rich.console import Console
    from buseval.dbc.health_report import build_health_report
    from buseval.report.terminal import render_health_terminal

    report = build_health_report(str(EXAMPLES / "sample_heavy.dbc"), bitrate_kbps=2000)
    buf = io.StringIO()
    render_health_terminal(
        report,
        console=Console(file=buf, width=200, no_color=True, highlight=False, force_terminal=False),
        use_color=False,
    )
    text = buf.getvalue()
    assert "╭" in text or "┌" in text
    assert "负载 = 27.392%，最坏延迟 = 0.60 ms" in text
    assert "64（字节） × 8（比特/字节） + 47（帧开销） = 559 bit" in text
    assert "负载 = " in text and "= 27.392%" in text
    assert "最坏延迟 = " in text and "= 0.60 ms" in text
    assert "LidarPointBatch0" in text


def test_can_node_chooses_file_or_generic():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from buseval.gui.dialogs import CanDialog

    class _Node:
        name = "CAN0"
        type = "can"
        enabled = True
        params = {"bitrate_mbps": 0.5, "load_pct": 0.4, "direction": "tx"}

    QApplication.instance() or QApplication([])
    dialog = CanDialog(node=_Node())
    assert dialog.mode.currentData() == "generic"
    assert dialog.file_row.isHidden()
    assert dialog.load.value() == pytest.approx(40)
    assert dialog.data_rate.maximum() == 1000
    assert dialog.data_rate.value() == 500
    saved = dialog.node_values()["params"]
    assert saved["standard"] == "can"
    assert saved["load_pct"] == pytest.approx(0.4)
    assert saved["direction"] == "tx"
    assert saved["bitrate_mbps"] == pytest.approx(0.5)
    assert "dbc_path" not in saved
    assert "bus_id" not in saved
    dialog.mode.setCurrentIndex(dialog.mode.findData("file"))
    assert not dialog.file_row.isHidden()
    assert dialog.load.isHidden()
    dialog.path.setText("/tmp/sample.dbc")
    filed = dialog.node_values()["params"]
    assert filed["dbc_path"] == "/tmp/sample.dbc"
    assert "load_pct" not in filed
