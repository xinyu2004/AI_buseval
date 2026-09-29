"""Resolve the image geometry each node actually uses.

Predict copies a master's width/height/fps/bpp/count into a downstream pipeline
at calculation time. This module does that walk first, so lint and the canvas
can show the inherited size before any estimator runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Topology
from .graph import normalize_source, topo_sort_pipelines

_GEOM_KEYS = ("width", "height", "fps", "bpp", "count")


@dataclass
class StreamInfo:
    name: str
    own: dict = field(default_factory=dict)
    sources: list[tuple[str, dict]] = field(default_factory=list)
    uses_upstream_mbps: bool = False

    @property
    def effective(self) -> dict:
        """Geometry a viewer should read on the node.

        The first source that has a picture wins. A pipeline source still
        carries that upstream picture, even when the estimator later uses the
        upstream's megabytes per second instead of these dimensions.
        """
        for _name, geom in self.sources:
            if has_geometry(geom):
                return geom
        return self.own


def has_geometry(geom: dict) -> bool:
    return all(k in geom and geom[k] not in (None, "") for k in ("width", "height", "fps"))


def geometry_of(params: dict) -> dict:
    out = {}
    for key in _GEOM_KEYS:
        if key in params and params[key] not in (None, ""):
            out[key] = params[key]
    return out


def same_geometry(a: dict, b: dict) -> bool:
    if not has_geometry(a) or not has_geometry(b):
        return False
    for key in ("width", "height", "fps"):
        if float(a[key]) != float(b[key]):
            return False
    return True


def format_geometry(geom: dict) -> str:
    if not has_geometry(geom):
        return ""
    fps = _trim(geom["fps"])
    text = f"{int(float(geom['width']))}×{int(float(geom['height']))} @{fps}"
    if "bpp" in geom:
        text += f" {_trim(geom['bpp'])}bpp"
    count = geom.get("count", 1)
    if count not in (None, "", 1) and float(count) != 1:
        text += f" ×{_trim(count)}"
    return text


def resolve_streams(topology: Topology) -> dict[str, StreamInfo]:
    infos: dict[str, StreamInfo] = {}
    masters = {m.name: m for m in topology.masters if m.enabled}
    for master in masters.values():
        infos[master.name] = StreamInfo(name=master.name, own=geometry_of(master.params))

    enabled = [p for p in topology.pipelines if p.enabled]
    try:
        order = topo_sort_pipelines(enabled)
    except ValueError:
        order = enabled

    for pipe in order:
        own = geometry_of(pipe.params)
        sources: list[tuple[str, dict]] = []
        uses_mbps = False
        for src_name in normalize_source(pipe.source):
            if src_name in masters:
                sources.append((src_name, geometry_of(masters[src_name].params)))
            elif src_name in infos:
                upstream = infos[src_name]
                sources.append((src_name, dict(upstream.effective)))
                uses_mbps = True
        infos[pipe.name] = StreamInfo(
            name=pipe.name,
            own=own,
            sources=sources,
            uses_upstream_mbps=uses_mbps,
        )
    return infos


def _trim(value) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number)
