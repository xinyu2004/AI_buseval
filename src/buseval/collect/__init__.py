"""Platform collectors. Each adapter writes the same meas.json shape."""
from .runner import collect_dir, load_manifest, run_collect

__all__ = ["collect_dir", "load_manifest", "run_collect"]
