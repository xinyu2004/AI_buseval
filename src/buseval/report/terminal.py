"""Terminal report using rich tables."""
from __future__ import annotations

import os

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from ..engine.predictor import PredictionResult
from ..dbc.health_report import HealthReport


_VERDICT_COLOR = {"OK": "green", "WARN": "yellow", "CRITICAL": "red"}


def _share_style(ratio: float, yellow: float, red: float) -> str:
    """Same lines as the DDR card: under yellow is green, then yellow, then red."""
    if ratio >= red:
        return "red"
    if ratio >= yellow:
        return "yellow"
    return "green"

# Assumption level → (tag, color) for the Lv column.
_LEVEL_TAG = {
    "red": ("RED", "bold red"),
    "yellow": ("YEL", "bold yellow"),
    "info": ("i", "dim cyan"),
}

# Color cycle for multi-source "from A+B+C" highlighting.
_SOURCE_COLORS = ["bold cyan", "bold magenta", "bold yellow", "bold green", "bold blue"]


def _build_source_color_map(items) -> dict:
    """Build a {name: style} map by scanning all items' source references
    (both `source_names` lists and single `source` fields) in order of first
    appearance. Ensures any name referenced as a source keeps the SAME color
    everywhere — both in its own NAME cell and in any pipeline's 'from X+Y'."""
    color_map = {}
    idx = 0
    for it in items:
        bd = it.breakdown if isinstance(it.breakdown, dict) else {}
        names = list(bd.get("source_names") or [])
        single = bd.get("source")
        if single and single not in names:
            names.append(single)
        for n in names:
            if n and n not in color_map:
                color_map[n] = _SOURCE_COLORS[idx % len(_SOURCE_COLORS)]
                idx += 1
    return color_map


def _colorize_source(text: str, use_color: bool, color_map: dict | None = None):
    """Colorize the 'from <source>' suffix in a dominant_factor string so the
    pipeline wiring stands out. Handles both '... from X+Y' and '...(from X+Y)'
    forms. Multi-source gives each source a different color. If color_map is
    provided, colors are consistent with the NAME column."""
    if not use_color:
        return text
    color_map = color_map or {}
    # Find the last occurrence of "from " — works for both " from X" and "(from X"
    idx = text.rfind("from ")
    if idx < 0:
        return text
    head = text[:idx]
    # Preserve any separator before "from" (space or "(", etc.)
    # Extract names part: everything after "from "
    names_part = text[idx + len("from "):]
    # Trim a trailing ")" if present (from the "(from X)" form)
    trailing = ""
    if names_part.endswith(")"):
        names_part = names_part[:-1]
        trailing = ")"
    names = names_part.split("+")
    out = Text(head)
    out.append("from ")
    for j, n in enumerate(names):
        if j > 0:
            out.append("+")
        style = color_map.get(n) or _SOURCE_COLORS[j % len(_SOURCE_COLORS)]
        out.append(n, style=style)
    if trailing:
        out.append(trailing)
    return out


def _name_cell(name: str, use_color: bool, color_map: dict):
    """Render the NAME cell; if this item is a source master referenced by some
    pipeline, color it to match its 'from X' appearance."""
    if not use_color:
        return name
    style = color_map.get(name)
    return Text(name, style=style) if style else name


def render_terminal(prediction: PredictionResult, console: Console | None = None, use_color: bool = True) -> str:
    console = console or Console(no_color=not use_color, highlight=False)
    margins = prediction.margins

    # DDR detail panel (controller vs module vs effective)
    for m in margins:
        if m.bottleneck != "n/a":
            ctrl_gb = m.controller_peak_mbps / 1000
            mod_gb = m.module_peak_mbps / 1000
            eff_gb = m.effective_peak_mbps / 1000
            avail_gb = m.available_mbps / 1000
            ctrl_formula = f"{m.controller_mt_s:.0f} × {m.controller_width_bits} / 8"
            if m.controller_groups > 1:
                ctrl_formula += f" × {m.controller_groups}"
            mod_formula = f"{m.module_mt_s:.0f} × {m.module_width_bits} / 8"
            if m.module_groups > 1:
                mod_formula += f" × {m.module_groups}"
            lines = [
                "  [Chip] DDR Controller (SoC internal, fixed)",
                f"    Speed:           {m.controller_mt_s:.0f} MT/s",
                f"    Bus width:       {m.controller_width_bits} bit" + (f" × {m.controller_groups} groups" if m.controller_groups > 1 else ""),
                f"    Type:            {m.controller_type or 'n/a'}",
                f"    Peak bandwidth:  {ctrl_formula} = {m.controller_peak_mbps:,.0f} MB/s ({ctrl_gb:.1f} GB/s)",
                "",
                "  [Onboard] DRAM Module (external, board design choice)",
                f"    Speed:           {m.module_mt_s:.0f} MT/s",
                f"    Bus width:       {m.module_width_bits} bit" + (f" × {m.module_groups} groups" if m.module_groups > 1 else ""),
                f"    Type:            {m.module_type or 'n/a'}",
                f"    Peak bandwidth:  {mod_formula} = {m.module_peak_mbps:,.0f} MB/s ({mod_gb:.1f} GB/s)",
                "",
                "  [Effective]",
                f"    Effective peak:  min({m.controller_peak_mbps:,.0f}, {m.module_peak_mbps:,.0f}) = {m.effective_peak_mbps:,.0f} MB/s ({eff_gb:.1f} GB/s)",
                f"    Bottleneck:      {m.bottleneck}",
                f"    Efficiency:      {m.efficiency}",
                f"    Available:       {m.effective_peak_mbps:,.0f} × {m.efficiency} = {m.available_mbps:,.0f} MB/s ({avail_gb:.1f} GB/s)",
            ]
            console.print(Panel("\n".join(lines), title=f"{m.name} — DDR Configuration",
                                border_style="blue"))

    # DDR margin table
    t = Table(title="DDR Bandwidth Report", show_lines=False)
    t.add_column("Channel")
    t.add_column("Eff Peak", justify="right")
    t.add_column("Avail MB/s", justify="right")
    t.add_column("R-demand", justify="right")
    t.add_column("R-util", justify="right")
    t.add_column("W-demand", justify="right")
    t.add_column("W-util", justify="right")
    t.add_column("Occupancy", justify="right")
    t.add_column("Verdict")

    thresholds = prediction.topology.alert_thresholds
    yellow = float(thresholds.get("yellow", 0.6))
    red = float(thresholds.get("red", 0.8))

    def _pct(ratio: float):
        text = f"{ratio * 100:.1f}%"
        if not use_color:
            return text
        return Text(text, style=_share_style(ratio, yellow, red))

    for m in margins:
        color = _VERDICT_COLOR.get(m.verdict, "white")
        t.add_row(
            m.name,
            f"{m.effective_peak_mbps:,.0f}",
            f"{m.available_mbps:,.0f}",
            f"{m.read_demand_mbps:,.0f}",
            _pct(m.read_util),
            f"{m.write_demand_mbps:,.0f}",
            _pct(m.write_util),
            _pct(m.occupancy),
            Text(m.verdict, style=color),
        )
    console.print(t)

    # Top contributors
    items_sorted = sorted(
        prediction.items, key=lambda i: i.read_bw_mbps + i.write_bw_mbps, reverse=True
    )
    total = prediction.total_read_mbps + prediction.total_write_mbps
    # Build a consistent source→color map so a source master keeps the same color
    # in its NAME cell and in any pipeline's 'from X+Y' reference.
    source_color_map = _build_source_color_map(prediction.items)
    tt = Table(title="Top Contributors (read + write)")
    tt.add_column("#")
    tt.add_column("Name")
    tt.add_column("Type")
    tt.add_column("Read MB/s", justify="right")
    tt.add_column("Write MB/s", justify="right")
    tt.add_column("Total", justify="right")
    tt.add_column("Share", justify="right")
    tt.add_column("Dominant factor")

    for i, it in enumerate(items_sorted[:15], 1):
        s = it.read_bw_mbps + it.write_bw_mbps
        share = (s / total * 100) if total else 0.0
        tt.add_row(
            str(i),
            _name_cell(it.name, use_color, source_color_map),
            it.type,
            f"{it.read_bw_mbps:,.2f}",
            f"{it.write_bw_mbps:,.2f}",
            f"{s:,.2f}",
            f"{share:.1f}%",
            _colorize_source(it.dominant_factor, use_color, source_color_map),
        )
    console.print(tt)

    # Assumptions (with RED/YEL/INFO level coloring)
    assumptions = prediction.assumptions
    if assumptions:
        ta = Table(title="Assumptions (verify before trusting)")
        ta.add_column("Lv", justify="center", width=3)
        ta.add_column("Item")
        ta.add_column("Message")
        for a in assumptions:
            lvl = a.get("level", "yellow")
            tag, color = _LEVEL_TAG.get(lvl, ("?", "white"))
            if use_color:
                ta.add_row(Text(tag, style=color), a["item"], a["message"])
            else:
                ta.add_row(tag, a["item"], a["message"])
        console.print(ta)

    return ""


def _use_ui_language() -> None:
    from PySide6.QtCore import QSettings

    from ..gui.i18n import set_lang

    override = os.environ.get("BUSEVAL_UI_SETTINGS")
    if override:
        settings = QSettings(override, QSettings.Format.IniFormat)
    else:
        settings = QSettings("buseval", "buseval")
    value = str(settings.value("language", "zh") or "zh")
    set_lang(value if value in ("zh", "en") else "zh")


def _colored_ends(line: str, style: str) -> Text:
    left, rest = line.split(" = ", 1)
    middle, right = rest.rsplit(" = ", 1)
    text = Text()
    text.append(f"{left} =", style=style)
    text.append(f" {middle} ")
    text.append(f"= {right}", style=style)
    return text


def _health_lines(bus, color: str, use_color: bool) -> list:
    from ..dbc.health_report import latency_text
    from ..gui.i18n import t

    if bus.illegal_count:
        key = "can_fit_can" if bus.standard == "can" else "can_fit_fd"
        sentence = t(key).format(n=bus.illegal_count)
        return [Text(sentence, style=color if use_color else "")]
    headline = t("can_result").format(
        load=f"{bus.load_pct * 100:.3f}",
        latency=f"{bus.worst_case_latency_ms:.2f}",
    )
    longest = max((int(msg["dlc"]) for msg in bus.top_messages), default=0)
    steps = latency_text(longest, bus.data_kbps, bus.load_pct, bus.total_kbps, standard=bus.standard)
    chunks = [Text(headline, style=color if use_color else ""), Text("")]
    for index, line in enumerate(steps):
        if use_color and index in (2, 4):
            chunks.append(_colored_ends(line, color))
        else:
            chunks.append(Text(line))
    return chunks


def render_health_terminal(report: HealthReport, console: Console | None = None, use_color: bool = True) -> str:
    console = console or Console(no_color=not use_color, highlight=False)
    _use_ui_language()
    for bus in report.buses:
        color = _VERDICT_COLOR.get(bus.verdict, "white")
        title = Text(
            f"{bus.name}  bitrate {bus.bitrate_kbps:.0f}kbps  load {bus.load_pct*100:.1f}%  {bus.verdict}",
            style=color if use_color else "",
        )
        chunks = _health_lines(bus, color, use_color)
        chunks.append(Text(""))
        chunks.append(Text("Top messages:"))
        for msg in bus.top_messages:
            chunks.append(Text(
                f"  {msg['name']:<28} {msg['id']:<8} DLC={msg['dlc']:<3} "
                f"{msg['cycle_ms']:.0f}ms  {msg['bps']:.0f}bps  {msg['share_pct']:.1f}%"
            ))
        console.print(Panel(Group(*chunks), title=title, border_style=color))
    return ""
