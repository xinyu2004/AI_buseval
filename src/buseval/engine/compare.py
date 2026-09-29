"""Compare a prediction from topology YAML with a meas.json file."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..estimators.registry import get_coefficients
from .predictor import PredictionResult


@dataclass
class CompareRow:
    name: str
    predicted_read: float | None
    predicted_write: float | None
    measured_read: float | None
    measured_write: float | None
    read_error_pct: float | None
    write_error_pct: float | None
    verdict: str
    note: str


def load_measurement(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("measurement file must be a JSON object with an items list")
    return data


def _thresholds() -> tuple[float, float]:
    cfg = get_coefficients().get("compare", {})
    return float(cfg.get("ok_error_pct", 0.15)), float(cfg.get("warn_error_pct", 0.30))


def _error_pct(predicted: float | None, measured: float | None) -> float | None:
    if predicted is None or measured is None:
        return None
    if predicted == 0:
        return 0.0 if measured == 0 else None
    return abs(measured - predicted) / abs(predicted)


def _verdict(errors: list[float | None], ok: float, warn: float) -> str:
    vals = [e for e in errors if e is not None]
    if not vals:
        return "UNMATCHED"
    worst = max(vals)
    if worst < ok:
        return "OK"
    if worst < warn:
        return "WARN"
    return "FAIL"


def compare_measurement(prediction: PredictionResult, measurement: dict) -> list[CompareRow]:
    """Align measurement items to prediction items or DDR channels by name.

    CLI and GUI both call this. Neither reads the other's output.
    """
    ok, warn = _thresholds()
    by_item = {it.name: it for it in prediction.items}
    by_ddr = {m.name: m for m in prediction.margins}
    measured_items = list(measurement.get("items") or [])
    measured_names = {str(it.get("name")) for it in measured_items}
    aggregate_only = bool(measured_names) and measured_names <= set(by_ddr)
    rows: list[CompareRow] = []
    seen: set[str] = set()

    for raw in measured_items:
        name = str(raw.get("name", ""))
        seen.add(name)
        meas_r = float(raw.get("read_bw_mbps", 0) or 0)
        meas_w = float(raw.get("write_bw_mbps", 0) or 0)
        note = ""
        if name in by_item:
            pred_r = by_item[name].read_bw_mbps
            pred_w = by_item[name].write_bw_mbps
        elif name in by_ddr:
            pred_r = by_ddr[name].read_demand_mbps
            pred_w = by_ddr[name].write_demand_mbps
            if aggregate_only:
                note = "未拆到 master"
        else:
            rows.append(CompareRow(name, None, None, meas_r, meas_w, None, None, "UNMATCHED", "实测项不在拓扑中"))
            continue
        read_err = _error_pct(pred_r, meas_r)
        write_err = _error_pct(pred_w, meas_w)
        rows.append(CompareRow(
            name, pred_r, pred_w, meas_r, meas_w, read_err, write_err,
            _verdict([read_err, write_err], ok, warn), note,
        ))

    for it in prediction.items:
        if it.name not in seen:
            rows.append(CompareRow(
                it.name, it.read_bw_mbps, it.write_bw_mbps, None, None, None, None,
                "UNMATCHED", "仅预测",
            ))
    return rows
