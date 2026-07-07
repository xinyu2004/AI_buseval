"""Predictor: run estimators over a topology and aggregate bandwidth."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Topology, BandwidthEstimate
from ..estimators.registry import get_estimator


@dataclass
class ItemEstimate:
    name: str
    type: str
    kind: str  # "master" | "pipeline"
    read_bw_mbps: float
    write_bw_mbps: float
    breakdown: dict
    dominant_factor: str
    assumptions: list[str]
    verify: bool = False


@dataclass
class PredictionResult:
    items: list[ItemEstimate] = field(default_factory=list)
    total_read_mbps: float = 0.0
    total_write_mbps: float = 0.0
    topology: Topology = None  # type: ignore[assignment]

    @property
    def assumptions(self) -> list[dict]:
        """One row per item, with all notes joined. Each row carries a `level`:
        - "red":    high-risk (DDR near-full, aggressive util >0.9, lane overflow)
        - "yellow": unverified default / non-typical coefficient / CAN load >0.7
        - "info":   source wiring (declared fact, shown for visibility)
        The row's level is the most severe among its notes.
        """
        from ..estimators.registry import get_coefficients
        try:
            alert_cfg = get_coefficients().get("alerts", {})
        except Exception:
            alert_cfg = {}
        ddr_near_full = float(alert_cfg.get("ddr_near_full_pct", 0.8))
        aggressive_util = float(alert_cfg.get("aggressive_util_pct", 0.9))
        aggressive_can = float(alert_cfg.get("aggressive_can_load_pct", 0.7))

        out = []
        for it in self.items:
            notes: list[tuple[str, str]] = []  # (level, message)
            bd = it.breakdown if isinstance(it.breakdown, dict) else {}

            # 1) estimator-internal assumptions (already classified by estimators)
            for a in it.assumptions:
                lvl = _classify_assumption(a, aggressive_util, aggressive_can)
                notes.append((lvl, a))

            # 2) source wiring (declared fact → info, not a risk)
            src_names = bd.get("source_names") or ([bd["source"]] if bd.get("source") else [])
            if src_names:
                src_join = "+".join(src_names)
                if it.type == "npu":
                    input_mbps = bd.get("input_frame_mbps", 0) or 0
                    notes.append(("info", f"input {input_mbps:.1f} MB/s from {src_join}"))
                else:
                    notes.append(("info", f"input from {src_join}"))

            # 3) verify flag → info (unverified default is a provenance note, not a risk)
            if it.verify:
                notes.append(("info", "uses unverified default value"))

            if notes:
                worst = _worst_level([n[0] for n in notes])
                out.append({
                    "item": it.name,
                    "level": worst,
                    "message": "; ".join(n[1] for n in notes),
                })

        # 4) DDR near-full warnings (one per channel at red/yellow)
        from .margin import evaluate_margin
        for m in evaluate_margin(self):
            if m.read_util >= ddr_near_full:
                out.append({
                    "item": m.name,
                    "level": "red",
                    "message": f"read util {m.read_util*100:.1f}% >= {ddr_near_full*100:.0f}% (DDR near full)",
                })
            elif m.write_util >= ddr_near_full:
                out.append({
                    "item": m.name,
                    "level": "red",
                    "message": f"write util {m.write_util*100:.1f}% >= {ddr_near_full*100:.0f}% (DDR near full)",
                })
        return out


_LEVEL_ORDER = {"red": 3, "yellow": 2, "info": 1}


def _worst_level(levels: list[str]) -> str:
    return max(levels, key=lambda l: _LEVEL_ORDER.get(l, 0))


def _classify_assumption(msg: str, aggressive_util: float, aggressive_can: float) -> str:
    """Classify an estimator-internal assumption string into red/yellow/info."""
    low = msg.lower()
    if "exceeds" in low and "lane" in low:
        return "red"           # lane overflow (physical impossibility)
    if "aggressive" in low and "util" in low:
        return "red"           # util > 0.9
    if "tops_used" in low and ">" in low:
        return "red"           # NPU tops over safety
    if "non-typical" in low:
        return "yellow"        # stage coefficient out of typical range
    if "async" in low:
        return "yellow"        # inference fps < source fps
    if "aggressive" in low and "can" in low:
        return "yellow"        # CAN load > 0.7
    return "yellow"            # default: treat unknown assumptions as yellow


def predict(topology: Topology) -> PredictionResult:
    items: list[ItemEstimate] = []

    # 1. Compute all master estimates first (pipelines may reference them via `source`).
    master_item_bw: dict[str, tuple[float, float]] = {}  # name -> (read, write)
    for m in topology.masters:
        if not m.enabled:
            continue
        est = get_estimator(m.type)
        result: BandwidthEstimate = est.estimate(m.params)
        it = _to_item(m.name, m.type, "master", result, getattr(m, "verify", False))
        items.append(it)
        master_item_bw[m.name] = (it.read_bw_mbps, it.write_bw_mbps)

    # 2. Compute pipelines in topological order (a pipeline may source another pipeline).
    #    source resolution:
    #      - master source  → inherit image dims (w/h/fps/bpp/count); downstream computes frame stream
    #      - pipeline source → inherit upstream's OUTPUT bandwidth (write_bw) as input_frame_mbps
    master_by_name = {m.name: m for m in topology.masters}
    pipeline_by_name = {p.name: p for p in topology.pipelines}

    order = _topo_sort_pipelines(topology.pipelines)
    pipeline_item_bw: dict[str, tuple[float, float]] = {}  # name -> (read, write)

    for p in order:
        if not p.enabled:
            continue
        est = get_estimator(p.type)
        params = dict(p.params)
        params["mode"] = p.mode
        params["stages"] = [s.model_dump() for s in p.stages]
        src_list = _normalize_source(p.source)

        if src_list:
            if p.type == "isp" and len(src_list) > 1:
                raise ValueError(
                    f"pipeline '{p.name}': ISP does not support multi-source (got {src_list})."
                )
            sources_spec = []
            for sname in src_list:
                if sname in master_by_name:
                    src_m = master_by_name[sname]
                    spec = {"name": sname}
                    for k in ("width", "height", "fps", "bpp", "count"):
                        if k in src_m.params:
                            spec[k] = src_m.params[k]
                    sources_spec.append(spec)
                elif sname in pipeline_by_name:
                    if sname not in pipeline_item_bw:
                        raise ValueError(
                            f"pipeline '{p.name}': source '{sname}' is not computed "
                            f"(cyclic dependency or disabled upstream)."
                        )
                    up_read, up_write = pipeline_item_bw[sname]
                    # For DSI sourcing from Display: Display's "output" is its read_bw
                    # (it reads framebuffer from DDR and carries it to the panel).
                    # For other p2p (ISP→NPU, ISP→VENC): upstream's output is write_bw.
                    if p.type == "mipi_dsi":
                        carried = up_read
                    else:
                        carried = up_write
                    sources_spec.append({"name": sname, "upstream_output_mbps": round(carried, 4)})
                else:
                    raise ValueError(
                        f"pipeline '{p.name}': source '{sname}' not found among masters or pipelines."
                    )
            # Dispatch by estimator type.
            # NPU: sources list — master sources carry dims (estimator computes MB/s),
            #      pipeline sources carry pre-computed input_mbps (upstream write_bw).
            if p.type == "npu":
                npu_sources = []
                for s in sources_spec:
                    if "upstream_output_mbps" in s:
                        npu_sources.append({
                            "name": s["name"],
                            "input_mbps": s["upstream_output_mbps"],
                        })
                    else:
                        npu_sources.append(s)  # master source: dims
                params["sources"] = npu_sources
            else:
                # ISP / VENC / VDEC / Display: single source.
                # - master source → flatten dims into params (estimator computes frame stream)
                # - pipeline source → pass source_input_mbps (estimator uses it directly)
                first = sources_spec[0]
                if "upstream_output_mbps" in first:
                    params["source_input_mbps"] = first["upstream_output_mbps"]
                    params["source"] = first["name"]
                else:
                    for k in ("width", "height", "fps", "bpp", "count"):
                        if k in first:
                            params[k] = first[k]
                    params["source"] = first["name"]

        result = est.estimate(params)
        it = _to_item(p.name, p.type, "pipeline", result, getattr(p, "verify", False))
        items.append(it)
        pipeline_item_bw[p.name] = (it.read_bw_mbps, it.write_bw_mbps)

    total_r = sum(it.read_bw_mbps for it in items)
    total_w = sum(it.write_bw_mbps for it in items)
    return PredictionResult(items=items, total_read_mbps=total_r, total_write_mbps=total_w, topology=topology)


def _normalize_source(source) -> list[str]:
    if source is None:
        return []
    if isinstance(source, str):
        return [source]
    return list(source)


def _topo_sort_pipelines(pipelines) -> list:
    """Topologically sort pipelines so that any pipeline sourced by another comes
    first. Raises ValueError on cyclic dependencies."""
    by_name = {p.name: p for p in pipelines}
    visited: dict[str, int] = {}  # 0=visiting, 1=done
    order: list = []

    def visit(name: str, stack: list[str]):
        state = visited.get(name)
        if state == 1:
            return
        if state == 0:
            cycle = " -> ".join(stack + [name])
            raise ValueError(f"cyclic pipeline dependency: {cycle}")
        p = by_name.get(name)
        if p is None:
            return
        visited[name] = 0
        stack.append(name)
        for s in _normalize_source(p.source):
            if s in by_name:
                visit(s, stack)
        stack.pop()
        visited[name] = 1
        order.append(p)

    for p in pipelines:
        visit(p.name, [])
    return order

    total_r = sum(it.read_bw_mbps for it in items)
    total_w = sum(it.write_bw_mbps for it in items)
    return PredictionResult(items=items, total_read_mbps=total_r, total_write_mbps=total_w, topology=topology)


def _to_item(name, type_, kind, est: BandwidthEstimate, verify: bool) -> ItemEstimate:
    return ItemEstimate(
        name=name,
        type=type_,
        kind=kind,
        read_bw_mbps=est.read_bw_mbps,
        write_bw_mbps=est.write_bw_mbps,
        breakdown=est.breakdown,
        dominant_factor=est.dominant_factor,
        assumptions=list(est.assumptions),
        verify=verify,
    )
