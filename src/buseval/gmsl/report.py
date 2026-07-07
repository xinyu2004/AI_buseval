"""GMSL terminal + structured report rendering."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .calculator import GmslReport, GmslLinkResult


def render_gmsl_terminal(report: GmslReport, console: Console | None = None, use_color: bool = True) -> None:
    console = console or Console(no_color=not use_color, highlight=False)

    for link in report.links:
        _render_link(link, console, use_color)

    # summary table
    if len(report.links) > 1:
        _render_summary(report, console, use_color)


def _render_link(link: GmslLinkResult, console: Console, use_color: bool) -> None:
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

    title_style = "bold" if use_color else ""
    console.print(Panel("\n".join(lines), title=f"GMSL Link: {link.name}", border_style="cyan"))


def _render_summary(report: GmslReport, console: Console, use_color: bool) -> None:
    t = Table(title="GMSL Summary (all links)", show_lines=False)
    t.add_column("Link")
    t.add_column("Resolution")
    t.add_column("FPS")
    t.add_column("Link BW (Mbps)", justify="right")
    t.add_column("Link BW (Gbps)", justify="right")
    t.add_column("Best fit")

    for link in report.links:
        t.add_row(
            link.name,
            f"{link.width}×{link.height}",
            f"{link.fps}",
            f"{link.link_bw_mbps:,.1f}",
            f"{link.link_bw_mbps/1000:.2f}",
            link.best_fit.upper() if link.best_fit else "NONE",
        )
    t.add_row(
        "TOTAL",
        "",
        "",
        f"{report.total_link_bw_mbps:,.1f}",
        f"{report.total_link_bw_mbps/1000:.2f}",
        "",
    )
    console.print(t)

    # tier summary
    coeffs_summary = report.to_dict().get("summary", {})
    tier_sum = coeffs_summary.get("tier_summary", {})
    if tier_sum:
        tt = Table(title="Tier Summary (per-link, worst case)", show_lines=False)
        tt.add_column("Tier")
        tt.add_column("Capacity (Gbps)", justify="right")
        tt.add_column("Worst link util", justify="right")
        tt.add_column("Fits all?")
        for name, info in tier_sum.items():
            fits = info["fits_all"]
            mark = "✓" if fits else "✗"
            style = "green" if fits else "red"
            if use_color:
                tt.add_row(
                    name.upper(),
                    f"{info['capacity_mbps']/1000:.1f}",
                    f"{info['max_link_util']*100:.1f}%",
                    Text(mark, style=style),
                )
            else:
                tt.add_row(
                    name.upper(),
                    f"{info['capacity_mbps']/1000:.1f}",
                    f"{info['max_link_util']*100:.1f}%",
                    mark,
                )
        console.print(tt)


def build_gmsl_structured(report: GmslReport) -> dict:
    return report.to_dict()
