"""Session, measurement compare, and canvas share colors."""
import json
from pathlib import Path

import pytest

from buseval.cli import build_parser, main as cli_main
from buseval.collect.perf_text import measurement_from_perf, panel_lines, parse_collect_text, parse_perf_stat
from buseval.collect.pmu import select_ddr_events
from buseval.collect.consent import confirm
from buseval.collect.runner import embedded_dir
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
         1,024,027      amd_umc/umc_cas_cmd.wr/
         5,602,648      amd_umc/umc_cas_cmd.rd/
       1.003544283 seconds time elapsed
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


def test_perf_text_becomes_ddr_measurement():
    parsed = parse_perf_stat(PERF_TEXT)
    body = measurement_from_perf(parsed)
    elapsed = 1.003544283
    assert body["kind"] == "ddr_bw"
    assert body["source"] == "ddr"
    assert body["items"][0]["name"] == "DDR"
    assert body["items"][0]["read_bw_mbps"] == pytest.approx(5_602_648 * 64 / elapsed / 1e6)
    assert body["items"][0]["write_bw_mbps"] == pytest.approx(1_024_027 * 64 / elapsed / 1e6)
    assert panel_lines(body)[2] == "DDR read  5602648 times"
    assert panel_lines(body)[5] == "DDR write  1024027 times"


def test_ddr_events_come_from_the_perf_list():
    amd = """
  amd_umc/umc_cas_cmd.rd/
  amd_umc/umc_cas_cmd.wr/
  amd_umc/umc_act_cmd.rd/
  amd_umc/umc_data_slot_clks.rd/
  cache-misses
  ls_any_fills_from_sys.dram_io_all
"""
    both = amd + """
  amd_df/local_or_remote_socket_read_data_beats_dram_0/
  amd_df/local_or_remote_socket_write_data_beats_dram_0/
  amd_iommu/page_tbl_read_tot/
"""
    assert select_ddr_events(both) == ("amd_umc/umc_cas_cmd.rd/", "amd_umc/umc_cas_cmd.wr/")
    channels = """
  amd_df/local_or_remote_socket_read_data_beats_dram_0/
  amd_df/local_or_remote_socket_read_data_beats_dram_1/
  amd_df/local_or_remote_socket_write_data_beats_dram_0/
  amd_df/local_or_remote_socket_upstream_read_data_beats_io_0/
  amd_df/local_or_remote_socket_upstream_write_data_beats_io_0/
  amd_df/local_or_remote_socket_write_data_beats_dram_1/
"""
    assert select_ddr_events(channels) == (
        "amd_df/local_or_remote_socket_read_data_beats_dram_0/",
        "amd_df/local_or_remote_socket_read_data_beats_dram_1/",
        "amd_df/local_or_remote_socket_write_data_beats_dram_0/",
        "amd_df/local_or_remote_socket_write_data_beats_dram_1/",
    )
    intel = """
  uncore_imc_0/data_read/
  uncore_imc_0/data_write/
  uncore_imc_0/cas_count_read/
  uncore_imc_0/cas_count_write/
  cpu/cache-misses/
"""
    assert select_ddr_events(intel) == ("uncore_imc/cas_count_read/", "uncore_imc/cas_count_write/")
    assert select_ddr_events("  cpu/cache-misses/\n") is None


def test_umc_cas_counts_are_memory_controller_bytes():
    text = """
         1,024,027      umc_cas_cmd.wr                   #     65.3 MB/s  umc_mem_write_bandwidth
     1,003,627,268      duration_time
         5,602,648      umc_cas_cmd.rd                   #    357.3 MB/s  umc_mem_read_bandwidth
           100,000      ls_any_fills_from_sys.dram_io_all
       1.003544283 seconds time elapsed
"""
    body = measurement_from_perf(parse_perf_stat(text), source="umc")
    elapsed = 1.003544283
    assert body["items"][0]["read_bw_mbps"] == pytest.approx(5_602_648 * 64 / elapsed / 1e6)
    assert body["items"][0]["write_bw_mbps"] == pytest.approx(1_024_027 * 64 / elapsed / 1e6)
    assert "duration_time" not in body["items"][0]["raw"]
    shown = panel_lines(body)
    assert shown == [
        "count window  [magenta]1.003544283 s[/magenta]",
        "",
        "DDR read  5602648 times",
        f"5602648 × 64 B / [magenta]1.003544283 s[/magenta] = {body['items'][0]['read_bw_mbps']:.4f} MB/s",
        "",
        "DDR write  1024027 times",
        f"1024027 × 64 B / [magenta]1.003544283 s[/magenta] = {body['items'][0]['write_bw_mbps']:.4f} MB/s",
    ]


def test_count_notice_is_asked_once(tmp_path, monkeypatch, capsys):
    from buseval.collect import consent

    monkeypatch.setattr(consent, "consent_dir", lambda: tmp_path)
    monkeypatch.setattr(consent.sys.stdin, "isatty", lambda: True)
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert confirm("notice", "perf-stat") is True
    assert confirm("notice", "perf-stat") is True
    assert capsys.readouterr().err.count("notice") == 1


def test_notice_appears_only_when_sudo_will_ask_for_a_password(tmp_path, monkeypatch):
    import subprocess

    from buseval.collect import privilege

    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(privilege.shutil, "which", lambda _name: "/usr/bin/sudo")
    asked: list[str] = []

    def fake_confirm(text: str, name: str) -> bool:
        asked.append(name)
        return True

    monkeypatch.setattr(privilege, "confirm", fake_confirm)
    monkeypatch.setattr(
        privilege.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(privilege, "password_required", lambda: False)
    privilege.run_privileged(["perf", "stat"], notice="notice", consent_name="perf-stat")
    assert asked == []

    monkeypatch.setattr(privilege, "password_required", lambda: True)
    privilege.run_privileged(["perf", "stat"], notice="notice", consent_name="perf-stat")
    assert asked == ["perf-stat"]


def test_tab_completion_lists_commands_and_collect_flags(tmp_path):
    import subprocess

    from buseval.complete import install_shell_completion

    install_shell_completion()
    script = tmp_path / "bash-completion" / "buseval"
    text = script.read_text(encoding="utf-8")
    assert "--probe" in text
    assert "--from" in text
    assert "--pattern" not in text
    assert "--bytes" not in text
    assert "--threads" not in text
    first = script.stat().st_mtime_ns
    install_shell_completion()
    assert script.stat().st_mtime_ns == first

    def complete(line: str) -> list[str]:
        words = line.split()
        if line.endswith(" "):
            words.append("")
        index = len(words) - 1
        array = " ".join(f'"{word}"' for word in words)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", f'''
                source "{script}"
                COMP_WORDS=({array})
                COMP_CWORD={index}
                COMP_LINE={line!r}
                COMP_POINT=${{#COMP_LINE}}
                _buseval
                printf "%s\\n" "${{COMPREPLY[@]}}"
            '''],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.split()

    commands = complete("buseval ")
    assert "collect" in commands
    assert "predict" in commands
    flags = complete("buseval collect --")
    assert "--probe" in flags
    assert "--from" in flags
    assert "--pattern" not in flags
    assert "estimators" in complete("buseval list ")
    assert "tda4vh" in complete("buseval predict --soc ")


def test_completion_dir_follows_bash_lookup(tmp_path, monkeypatch):
    import sys

    from buseval.complete import command_name, completion_dir, install_shell_completion

    monkeypatch.delenv("BUSEVAL_COMPLETION_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    user = tmp_path / "custom"
    monkeypatch.setenv("BASH_COMPLETION_USER_DIR", f"{user}:/ignored")
    assert completion_dir() == user / "completions"
    install_shell_completion()
    assert (user / "completions" / "buseval").is_file()

    monkeypatch.delenv("BASH_COMPLETION_USER_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert completion_dir() == tmp_path / "xdg" / "bash-completion" / "completions"

    monkeypatch.setattr(sys, "argv", ["/opt/bin/buseval-tool"])
    monkeypatch.setenv("BUSEVAL_COMPLETION_DIR", str(tmp_path / "named"))
    assert command_name() == "buseval-tool"
    install_shell_completion()
    text = (tmp_path / "named" / "buseval-tool").read_text(encoding="utf-8")
    assert "complete -F _buseval_tool buseval-tool" in text


def test_report_text_keeps_the_perf_time(tmp_path, capsys):
    elapsed = "1.003544283"
    read_bw = round(5_602_648 * 64 / float(elapsed) / 1e6, 4)
    write_bw = round(1_024_027 * 64 / float(elapsed) / 1e6, 4)
    report = "\n".join([
        f"count window  {elapsed} s",
        "",
        "DDR read  5602648 times",
        f"5602648 × 64 B / {elapsed} s = {read_bw:.4f} MB/s",
        "",
        "DDR write  1024027 times",
        f"1024027 × 64 B / {elapsed} s = {write_bw:.4f} MB/s",
        "",
    ])
    raw = tmp_path / "report.txt"
    raw.write_text(report, encoding="utf-8")
    body = measurement_from_perf(parse_collect_text(report))
    assert body["duration_text"] == elapsed
    assert body["items"][0]["read_bw_mbps"] == pytest.approx(read_bw)
    assert panel_lines(body)[0] == f"count window  [magenta]{elapsed} s[/magenta]"
    rc = cli_main(["collect", "--from", str(raw), "--no-color"])
    assert rc == 0
    assert f"count window  {elapsed} s" in capsys.readouterr().out


def test_pc_pmu_prints_the_same_lines(tmp_path):
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/bin/sh\nif [ \"$1\" = \"-n\" ]; then exit 0; fi\nexec \"$@\"\n", encoding="utf-8")
    perf = bin_dir / "perf"
    perf.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"list\" ]; then\n"
        "  printf '%s\\n' '  amd_umc/umc_cas_cmd.rd/' '  amd_umc/umc_cas_cmd.wr/'\n"
        "  exit 0\n"
        "fi\n"
        "cat >&2 <<'EOF'\n"
        "         5,602,648      amd_umc/umc_cas_cmd.rd/\n"
        "         1,024,027      amd_umc/umc_cas_cmd.wr/\n"
        "       1.003544283 seconds time elapsed\n"
        "EOF\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    perf.chmod(0o755)
    out = tmp_path / "report.txt"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        ["sh", str(embedded_dir() / "pc_pmu.sh"), "-o", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    elapsed = "1.003544283"
    read_bw = round(5_602_648 * 64 / float(elapsed) / 1e6, 4)
    write_bw = round(1_024_027 * 64 / float(elapsed) / 1e6, 4)
    plain = "\n".join([
        f"count window  {elapsed} s",
        "",
        "DDR read  5602648 times",
        f"5602648 × 64 B / {elapsed} s = {read_bw:.4f} MB/s",
        "",
        "DDR write  1024027 times",
        f"1024027 × 64 B / {elapsed} s = {write_bw:.4f} MB/s",
    ])
    assert proc.stdout.strip() == plain
    assert out.read_text(encoding="utf-8").strip() == plain
    assert "\033[35m" not in out.read_text(encoding="utf-8")


def test_pc_pmu_probe_matches_collect(capsys):
    import shutil
    import subprocess

    if shutil.which("perf") is None:
        pytest.skip("perf is not installed")
    proc = subprocess.run(["sh", str(embedded_dir() / "pc_pmu.sh"), "--probe"], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.skip(proc.stderr.strip())
    rc = cli_main(["collect", "--probe"])
    assert rc == 0
    printed = capsys.readouterr().out
    shell_events = next(line for line in proc.stdout.splitlines() if line.startswith("events="))
    cli_events = next(line for line in printed.splitlines() if line.startswith("events="))
    assert shell_events == cli_events


def test_embedded_script_does_not_call_buseval():
    script = (embedded_dir() / "pc_pmu.sh").read_text(encoding="utf-8")
    assert "python3 -m buseval" not in script
    assert "python3 -c" not in script
    assert "perf list" in script
    assert "amd_uncore" in script
    assert "cas_count_read" in script
    assert "sudo -n true" in script
    assert "cache-misses" not in script


def test_collect_prints_a_panel_without_a_required_file(tmp_path, capsys):
    raw = tmp_path / "perf.txt"
    raw.write_text(PERF_TEXT, encoding="utf-8")
    out = tmp_path / "meas.json"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["collect", "--platform", "pc"])
    rc = cli_main(["collect", "--from", str(raw), "-o", str(out), "--no-color"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "buseval collect" in printed
    assert "DDR read" in printed
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["source"] == "ddr"
    assert saved["items"][0]["read_bw_mbps"] == pytest.approx(5_602_648 * 64 / 1.003544283 / 1e6)


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
