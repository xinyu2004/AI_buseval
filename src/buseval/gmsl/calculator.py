"""GMSL link bandwidth.

Coax (pixel mode, ADI GMSL2 User Guide §19.3):

  PCLK    = width × height × fps × (blanking if heartbeat else 1)
  bpp_link = max(bpp, 9)
  link_bw = PCLK × (bpp_link + crc) × encoding × (2048/2047) × fec

CSI always uses the real bpp and blanking. Encoding, CRC, and FEC stay on the coax.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..estimators.formats import resolve_bpp
from ..estimators.registry import get_coefficients

PACKET = 2048 / 2047
FEC = 128 / 120
CPHY_BITS_PER_SYMBOL = 2.28
MAX_STREAMS = 4
ENCODINGS = {
    "9b10b": 10 / 9,
    "8b10b": 10 / 8,
    "none": 1.0,
}
ENCODING_EXPR = {
    "9b10b": "10/9",
    "8b10b": "10/8",
    "none": "1",
}
_ENCODING_ALIASES = {
    "9b10b": "9b10b",
    "9b/10b": "9b10b",
    "8b10b": "8b10b",
    "8b/10b": "8b10b",
    "none": "none",
}
# name, default line rate Mbps, FEC always applied (GMSL3 PAM4)
_TIER_ORDER = (
    ("gmsl1", 3120.0, False),
    ("gmsl2_3g", 3000.0, False),
    ("gmsl2_6g", 6000.0, False),
    ("gmsl3", 12000.0, True),
)
TIER_TITLES = {
    "gmsl1": "GMSL1",
    "gmsl2_3g": "GMSL2 3G",
    "gmsl2_6g": "GMSL2 6G",
    "gmsl3": "GMSL3",
}


@dataclass
class GmslLinkResult:
    name: str
    width: int
    height: int
    fps: float
    bpp: float
    bpp_link: float
    blanking: float
    heartbeat: bool
    encoding: str
    encoding_expr: str
    encoding_factor: float
    crc_bpp: float
    crc_factor: float
    packet_factor: float
    fec_enabled: bool
    fec_factor: float
    pclk_mhz: float
    pixel_rate_mbps: float
    after_crc_mbps: float
    after_encoding_mbps: float
    after_packet_mbps: float
    link_bw_mbps: float
    csi_mbps: float
    recommendations: list[dict] = field(default_factory=list)
    best_fit: str = ""


@dataclass
class GmslReport:
    links: list[GmslLinkResult] = field(default_factory=list)
    total_link_bw_mbps: float = 0.0
    phy: str = "dphy"
    lanes: int = 4

    def to_dict(self) -> dict:
        return {
            "links": [_link_to_dict(link) for link in self.links],
            "total_link_bw_mbps": round(self.total_link_bw_mbps, 4),
            "phy": self.phy,
            "lanes": self.lanes,
            "summary": _summary(self.links),
        }


def _round_rec(row: dict) -> dict:
    out = dict(row)
    out["bw_mbps"] = round(row["bw_mbps"], 4)
    out["util"] = round(row["util"], 4)
    return out


def _link_to_dict(link: GmslLinkResult) -> dict:
    return {
        "name": link.name,
        "width": link.width,
        "height": link.height,
        "fps": link.fps,
        "bpp": link.bpp,
        "bpp_link": link.bpp_link,
        "blanking": link.blanking,
        "heartbeat": link.heartbeat,
        "encoding": link.encoding,
        "encoding_expr": link.encoding_expr,
        "encoding_factor": round(link.encoding_factor, 6),
        "crc_bpp": link.crc_bpp,
        "crc_factor": round(link.crc_factor, 6),
        "packet_factor": round(link.packet_factor, 6),
        "fec_enabled": link.fec_enabled,
        "fec_factor": round(link.fec_factor, 6),
        "pclk_mhz": round(link.pclk_mhz, 4),
        "pixel_rate_mbps": round(link.pixel_rate_mbps, 4),
        "after_crc_mbps": round(link.after_crc_mbps, 4),
        "after_encoding_mbps": round(link.after_encoding_mbps, 4),
        "after_packet_mbps": round(link.after_packet_mbps, 4),
        "link_bw_mbps": round(link.link_bw_mbps, 4),
        "csi_mbps": round(link.csi_mbps, 4),
        "recommendations": [_round_rec(row) for row in link.recommendations],
        "best_fit": link.best_fit,
    }


def coax_tier_rows(links: list[GmslLinkResult]) -> tuple[list[dict], str]:
    """One coax. Each tier's bandwidth is the sum of the streams."""
    if not links:
        return [], ""
    fec_on = links[0].fec_enabled
    rows = []
    best = ""
    for name, default_cap, fec_required in _TIER_ORDER:
        cap = _tier_capacity(name, default_cap)
        bw = sum(
            next(row["bw_mbps"] for row in link.recommendations if row["tier"] == name)
            for link in links
        )
        util = bw / cap if cap else 0.0
        fits = bw <= cap
        if fits and not best:
            best = name
        rows.append({
            "tier": name,
            "title": TIER_TITLES[name],
            "capacity_mbps": cap,
            "bw_mbps": bw,
            "util": util,
            "fits": fits,
            "fec_extra": fec_required and not fec_on,
        })
    return rows, best


def _summary(links: list[GmslLinkResult]) -> dict:
    if not links:
        return {}
    total = sum(link.link_bw_mbps for link in links)
    csi = sum(link.csi_mbps for link in links)
    packet = sum(link.after_packet_mbps for link in links)
    rows, best = coax_tier_rows(links)
    tier_summary = {
        row["tier"]: {
            "title": row["title"],
            "capacity_mbps": row["capacity_mbps"],
            "bw_mbps": round(row["bw_mbps"], 4),
            "util": round(row["util"], 4),
            "fits": row["fits"],
        }
        for row in rows
    }
    return {
        "link_count": len(links),
        "total_link_bw_mbps": round(total, 4),
        "total_link_bw_gbps": round(total / 1000, 4),
        "total_csi_mbps": round(csi, 4),
        "after_packet_mbps": round(packet, 4),
        "aggregate_best_fit": best,
        "tier_summary": tier_summary,
    }


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _opt_bool(overrides: dict, key: str, default: bool) -> bool:
    if key not in overrides or overrides[key] is None:
        return default
    return _as_bool(overrides[key], default)


def normalize_encoding(value) -> str:
    key = str(value or "9b10b").strip().lower()
    return _ENCODING_ALIASES.get(key, "9b10b")


def normalize_phy(value) -> str:
    key = str(value or "dphy").strip().lower().replace("-", "")
    return "cphy" if key == "cphy" else "dphy"


def normalize_lanes(value, phy: str = "dphy") -> int:
    try:
        lanes = int(value)
    except (TypeError, ValueError):
        lanes = 4
    cap = 3 if normalize_phy(phy) == "cphy" else 4
    return min(cap, max(1, lanes))


def csi_rate(payload_mbps: float, phy: str, lanes: int) -> dict:
    """Deserializer CSI port. Config steps are 0.1 Gbps."""
    kind = normalize_phy(phy)
    count = normalize_lanes(lanes, kind)
    per_gbps = (payload_mbps / count) / 1000.0
    config_gbps = math.ceil(per_gbps * 10 - 1e-9) / 10
    if kind == "cphy":
        return {
            "phy": kind,
            "lanes": count,
            "per_lane_gbps": per_gbps,
            "config_gbps": config_gbps,
            "clock_mhz": None,
            "symbol_msym": per_gbps / CPHY_BITS_PER_SYMBOL * 1000.0,
            "deskew": False,
        }
    return {
        "phy": kind,
        "lanes": count,
        "per_lane_gbps": per_gbps,
        "config_gbps": config_gbps,
        "clock_mhz": (payload_mbps / count) / 2.0,
        "symbol_msym": None,
        "deskew": per_gbps > 1.5,
    }


def _tier_capacity(name: str, default: float) -> float:
    tiers = get_coefficients().get("gmsl", {}).get("link_tiers", {})
    if name in tiers:
        return float(tiers[name])
    return default


def calculate_link(
    name: str,
    width: int,
    height: int,
    fps: float,
    bpp: float,
    blanking: float | None = None,
    *,
    heartbeat: bool = False,
    encoding: str = "9b10b",
    fec: bool = False,
    pixel_crc: bool = True,
) -> GmslLinkResult:
    """One coax. GMSL3 adds FEC when the user left FEC off."""
    coeffs = get_coefficients().get("gmsl", {})
    if blanking is None:
        blanking = float(coeffs.get("default_blanking", 1.2))
    encoding_name = normalize_encoding(encoding)
    encoding_factor = ENCODINGS[encoding_name]
    bpp_link = max(float(bpp), 9.0)
    crc_bpp = 0.5 if pixel_crc else 0.0
    crc_factor = (bpp_link + crc_bpp) / bpp_link
    fec_factor = FEC if fec else 1.0
    pixels = width * height * fps
    pclk_hz = pixels * (blanking if heartbeat else 1.0)
    after_crc = pclk_hz * (bpp_link + crc_bpp) / 1e6
    after_encoding = after_crc * encoding_factor
    after_packet = after_encoding * PACKET
    link_bw = after_packet * fec_factor
    csi = pixels * blanking * bpp / 1e6

    recs = []
    best = ""
    for tier_name, default_cap, fec_required in _TIER_ORDER:
        cap = _tier_capacity(tier_name, default_cap)
        extra = fec_required and not fec
        bw = link_bw * (FEC if extra else 1.0)
        util = bw / cap if cap else 0.0
        fits = bw <= cap
        recs.append({
            "tier": tier_name,
            "title": TIER_TITLES[tier_name],
            "capacity_mbps": cap,
            "bw_mbps": bw,
            "util": util,
            "fits": fits,
            "fec_extra": extra,
        })
        if fits and not best:
            best = tier_name

    return GmslLinkResult(
        name=name,
        width=width,
        height=height,
        fps=fps,
        bpp=bpp,
        bpp_link=bpp_link,
        blanking=blanking,
        heartbeat=heartbeat,
        encoding=encoding_name,
        encoding_expr=ENCODING_EXPR[encoding_name],
        encoding_factor=encoding_factor,
        crc_bpp=crc_bpp,
        crc_factor=crc_factor,
        packet_factor=PACKET,
        fec_enabled=fec,
        fec_factor=fec_factor,
        pclk_mhz=pclk_hz / 1e6,
        pixel_rate_mbps=pixels * bpp / 1e6,
        after_crc_mbps=after_crc,
        after_encoding_mbps=after_encoding,
        after_packet_mbps=after_packet,
        link_bw_mbps=link_bw,
        csi_mbps=csi,
        recommendations=recs,
        best_fit=best,
    )


def link_options(overrides: dict | None) -> dict:
    """Shared coax and CSI settings. Old encoding_factor / overhead_factor are ignored."""
    overrides = overrides or {}
    phy = normalize_phy(overrides.get("phy"))
    return {
        "heartbeat": _opt_bool(overrides, "heartbeat", False),
        "encoding": normalize_encoding(overrides.get("encoding")),
        "fec": _opt_bool(overrides, "fec", False),
        "pixel_crc": _opt_bool(overrides, "pixel_crc", True),
        "phy": phy,
        "lanes": normalize_lanes(overrides.get("lanes", 4), phy),
    }


def parse_param_string(s: str) -> dict:
    """Parse key=value pairs separated by spaces and/or commas into a dict."""
    import re
    out = {}
    for pair in re.split(r"[,\s]+", s.strip()):
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Expected key=value, got: '{pair}'")
        k, v = pair.split("=", 1)
        k = k.strip()
        v = v.strip()
        lowered = v.lower()
        if lowered in ("true", "false", "yes", "no", "on", "off"):
            out[k] = lowered in ("true", "yes", "on")
            continue
        try:
            if "." in v:
                out[k] = float(v)
            else:
                out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


def load_yaml(path: str | Path) -> tuple[list[dict], dict]:
    """Load a GMSL YAML file. Returns (links, global_overrides)."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    links = data.get("links", [])
    overrides = {k: v for k, v in data.items() if k != "links"}
    return links, overrides


def save_yaml(
    path: str | Path,
    links: list[dict],
    blanking: float,
    *,
    heartbeat: bool = False,
    encoding: str = "9b10b",
    fec: bool = False,
    pixel_crc: bool = True,
    phy: str = "dphy",
    lanes: int = 4,
) -> None:
    """Write the multi-link file the CLI reads."""
    body = {
        "blanking": _whole(blanking),
        "heartbeat": bool(heartbeat),
        "encoding": normalize_encoding(encoding),
        "fec": bool(fec),
        "pixel_crc": bool(pixel_crc),
        "phy": normalize_phy(phy),
        "lanes": normalize_lanes(lanes),
        "links": links,
    }
    Path(path).write_text(
        yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _whole(value: float):
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def build_report_from_links(links_spec: list[dict], overrides: dict | None = None) -> GmslReport:
    """Build a GmslReport from link specs plus optional global overrides."""
    overrides = overrides or {}
    if len(links_spec) > MAX_STREAMS:
        raise ValueError(f"one coax carries at most {MAX_STREAMS} streams")
    options = link_options(overrides)
    results = []
    for spec in links_spec:
        name = spec.get("name", f"LINK{len(results)+1}")
        blanking = spec.get("blanking", overrides.get("blanking"))
        results.append(calculate_link(
            name=name,
            width=int(spec["width"]),
            height=int(spec["height"]),
            fps=float(spec["fps"]),
            bpp=resolve_bpp(spec),
            blanking=blanking,
            heartbeat=options["heartbeat"],
            encoding=options["encoding"],
            fec=options["fec"],
            pixel_crc=options["pixel_crc"],
        ))
    total = sum(link.link_bw_mbps for link in results)
    return GmslReport(
        links=results,
        total_link_bw_mbps=total,
        phy=options["phy"],
        lanes=options["lanes"],
    )
