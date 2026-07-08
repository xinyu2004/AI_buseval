"""GMSL link bandwidth calculator.

Formula:
  link_bw = width × height × fps × bpp × blanking × encoding_factor × overhead_factor

blanking / encoding_factor / overhead_factor come from _coefficients.yaml (gmsl section).
blanking can be overridden per YAML file (top-level) or per CLI param string.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..estimators.registry import get_coefficients


@dataclass
class GmslLinkResult:
    name: str
    width: int
    height: int
    fps: float
    bpp: float
    blanking: float
    encoding_factor: float
    overhead_factor: float
    # breakdown
    pixel_rate_mbps: float       # w×h×fps×bpp
    after_blanking_mbps: float   # × blanking
    after_encoding_mbps: float   # × encoding
    link_bw_mbps: float          # × overhead (final)
    # recommendation
    recommendations: list[dict] = field(default_factory=list)  # [{tier, capacity, util, fits}]
    best_fit: str = ""


@dataclass
class GmslReport:
    links: list[GmslLinkResult] = field(default_factory=list)
    total_link_bw_mbps: float = 0.0

    def to_dict(self) -> dict:
        return {
            "links": [_link_to_dict(l) for l in self.links],
            "total_link_bw_mbps": round(self.total_link_bw_mbps, 4),
            "summary": _summary(self.links),
        }


def _link_to_dict(l: GmslLinkResult) -> dict:
    return {
        "name": l.name,
        "width": l.width,
        "height": l.height,
        "fps": l.fps,
        "bpp": l.bpp,
        "blanking": l.blanking,
        "encoding_factor": l.encoding_factor,
        "overhead_factor": l.overhead_factor,
        "pixel_rate_mbps": round(l.pixel_rate_mbps, 4),
        "after_blanking_mbps": round(l.after_blanking_mbps, 4),
        "after_encoding_mbps": round(l.after_encoding_mbps, 4),
        "link_bw_mbps": round(l.link_bw_mbps, 4),
        "recommendations": l.recommendations,
        "best_fit": l.best_fit,
    }


def _summary(links: list[GmslLinkResult]) -> dict:
    if not links:
        return {}
    coeffs = get_coefficients().get("gmsl", {})
    tiers = coeffs.get("link_tiers", {})
    total = sum(l.link_bw_mbps for l in links)
    max_bw = max(l.link_bw_mbps for l in links)
    # aggregate best fit: smallest tier that fits the TOTAL (all links share the aggregate)
    agg_best = ""
    for tier_name in ("gmsl1", "gmsl2", "gmsl3"):
        cap = float(tiers.get(tier_name, 0))
        if total <= cap:
            agg_best = tier_name
            break
    return {
        "link_count": len(links),
        "total_link_bw_mbps": round(total, 4),
        "total_link_bw_gbps": round(total / 1000, 4),
        "max_link_bw_mbps": round(max_bw, 4),
        "aggregate_best_fit": agg_best,
        "tier_summary": {
            name: {
                "capacity_mbps": cap,
                "total_util": round(total / cap, 4) if cap else 0,
                "max_link_util": round(max_bw / cap, 4) if cap else 0,
                "fits_all": all(l.link_bw_mbps <= cap for l in links),
                "fits_aggregate": total <= cap,
            }
            for name, cap in tiers.items()
        },
    }


def calculate_link(
    name: str,
    width: int,
    height: int,
    fps: float,
    bpp: float,
    blanking: float | None = None,
    encoding_factor: float | None = None,
    overhead_factor: float | None = None,
) -> GmslLinkResult:
    """Calculate GMSL link bandwidth for one link."""
    coeffs = get_coefficients().get("gmsl", {})
    blanking = blanking if blanking is not None else float(coeffs.get("default_blanking", 1.2))
    enc = encoding_factor if encoding_factor is not None else float(coeffs.get("encoding_factor", 1.15))
    oh = overhead_factor if overhead_factor is not None else float(coeffs.get("overhead_factor", 1.067))

    pixel_rate = width * height * fps * bpp / 1e6     # Mbps (was bps, now ÷1e6)
    after_blank = pixel_rate * blanking
    after_enc = after_blank * enc
    link_bw = after_enc * oh

    tiers = coeffs.get("link_tiers", {})
    recs = []
    best = ""
    for tier_name in ("gmsl1", "gmsl2", "gmsl3"):
        cap = float(tiers.get(tier_name, 0))
        util = link_bw / cap if cap else 0
        fits = link_bw <= cap
        recs.append({
            "tier": tier_name,
            "capacity_mbps": cap,
            "util": round(util, 4),
            "fits": fits,
        })
        if fits and not best:
            best = tier_name

    return GmslLinkResult(
        name=name,
        width=width,
        height=height,
        fps=fps,
        bpp=bpp,
        blanking=blanking,
        encoding_factor=enc,
        overhead_factor=oh,
        pixel_rate_mbps=pixel_rate,
        after_blanking_mbps=after_blank,
        after_encoding_mbps=after_enc,
        link_bw_mbps=link_bw,
        recommendations=recs,
        best_fit=best,
    )


def parse_param_string(s: str) -> dict:
    """Parse key=value pairs separated by spaces and/or commas into a dict.

    Supports:
      'width=1920 height=1080 fps=30 bpp=12'      (space-separated)
      'width=1920,height=1080,fps=30,bpp=12'      (comma-separated, legacy)
      'width=1920 height=1080, fps=30, bpp=12'    (mixed)
    """
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
        # numeric coercion
        try:
            if "." in v:
                out[k] = float(v)
            else:
                out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


def load_yaml(path: str | Path) -> tuple[list[dict], dict]:
    """Load a GMSL YAML file. Returns (links, global_overrides).

    YAML structure:
      blanking: 1.25          # optional global override
      encoding_factor: 1.15   # optional
      links:
        - {name: CAM_FRONT, width: 1920, height: 1080, fps: 30, bpp: 12}
        - ...
    """
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    links = data.get("links", [])
    overrides = {k: v for k, v in data.items() if k != "links"}
    return links, overrides


def build_report_from_links(links_spec: list[dict], overrides: dict | None = None) -> GmslReport:
    """Build a GmslReport from a list of link specs + optional global overrides."""
    overrides = overrides or {}
    results = []
    for spec in links_spec:
        name = spec.get("name", f"LINK{len(results)+1}")
        r = calculate_link(
            name=name,
            width=int(spec["width"]),
            height=int(spec["height"]),
            fps=float(spec["fps"]),
            bpp=float(spec["bpp"]),
            blanking=spec.get("blanking", overrides.get("blanking")),
            encoding_factor=overrides.get("encoding_factor"),
            overhead_factor=overrides.get("overhead_factor"),
        )
        results.append(r)
    total = sum(r.link_bw_mbps for r in results)
    return GmslReport(links=results, total_link_bw_mbps=total)
