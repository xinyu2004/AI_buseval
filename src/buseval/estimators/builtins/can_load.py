"""CAN estimator (load mode): bitrate × load% × payload efficiency."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate, note


@register("can")
class CanLoadEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        dbc_path = str(params.get("dbc_path") or "").strip()
        if dbc_path:
            from .can_dbc import CANDbcEstimator
            return CANDbcEstimator().estimate(params)
        coeffs = get_coefficients()["can"]
        bitrate_mbps = _generic_bitrate_mbps(params, coeffs)
        load_pct = float(params.get("load_pct", 0.3))
        direction = params.get("direction", "both")

        bw = bitrate_mbps * load_pct * coeffs["payload_efficiency"]
        r, w = _split(bw, direction)

        assumptions = []
        if load_pct > get_coefficients()["alerts"]["aggressive_can_load_pct"]:
            assumptions.append(note(f"aggressive CAN load_pct={load_pct}", "yellow"))

        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "bitrate_mbps": bitrate_mbps,
                "load_pct": load_pct,
                "payload_efficiency": coeffs["payload_efficiency"],
            },
            dominant_factor=f"{bitrate_mbps}Mbps × load {load_pct:.0%}",
            assumptions=assumptions,
        )


def _generic_bitrate_mbps(params: dict, coeffs: dict) -> float:
    from ...dbc.health_report import resolve_can_rates

    standard = params.get("standard")
    data = params.get("data_kbps")
    if standard in ("can", "canfd"):
        _, _, rate = resolve_can_rates(
            standard=standard,
            arbitration_kbps=params.get("arbitration_kbps"),
            data_kbps=data if data else None,
        )
        return rate / 1000.0
    bitrate = params.get("bitrate_mbps")
    if bitrate:
        return float(bitrate)
    return float(coeffs["default_bitrate_kbps"]) / 1000.0


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
