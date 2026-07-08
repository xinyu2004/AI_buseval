"""GMSL terminal + structured report rendering."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .calculator import GmslReport, GmslLinkResult

# Color cycle for per-link identification (matches Summary table Link column).
_LINK_COLORS = ["bold cyan", "bold magenta", "bold yellow", "bold green", "bold blue"]
# Border styles (without "bold" prefix, for Panel border_style).
_LINK_BORDER = ["cyan", "magenta", "yellow", "green", "blue"]


def render_gmsl_terminal(report: GmslReport, console: Console | None = None, use_color: bool = True) -> None:
    console = console or Console(no_color=not use_color, highlight=False)

    for idx, link in enumerate(report.links):
        _render_link(link, console, use_color, idx)

    # summary table
    if len(report.links) > 1:
        _render_summary(report, console, use_color)


def _render_link(link: GmslLinkResult, console: Console, use_color: bool, idx: int) -> None:
    color = _LINK_COLORS[idx % len(_LINK_COLORS)]
    border = _LINK_BORDER[idx % len(_LINK_BORDER)]
    lines = [
        f"  Resolution:  {link.width} × {link.height}",
        f"  FPS:         {link.fps}",
        f"  BPP:         {link.bpp}",
        f"  Blanking:    {link.blanking} ({(link.blanking-1)*100:.0f}% over)",
        "",
        "  Breakdown:",
        f"    Pixel rate:       {link.width}×{link.height}×{link.fps}×{link.bpp} = {link.pixel_rate_mbps:,.1f} Mbps",
        f"    + blanking:       ×{link.blanking} = {link.after_blanking_mbps:,.1f} Mbps",
        f"    + encoding {link.encoding_factor}: ×{link.encoding_factor} = {link.after_encoding_mbps:,.1f} Mbps",
        f"    + overhead {link.overhead_factor}:×{link.overhead_factor} = {link.link_bw_mbps:,.1f} Mbps",
        "",
        "  Result:",
        f"    GMSL link BW:    {link.link_bw_mbps:,.1f} Mbps ({link.link_bw_mbps/1000:.2f} Gbps)",
        "",
        "  Recommendation:",
    ]
    for rec in link.recommendations:
        cap = rec["capacity_mbps"]
        util = rec["util"] * 100
        fits = rec["fits"]
        mark = "✓" if fits else "✗"
        style = "green" if fits else "red"
        tag = f"{rec['tier'].upper()} ({cap/1000:.1f} Gbps)"
        if use_color:
            lines.append(f"    {Text(tag, style=style).plain}:  {mark} fits ({util:.1f}% util)")
        else:
            lines.append(f"    {tag}:  {mark} fits ({util:.1f}% util)")
    if link.best_fit:
        lines.append(f"    Best fit: {link.best_fit.upper()}")
    else:
        lines.append(f"    Best fit: NONE (exceeds all tiers)")

    title_text = Text(f"GMSL Link: {link.name}", style=color)
    console.print(Panel("\n".join(lines), title=title_text, border_style=border))


def _render_summary(report: GmslReport, console: Console, use_color: bool) -> None:
    t = Table(title="GMSL Summary (all links)", show_lines=False)
    t.add_column("Link")
    t.add_column("Resolution")
    t.add_column("FPS")
    t.add_column("Link BW (Mbps)", justify="right")
    t.add_column("Link BW (Gbps)", justify="right")
    t.add_column("Best fit")

    for idx, link in enumerate(report.links):
        name_cell = Text(link.name, style=_LINK_COLORS[idx % len(_LINK_COLORS)]) if use_color else link.name
        t.add_row(
            name_cell,
            f"{link.width}×{link.height}",
            f"{link.fps}",
            f"{link.link_bw_mbps:,.1f}",
            f"{link.link_bw_mbps/1000:.2f}",
            "",
        )
    t.add_row(
        "TOTAL",
        "",
        "",
        f"{report.total_link_bw_mbps:,.1f}",
        f"{report.total_link_bw_mbps/1000:.2f}",
        report.to_dict().get("summary", {}).get("aggregate_best_fit", "").upper() or "NONE",
    )
    console.print(t)

    # tier summary (aggregate: does the TOTAL fit?)
    coeffs_summary = report.to_dict().get("summary", {})
    tier_sum = coeffs_summary.get("tier_summary", {})
    if tier_sum:
        tt = Table(title="Tier Summary (aggregate bandwidth vs tier)", show_lines=False)
        tt.add_column("Tier")
        tt.add_column("Capacity (Gbps)", justify="right")
        tt.add_column("Total BW (Mbps)", justify="right")
        tt.add_column("Aggregate util", justify="right")
        tt.add_column("Fits aggregate?")
        for name, info in tier_sum.items():
            fits = info["fits_aggregate"]
            mark = "✓" if fits else "✗"
            style = "green" if fits else "red"
            total_bw = report.total_link_bw_mbps
            if use_color:
                tt.add_row(
                    name.upper(),
                    f"{info['capacity_mbps']/1000:.1f}",
                    f"{total_bw:,.1f}",
                    f"{info['total_util']*100:.1f}%",
                    Text(mark, style=style),
                )
            else:
                tt.add_row(
                    name.upper(),
                    f"{info['capacity_mbps']/1000:.1f}",
                    f"{total_bw:,.1f}",
                    f"{info['total_util']*100:.1f}%",
                    mark,
                )
        console.print(tt)


def build_gmsl_structured(report: GmslReport) -> dict:
    return report.to_dict()
