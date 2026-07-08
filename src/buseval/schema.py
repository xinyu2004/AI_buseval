"""Pydantic data models for topology and bandwidth estimates."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class Master(BaseModel):
    """A peripheral master that issues DDR traffic."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    enabled: bool = True
    params: dict = Field(default_factory=dict)
    verify: bool = False  # True when value is an unverified default


class PipelineStage(BaseModel):
    """One stage inside a pipeline (e.g. one ISP block)."""

    name: str
    read_factor: float = 1.0
    write_factor: float = 1.0


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
        "Optional; when set, the pipeline inherits width/height/fps/bpp/count "
        "from each source and recomputes the frame stream (no sync/cap — each "
        "source keeps its native fps). String form is backward-compatible. "
        "Null = pipeline uses its own params.width/height/fps.",
    )
    params: dict = Field(default_factory=dict)
    stages: list[PipelineStage] = Field(default_factory=list)
    verify: bool = False


class DDRChannel(BaseModel):
    """A DDR channel (one controller / one rank).

    Two ways to declare bandwidth:
      1. Physical params (recommended): controller_mt_s × controller_width_bits
         for the chip's DDR IP, and module_mt_s × module_width_bits × module_groups
         for the external DRAM. The engine computes:
           controller_peak = controller_mt_s × controller_width_bits / 8
           module_peak     = module_mt_s     × module_width_bits     / 8 × module_groups
           effective_peak  = min(controller_peak, module_peak)
         (MT/s already includes DDR double data rate; ÷8 bits→bytes)
      2. Legacy shorthand: theoretical_peak_mbps (assumes controller = module = this value).

    Both cannot be mixed; if physical params are present, theoretical_peak is ignored.
    """

    name: str
    # Legacy shorthand
    theoretical_peak_mbps: Optional[float] = None
    # Physical params — chip DDR controller (SoC internal, fixed)
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
        description="Fraction of available bandwidth usable for reads; "
        "None means symmetric (0.5).",
    )


class Topology(BaseModel):
    """Full chip topology: masters + pipelines + DDR channels."""

    masters: list[Master] = Field(default_factory=list)
    pipelines: list[Pipeline] = Field(default_factory=list)
    ddr_channels: list[DDRChannel] = Field(default_factory=list)
    alert_thresholds: dict = Field(
        default_factory=lambda: {"yellow": 0.6, "red": 0.8}
    )


class BandwidthEstimate(BaseModel):
    """Output of one estimator invocation."""

    read_bw_mbps: float = 0.0
    write_bw_mbps: float = 0.0
    breakdown: dict = Field(default_factory=dict)
    dominant_factor: str = ""
    assumptions: list[str] = Field(default_factory=list)
