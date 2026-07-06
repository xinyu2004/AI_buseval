"""Estimator registry: pluggable bandwidth estimators per master type."""
from __future__ import annotations

from typing import Optional

from ..schema import BandwidthEstimate


class Estimator:
    """Base class for all estimators. Subclasses set `type` and implement estimate()."""

    type: str = ""

    def estimate(self, params: dict) -> BandwidthEstimate:  # noqa: D401
        raise NotImplementedError


_REGISTRY: dict[str, Estimator] = {}


def register(type_name: str):
    """Decorator: register an Estimator subclass instance under `type_name`."""

    def _deco(cls):
        inst = cls()
        inst.type = type_name
        _REGISTRY[type_name] = inst
        return cls

    return _deco


def get_estimator(type_name: str) -> Estimator:
    if type_name not in _REGISTRY:
        raise ValueError(
            f"Unknown estimator type: '{type_name}'. "
            f"Known: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[type_name]


def list_estimators() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_coefficients() -> dict:
    """Load the tunable coefficients file. Cached after first call."""
    global _COEFFS
    if _COEFFS is None:
        import yaml
        from pathlib import Path

        p = Path(__file__).parent / "_coefficients.yaml"
        with open(p) as f:
            _COEFFS = yaml.safe_load(f) or {}
    return _COEFFS


_COEFFS: Optional[dict] = None
