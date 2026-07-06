"""YAML / JSON topology loader."""
from __future__ import annotations

from pathlib import Path

import yaml

from .schema import Topology


def load_topology(path: str | Path) -> Topology:
    """Load a topology YAML/JSON file and validate via pydantic."""
    p = Path(path)
    with open(p) as f:
        data = yaml.safe_load(f)
    return Topology.model_validate(data)


def load_topology_from_dict(data: dict) -> Topology:
    return Topology.model_validate(data)
