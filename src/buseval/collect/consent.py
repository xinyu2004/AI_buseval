"""Remember that the user accepted one administrator action.

The file is a flag under `~/.config/buseval`. It does not store a password.
"""
from __future__ import annotations

import sys
from pathlib import Path


def consent_dir() -> Path:
    return Path.home() / ".config" / "buseval"


def accepted(name: str) -> bool:
    return (consent_dir() / name).is_file()


def remember(name: str) -> None:
    directory = consent_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("accepted\n", encoding="utf-8")


def confirm(text: str, name: str) -> bool:
    """Print `text` and ask once. A later call with the same `name` stays quiet."""
    if accepted(name):
        return True
    print(text, file=sys.stderr)
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("Continue and enter the administrator password? [y/N] ")
    except EOFError:
        return False
    if answer.strip().lower() not in {"y", "yes"}:
        return False
    remember(name)
    return True
