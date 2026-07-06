"""Margin evaluation: compare predicted demand against DDR available bandwidth."""
from __future__ import annotations

from dataclasses import dataclass

from ..schema import Topology
from .predictor import PredictionResult


@dataclass
class ChannelMargin:
    name: str
    peak_mbps: float
    efficiency: float
    available_mbps: float
    available_read_mbps: float
    available_write_mbps: float
    read_demand_mbps: float
    write_demand_mbps: float
    read_util: float
    write_util: float
    verdict: str  # OK | WARN | CRITICAL
    rw_imbalance: float
    rw_imbalance_flag: bool


def evaluate_margin(prediction: PredictionResult) -> list[ChannelMargin]:
    topology: Topology = prediction.topology
    thresholds = topology.alert_thresholds
    yellow = float(thresholds.get("yellow", 0.6))
    red = float(thresholds.get("red", 0.8))

    out = []
    for ch in topology.ddr_channels:
        avail = ch.theoretical_peak_mbps * ch.efficiency
        r_ratio = ch.read_write_ratio if ch.read_write_ratio is not None else 0.5
        avail_r = avail * r_ratio
        avail_w = avail * (1.0 - r_ratio)

        util_r = (prediction.total_read_mbps / avail_r) if avail_r > 0 else float("inf")
        util_w = (prediction.total_write_mbps / avail_w) if avail_w > 0 else float("inf")

        max_util = max(util_r, util_w)
        if max_util >= red:
            verdict = "CRITICAL"
        elif max_util >= yellow:
            verdict = "WARN"
        else:
            verdict = "OK"

        denom = max(util_r, util_w) if max(util_r, util_w) > 0 else 1e-9
        imbalance = abs(util_r - util_w) / denom
        imbalance_flag = imbalance > 0.3 and max_util > 0.01

        out.append(
            ChannelMargin(
                name=ch.name,
                peak_mbps=ch.theoretical_peak_mbps,
                efficiency=ch.efficiency,
                available_mbps=round(avail, 2),
                available_read_mbps=round(avail_r, 2),
                available_write_mbps=round(avail_w, 2),
                read_demand_mbps=round(prediction.total_read_mbps, 2),
                write_demand_mbps=round(prediction.total_write_mbps, 2),
                read_util=round(util_r, 4),
                write_util=round(util_w, 4),
                verdict=verdict,
                rw_imbalance=round(imbalance, 4),
                rw_imbalance_flag=imbalance_flag,
            )
        )
    return out
