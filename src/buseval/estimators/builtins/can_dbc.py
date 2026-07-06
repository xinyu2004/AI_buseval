"""CAN estimator (DBC mode): parse a DBC file and sum message bandwidth."""
from __future__ import annotations

from ..registry import Estimator, register, get_coefficients
from ...schema import BandwidthEstimate


@register("can_dbc")
class CANDbcEstimator(Estimator):
    def estimate(self, params: dict) -> BandwidthEstimate:
        coeffs = get_coefficients()["can"]
        dbc_path = params["dbc_path"]
        target_bus = params.get("bus_id")
        direction = params.get("direction", "both")  # rx|tx|both

        import cantools

        db = cantools.database.load_file(dbc_path)
        total_bits_per_sec = 0.0
        per_msg = []
        for msg in db.messages:
            # bus match (cantools may expose msg.bus as str or None)
            msg_bus = getattr(msg, "bus", None)
            if target_bus is not None and msg_bus not in (None, str(target_bus)):
                continue
            cycle = msg.cycle_time
            if not cycle or cycle <= 0:
                continue
            payload_bits = msg.length * 8
            bps = payload_bits * (1000.0 / cycle)
            total_bits_per_sec += bps
            per_msg.append(
                {"name": msg.name, "dlc": msg.length, "cycle_ms": cycle, "bps": round(bps, 1)}
            )

        # frame overhead
        total_bits_per_sec *= coeffs["frame_overhead_factor"]
        bw_mbps = total_bits_per_sec / 8.0 / 1e6  # MB/s
        per_msg.sort(key=lambda m: m["bps"], reverse=True)

        r, w = _split(bw_mbps, direction)
        return BandwidthEstimate(
            read_bw_mbps=round(r, 4),
            write_bw_mbps=round(w, 4),
            breakdown={
                "dbc_path": dbc_path,
                "bus_id": target_bus,
                "messages": per_msg[:10],
                "message_count": len(per_msg),
                "raw_bits_per_sec": round(total_bits_per_sec, 1),
                "frame_overhead_factor": coeffs["frame_overhead_factor"],
            },
            dominant_factor=f"{len(per_msg)} messages, top: "
            + (per_msg[0]["name"] if per_msg else "none"),
            assumptions=[],
        )


def _split(bw: float, direction: str):
    if direction == "rx":
        return 0.0, bw
    if direction == "tx":
        return bw, 0.0
    return bw * 0.5, bw * 0.5
