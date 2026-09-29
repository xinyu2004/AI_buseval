"""Shared operations for the CLI and the GUI.

The two front ends do not call each other. Both call this module, and both
read and write the same topology YAML.
"""
from __future__ import annotations

from pathlib import Path

from .dbc.health_report import HealthReport, build_health_report
from .engine.predictor import PredictionResult, predict
from .gmsl.calculator import GmslReport, build_report_from_links, calculate_link
from .lint import LintIssue, lint
from .loader import load_topology, save_topology
from .schema import Topology

PRESETS_DIR = Path(__file__).parent / "presets"


def list_presets() -> list[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def preset_path(name: str) -> Path:
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Unknown SoC preset '{name}'. Available: {', '.join(list_presets())}"
        )
    return path


def load_preset(name: str) -> Topology:
    return load_topology(preset_path(name))


def evaluate(topology: Topology) -> PredictionResult:
    """Predict bandwidth and compute DDR margins once."""
    result = predict(topology)
    _ = result.margins
    return result


def lint_topology(topology: Topology) -> list[LintIssue]:
    return lint(topology)


def can_health(
    dbc_path: str,
    bitrate_kbps: float | None = None,
    *,
    standard: str | None = None,
    arbitration_kbps: float | None = None,
    data_kbps: float | None = None,
) -> HealthReport:
    return build_health_report(
        dbc_path,
        bitrate_kbps=bitrate_kbps,
        standard=standard,
        arbitration_kbps=arbitration_kbps,
        data_kbps=data_kbps,
    )


def gmsl_from_params(params: dict, overrides: dict | None = None) -> GmslReport:
    spec = dict(params)
    name = spec.pop("name", "LINK1")
    links_spec = [{**spec, "name": name}]
    return build_report_from_links(links_spec, overrides or {})


def gmsl_link(
    name: str,
    width: int,
    height: int,
    fps: float,
    bpp: float,
    blanking: float | None = None,
) -> GmslReport:
    link = calculate_link(name, width, height, fps, bpp, blanking=blanking)
    report = GmslReport(links=[link])
    report.total_link_bw_mbps = link.link_bw_mbps
    return report


__all__ = [
    "PRESETS_DIR",
    "can_health",
    "evaluate",
    "gmsl_from_params",
    "gmsl_link",
    "lint_topology",
    "list_presets",
    "load_preset",
    "load_topology",
    "preset_path",
    "save_topology",
]
