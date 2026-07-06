"""Estimators package: imports builtins to register them on import."""
from .registry import Estimator, register, get_estimator, list_estimators, get_coefficients  # noqa: F401
from . import builtins  # noqa: F401  (registers all built-in estimators)
