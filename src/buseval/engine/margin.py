"""Margin evaluation: compare predicted demand against DDR available bandwidth.

Effective DDR peak = min(controller_peak, module_peak) — the bottleneck of the
chip's DDR IP vs the external DRAM module. Available = effective_peak × efficiency.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schema import Topology, DDRChannel
from .predictor import PredictionResult


@dataclass
class ChannelMargin:
    name: str
    # raw params (for detailed reporting)
    controller_mt_s: float
    controller_width_bits: int
    controller_groups: int
    controller_type: str
    module_mt_s: float
    module_width_bits: int
    module_groups: int
    module_type: str
    # computed peaks
    controller_peak_mbps: float
    module_peak_mbps: float
    effective_peak_mbps: float
    bottleneck: str           # "controller" | "module" | "matched" | "n/a"
    efficiency: float
    available_mbps: float
    available_read_mbps: float
    available_write_mbps: float
    read_demand_mbps: float
    write_demand_mbps: float
    read_util: float
    write_util: float
    verdict: str              # OK | WARN | CRITICAL
    rw_imbalance: float
    rw_imbalance_flag: bool


def _compute_peaks(ch: DDRChannel) -> tuple[float, float, float, str]:
    """Return (controller_peak, module_peak, effective_peak, bottleneck).

    If physical params (controller_mt_s etc.) are present, compute from them.
    Else fall back to theoretical_peak_mbps (legacy shorthand: controller = module = theoretical).
    """
    if ch.controller_mt_s is not None and ch.module_mt_s is not None:
        # MT/s already includes DDR double data rate — no ×2 needed.
        # controller_groups and module_groups model multi-rank / multi-channel configs
        # (e.g., 4×32-bit controller = 128-bit effective with controller_groups=4).
        ctrl = ch.controller_mt_s * (ch.controller_width_bits or 32) / 8.0 * ch.controller_groups
        mod = ch.module_mt_s * (ch.module_width_bits or 32) / 8.0 * ch.module_groups
        eff = min(ctrl, mod)
        if ctrl < mod:
            bottleneck = "controller"
        elif mod < ctrl:
            bottleneck = "module"
        else:
            bottleneck = "matched"
        return round(ctrl, 2), round(mod, 2), round(eff, 2), bottleneck
    else:
        # Legacy: theoretical_peak_mbps (or 0 if missing)
        tp = ch.theoretical_peak_mbps or 0
        return tp, tp, tp, "n/a"


def evaluate_margin(prediction: PredictionResult) -> list[ChannelMargin]:
    topology: Topology = prediction.topology
    thresholds = topology.alert_thresholds
    yellow = float(thresholds.get("yellow", 0.6))
    red = float(thresholds.get("red", 0.8))

    out = []
    for ch in topology.ddr_channels:
        ctrl_peak, mod_peak, eff_peak, bottleneck = _compute_peaks(ch)
        avail = eff_peak * ch.efficiency
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
                controller_mt_s=ch.controller_mt_s or 0,
                controller_width_bits=ch.controller_width_bits or 0,
                controller_groups=ch.controller_groups,
                controller_type=ch.controller_type or "",
                module_mt_s=ch.module_mt_s or 0,
                module_width_bits=ch.module_width_bits or 0,
                module_groups=ch.module_groups,
                module_type=ch.module_type or "",
                controller_peak_mbps=ctrl_peak,
                module_peak_mbps=mod_peak,
                effective_peak_mbps=eff_peak,
                bottleneck=bottleneck,
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
