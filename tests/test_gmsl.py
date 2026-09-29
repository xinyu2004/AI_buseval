"""Tests for GMSL link bandwidth calculator."""
import pytest

from buseval.gmsl.calculator import (
    calculate_link,
    parse_param_string,
    load_yaml,
    build_report_from_links,
)
from buseval.gmsl.report import build_gmsl_structured

EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


def test_gmsl_single_link_formula():
    """1920×1080×30 RAW12, heartbeat off, 9b/10b, CRC on, FEC off → 864.4 Mbps."""
    r = calculate_link("TEST", width=1920, height=1080, fps=30, bpp=12)
    assert abs(r.pixel_rate_mbps - 746.496) < 1e-3
    assert abs(r.csi_mbps - 895.7952) < 1e-3
    assert abs(r.after_encoding_mbps - 864.0) < 1e-3
    assert abs(r.link_bw_mbps - 864.422081) < 1e-2
    assert r.encoding == "9b10b"
    assert r.bpp_link == 12
    assert r.best_fit == "gmsl1"
    assert [row["tier"] for row in r.recommendations] == ["gmsl1", "gmsl2_3g", "gmsl2_6g", "gmsl3"]
    assert r.recommendations[3]["fec_extra"] is True
    from buseval.gmsl.calculator import csi_rate
    assert csi_rate(r.csi_mbps, "cphy", 4)["lanes"] == 3
    assert csi_rate(r.csi_mbps, "dphy", 4)["lanes"] == 4


def test_gmsl_custom_blanking():
    r_default = calculate_link("T", width=1920, height=1080, fps=30, bpp=12)
    r_off = calculate_link("T", width=1920, height=1080, fps=30, bpp=12, blanking=1.5)
    r_on = calculate_link("T", width=1920, height=1080, fps=30, bpp=12, blanking=1.5, heartbeat=True)
    assert r_off.blanking == 1.5
    assert abs(r_off.link_bw_mbps - r_default.link_bw_mbps) < 1e-6
    assert r_off.csi_mbps > r_default.csi_mbps
    assert r_on.link_bw_mbps > r_default.link_bw_mbps


def test_gmsl_param_string_parse():
    params = parse_param_string("width=1920 height=1080 fps=30 bpp=12 blanking=1.25")
    assert params["width"] == 1920
    assert params["height"] == 1080
    assert params["fps"] == 30
    assert params["bpp"] == 12
    assert params["blanking"] == 1.25


def test_gmsl_param_string_comma_legacy():
    """Legacy comma-separated form still works."""
    params = parse_param_string("width=1920,height=1080,fps=30,bpp=12")
    assert params["width"] == 1920
    assert params["height"] == 1080
    assert params["fps"] == 30
    assert params["bpp"] == 12


def test_gmsl_param_string_mixed_separators():
    """Mixed comma and space separators work."""
    params = parse_param_string("width=1920 height=1080, fps=30, bpp=12")
    assert params["width"] == 1920
    assert params["fps"] == 30
    assert len(params) == 4


def test_gmsl_param_string_bad():
    with pytest.raises(ValueError):
        parse_param_string("width=1920,no_equals")


def test_gmsl_yaml_multi_link():
    links, overrides = load_yaml(EXAMPLES / "gmsl_links.yaml")
    assert len(links) == 4
    assert links[0]["name"] == "CAM_FRONT"
    assert overrides["blanking"] == 1.2
    assert links[0]["format"] == "yuv420"


def test_gmsl_yaml_global_blanking_override():
    """If YAML has top-level blanking, it applies to all links."""
    import tempfile, yaml as _yaml
    content = _yaml.dump({
        "blanking": 1.5,
        "links": [{"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12}],
    })
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(content)
        path = f.name
    links, overrides = load_yaml(path)
    assert overrides["blanking"] == 1.5
    report = build_report_from_links(links, overrides)
    assert report.links[0].blanking == 1.5


def test_gmsl_recommendation_table():
    """864 Mbps fits every tier; the lowest is GMSL1."""
    r = calculate_link("T", width=1920, height=1080, fps=30, bpp=12)
    assert len(r.recommendations) == 4
    assert r.recommendations[0]["tier"] == "gmsl1"
    assert r.recommendations[0]["fits"] is True
    assert r.recommendations[1]["fits"] is True
    assert r.best_fit == "gmsl1"


def test_gmsl_raw8_link_bpp_is_nine():
    r = calculate_link("T", width=1280, height=720, fps=60, bpp=8)
    assert r.bpp == 8
    assert r.bpp_link == 9
    assert abs(r.link_bw_mbps - 583.965) < 1e-2


def test_gmsl_overflow_no_tier_fits():
    """8K@60×16bpp exceeds GMSL3's 12 Gbps line rate."""
    r = calculate_link("HUGE", width=7680, height=4320, fps=60, bpp=16)
    assert r.link_bw_mbps > 12000
    assert all(not rec["fits"] for rec in r.recommendations)
    assert r.best_fit == ""


def test_gmsl_report_total():
    report = build_report_from_links([
        {"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12},
        {"name": "B", "width": 1280, "height": 720, "fps": 60, "bpp": 12},
    ])
    assert len(report.links) == 2
    expected_total = report.links[0].link_bw_mbps + report.links[1].link_bw_mbps
    assert abs(report.total_link_bw_mbps - expected_total) < 1e-3
    four = build_report_from_links([
        {"name": f"CAM{i}", "width": 1920, "height": 1080, "fps": 30, "bpp": 12}
        for i in range(4)
    ])
    assert four.to_dict()["summary"]["aggregate_best_fit"] == "gmsl2_6g"
    with pytest.raises(ValueError):
        build_report_from_links([
            {"name": f"CAM{i}", "width": 640, "height": 480, "fps": 30, "bpp": 8}
            for i in range(5)
        ])


def test_gmsl_structured_output():
    report = build_report_from_links([
        {"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12},
    ])
    d = build_gmsl_structured(report)
    assert "links" in d
    assert "total_link_bw_mbps" in d
    assert "summary" in d
    assert d["links"][0]["name"] == "A"
    assert d["summary"]["link_count"] == 1


def test_gmsl_cli_text_matches_dialog_wording():
    import io

    from rich.console import Console

    from buseval.gmsl.report import render_gmsl_terminal

    buf = io.StringIO()
    single = build_report_from_links([
        {"name": "CAM1", "width": 1920, "height": 1080, "fps": 30, "bpp": 12},
    ])
    render_gmsl_terminal(single, console=Console(file=buf, width=200, no_color=True, highlight=False, force_terminal=False), use_color=False)
    text = buf.getvalue()
    assert "1920×1080×30 = 62.208 MHz" in text
    assert "746.5 × 1.0417  (12 + 0.5) / 12 = 777.6 Mbps" in text
    assert "864.4 × 1.0667  (128/120) = 922.1 Mbps" in text
    assert "链路带宽  864.4 Mbps（0.86 Gbps）" in text
    assert "GMSL Link: CAM1" in text
    assert "GMSL1" in text and "27.7%" in text and "推荐" in text
    assert "CSI 配置" not in text
    assert "895.8 Mbps" not in text

    buf = io.StringIO()
    four = build_report_from_links([
        {"name": f"CAM{i}", "width": 1920, "height": 1080, "fps": 30, "bpp": 12}
        for i in range(1, 5)
    ])
    render_gmsl_terminal(four, console=Console(file=buf, width=200, no_color=True, highlight=False, force_terminal=False), use_color=False)
    text = buf.getvalue()
    assert "864.4 + 864.4 + 864.4 + 864.4 = 3,457.7 Mbps" in text
    assert "3,457.7 × 1.0667  (128/120) = 3,688.2 Mbps" in text
    assert "57.6%" in text and "推荐" in text
    assert "GMSL Link:" not in text
    assert "CSI 配置" not in text


def test_gmsl_cli_single_link(capsys):
    from buseval.cli import main
    rc = main(["predict", "--GMSL", "width=1920", "height=1080", "fps=30", "bpp=12", "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert d["links"][0]["link_bw_mbps"] > 800
    assert d["links"][0]["best_fit"] == "gmsl1"


def test_gmsl_cli_single_link_legacy_comma(capsys):
    """Legacy comma-separated form still works via CLI."""
    from buseval.cli import main
    rc = main(["predict", "--GMSL", "width=1920,height=1080,fps=30,bpp=12", "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert d["links"][0]["link_bw_mbps"] > 800


def test_gmsl_cli_yaml_multi(capsys):
    from buseval.cli import main
    rc = main(["predict", "--GMSL", str(EXAMPLES / "gmsl_links.yaml"), "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert len(d["links"]) == 4
    assert d["summary"]["link_count"] == 4


def test_gmsl_single_dialog_colors_tiers_and_clock():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from buseval.gui.dialogs import GmslDialog

    QApplication.instance() or QApplication([])
    dialog = GmslDialog()
    assert dialog.format_row._format_box.currentText() == "raw12"
    shown = [label.text() for label in dialog.format_row.findChildren(QLabel) if label.text()]
    assert "12 bpp" in shown
    assert dialog.csi_lines.text() == ""
    dialog._single()
    steps = dialog.steps.text()
    assert "1920×1080×30 = 62.208 MHz" in steps
    assert "62.208 × 12 = 746.5 Mbps" in steps
    assert "746.5 × 1.0417  (12 + 0.5) / 12 = 777.6 Mbps" in steps
    assert "777.6 × 1.1111  (10/9) = 864.0 Mbps" in steps
    assert "864.0 × 1.0005  (2048/2047) = 864.4 Mbps" in steps
    assert "864.4 × 1  (关) = 864.4 Mbps" in steps
    assert "864.4 × 1.0667  (128/120) = 922.1 Mbps" in steps
    assert dialog.link_bw.text() == "864.4 Mbps（0.86 Gbps）"
    assert dialog.tier_boxes[0].name.text() == "GMSL1"
    assert dialog.tier_boxes[0].capacity.text() == "3.12 Gbps"
    assert dialog.tier_boxes[0].occupancy.text() == "27.7%"
    assert dialog.tier_boxes[0].fit.text() == "推荐"
    assert dialog.lanes.maximum() == 4
    assert dialog.link_bw.font().pointSize() >= 22
    assert "rgb(125, 222, 150)" in dialog.tier_boxes[0].styleSheet()
    assert dialog.tier_boxes[1].name.text() == "GMSL2 3G"
    assert dialog.tier_boxes[1].occupancy.text() == "28.8%"
    assert dialog.tier_boxes[2].name.text() == "GMSL2 6G"
    assert dialog.tier_boxes[2].capacity.text() == "6.0 Gbps"
    assert dialog.tier_boxes[3].name.text() == "GMSL3"
    assert dialog.tier_boxes[3].capacity.text() == "12.0 Gbps"
    assert dialog.tier_boxes[3].occupancy.text() == "7.7%"
    assert dialog.tier_boxes[3].fit.text() == ""
    assert "强制" in dialog.fec.toolTip()
    assert "128/120 = 1.0667" in dialog.fec.toolTip()
    assert "10/9 = 1.1111" in dialog.encoding.toolTip()
    assert "LIM_HEART" in dialog.heartbeat.toolTip()
    assert "2048/2047" in dialog.steps.toolTip()
    csi = dialog.csi_lines.text()
    assert "895.8 Mbps" in csi
    assert "0.224 Gbps，配置 0.3 Gbps" in csi
    assert "112.0 MHz" in csi
    dialog.lanes.setValue(2)
    csi = dialog.csi_lines.text()
    assert "0.448 Gbps，配置 0.5 Gbps" in csi
    assert "223.9 MHz" in csi
    dialog.fec.setChecked(True)
    assert "128/120" in dialog.steps.text()
    assert "× 1.0667  (128/120)" in dialog.steps.text()
    assert "GMSL3" not in dialog.steps.text()
    assert dialog.tier_boxes[3].fit.text() == ""
    dialog.cphy.setChecked(True)
    assert dialog.lanes.maximum() == 3
    assert dialog.lanes.value() == 2
    csi = dialog.csi_lines.text()
    assert "Msym/s" in csi
    assert "MHz" not in csi
    dialog.format_row._format_box.setCurrentText("custom")
    dialog.format_row._bpp_spin.setValue(16)
    dialog._single()
    assert "62.208 × 16 = " in dialog.steps.text()
    dialog.close()


def test_format_yaml_resolves_bpp_and_old_bpp_maps_to_raw(tmp_path):
    from buseval.estimators.formats import format_for_bpp, format_for_link
    from buseval.gmsl.calculator import save_yaml

    assert format_for_bpp(12) == "raw12"
    assert format_for_bpp(9) is None
    assert format_for_link({"bpp": 8}) == ("raw8", None)
    assert format_for_link({"bpp": 9}) == ("custom", 9.0)
    path = tmp_path / "links.yaml"
    save_yaml(path, [{
        "name": "CAM_FRONT",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "format": "raw12",
    }], 1.2)
    text = path.read_text(encoding="utf-8")
    assert "format: raw12" in text
    assert "bpp" not in text
    links, overrides = load_yaml(path)
    report = build_report_from_links(links, overrides)
    assert report.links[0].bpp == 12
    assert abs(report.links[0].link_bw_mbps - 864.422081) < 1e-2


def test_multi_dialog_sums_one_port_and_saves_format(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.dialogs import GmslDialog

    QApplication.instance() or QApplication([])
    dialog = GmslDialog()
    dialog.multi_table.cellWidget(0, 0).setText("CAM_FRONT")
    dialog._add_link_row({
        "name": "CAM_LEFT",
        "width": 1280,
        "height": 720,
        "fps": 60,
        "format": "raw8",
    })
    dialog._calculate_multi()
    steps = dialog.multi_steps.text()
    assert "CAM_FRONT" in steps
    assert "62.208 × 12 × 1.0417 × 1.1111 × 1.0005 × 1 = 864.4 Mbps" in steps
    assert "55.296 × 9 × 1.0556 × 1.1111 × 1.0005 × 1 = 584.0 Mbps" in steps
    assert "同轴 bpp 9" in steps
    assert "合计 = 1,188.9 Mbps" not in steps
    assert "× 1.0417  (12 + 0.5) / 12" in steps
    assert "× 1.0556  (9 + 0.5) / 9" in steps
    assert "× 1.1111  (10/9)" in steps
    assert "× 1.0005  (2048/2047)" in steps
    assert "× 1  (关)" in steps
    assert "heartbeat 关，未乘 blanking" in steps
    assert "864.4 + 584.0 = 1,448.4 Mbps" in steps
    assert "1,448.4 × 1.0667  (128/120) = 1,544.9 Mbps" in steps
    assert dialog.multi_link_bw.text() == "1,448.4 Mbps（1.45 Gbps）"
    assert dialog.multi_link_bw.font().pointSize() >= 22
    assert dialog.multi_tiers[0].fit.text() == "推荐"
    assert dialog.multi_tiers[0].occupancy.text() == "46.4%"
    assert "rgb(125, 222, 150)" in dialog.multi_tiers[0].styleSheet()
    assert dialog.multi_tiers[1].name.text() == "GMSL2 3G"
    assert dialog.multi_lanes.maximum() == 4
    csi = dialog.multi_csi_lines.text()
    assert "1,426.6 Mbps" in csi
    assert "0.357 Gbps，配置 0.4 Gbps" in csi
    assert "178.3 MHz" in csi
    assert "强制" in dialog.multi_fec.toolTip()
    assert "128/120 = 1.0667" in dialog.multi_fec.toolTip()
    dialog.multi_table.cellWidget(0, 1).setValue(1280)
    assert dialog.multi_link_bw.text() == ""
    assert dialog.multi_csi_lines.text() == ""
    dialog._load_multi(str(EXAMPLES / "gmsl_links.yaml"))
    assert dialog.multi_table.rowCount() == 4
    assert dialog.multi_add.isEnabled() is False
    assert dialog.multi_table.minimumHeight() == dialog.multi_table.maximumHeight()
    dialog._add_link_row()
    assert dialog.multi_table.rowCount() == 4
    assert dialog.multi_table.cellWidget(0, 0).text() == "CAM_FRONT"
    assert dialog.multi_table.cellWidget(0, 4)._format_box.currentText() == "yuv420"
    assert dialog.multi_steps.text() == ""
    dialog.multi_table.setRowCount(0)
    dialog._add_link_row({
        "name": "CAM_FRONT",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "format": "raw12",
    })
    dialog._multi_path = str(tmp_path / "out.yaml")
    dialog._save_multi()
    saved = (tmp_path / "out.yaml").read_text(encoding="utf-8")
    assert "format: raw12" in saved
    assert "bpp:" not in saved
    assert "encoding: 9b10b" in saved
    assert "lanes: 4" in saved
    dialog.close()
