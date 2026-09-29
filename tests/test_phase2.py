"""Session, measurement compare, and canvas share colors."""
import json
from pathlib import Path

import pytest

from buseval.cli import main as cli_main
from buseval.collect.perf_text import measurement_from_perf, parse_perf_stat
from buseval.engine.compare import compare_measurement
from buseval.gui.document import (
    add_block,
    ddr_level_rgb,
    rank_levels,
    fill_rgb,
    load_param_schema,
    new_topology,
    set_node_pos,
    text_is_white,
    traffic_shares,
    try_connect,
)
from buseval.report.structured import build_structured
from buseval.schema import DDRChannel, Master, Topology
from buseval.session import evaluate, load_preset, save_topology

PERF_TEXT = """
 Performance counter stats for 'python3':

         1,000,000      l2d_cache_refill
           250,000      l2d_cache_wb

       1.000000000 seconds time elapsed
"""


def _lightness(rgb: tuple[int, int, int]) -> float:
    return max(rgb) + min(rgb)


def _hue(rgb: tuple[int, int, int]) -> float:
    red, green, blue = [channel / 255 for channel in rgb]
    peak = max(red, green, blue)
    floor = min(red, green, blue)
    span = peak - floor
    if span == 0:
        return 0.0
    if peak == red:
        hue = (green - blue) / span % 6
    elif peak == green:
        hue = (blue - red) / span + 2
    else:
        hue = (red - green) / span + 4
    return hue * 60


def _hue_clear_of_verdict(hue: float) -> bool:
    """Green, yellow, and red stay on the DDR card."""
    if hue <= 25 or hue >= 345:
        return False
    if 40 <= hue <= 75:
        return False
    if 90 <= hue <= 160:
        return False
    return True


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


def test_save_roundtrip_ignores_ui_in_hash(tmp_path):
    original = load_preset("tda4vh")
    before = build_structured(evaluate(original))["topology_hash"]
    set_node_pos(original, original.masters[0].name, 12, 34)
    set_node_pos(original, original.ddr_channels[0].name, 400, 20)
    path = tmp_path / "tda4vh.yaml"
    save_topology(original, path)
    loaded = evaluate(__import__("buseval.loader", fromlist=["load_topology"]).load_topology(path))
    assert build_structured(loaded)["topology_hash"] == before
    text = path.read_text(encoding="utf-8")
    assert "ui_x" in text
    from buseval.gui.document import node_pos
    from buseval.loader import load_topology
    again = load_topology(path)
    assert node_pos(again.masters[0]) == (12.0, 34.0)
    again.ui_lang = "en"
    save_topology(again, path)
    restored = load_topology(path)
    assert restored.ui_lang is None
    assert "ui_lang" not in path.read_text(encoding="utf-8")
    assert node_pos(restored.masters[0]) == (12.0, 34.0)
    assert node_pos(restored.ddr_channels[0]) == (400.0, 20.0)


def test_connect_changes_prediction_the_same_way_as_yaml():
    schema = load_param_schema()
    topo = new_topology()
    add_block(topo, "mipi_csi", 0, 0, schema)
    add_block(topo, "isp", 200, 0, schema)
    csi = topo.masters[0].name
    isp = topo.pipelines[0].name
    assert try_connect(topo, csi, isp) is None
    via_gui = evaluate(topo)
    dumped = topo.model_dump()
    via_cli = evaluate(Topology.model_validate(dumped))
    assert via_gui.total_read_mbps == pytest.approx(via_cli.total_read_mbps)
    assert via_gui.total_write_mbps == pytest.approx(via_cli.total_write_mbps)
    assert any(item.name == isp and item.read_bw_mbps > 0 for item in via_gui.items)


def test_share_color_darkens_the_largest_item():
    topo = Topology(
        masters=[
            Master(name="A", type="usb", params={"version": "2", "util_pct": 0.1}),
            Master(name="B", type="usb", params={"version": "3.2", "util_pct": 1.0}),
        ],
        ddr_channels=[_ddr(100000)],
    )
    shares = traffic_shares(evaluate(topo))
    largest = max(shares, key=shares.get)
    assert largest == "B"
    levels = rank_levels({"big": 50, "mid": 10, "same": 10, "small": 1})
    assert levels["big"] == 1
    assert levels["mid"] == levels["same"] == 0.5
    assert levels["small"] == 0
    assert levels["big"] > rank_levels(shares)["A"]
    strong, mid, pale = fill_rgb(1), fill_rgb(0.5), fill_rgb(0)
    assert strong == (0xE3, 0x1C, 0x9A)
    assert _lightness(strong) < _lightness(mid) < _lightness(pale)
    assert _hue(strong) > _hue(fill_rgb(0.6)) > _hue(mid)
    assert _hue(mid) < 250
    for rgb in (strong, mid, fill_rgb(0.6)):
        assert _hue_clear_of_verdict(_hue(rgb))
    assert max(pale) - min(pale) < 12
    assert not text_is_white(0)
    assert ddr_level_rgb(0.5, 0.6, 0.8) == (0x7D, 0xDE, 0x96)
    assert ddr_level_rgb(0.6, 0.6, 0.8) == (0xF6, 0xD3, 0x65)
    assert ddr_level_rgb(0.8, 0.6, 0.8) == (0xF0, 0x8A, 0x80)


def test_perf_text_becomes_cpu_measurement():
    parsed = parse_perf_stat(PERF_TEXT)
    body = measurement_from_perf(parsed)
    assert body["kind"] == "cpu_bw"
    assert body["source"] == "arm_pmu"
    # 1_000_000 refills * 64 bytes / 1s / 1e6 = 64 MB/s
    assert body["items"][0]["read_bw_mbps"] == pytest.approx(64.0)
    assert body["items"][0]["write_bw_mbps"] == pytest.approx(16.0)


def test_compare_ddr_aggregate_and_master_item():
    topo = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.3})],
        ddr_channels=[_ddr(10000, 1.0)],
    )
    prediction = evaluate(topo)
    usb = next(item for item in prediction.items if item.name == "USB0")
    item_meas = {
        "kind": "ddr",
        "source": "tda4_ddr",
        "items": [{"name": "USB0", "read_bw_mbps": usb.read_bw_mbps, "write_bw_mbps": usb.write_bw_mbps}],
    }
    rows = {row.name: row for row in compare_measurement(prediction, item_meas)}
    assert rows["USB0"].verdict == "OK"
    ddr_meas = {
        "kind": "ddr",
        "source": "tda4_ddr",
        "items": [{"name": "DDR0", "read_bw_mbps": prediction.total_read_mbps, "write_bw_mbps": prediction.total_write_mbps}],
    }
    ddr_rows = compare_measurement(prediction, ddr_meas)
    ddr = next(row for row in ddr_rows if row.name == "DDR0")
    assert ddr.note == "未拆到 master"
    assert ddr.verdict == "OK"


def test_cli_compare(tmp_path, capsys):
    topo_path = tmp_path / "t.yaml"
    meas_path = tmp_path / "m.json"
    topo = Topology(
        masters=[Master(name="USB0", type="usb", params={"version": "3", "util_pct": 0.3})],
        ddr_channels=[_ddr(100000)],
    )
    save_topology(topo, topo_path)
    prediction = evaluate(topo)
    usb = next(item for item in prediction.items if item.name == "USB0")
    meas_path.write_text(json.dumps({
        "kind": "ddr",
        "source": "tda4_ddr",
        "items": [{"name": "USB0", "read_bw_mbps": usb.read_bw_mbps, "write_bw_mbps": usb.write_bw_mbps}],
    }), encoding="utf-8")
    rc = cli_main(["compare", "-t", str(topo_path), "-m", str(meas_path)])
    assert rc == 0
    assert "USB0" in capsys.readouterr().out


def test_language_follows_the_user_not_the_project(monkeypatch, tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.app import MainWindow
    from buseval.gui.i18n import lang, set_lang, t

    monkeypatch.setenv("BUSEVAL_UI_SETTINGS", str(tmp_path / "ui.ini"))
    QApplication.instance() or QApplication([])
    set_lang("zh")
    first = MainWindow()
    assert lang() == "zh"
    first.set_language("en")
    assert lang() == "en"
    assert first.dirty is False
    first.close()

    set_lang("zh")
    second = MainWindow()
    assert lang() == "en"
    second.open_preset("tda4vh")
    assert lang() == "en"
    assert t("ddr_pending") == "DDR  not evaluated"
    second.close()
    set_lang("zh")


def test_language_switch_rewrites_lint_and_chrome():
    from buseval.gui.i18n import STRINGS, set_lang, t
    from buseval.lint import lint
    assert set(STRINGS["zh"]) == set(STRINGS["en"])
    try:
        set_lang("en")
        assert t("tab_lint") == "Lint"
        assert t("tab_assume") == "Assumptions"
        assert any("No DDR" in issue.message for issue in lint(Topology()))
        set_lang("zh")
        assert t("tab_lint") == "校验"
        assert any("没有声明 DDR" in issue.message for issue in lint(Topology()))
    finally:
        set_lang("zh")


def test_ddr_dialog_conclusion_matches_engine_peaks():
    from buseval.gui.dialogs import ddr_conclusion_text

    text = ddr_conclusion_text(load_preset("tda4vh").ddr_channels[0])
    assert "4266 × 32 / 8 × 4 = 68,256 MB/s" in text
    assert "3200 × 32 / 8 × 4 = 51,200 MB/s" in text
    assert "min(68,256, 51,200) = 51,200 MB/s" in text
    assert "51,200 × 0.7 = 35,840 MB/s" in text
    assert "颗粒" in text
    empty = ddr_conclusion_text(DDRChannel(name="DDR0"))
    assert "控制器峰值还没填" in empty
    assert "颗粒峰值还没填" in empty
    assert "有效峰值还没填。填上控制器和颗粒两边的速率。" in empty


def test_blank_soc_can_fill_chip_spec_and_unset_peak_stands_out():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.app import MainWindow
    from buseval.gui.dialogs import DdrDialog

    QApplication.instance() or QApplication([])
    blank = DdrDialog(new_topology())
    assert blank.ctrl_mts.isReadOnly() is False
    assert blank.ctrl_type.isEnabled()
    blank.ctrl_mts.setValue(4266)
    blank.ctrl_width.setValue(32)
    blank.ctrl_groups.setValue(4)
    blank.ctrl_type.setCurrentText("LPDDR4")
    blank.mod_mts.setValue(3200)
    blank.mod_width.setValue(32)
    blank.mod_groups.setValue(4)
    assert "4266 × 32 / 8 × 4" in blank.ctrl_summary.text()
    blank.apply()
    channel = blank.topology.ddr_channels[0]
    assert channel.controller_mt_s == 4266
    assert channel.controller_width_bits == 32
    assert channel.controller_groups == 4
    assert channel.controller_type == "LPDDR4"
    assert channel.module_mt_s == 3200
    assert not hasattr(blank, "peak")

    preset = DdrDialog(load_preset("tda4vh"))
    assert preset.ctrl_mts.isReadOnly()
    assert preset.ctrl_type.isEnabled() is False
    original = preset.topology.ddr_channels[0].controller_mt_s
    preset.ctrl_mts.setValue(1000)
    preset.apply()
    assert preset.topology.ddr_channels[0].controller_mt_s == original

    window = MainWindow()
    window.topology = new_topology()
    window.at_home = False
    window.ddr_mode = "unset"
    window._paint_ddr()
    assert "未设置峰值" in window.ddr_bar.text()
    assert "#b86a12" in window.ddr_bar.styleSheet()
    window.topology.ddr_channels[0].controller_mt_s = 3200
    window.topology.ddr_channels[0].module_mt_s = 3200
    window.ddr_mode = "pending"
    window._paint_ddr()
    assert "#1f4e79" in window.ddr_bar.styleSheet()
    window.close()
    blank.close()
    preset.close()


def test_ddr_bar_explains_verdict_and_keeps_cli_columns():
    from buseval.engine.margin import ChannelMargin
    from buseval.gui.app import ddr_bar_text

    margin = ChannelMargin(
        name="DDR0",
        controller_mt_s=4266, controller_width_bits=32, controller_groups=4, controller_type="LPDDR4",
        module_mt_s=3200, module_width_bits=32, module_groups=4, module_type="LPDDR4",
        controller_peak_mbps=68256, module_peak_mbps=51200, effective_peak_mbps=51200,
        bottleneck="module", efficiency=0.7, available_mbps=35840,
        read_demand_mbps=6589, write_demand_mbps=3762,
        read_util=0.1838, write_util=0.1050, occupancy=0.2888, verdict="OK",
    )
    text = ddr_bar_text(margin, 0.6, 0.8)
    assert "6,589" in text and "3,762" in text
    assert "占用 28.9%" in text
    assert "读 6,589 + 写 3,762 = 10,351" in text
    assert "低于黄线 60%，所以吻合" in text
    assert "读写差" not in text


def test_report_tables_fill_width_and_assumption_opens_the_node():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QHeaderView

    from buseval.gui.app import MainWindow
    from buseval.session import load_preset

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._asked_kind = True
    window.resize(1100, 700)
    window.show()
    app.processEvents()
    for table in (window.lint_table, window.assume_table, window.compare_table):
        header = table.horizontalHeader()
        window.tabs.setCurrentWidget(table)
        app.processEvents()
        width = table.viewport().width()
        filled = sum(header.sectionSize(i) for i in range(table.columnCount()))
        assert header.stretchLastSection()
        assert header.sectionResizeMode(table.columnCount() - 1) == QHeaderView.ResizeMode.Stretch
        assert abs(filled - width) <= 2

    opened = []
    window.edit_node = lambda name: opened.append(name)
    window.topology = load_preset("tda4vh")
    window.at_home = False
    window._show_topology(keep_positions=False)
    window.run_evaluate()
    app.processEvents()
    messages = [
        window.assume_table.item(row, 2).text()
        for row in range(window.assume_table.rowCount())
    ]
    assert not any("input from" in text or "MB/s from" in text for text in messages)
    if window.assume_table.rowCount():
        window.assume_table.cellDoubleClicked.emit(0, 1)
        name = window.assume_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        assert opened == [name]
    window.close()


def test_wires_use_bezier_and_source_module_color():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QPainterPath
    from PySide6.QtWidgets import QApplication, QGraphicsScene

    from buseval.gui.canvas import EdgeItem, NodeItem

    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    src = NodeItem("CSI0", "mipi_csi", "master", "1920×1080")
    dst = NodeItem("NPU0", "npu", "pipeline", "input")
    src.setPos(0, 40)
    dst.setPos(320, 40)
    scene.addItem(src)
    scene.addItem(dst)
    edge = EdgeItem(src, dst)
    scene.addItem(edge)
    kinds = [edge.path().elementAt(index).type for index in range(edge.path().elementCount())]
    assert QPainterPath.ElementType.CurveToElement in kinds
    assert edge.pen().color().name().lower() == "#2471a3"
    edge.set_flow(400, 1.0, 40)
    assert edge.pen().color().name().lower() == "#2471a3"
    assert edge.pen().widthF() < 2.5


def test_node_caption_stays_inside_the_card():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.canvas import CAPTION_W, CAPTION_X, NODE_W, NodeItem

    QApplication.instance() or QApplication([])
    caption = "CSI0 1920×1080 @30 12bpp ×4 + ISP0 1280×720 @60 12bpp"
    node = NodeItem("NPU0", "npu", "pipeline", caption)
    rect = node.rect()
    text = node.caption.boundingRect()
    assert text.width() <= CAPTION_W + 1
    assert CAPTION_X + text.width() <= NODE_W - 8
    assert node.caption.pos().y() + text.height() <= rect.height()
    assert text.height() > 20
    node.set_share(12.0, 100.0, 0.2)
    assert node.detail.pos().y() + node.detail.boundingRect().height() <= node.rect().height()


def test_linkable_types_use_distant_stripes():
    """Types that can sit on one wire must not share a hue."""
    import colorsys

    from buseval.gui.document import TYPE_COLORS

    pairs = (
        ("mipi_csi", "isp"),
        ("mipi_csi", "npu"),
        ("mipi_csi", "venc"),
        ("mipi_csi", "display"),
        ("isp", "venc"),
        ("isp", "npu"),
        ("isp", "display"),
        ("isp", "gpu"),
        ("vdec", "venc"),
        ("vdec", "npu"),
        ("vdec", "display"),
        ("gpu", "display"),
        ("display", "mipi_dsi"),
    )

    def hue(hex_color: str) -> float:
        red = int(hex_color[1:3], 16) / 255
        green = int(hex_color[3:5], 16) / 255
        blue = int(hex_color[5:7], 16) / 255
        return colorsys.rgb_to_hsv(red, green, blue)[0] * 360

    for left, right in pairs:
        gap = abs(hue(TYPE_COLORS[left]) - hue(TYPE_COLORS[right]))
        gap = min(gap, 360 - gap)
        assert gap >= 40, (left, right, gap)


def test_every_module_field_has_a_tip():
    schema = load_param_schema()
    for type_name, spec in schema.items():
        fields = spec.get("fields") or []
        if type_name == "can":
            continue
        assert fields, type_name
        for field in fields:
            assert field.get("tip"), (type_name, field.get("key"))


def test_edge_flow_width_follows_link_bandwidth():
    from buseval.gui.document import edge_flows

    topo = load_preset("tda4vh")
    flows = edge_flows(topo, evaluate(topo))
    assert flows[("CSI1", "ISP0")] == pytest.approx(82.944, rel=1e-3)
    assert flows[("CSI0", "NPU0")] > flows[("CSI1", "ISP0")]
    assert ("ISP0", "VENC0") in flows
    assert ("DISP0", "DSI0") in flows


def test_lint_scans_before_predict_and_resolves_inherited_size():
    from buseval.gui.document import node_captions
    from buseval.lint import lint
    from buseval.schema import Pipeline

    topo = load_preset("tda4vh")
    issues = lint(topo)
    assert not any(i.level == "error" for i in issues)
    assert not any(i.rule == "geometry-ignored" for i in issues)
    captions = node_captions(topo)
    assert "CSI1 via DDR" in captions["ISP0"]
    assert "1280" in captions["ISP0"]
    assert "ISP0 via DDR" in captions["DISP0"]
    assert "via DDR" not in captions["DSI0"]
    assert "CAN0" not in captions
    assert "USB" in captions["USB0"]

    bare = Topology(
        pipelines=[Pipeline(name="ISP0", type="isp")],
        ddr_channels=[_ddr(10000)],
    )
    missing = [i for i in lint(bare) if i.rule == "param-missing"]
    assert missing and missing[0].node == "ISP0"
    assert "width" in missing[0].message


def test_open_does_not_evaluate_and_save_requires_a_clean_check(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.app import MainWindow
    from buseval.schema import Pipeline

    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.open_preset("tda4vh")
    assert window.evaluated is False
    assert window.ddr_mode == "pending"
    assert window.last_result is None

    bad = tmp_path / "bad.yaml"
    window.topology = Topology(
        pipelines=[Pipeline(name="ISP0", type="isp")],
        ddr_channels=[_ddr(10000)],
    )
    window.path = bad
    window.at_home = False
    window.dirty = True
    window.save()
    assert not bad.exists()
    assert window.dirty is True

    good = tmp_path / "good.yaml"
    window.topology = new_topology()
    window.path = good
    window.save()
    assert good.exists()
    assert window.dirty is False


def test_cli_does_not_list_cpu_estimator(capsys):
    rc = cli_main(["list", "estimators"])
    assert rc == 0
    names = capsys.readouterr().out.split()
    assert "cpu" not in names
    assert "usb" in names


def test_soc_start_offers_blank_common_or_private():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.dialogs import KindDialog

    QApplication.instance() or QApplication([])
    dialog = KindDialog()
    assert dialog.chosen() == "soc"
    assert dialog.source() == "common"
    assert dialog.sources.isVisibleTo(dialog)
    assert [button.text() for button in dialog._source_buttons.values()] == ["空白画布", "常用芯片", "私有芯片"]
    dialog.names.setCurrentRow(1)
    assert dialog.chosen() == "can"
    assert not dialog.sources.isVisibleTo(dialog)
    dialog.close()


def test_home_has_no_ddr_card():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.app import MainWindow
    from buseval.gui.document import ddr_card_text

    QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.at_home
    assert "DDR0" not in window.canvas.nodes
    assert ddr_card_text(window.topology.ddr_channels[0]) == "未设置峰值，双击填写"
    window.close()


def test_venc_can_connect_to_eth():
    schema = load_param_schema()
    topo = new_topology()
    add_block(topo, "venc", 0, 0, schema)
    add_block(topo, "eth", 280, 0, schema)
    add_block(topo, "mipi_csi", 0, 120, schema)
    venc = next(p.name for p in topo.pipelines if p.type == "venc")
    eth = next(m.name for m in topo.masters if m.type == "eth")
    csi = next(m.name for m in topo.masters if m.type == "mipi_csi")
    assert try_connect(topo, venc, eth) is None
    assert next(m for m in topo.masters if m.name == eth).source == venc
    assert try_connect(topo, csi, eth) is not None


def test_ddr_node_wires_clients_and_skips_dsi():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from buseval.gui.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1100, 700)
    window.topology = load_preset("tda4vh")
    window.at_home = False
    window._show_topology(keep_positions=False)
    app.processEvents()
    assert window.canvas.nodes["DDR0"].kind == "ddr"
    card = window.canvas.nodes["DDR0"].caption.toPlainText()
    assert card == "35,840 MB/s"
    eth = window.canvas.nodes["ETH0"]
    assert eth.in_port is None and eth.out_port is None
    assert window.canvas.nodes["CSI0"].out_port is not None
    memory = [edge for edge in window.canvas.edges if edge.memory and edge.dst.node_name == "DDR0"]
    sources = {edge.src.node_name for edge in memory}
    assert {"ETH0", "USB0", "GPU0", "VDEC0"} <= sources
    assert not sources & {"CSI0", "CSI1", "ISP0", "VENC0", "DISP0", "DSI0", "NPU0"}
    assert all(edge.pen().dashPattern() for edge in memory)
    usb = next(edge for edge in memory if edge.src.node_name == "USB0")
    assert usb.pen().color().name().lower() == "#c0392b"
    assert usb.src_dot is not None and usb.dst_dot is not None
    assert usb.src_dot.parentItem() is window.canvas.nodes["USB0"]
    assert usb.dst_dot.parentItem() is window.canvas.nodes["DDR0"]
    opened = []
    window._open_ddr = lambda event=None: opened.append("ddr")
    window.edit_node("DDR0")
    assert opened == ["ddr"]
    window.delete_node("DDR0")
    assert any(ch.name == "DDR0" for ch in window.topology.ddr_channels)
    window.run_evaluate()
    app.processEvents()
    labeled = next(edge for edge in window.canvas.edges if edge.memory and edge.src.node_name == "USB0")
    assert labeled.tag.text()
    ddr = window.canvas.nodes["DDR0"]
    assert ddr.occupancy.text().endswith("%")
    assert ddr.occupancy.font().pointSize() > ddr.title.font().pointSize()
    center = ddr.occupancy.pos() + ddr.occupancy.boundingRect().center()
    assert center.x() > ddr.rect().width() * 0.5
    window.close()
