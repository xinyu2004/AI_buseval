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
        out = []
        for it in self.items:
            for a in it.assumptions:
                out.append({"item": it.name, "message": a})
            if it.verify:
                out.append({"item": it.name, "message": "uses unverified default value"})
        return out


def predict(topology: Topology) -> PredictionResult:
    items: list[ItemEstimate] = []

    for m in topology.masters:
        if not m.enabled:
            continue
        est = get_estimator(m.type)
        result: BandwidthEstimate = est.estimate(m.params)
        items.append(_to_item(m.name, m.type, "master", result, getattr(m, "verify", False)))

    for p in topology.pipelines:
        if not p.enabled:
            continue
        est = get_estimator(p.type)
        params = dict(p.params)
        params["mode"] = p.mode
        params["stages"] = [s.model_dump() for s in p.stages]
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
