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
        """One row per item, with all notes joined (risks + source wiring + verify).
        Combining into one row avoids the same item appearing multiple times."""
        out = []
        for it in self.items:
            notes = list(it.assumptions)
            # source wiring info (a declared fact, shown here for visibility — not a risk)
            bd = it.breakdown if isinstance(it.breakdown, dict) else {}
            src = bd.get("source")
            if src:
                if it.type == "npu":
                    input_mbps = bd.get("input_frame_mbps", 0) or 0
                    notes.append(f"input {input_mbps:.1f} MB/s from {src}")
                else:
                    notes.append(f"input from {src}")
            if it.verify:
                notes.append("uses unverified default value")
            if notes:
                out.append({"item": it.name, "message": "; ".join(notes)})
        return out


def predict(topology: Topology) -> PredictionResult:
    items: list[ItemEstimate] = []

    # 1. Compute all master estimates first (pipelines may reference them via `source`).
    for m in topology.masters:
        if not m.enabled:
            continue
        est = get_estimator(m.type)
        result: BandwidthEstimate = est.estimate(m.params)
        items.append(_to_item(m.name, m.type, "master", result, getattr(m, "verify", False)))

    # 2. Compute pipelines; resolve `source` to inherit image dimensions.
    master_by_name = {m.name: m for m in topology.masters}
    pipeline_names = {p.name for p in topology.pipelines}
    for p in topology.pipelines:
        if not p.enabled:
            continue
        est = get_estimator(p.type)
        params = dict(p.params)
        params["mode"] = p.mode
        params["stages"] = [s.model_dump() for s in p.stages]
        if p.source:
            if p.source in master_by_name:
                # Inherit image dimensions from the source master (CSI/DSI).
                # The estimator recomputes the frame stream from these — no
                # pre-computed bandwidth field, users edit width/height/fps.
                src = master_by_name[p.source]
                for k in ("width", "height", "fps", "bpp", "count"):
                    if k in src.params:
                        params.setdefault(k, src.params[k])
                params["source"] = p.source
            elif p.source in pipeline_names:
                raise ValueError(
                    f"pipeline '{p.name}': source '{p.source}' references another "
                    f"pipeline; pipeline-to-pipeline chaining is not supported yet."
                )
            else:
                raise ValueError(
                    f"pipeline '{p.name}': source '{p.source}' not found among masters."
                )
        result = est.estimate(params)
        items.append(_to_item(p.name, p.type, "pipeline", result, getattr(p, "verify", False)))

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
