"""Run a platform adapter. The adapter may be a shell script, Python, or C."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from ..engine.compare import load_measurement


def collect_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "collectors"


def load_manifest() -> dict:
    path = collect_dir() / "manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("platforms") or {}


def run_collect(platform: str, output: str | Path, extra_args: list[str] | None = None) -> dict:
    platforms = load_manifest()
    if platform not in platforms:
        known = ", ".join(sorted(platforms)) or "(none)"
        raise ValueError(f"Unknown collect platform '{platform}'. Known: {known}")
    script = collect_dir() / platforms[platform]["run"]
    if not script.exists():
        raise FileNotFoundError(f"Collector script not found: {script}")
    out = Path(output)
    cmd = ["sh", str(script), "-o", str(out), *(extra_args or [])]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "collect failed").strip()
        raise RuntimeError(detail)
    return load_measurement(out)


def probe_platform(platform: str) -> str:
    platforms = load_manifest()
    if platform not in platforms:
        raise ValueError(f"Unknown collect platform '{platform}'")
    script = collect_dir() / platforms[platform]["run"]
    proc = subprocess.run(["sh", str(script), "--probe"], capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise RuntimeError(text.strip() or "probe failed")
    return text
