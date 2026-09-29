"""Predictor: run estimators over a topology and aggregate bandwidth."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Topology, BandwidthEstimate, note_level, note_message
from ..estimators.registry import get_estimator
from .graph import isp_multi_source_message, normalize_source, source_not_found_message, topo_sort_pipelines


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


@dataclass
class PredictionResult:
    items: list[ItemEstimate] = field(default_factory=list)
    total_read_mbps: float = 0.0
    total_write_mbps: float = 0.0
    topology: Topology = None  # type: ignore[assignment]

    @property
    def margins(self) -> list:
        """DDR margins for this prediction, computed once."""
        cached = getattr(self, "_margin_cache", None)
        if cached is None:
            from .margin import evaluate_margin
            cached = evaluate_margin(self)
            object.__setattr__(self, "_margin_cache", cached)
        return cached

    @property
    def assumptions(self) -> list[dict]:
        """One row per item, with all notes joined. Each row carries a `level`:
        - "red":    high-risk (DDR near-full, aggressive util >0.9, lane overflow)
        - "yellow": non-typical coefficient / CAN load >0.7
        - "info":   a declared fact from an estimator, when one still emits it
        The row's level is the most severe among its notes.
        Source wiring is not repeated here; the canvas edges already show it.
        """
        from ..estimators.registry import get_coefficients
        try:
            alert_cfg = get_coefficients().get("alerts", {})
        except Exception:
            alert_cfg = {}
        ddr_near_full = float(alert_cfg.get("ddr_near_full_pct", 0.8))

        out = []
        for it in self.items:
            notes: list[tuple[str, str]] = []  # (level, message)

            # Estimators attach level on each note. No sentence parsing.
            for a in it.assumptions:
                notes.append((note_level(a), note_message(a)))

            if notes:
                worst = _worst_level([n[0] for n in notes])
                out.append({
                    "item": it.name,
                    "level": worst,
                    "message": "; ".join(n[1] for n in notes),
                })

        # 4) DDR near-full warnings (one per channel at red/yellow)
        for m in self.margins:
            if m.occupancy >= ddr_near_full:
                out.append({
                    "item": m.name,
                    "level": "red",
                    "message": f"occupancy {m.occupancy*100:.1f}% >= {ddr_near_full*100:.0f}% (DDR near full)",
                })
        return out


_LEVEL_ORDER = {"red": 3, "yellow": 2, "info": 1}


def _worst_level(levels: list[str]) -> str:
    return max(levels, key=lambda l: _LEVEL_ORDER.get(l, 0))


def _fed_by_pipeline(master, pipeline_names: set[str]) -> bool:
    return master.type == "eth" and any(
        name in pipeline_names for name in normalize_source(master.source)
    )


def _estimate_master(master, items: list[ItemEstimate], master_item_bw: dict, params: dict | None = None):
    est = get_estimator(master.type)
    result: BandwidthEstimate = est.estimate(master.params if params is None else params)
    it = _to_item(master.name, master.type, "master", result)
    items.append(it)
    master_item_bw[master.name] = (it.read_bw_mbps, it.write_bw_mbps)


def predict(topology: Topology) -> PredictionResult:
    items: list[ItemEstimate] = []

    # 1. Masters first, except an ETH that reads a pipeline (its bitstream is not
    #    known until that pipeline has been estimated).
    master_item_bw: dict[str, tuple[float, float]] = {}  # name -> (read, write)
    pipeline_names = {p.name for p in topology.pipelines}
    deferred = []
    for m in topology.masters:
        if not m.enabled:
            continue
        if _fed_by_pipeline(m, pipeline_names):
            deferred.append(m)
            continue
        _estimate_master(m, items, master_item_bw)

    # 2. Compute pipelines in topological order (a pipeline may source another pipeline).
    #    source resolution:
    #      - master source  → inherit image dims (w/h/fps/bpp, and count except on ISP)
    #      - pipeline source → inherit the upstream picture (output_mbps, else write_bw)
    master_by_name = {m.name: m for m in topology.masters}
    pipeline_by_name = {p.name: p for p in topology.pipelines}

    order = topo_sort_pipelines(topology.pipelines)
    pipeline_item_bw: dict[str, tuple[float, float]] = {}  # name -> (read, write)
    pipeline_output: dict[str, float] = {}  # picture handed to the next block

    for p in order:
        if not p.enabled:
            continue
        est = get_estimator(p.type)
        params = dict(p.params)
        params["mode"] = p.mode
        params["stages"] = [s.model_dump() for s in p.stages]
        src_list = normalize_source(p.source)

        if src_list:
            if p.type == "isp" and len(src_list) > 1:
                raise ValueError(isp_multi_source_message(p.name, src_list))
            sources_spec = []
            for sname in src_list:
                if sname in master_by_name:
                    src_m = master_by_name[sname]
                    spec = {"name": sname}
                    for k in ("width", "height", "fps", "bpp", "count", "format"):
                        if k in src_m.params:
                            spec[k] = src_m.params[k]
                    sources_spec.append(spec)
                elif sname in pipeline_by_name:
                    if sname not in pipeline_item_bw:
                        raise ValueError(
                            f"pipeline '{p.name}': source '{sname}' is not computed "
                            f"(cyclic dependency or disabled upstream)."
                        )
                    up_read, _up_write = pipeline_item_bw[sname]
                    # DSI carries the display's framebuffer read. Every other block
                    # reads the upstream picture (last ISP stage), not that block's
                    # internal peak write.
                    if p.type == "mipi_dsi":
                        carried = up_read
                    else:
                        carried = pipeline_output.get(sname, _up_write)
                    sources_spec.append({"name": sname, "upstream_output_mbps": round(carried, 4)})
                else:
                    raise ValueError(source_not_found_message(p.name, sname))
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
                    # ISP is one use case. A CSI count means several cameras on
                    # that port, not several copies of this ISP.
                    keys = (
                        ("width", "height", "fps", "bpp", "format")
                        if p.type == "isp"
                        else ("width", "height", "fps", "bpp", "count", "format")
                    )
                    for k in keys:
                        if k in first:
                            params[k] = first[k]
                    params["source"] = first["name"]

        result = est.estimate(params)
        it = _to_item(p.name, p.type, "pipeline", result)
        items.append(it)
        pipeline_item_bw[p.name] = (it.read_bw_mbps, it.write_bw_mbps)
        picture = it.breakdown.get("output_mbps") if isinstance(it.breakdown, dict) else None
        pipeline_output[p.name] = float(picture) if picture is not None else it.write_bw_mbps

    # 3. ETH fed by a pipeline: DDR read is that picture or bitstream.
    for m in deferred:
        carried = 0.0
        names = []
        for sname in normalize_source(m.source):
            if sname not in pipeline_output:
                if sname in pipeline_by_name:
                    raise ValueError(
                        f"master '{m.name}': source '{sname}' is not computed "
                        f"(cyclic dependency or disabled upstream)."
                    )
                raise ValueError(source_not_found_message(m.name, sname))
            carried += pipeline_output[sname]
            names.append(sname)
        params = dict(m.params)
        params["source_input_mbps"] = round(carried, 4)
        params["source"] = "+".join(names)
        _estimate_master(m, items, master_item_bw, params)

    total_r = sum(it.read_bw_mbps for it in items)
    total_w = sum(it.write_bw_mbps for it in items)
    return PredictionResult(items=items, total_read_mbps=total_r, total_write_mbps=total_w, topology=topology)


def _to_item(name, type_, kind, est: BandwidthEstimate) -> ItemEstimate:
    return ItemEstimate(
        name=name,
        type=type_,
        kind=kind,
        read_bw_mbps=est.read_bw_mbps,
        write_bw_mbps=est.write_bw_mbps,
        breakdown=est.breakdown,
        dominant_factor=est.dominant_factor,
        assumptions=list(est.assumptions),
    )
