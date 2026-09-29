"""GMSL terminal report. The wording matches the dialog, inside one panel."""
from __future__ import annotations

import os

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from .calculator import GmslReport
from .text import bandwidth_line, link_steps, multi_steps, tier_lines
from ..gui.i18n import set_lang, t


def _use_ui_language() -> None:
    from PySide6.QtCore import QSettings

    override = os.environ.get("BUSEVAL_UI_SETTINGS")
    if override:
        settings = QSettings(override, QSettings.Format.IniFormat)
    else:
        settings = QSettings("buseval", "buseval")
    value = str(settings.value("language", "zh") or "zh")
    set_lang(value if value in ("zh", "en") else "zh")


def _occupancy_style(util: float) -> str:
    if util >= 0.8:
        return "red"
    if util >= 0.6:
        return "yellow"
    return "green"


def _report_body(report: GmslReport, use_color: bool) -> Group:
    links = report.links
    if len(links) == 1:
        steps = link_steps(links[0])
        coax = links[0].link_bw_mbps
    else:
        steps = multi_steps(links)
        coax = sum(link.link_bw_mbps for link in links)
    chunks: list = [Text(steps), Text(""), Text(f"{t('gmsl_link_bw')}  {bandwidth_line(coax)}"), Text("")]
    for line, util in tier_lines(links):
        style = _occupancy_style(util) if use_color else ""
        chunks.append(Text(line, style=style))
    return Group(*chunks)


def render_gmsl_terminal(report: GmslReport, console: Console | None = None, use_color: bool = True) -> None:
    console = console or Console(no_color=not use_color, highlight=False)
    _use_ui_language()
    links = report.links
    if not links:
        return
    title = f"GMSL Link: {links[0].name}" if len(links) == 1 else "GMSL"
    console.print(Panel(
        _report_body(report, use_color),
        title=title,
        border_style="cyan",
    ))


def build_gmsl_structured(report: GmslReport) -> dict:
    return report.to_dict()
