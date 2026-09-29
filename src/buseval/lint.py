"""Lint: check a topology for missing categories, contradictions, invalid params."""
from __future__ import annotations

from dataclasses import dataclass, field

from .engine.graph import find_cycle, normalize_source
from .engine.stream import format_geometry, has_geometry, resolve_streams, same_geometry
from .gui.i18n import t
from .schema import Topology


def _msg(key: str, **kwargs) -> str:
    text = t(key)
    return text.format(**kwargs) if kwargs else text

# Estimators read these keys directly. The scan must see them before predict.
_REQUIRED = {
    "can": ("bitrate_mbps", "load_pct"),
    "spi": ("clock_mhz", "xfer_bytes", "xfer_hz"),
    "mipi_csi": ("width", "height", "fps", "bpp"),
    "usb": ("version", "util_pct"),
    "eth": ("link_gbps", "util_pct"),
    "flash": ("type", "seq_read_mbps", "seq_write_mbps", "util_pct"),
    "npu": ("inference_fps",),
    "gpu": ("width", "height", "fps"),
}
# These use an upstream pipeline's megabytes/second and do not need their own size.
_MBPS_TYPES = {"display", "venc", "vdec", "mipi_dsi"}


@dataclass
class LintIssue:
    level: str  # "error" | "warning"
    rule: str
    message: str
    node: str | None = None


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
        issues.append(LintIssue("error", "no-ddr", _msg("lint_no_ddr")))

    # No output sinks at all
    if not (pipeline_types & {"gpu", "display"} or master_types & {"mipi_dsi"}):
        issues.append(
            LintIssue(
                "warning",
                "no-output",
                _msg("lint_no_output"),
            )
        )

    # No compute pipelines
    if not (pipeline_types & {"npu", "gpu"}):
        issues.append(
            LintIssue(
                "warning",
                "no-compute",
                _msg("lint_no_compute"),
            )
        )

    # CSI without ISP
    if "mipi_csi" in master_types and "isp" not in pipeline_types:
        issues.append(
            LintIssue(
                "warning",
                "csi-without-isp",
                _msg("lint_csi_no_isp"),
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
                _msg("lint_npu_weights"),
            )
        )

    # Param sanity
    for m in enabled_masters:
        params = m.params
        if m.type in ("can",):
            load = float(params.get("load_pct", 0))
            if not 0.0 <= load <= 1.0:
                issues.append(
                    LintIssue("error", "param-range", _msg("lint_load", name=m.name, load=load), m.name)
                )
        if m.type.startswith("mipi"):
            lanes = params.get("lanes")
            if lanes is not None and lanes not in (1, 2, 3, 4):
                issues.append(
                    LintIssue("error", "param-range", _msg("lint_lanes", name=m.name, lanes=lanes), m.name)
                )
        if m.type == "usb":
            v = str(params.get("version", ""))
            if v and v not in ("2", "3", "3.2"):
                issues.append(
                    LintIssue("error", "param-range", _msg("lint_usb", name=m.name, version=v), m.name)
                )

    # Pipeline `source` validation (source may be str or list[str]; may reference
    # masters OR other pipelines — p2p chaining is supported via topological sort.)
    master_by_name = {m.name: m for m in topology.masters}
    pipeline_by_name = {p.name: p for p in topology.pipelines}
    # Detect cyclic pipeline dependencies.
    cycle = find_cycle(topology.pipelines)
    if cycle:
        issues.append(LintIssue("error", "source-cyclic", _msg("lint_cycle", path=" -> ".join(cycle))))
    for p in enabled_pipelines:
        src_list = normalize_source(p.source)
        if not src_list:
            continue
        if p.type == "isp" and len(src_list) > 1:
            issues.append(
                LintIssue("error", "isp-multi-source", _msg("lint_isp_multi", name=p.name, src=src_list), p.name)
            )
            continue
        for sname in src_list:
            if sname in pipeline_by_name:
                upstream = pipeline_by_name[sname]
                if not upstream.enabled:
                    issues.append(
                        LintIssue(
                            "warning",
                            "source-pipeline-disabled",
                            _msg("lint_disabled", name=p.name, src=sname),
                            p.name,
                        )
                    )
                continue  # p2p is OK
            if sname not in master_names:
                issues.append(
                    LintIssue("error", "source-not-found", _msg("lint_missing_source", name=p.name, src=sname), p.name)
                )
                continue
            if p.type == "isp" and ("width" in p.params or "height" in p.params or "fps" in p.params):
                issues.append(
                    LintIssue(
                        "warning",
                        "source-override",
                        _msg("lint_override", name=p.name, src=sname),
                        p.name,
                    )
                )
            if p.type == "npu":
                inf_fps = p.params.get("inference_fps")
                src_master = master_by_name.get(sname)
                src_fps = src_master.params.get("fps") if src_master else None
                if inf_fps is not None and src_fps is not None and float(inf_fps) < float(src_fps):
                    issues.append(
                        LintIssue(
                            "warning",
                            "npu-fps-below-source",
                            _msg("lint_npu_fps", name=p.name, inf=inf_fps, src=sname, fps=src_fps),
                            p.name,
                        )
                    )

    streams = resolve_streams(topology)
    _scan_required(issues, enabled_masters, enabled_pipelines, streams)
    _scan_geometry(issues, enabled_pipelines, streams)
    _scan_isp_budget(issues, topology, enabled_pipelines, streams)
    return issues


def _missing_keys(params: dict, keys: tuple[str, ...]) -> list[str]:
    missing = []
    for key in keys:
        value = params.get(key)
        if value is None or value == "":
            missing.append(key)
    return missing


def _scan_required(issues, masters, pipelines, streams) -> None:
    for master in masters:
        missing = _missing_keys(master.params, _REQUIRED.get(master.type, ()))
        if missing:
            issues.append(
                LintIssue(
                    "error",
                    "param-missing",
                    _msg("lint_param_missing", name=master.name, keys=", ".join(missing)),
                    master.name,
                )
            )
        _scan_numbers(issues, master.name, master.type, master.params)

    for pipe in pipelines:
        info = streams.get(pipe.name)
        missing = _missing_keys(pipe.params, _REQUIRED.get(pipe.type, ()))
        if pipe.type in ("isp", "display", "venc", "vdec", "mipi_dsi") and _needs_own_geometry(pipe, info):
            geom_missing = _missing_keys(pipe.params, ("width", "height", "fps"))
            for key in geom_missing:
                if key not in missing:
                    missing.append(key)
        if missing:
            issues.append(
                LintIssue(
                    "error",
                    "param-missing",
                    _msg("lint_param_missing", name=pipe.name, keys=", ".join(missing)),
                    pipe.name,
                )
            )
        _scan_numbers(issues, pipe.name, pipe.type, pipe.params)


def _needs_own_geometry(pipe, info) -> bool:
    """True when predict would ask this node for width/height/fps."""
    if info is None:
        return True
    inherited = any(has_geometry(geom) for _name, geom in info.sources)
    if pipe.type == "isp":
        # ISP only inherits a master's image size. A pipeline upstream does not fill width.
        return not (inherited and not info.uses_upstream_mbps)
    if pipe.type in _MBPS_TYPES:
        if info.uses_upstream_mbps:
            return False
        return not inherited
    return False


def _scan_numbers(issues, name: str, type_name: str, params: dict) -> None:
    if type_name == "npu" and "inference_fps" in params:
        try:
            fps = float(params["inference_fps"])
        except (TypeError, ValueError):
            fps = 0
        if fps <= 0:
            issues.append(LintIssue("error", "param-range", _msg("lint_fps", name=name), name))


def _isp_mpix(pipe, streams) -> float | None:
    """Megapixels per second of one ISP. Stage size wins; fps follows the camera."""
    w = h = fps = None
    if pipe.stages and pipe.stages[0].width and pipe.stages[0].height:
        w, h = pipe.stages[0].width, pipe.stages[0].height
    info = streams.get(pipe.name)
    effective = info.effective if info is not None else {}
    if has_geometry(effective):
        if w is None:
            w, h = effective["width"], effective["height"]
        fps = effective.get("fps")
    if fps is None:
        fps = pipe.params.get("fps")
    if w is None:
        w, h = pipe.params.get("width"), pipe.params.get("height")
    if not w or not h or not fps:
        return None
    return float(w) * float(h) * float(fps) / 1e6


def _scan_isp_budget(issues, topology, pipelines, streams) -> None:
    isps = [pipe for pipe in pipelines if pipe.type == "isp"]
    if not isps:
        return
    slots = {slot.name: float(slot.mpix_s) for slot in topology.isp_vpacs}
    if not slots:
        issues.append(LintIssue("warning", "isp-spec-missing", _msg("lint_isp_unset")))
        return
    known = ", ".join(slots)
    for pipe in isps:
        if pipe.name not in slots:
            issues.append(
                LintIssue(
                    "warning",
                    "isp-unknown",
                    _msg("lint_isp_unknown", name=pipe.name, slots=known),
                    pipe.name,
                )
            )
            continue
        mpix = _isp_mpix(pipe, streams)
        rate = slots[pipe.name]
        if mpix is not None and mpix > rate:
            issues.append(
                LintIssue(
                    "warning",
                    "isp-vpac-over",
                    _msg("lint_isp_over", name=pipe.name, used=f"{mpix:.1f}", rate=f"{rate:.0f}"),
                    pipe.name,
                )
            )


def _scan_geometry(issues, pipelines, streams) -> None:
    for pipe in pipelines:
        info = streams.get(pipe.name)
        if info is None or not has_geometry(info.own):
            continue
        for src_name, geom in info.sources:
            if has_geometry(geom) and not same_geometry(info.own, geom):
                issues.append(
                    LintIssue(
                        "warning",
                        "geometry-mismatch",
                        _msg(
                            "lint_geom",
                            name=pipe.name,
                            own=format_geometry(info.own),
                            src=src_name,
                            geom=format_geometry(geom),
                        ),
                        pipe.name,
                    )
                )
