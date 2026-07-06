"""Structured report: YAML / JSON serialization."""
from __future__ import annotations

import json
import time

import yaml

from ..engine.predictor import PredictionResult
from ..engine.margin import evaluate_margin


def build_structured(prediction: PredictionResult) -> dict:
    margins = evaluate_margin(prediction)
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "total_read_mbps": round(prediction.total_read_mbps, 4),
            "total_write_mbps": round(prediction.total_write_mbps, 4),
        },
        "ddr_channels": [_margin_to_dict(m) for m in margins],
        "items": [_item_to_dict(it) for it in prediction.items],
        "assumptions": prediction.assumptions,
    }


def _margin_to_dict(m) -> dict:
    return {
        "name": m.name,
        "peak_mbps": m.peak_mbps,
        "efficiency": m.efficiency,
        "available_mbps": m.available_mbps,
        "available_read_mbps": m.available_read_mbps,
        "available_write_mbps": m.available_write_mbps,
        "read_demand_mbps": m.read_demand_mbps,
        "write_demand_mbps": m.write_demand_mbps,
        "read_util": m.read_util,
        "write_util": m.write_util,
        "rw_imbalance": m.rw_imbalance,
        "rw_imbalance_flag": m.rw_imbalance_flag,
        "verdict": m.verdict,
    }


def _item_to_dict(it) -> dict:
    return {
        "name": it.name,
        "type": it.type,
        "kind": it.kind,
        "read_bw_mbps": it.read_bw_mbps,
        "write_bw_mbps": it.write_bw_mbps,
        "dominant_factor": it.dominant_factor,
        "assumptions": it.assumptions,
        "verify": it.verify,
        "breakdown": it.breakdown,
    }


def dump_yaml(report: dict) -> str:
    return yaml.safe_dump(report, sort_keys=False, allow_unicode=True)


def dump_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)
