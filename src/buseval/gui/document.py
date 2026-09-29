"""Topology edits shared by the canvas. No Qt imports, so tests can call this directly."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..engine.graph import find_cycle, normalize_source
from ..engine.predictor import PredictionResult
from ..engine.stream import format_geometry, has_geometry, resolve_streams
from .i18n import t
from ..schema import DDRChannel, Master, Pipeline, PipelineStage, Topology

# Left-stripe colors. Types that may share a wire use distant hues.
# Image path: CSI → ISP → Display → DSI, and CSI/ISP also feed NPU and VENC.
TYPE_COLORS = {
    "mipi_csi": "#2471A3",
    "mipi_dsi": "#4A235A",
    "isp": "#1E8449",
    "npu": "#8E44AD",
    "venc": "#E67E22",
    "vdec": "#117A65",
    "gpu": "#2980B9",
    "display": "#C2185B",
    "can": "#5D6D7E",
    "can_dbc": "#5D6D7E",
    "usb": "#C0392B",
    "eth": "#2C3E50",
    "spi": "#6E2C00",
    "flash": "#7D6608",
    "ddr": "#1F4E79",
}

_SCHEMA_PATH = Path(__file__).with_name("param_schema.yaml")
# Continuous rank ramp. t = 1 is magenta, then purple, blue-violet, sky blue,
# and a pale cyan that ends near white. Green, yellow, and red stay on the DDR card.
_RANK_STOPS = (
    (1.0, 322.0, 0.78, 0.50),
    (0.8, 292.0, 0.64, 0.56),
    (0.6, 258.0, 0.55, 0.62),
    (0.4, 208.0, 0.50, 0.70),
    (0.2, 196.0, 0.32, 0.84),
    (0.0, 196.0, 0.06, 0.96),
)


def load_param_schema() -> dict:
    return yaml.safe_load(_SCHEMA_PATH.read_text(encoding="utf-8")) or {}


def type_color(type_name: str) -> str:
    return TYPE_COLORS.get(type_name, "#7F8C8D")


def ddr_card_text(channel) -> str:
    """Available bandwidth on the canvas DDR card. The spec stays in the dialog."""
    from ..engine.margin import _compute_peaks

    if channel.controller_mt_s is None or channel.module_mt_s is None:
        return t("ddr_unset")
    _ctrl, _mod, effective, _bottleneck = _compute_peaks(channel)
    available = effective * float(channel.efficiency)
    return f"{available:,.0f} MB/s"


def node_captions(topology: Topology) -> dict[str, str]:
    """One line per enabled node: inherited resolution, or the peripheral's own rate."""
    streams = resolve_streams(topology)
    captions = {}
    for node in iter_nodes(topology):
        if not node.enabled:
            continue
        info = streams.get(node.name)
        captions[node.name] = _caption(node, info)
    return captions


def _caption(node, info) -> str:
    params = node.params or {}
    if info is not None and info.sources:
        # DSI does not read DDR again; every other picture input does.
        via = "" if node.type == "mipi_dsi" else " via DDR"
        parts = []
        for src_name, geom in info.sources:
            if has_geometry(geom):
                parts.append(f"{src_name}{via} {format_geometry(geom)}")
            else:
                parts.append(f"{src_name}{via}")
        line = " + ".join(parts)
        own = format_geometry(info.own)
        if own and info.sources and not any(
            has_geometry(geom) and format_geometry(geom) == own for _n, geom in info.sources
        ):
            line = f"{line} · {t('own_level')} {own}" if line else f"{t('own_level')} {own}"
        if line:
            return line
    if info is not None and has_geometry(info.effective):
        return format_geometry(info.effective)
    if node.type == "usb":
        util = params.get("util_pct")
        util_s = f" × {float(util):.0%}" if util is not None else ""
        return f"USB{params.get('version', '')}{util_s}"
    if node.type == "eth":
        util = params.get("util_pct")
        util_s = f" × {float(util):.0%}" if util is not None else ""
        return f"{params.get('link_gbps', '')} Gbps{util_s}"
    if node.type == "flash":
        return (
            f"{params.get('type', '')} {t('read')} {params.get('seq_read_mbps', '')}"
            f" / {t('write')} {params.get('seq_write_mbps', '')}"
        )
    if node.type == "spi":
        return f"{params.get('clock_mhz', '')} MHz"
    if str(node.type).startswith("can"):
        return f"{params.get('bitrate_mbps', '')} Mbps"
    return node.type


def palette_groups(schema: dict | None = None) -> list[tuple[str, list[str]]]:
    schema = schema if schema is not None else load_param_schema()
    order = ["外设", "Pipeline"]
    buckets: dict[str, list[str]] = {name: [] for name in order}
    for type_name, spec in schema.items():
        buckets.setdefault(spec.get("group", "外设"), []).append(type_name)
    return [(name, types) for name, types in buckets.items() if types]


def new_topology() -> Topology:
    return Topology(
        ddr_channels=[DDRChannel(name="DDR0")],
    )


def iter_nodes(topology: Topology):
    yield from topology.masters
    yield from topology.pipelines


def find_node(topology: Topology, name: str):
    for node in iter_nodes(topology):
        if node.name == name:
            return node
    return None


def unique_name(topology: Topology, prefix: str) -> str:
    taken = {node.name for node in iter_nodes(topology)}
    n = 0
    while True:
        candidate = f"{prefix}{n}"
        if candidate not in taken:
            return candidate
        n += 1


def _with_pos(model, x: float, y: float):
    data = model.model_dump()
    data["ui_x"] = float(x)
    data["ui_y"] = float(y)
    return model.__class__.model_validate(data)


def node_pos(model) -> tuple[float, float] | None:
    extra = model.__pydantic_extra__ or {}
    x = extra.get("ui_x")
    y = extra.get("ui_y")
    if x is None or y is None:
        data = model.model_dump()
        x = data.get("ui_x")
        y = data.get("ui_y")
    if x is None or y is None:
        return None
    return float(x), float(y)


def _parse_port(value) -> tuple[str, float] | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    side, _, raw = value.partition(":")
    if side not in ("top", "right", "bottom", "left"):
        return None
    try:
        return side, float(raw)
    except ValueError:
        return None


def ddr_port(model) -> tuple[str, float] | None:
    """Where this module's DDR wire leaves, if the user has dragged the dot."""
    extra = model.__pydantic_extra__ or {}
    value = extra.get("ui_ddr_port")
    if value is None:
        value = model.model_dump().get("ui_ddr_port")
    return _parse_port(value)


def ddr_peer_port(channel, client_name: str) -> tuple[str, float] | None:
    peers = channel.ui_peer_port or {}
    return _parse_port(peers.get(client_name))


def set_ddr_port(topology: Topology, name: str, side: str, u: float) -> None:
    node = find_node(topology, name)
    if node is None:
        return
    data = node.model_dump()
    data["ui_ddr_port"] = f"{side}:{float(u):.4f}"
    _replace(topology, name, node.__class__.model_validate(data))


def set_ddr_peer_port(topology: Topology, channel_name: str, client_name: str, side: str, u: float) -> None:
    for index, channel in enumerate(topology.ddr_channels):
        if channel.name != channel_name:
            continue
        peers = dict(channel.ui_peer_port or {})
        peers[client_name] = f"{side}:{float(u):.4f}"
        topology.ddr_channels[index] = channel.model_copy(update={"ui_peer_port": peers})
        return


def set_node_pos(topology: Topology, name: str, x: float, y: float) -> None:
    node = find_node(topology, name)
    if node is not None:
        _replace(topology, name, _with_pos(node, x, y))
        return
    for index, channel in enumerate(topology.ddr_channels):
        if channel.name == name:
            topology.ddr_channels[index] = channel.model_copy(
                update={"ui_x": float(x), "ui_y": float(y)}
            )
            return


def add_block(topology: Topology, type_name: str, x: float, y: float, schema: dict | None = None) -> str:
    schema = schema if schema is not None else load_param_schema()
    spec = schema[type_name]
    name = unique_name(topology, spec.get("prefix", type_name.upper()))
    params = dict(spec.get("defaults") or {})
    if spec.get("kind") == "pipeline":
        stages = [PipelineStage(**row) for row in spec.get("stages") or []]
        node = Pipeline(
            name=name,
            type=type_name,
            params=params,
            stages=stages,
            mode=spec.get("default_mode", "serial"),
        )
    else:
        node = Master(name=name, type=type_name, params=params)
    node = _with_pos(node, x, y)
    if isinstance(node, Pipeline):
        topology.pipelines.append(node)
    else:
        topology.masters.append(node)
    return name


def _replace(topology: Topology, name: str, updated) -> None:
    for i, node in enumerate(topology.masters):
        if node.name == name:
            topology.masters[i] = updated
            return
    for i, node in enumerate(topology.pipelines):
        if node.name == name:
            topology.pipelines[i] = updated
            return


def update_node(topology: Topology, name: str, *, new_name: str, enabled: bool, params: dict, mode: str | None, stages: list[dict] | None) -> str | None:
    node = find_node(topology, name)
    if node is None:
        return t("err_missing_node")
    if new_name != name and find_node(topology, new_name) is not None:
        return t("err_name_taken").format(name=new_name)
    data = node.model_dump()
    data["name"] = new_name
    data["enabled"] = enabled
    data["params"] = params
    if isinstance(node, Pipeline):
        if mode:
            data["mode"] = mode
        if stages is not None:
            data["stages"] = stages
    updated = node.__class__.model_validate(data)
    _replace(topology, name, updated)
    if new_name != name:
        _rename_sources(topology, name, new_name)
    return None


def _set_source(node, srcs: list[str]) -> None:
    if not srcs:
        node.source = None
    elif len(srcs) == 1:
        node.source = srcs[0]
    else:
        node.source = srcs


def _rename_sources(topology: Topology, old: str, new: str) -> None:
    for node in list(topology.pipelines) + list(topology.masters):
        srcs = normalize_source(node.source)
        if old not in srcs:
            continue
        _set_source(node, [new if s == old else s for s in srcs])


def try_connect(topology: Topology, src_name: str, dst_name: str) -> str | None:
    src = find_node(topology, src_name)
    dst = find_node(topology, dst_name)
    if src is None or dst is None:
        return t("err_ends")
    if isinstance(dst, Master):
        # ETH is the one peripheral that can take a pipeline upstream.
        if dst.type != "eth" or not isinstance(src, Pipeline):
            return t("err_no_inport")
        dst.source = src_name
        return None
    if not isinstance(dst, Pipeline):
        return t("err_no_inport")
    srcs = normalize_source(dst.source)
    if src_name in srcs:
        return None
    if dst.type == "isp" and srcs:
        return t("err_isp_multi").format(name=dst.name, src=srcs + [src_name])
    proposed = srcs + [src_name]
    previous = dst.source
    dst.source = proposed[0] if len(proposed) == 1 else proposed
    cycle = find_cycle(topology.pipelines)
    if cycle:
        dst.source = previous
        return t("err_cycle")
    return None


def remove_edge(topology: Topology, src_name: str, dst_name: str) -> None:
    dst = find_node(topology, dst_name)
    if not isinstance(dst, (Pipeline, Master)):
        return
    _set_source(dst, [s for s in normalize_source(dst.source) if s != src_name])


def remove_node(topology: Topology, name: str) -> None:
    topology.masters = [m for m in topology.masters if m.name != name]
    topology.pipelines = [p for p in topology.pipelines if p.name != name]
    for node in list(topology.pipelines) + list(topology.masters):
        _set_source(node, [s for s in normalize_source(node.source) if s != name])


def auto_layout(topology: Topology) -> None:
    """Linked nodes left to right. Enabled nodes with no wire sit on a lower row."""
    masters = [m for m in topology.masters if m.enabled]
    pipes_list = [p for p in topology.pipelines if p.enabled]
    pipes = {p.name: p for p in pipes_list}
    referenced: set[str] = set()
    has_source: set[str] = set()
    for pipe in pipes_list:
        srcs = normalize_source(pipe.source)
        if srcs:
            has_source.add(pipe.name)
            referenced.update(srcs)
    linked = referenced | has_source
    memo: dict[str, int] = {}

    def depth(name: str, stack: tuple[str, ...] = ()) -> int:
        if name in memo:
            return memo[name]
        if name in stack or name not in pipes:
            return 0
        srcs = [s for s in normalize_source(pipes[name].source) if s in pipes]
        memo[name] = 0 if not srcs else 1 + max(depth(s, stack + (name,)) for s in srcs)
        return memo[name]

    row = 0
    for master in masters:
        if master.name not in linked or node_pos(master) is not None:
            continue
        _replace(topology, master.name, _with_pos(master, 40, 40 + row * 110))
        row += 1
    columns: dict[int, int] = {}
    main_rows = max(row, 1)
    for pipe in pipes_list:
        if pipe.name not in linked or node_pos(pipe) is not None:
            continue
        col = depth(pipe.name) + 1
        slot = columns.get(col, 0)
        columns[col] = slot + 1
        main_rows = max(main_rows, slot + 1)
        _replace(topology, pipe.name, _with_pos(pipe, 40 + col * 280, 40 + slot * 110))
    bypass_y = 40 + main_rows * 110 + 50
    slot = 0
    for node in list(masters) + pipes_list:
        if node.name in linked or node_pos(node) is not None:
            continue
        _replace(topology, node.name, _with_pos(node, 40 + slot * 280, bypass_y))
        slot += 1


def edge_flows(topology: Topology, prediction: PredictionResult) -> dict[tuple[str, str], float]:
    """Megabytes per second on each source → destination wire."""
    items = {it.name: it for it in prediction.items}
    flows: dict[tuple[str, str], float] = {}
    for pipe in topology.pipelines:
        if not pipe.enabled or pipe.name not in items:
            continue
        breakdown = items[pipe.name].breakdown if isinstance(items[pipe.name].breakdown, dict) else {}
        srcs = normalize_source(pipe.source)
        per_source = breakdown.get("sources")
        used = False
        if isinstance(per_source, list):
            for src in per_source:
                if isinstance(src, dict) and src.get("name") and "input_mbps" in src:
                    flows[(str(src["name"]), pipe.name)] = float(src["input_mbps"])
                    used = True
        if used:
            continue
        if len(srcs) != 1:
            continue
        mbps = breakdown.get("frame_stream_mbps", breakdown.get("source_input_mbps", breakdown.get("carried_mbps")))
        if mbps is None:
            continue
        flows[(srcs[0], pipe.name)] = float(mbps)
    for master in topology.masters:
        if not master.enabled or master.name not in items:
            continue
        srcs = normalize_source(master.source)
        if len(srcs) != 1:
            continue
        breakdown = items[master.name].breakdown if isinstance(items[master.name].breakdown, dict) else {}
        mbps = breakdown.get("source_input_mbps")
        if mbps is None:
            continue
        flows[(srcs[0], master.name)] = float(mbps)
    return flows


def traffic_shares(prediction: PredictionResult) -> dict[str, float]:
    """Share of total read+write. Disabled items are absent from prediction.items."""
    total = sum(it.read_bw_mbps + it.write_bw_mbps for it in prediction.items)
    if total <= 0:
        return {it.name: 0.0 for it in prediction.items}
    return {it.name: (it.read_bw_mbps + it.write_bw_mbps) / total for it in prediction.items}


def rank_levels(values: dict) -> dict:
    """Equal color steps by distinct value. The largest is 1, the smallest is 0.

    Two modules with the same value share one step, so the ladder has one fewer color.
    """
    if not values:
        return {}
    unique = sorted({float(v) for v in values.values()}, reverse=True)
    count = len(unique)
    step = {
        value: (1.0 if count == 1 else 1.0 - index / (count - 1))
        for index, value in enumerate(unique)
    }
    return {key: step[float(value)] for key, value in values.items()}


def color_t(share: float, max_share: float) -> float:
    if max_share <= 0:
        return 0.0
    return max(0.0, min(1.0, share / max_share))


def _hsl_to_rgb(hue: float, sat: float, light: float) -> tuple[int, int, int]:
    hue = hue % 360.0
    sat = max(0.0, min(1.0, sat))
    light = max(0.0, min(1.0, light))
    chroma = (1.0 - abs(2.0 * light - 1.0)) * sat
    sector = hue / 60.0
    x = chroma * (1.0 - abs(sector % 2.0 - 1.0))
    if sector < 1:
        red, green, blue = chroma, x, 0.0
    elif sector < 2:
        red, green, blue = x, chroma, 0.0
    elif sector < 3:
        red, green, blue = 0.0, chroma, x
    elif sector < 4:
        red, green, blue = 0.0, x, chroma
    elif sector < 5:
        red, green, blue = x, 0.0, chroma
    else:
        red, green, blue = chroma, 0.0, x
    match = light - chroma / 2.0
    return tuple(int(round((channel + match) * 255)) for channel in (red, green, blue))


def fill_rgb(t: float) -> tuple[int, int, int]:
    """Place t on the rank ramp. 1 is magenta, 0 is near white, and the hue shifts along the way."""
    t = max(0.0, min(1.0, t))
    upper = _RANK_STOPS[0]
    lower = _RANK_STOPS[-1]
    for stop, nxt in zip(_RANK_STOPS, _RANK_STOPS[1:]):
        if t <= stop[0] and t >= nxt[0]:
            upper, lower = stop, nxt
            break
    span = upper[0] - lower[0] or 1.0
    mix = (t - lower[0]) / span
    hue, sat, light = (
        lower[i] + (upper[i] - lower[i]) * mix for i in range(1, 4)
    )
    return _hsl_to_rgb(hue, sat, light)


def text_is_white(t: float) -> bool:
    red, green, blue = fill_rgb(t)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) < 140


def ddr_level_rgb(occupancy: float, yellow: float, red: float) -> tuple[int, int, int]:
    """Bright green, yellow, or red for the DDR card. 0.6 and 0.8 are the defaults."""
    if occupancy >= red:
        return (0xF0, 0x8A, 0x80)
    if occupancy >= yellow:
        return (0xF6, 0xD3, 0x65)
    return (0x7D, 0xDE, 0x96)
