"""Lint: check a topology for missing categories, contradictions, invalid params."""
from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Topology


@dataclass
class LintIssue:
    level: str  # "error" | "warning"
    rule: str
    message: str


def lint(topology: Topology) -> list[LintIssue]:
    issues: list[LintIssue] = []

    enabled_masters = [m for m in topology.masters if m.enabled]
    enabled_pipelines = [p for p in topology.pipelines if p.enabled]
    master_types = {m.type for m in enabled_masters}
    pipeline_types = {p.type for p in enabled_pipelines}

    # No DDR
    if not topology.ddr_channels:
        issues.append(LintIssue("error", "no-ddr", "No DDR channels declared."))

    # No output sinks at all
    if not (pipeline_types & {"gpu", "display"} or master_types & {"mipi_dsi"}):
        issues.append(
            LintIssue(
                "warning",
                "no-output",
                "No GPU/Display/DSI declared — does this system really produce no output?",
            )
        )

    # No compute pipelines
    if not (pipeline_types & {"npu", "gpu"}):
        issues.append(
            LintIssue(
                "warning",
                "no-compute",
                "No NPU/GPU declared — verify this is intentional.",
            )
        )

    # CSI without ISP
    if "mipi_csi" in master_types and "isp" not in pipeline_types:
        issues.append(
            LintIssue(
                "warning",
                "csi-without-isp",
                "CSI input declared but no ISP pipeline — confirm raw passthrough is intended.",
            )
        )

    # NPU without weight source
    if "npu" in pipeline_types and not (
        master_types & {"flash", "spi"} or "isp" in pipeline_types
    ):
        issues.append(
            LintIssue(
                "warning",
                "npu-no-weights",
                "NPU declared but no FLASH/SPI/ISP source for weights detected.",
            )
        )

    # Param sanity
    for m in enabled_masters:
        params = m.params
        if m.type in ("can",):
            load = float(params.get("load_pct", 0))
            if not 0.0 <= load <= 1.0:
                issues.append(
                    LintIssue("error", "param-range", f"{m.name}: load_pct out of [0,1]: {load}")
                )
        if m.type.startswith("mipi"):
            lanes = params.get("lanes")
            if lanes is not None and lanes not in (1, 2, 3, 4):
                issues.append(
                    LintIssue("error", "param-range", f"{m.name}: lanes must be 1/2/3/4: {lanes}")
                )
        if m.type == "usb":
            v = str(params.get("version", ""))
            if v and v not in ("2", "3", "3.2"):
                issues.append(
                    LintIssue("error", "param-range", f"{m.name}: unknown USB version '{v}'")
                )

    return issues
