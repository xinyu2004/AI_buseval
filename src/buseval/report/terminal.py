"""Terminal report using rich tables."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from ..engine.predictor import PredictionResult
from ..engine.margin import ChannelMargin, evaluate_margin
from ..dbc.health_report import HealthReport


_VERDICT_COLOR = {"OK": "green", "WARN": "yellow", "CRITICAL": "red"}

# Color cycle for multi-source "from A+B+C" highlighting.
_SOURCE_COLORS = ["bold cyan", "bold magenta", "bold yellow", "bold green", "bold blue"]


def _build_source_color_map(items) -> dict:
    """Build a {master_name: style} map by scanning all pipeline items' source_names
    in order of first appearance. Ensures a source master has the SAME color everywhere
    — both in its own NAME cell and in any pipeline's 'from X+Y' reference."""
    color_map = {}
    idx = 0
    for it in items:
        bd = it.breakdown if isinstance(it.breakdown, dict) else {}
        names = bd.get("source_names") or []
        for n in names:
            if n and n not in color_map:
                color_map[n] = _SOURCE_COLORS[idx % len(_SOURCE_COLORS)]
                idx += 1
    return color_map


def _colorize_source(text: str, use_color: bool, color_map: dict | None = None):
    """Colorize the 'from <source>' suffix in a dominant_factor string so the
    pipeline wiring stands out. Multi-source 'from A+B+C' gives each source a
    different color. If color_map is provided, colors are consistent with the
    NAME column (a source master keeps the same color everywhere)."""
    if not use_color or " from " not in text:
        return text
    color_map = color_map or {}
    idx = text.find(" from ")
    head = text[:idx]
    names_part = text[idx + len(" from "):]  # "A+B+C"
    names = names_part.split("+")
    out = Text(head)
    out.append(" from ")
    for j, n in enumerate(names):
        if j > 0:
            out.append("+")
        style = color_map.get(n) or _SOURCE_COLORS[j % len(_SOURCE_COLORS)]
        out.append(n, style=style)
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
    margins = evaluate_margin(prediction)

    # DDR margin table
    t = Table(title="DDR Bandwidth Report", show_lines=False)
    t.add_column("Channel")
    t.add_column("Peak MB/s", justify="right")
    t.add_column("Avail MB/s", justify="right")
    t.add_column("R-demand", justify="right")
    t.add_column("R-util", justify="right")
    t.add_column("W-demand", justify="right")
    t.add_column("W-util", justify="right")
    t.add_column("R/W")
    t.add_column("Verdict")

    for m in margins:
        color = _VERDICT_COLOR.get(m.verdict, "white")
        t.add_row(
            m.name,
            f"{m.peak_mbps:,.0f}",
            f"{m.available_mbps:,.0f}",
            f"{m.read_demand_mbps:,.0f}",
            f"{m.read_util*100:.1f}%",
            f"{m.write_demand_mbps:,.0f}",
            f"{m.write_util*100:.1f}%",
            f"{m.rw_imbalance*100:.0f}{'*' if m.rw_imbalance_flag else ''}",
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

    # Assumptions
    assumptions = prediction.assumptions
    if assumptions:
        ta = Table(title="Assumptions (verify before trusting)")
        ta.add_column("Item")
        ta.add_column("Message")
        for a in assumptions:
            ta.add_row(a["item"], a["message"])
        console.print(ta)
    else:
        console.print("[green]No flagged assumptions.[/green]")

    return ""


def render_health_terminal(report: HealthReport, console: Console | None = None, use_color: bool = True) -> str:
    console = console or Console(no_color=not use_color, highlight=False)
    for b in report.buses:
        color = _VERDICT_COLOR.get(b.verdict, "white")
        title = Text(f"{b.name}  bitrate {b.bitrate_kbps:.0f}kbps  load {b.load_pct*100:.1f}%  {b.verdict}", style=color)
        lines = [
            f"Total load: {b.total_kbps:.2f} kbps ({b.load_pct*100:.1f}%)",
            f"Worst-case latency: {b.worst_case_latency_ms:.2f} ms",
        ]
        if b.suggestions:
            lines.append("Suggestions:")
            for s in b.suggestions:
                lines.append(f"  - {s}")
        lines.append("Top messages:")
        for m in b.top_messages:
            lines.append(
                f"  {m['name']:<28} {m['id']:<8} DLC={m['dlc']:<3} "
                f"{m['cycle_ms']:.0f}ms  {m['bps']:.0f}bps  {m['share_pct']:.1f}%"
            )
        console.print(Panel("\n".join(lines), title=title, border_style=color))
    return ""
