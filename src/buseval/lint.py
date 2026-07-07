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
    master_names = {m.name for m in topology.masters}
    pipeline_names = {p.name for p in topology.pipelines}

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

    # Pipeline `source` validation (source may be str or list[str])
    master_by_name = {m.name: m for m in topology.masters}
    for p in enabled_pipelines:
        # Normalize source to a list
        if p.source is None:
            src_list = []
        elif isinstance(p.source, str):
            src_list = [p.source]
        else:
            src_list = list(p.source)

        if not src_list:
            continue

        # ISP does not support multi-source
        if p.type == "isp" and len(src_list) > 1:
            issues.append(
                LintIssue(
                    "error",
                    "isp-multi-source",
                    f"pipeline '{p.name}': ISP does not support multi-source (got {src_list}).",
                )
            )
            continue

        for sname in src_list:
            if sname in pipeline_names:
                issues.append(
                    LintIssue(
                        "error",
                        "source-pipeline",
                        f"pipeline '{p.name}': source '{sname}' references another pipeline; "
                        f"pipeline-to-pipeline chaining is not supported yet.",
                    )
                )
                continue
            if sname not in master_names:
                issues.append(
                    LintIssue(
                        "error",
                        "source-not-found",
                        f"pipeline '{p.name}': source '{sname}' not found among masters.",
                    )
                )
                continue
            if p.type == "isp" and ("width" in p.params or "height" in p.params or "fps" in p.params):
                issues.append(
                    LintIssue(
                        "warning",
                        "source-override",
                        f"pipeline '{p.name}': source='{sname}' provides input; "
                        f"params.width/height/fps will be ignored.",
                    )
                )
            # NPU: warn per-source if inference_fps < source fps (async, not capped,
            # but worth flagging — NPU infers slower than frames arrive for that source)
            if p.type == "npu":
                inf_fps = p.params.get("inference_fps")
                src_master = master_by_name.get(sname)
                src_fps = src_master.params.get("fps") if src_master else None
                if inf_fps is not None and src_fps is not None and float(inf_fps) < float(src_fps):
                    issues.append(
                        LintIssue(
                            "warning",
                            "npu-fps-below-source",
                            f"pipeline '{p.name}': inference_fps {inf_fps} < source "
                            f"'{sname}' fps {src_fps} (async; not capped — NPU infers "
                            f"slower than frames arrive for this source).",
                        )
                    )

    return issues
