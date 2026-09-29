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


def save_topology(topology: Topology, path: str | Path) -> None:
    """Write a topology YAML that `buseval predict -t` can read."""
    data = topology.model_dump(exclude_none=True)
    data.pop("ui_lang", None)
    Path(path).write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
