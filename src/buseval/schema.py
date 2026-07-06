"""Pydantic data models for topology and bandwidth estimates."""
from __future__ import annotations

from typing import Literal, Optional

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
    params: dict = Field(default_factory=dict)
    stages: list[PipelineStage] = Field(default_factory=list)
    verify: bool = False


class DDRChannel(BaseModel):
    """A DDR channel (one controller / one rank)."""

    name: str
    theoretical_peak_mbps: float
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
