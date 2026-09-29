"""Pydantic data models for topology and bandwidth estimates."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Master(BaseModel):
    """A peripheral master that issues DDR traffic."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    enabled: bool = True
    # ETH can take one pipeline upstream (for example VENC). Other masters leave this empty.
    source: Optional[Union[str, list[str]]] = None
    params: dict = Field(default_factory=dict)


class PipelineStage(BaseModel):
    """One ISP stage.

    format is the picture this stage writes. Empty means the input format is kept.
    width and height are the output size. Empty means the input size is kept.
    read_factor and write_factor are extra passes of that picture, not a format change.
    """

    name: str
    read_factor: float = 1.0
    write_factor: float = 1.0
    format: Optional[str] = None
    bpp: Optional[float] = None          # used when format is custom
    width: Optional[int] = None
    height: Optional[int] = None


class Pipeline(BaseModel):
    """An internal pipeline such as ISP / NPU / GPU."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    mode: Literal["serial", "parallel"] = "serial"
    enabled: bool = True
    source: Optional[Union[str, list[str]]] = Field(
        default=None,
        description="Name of a master (e.g. CSI0) or a list of masters (e.g. "
        "[CSI0, CSI1]) whose image dimensions this pipeline consumes as input. "
        "Optional; when set, the pipeline inherits width/height/fps/bpp "
        "from each source and recomputes the frame stream (no sync/cap — each "
        "source keeps its native fps). ISP does not inherit count. "
        "String form is backward-compatible. "
        "Null = pipeline uses its own params.width/height/fps.",
    )
    params: dict = Field(default_factory=dict)
    stages: list[PipelineStage] = Field(default_factory=list)


class DDRChannel(BaseModel):
    """One DRAM. Effective peak is the slower of the chip controller and the onboard module.

    controller_peak = controller_mt_s × controller_width_bits / 8 × controller_groups
    module_peak     = module_mt_s × module_width_bits / 8 × module_groups
    effective_peak  = min(controller_peak, module_peak)

    MT/s already includes DDR. A missing width means 32.
    """

    name: str
    # Chip DDR controller (SoC internal, fixed once specified)
    controller_mt_s: Optional[float] = None
    controller_width_bits: Optional[int] = None
    controller_groups: int = 1                # e.g., 4×32-bit controller = 128-bit effective
    controller_type: Optional[str] = None    # LPDDR4 / LPDDR4X / LPDDR5 / DDR4 etc. (display only)
    # Physical params — external DRAM module (board design choice)
    module_mt_s: Optional[float] = None
    module_width_bits: Optional[int] = None
    module_groups: int = 1
    module_type: Optional[str] = None        # display only
    # Common
    efficiency: float = 0.7
    read_write_ratio: Optional[float] = Field(
        default=None,
        description="Unused. Old files may still carry it. Occupancy is (read + write) / available.",
    )
    # Canvas position and where each module's wire meets this card.
    # CLI ignores ui_ keys; the topology hash strips them.
    ui_x: Optional[float] = None
    ui_y: Optional[float] = None
    ui_peer_port: Optional[dict[str, str]] = None


class IspVpac(BaseModel):
    """One ISP on the chip. The name matches the ISP node, for example ISP0."""

    name: str
    mpix_s: float


class Topology(BaseModel):
    """Full chip topology: masters + pipelines + DDR channels."""

    masters: list[Master] = Field(default_factory=list)
    pipelines: list[Pipeline] = Field(default_factory=list)
    ddr_channels: list[DDRChannel] = Field(default_factory=list)
    alert_thresholds: dict = Field(
        default_factory=lambda: {"yellow": 0.6, "red": 0.8}
    )
    # Old files may still carry this. The GUI keeps language outside the topology.
    ui_lang: Optional[str] = None
    # Named chip ISPs. Each node with the same name is checked against that rate.
    isp_vpacs: list[IspVpac] = Field(default_factory=list)


def note(message: str, level: str) -> dict:
    """One estimator assumption. level is red, yellow, or info."""
    return {"message": message, "level": level}


def note_message(item) -> str:
    if isinstance(item, dict):
        return str(item.get("message", ""))
    return str(item)


def note_level(item) -> str:
    if isinstance(item, dict) and item.get("level"):
        return str(item["level"])
    return "yellow"


class BandwidthEstimate(BaseModel):
    """Output of one estimator invocation."""

    read_bw_mbps: float = 0.0
    write_bw_mbps: float = 0.0
    breakdown: dict = Field(default_factory=dict)
    dominant_factor: str = ""
    assumptions: list[dict] = Field(default_factory=list)

    @field_validator("assumptions", mode="before")
    @classmethod
    def _coerce_assumptions(cls, value):
        if not value:
            return []
        out = []
        for item in value:
            if isinstance(item, dict) and "message" in item:
                out.append({"message": str(item["message"]), "level": str(item.get("level") or "yellow")})
            else:
                out.append({"message": str(item), "level": "yellow"})
        return out
