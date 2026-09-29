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
    read_demand_mbps: float
    write_demand_mbps: float
    read_util: float          # read / available
    write_util: float         # write / available
    occupancy: float          # (read + write) / available
    verdict: str              # OK | WARN | CRITICAL


def _compute_peaks(ch: DDRChannel) -> tuple[float, float, float, str]:
    """Return (controller_peak, module_peak, effective_peak, bottleneck).

    Both rates are required. MT/s already includes DDR, so there is no extra ×2.
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
    return 0.0, 0.0, 0.0, "n/a"


def _finite_round(value: float) -> float:
    return value if value == float("inf") else round(value, 4)


def evaluate_margin(prediction: PredictionResult) -> list[ChannelMargin]:
    topology: Topology = prediction.topology
    thresholds = topology.alert_thresholds
    yellow = float(thresholds.get("yellow", 0.6))
    red = float(thresholds.get("red", 0.8))

    out = []
    for ch in topology.ddr_channels:
        ctrl_peak, mod_peak, eff_peak, bottleneck = _compute_peaks(ch)
        avail = eff_peak * ch.efficiency
        read_demand = prediction.total_read_mbps
        write_demand = prediction.total_write_mbps
        read_util = (read_demand / avail) if avail > 0 else float("inf")
        write_util = (write_demand / avail) if avail > 0 else float("inf")
        occupancy = ((read_demand + write_demand) / avail) if avail > 0 else float("inf")
        if occupancy >= red:
            verdict = "CRITICAL"
        elif occupancy >= yellow:
            verdict = "WARN"
        else:
            verdict = "OK"

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
                read_demand_mbps=round(read_demand, 2),
                write_demand_mbps=round(write_demand, 2),
                read_util=_finite_round(read_util),
                write_util=_finite_round(write_util),
                occupancy=_finite_round(occupancy),
                verdict=verdict,
            )
        )
    return out
