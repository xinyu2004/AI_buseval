"""Run one command as administrator.

`sudo -n true` tells whether this terminal still has a sudo ticket.
No ticket: show the notice, then sudo asks for the password.
A live ticket skips both the notice and the password.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from .consent import confirm


def password_required() -> bool:
    """True when the next sudo on this terminal will ask for a password."""
    if os.geteuid() == 0 or shutil.which("sudo") is None:
        return False
    proc = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
    return proc.returncode != 0


def run_privileged(
    argv: list[str],
    *,
    notice: str,
) -> subprocess.CompletedProcess[str]:
    """Run `argv` as root. Decline of the notice raises `cancelled`."""
    if os.geteuid() != 0:
        if shutil.which("sudo") is None:
            raise RuntimeError("sudo is not installed or not on PATH")
        if password_required() and not confirm(notice):
            raise RuntimeError("cancelled")
        argv = ["sudo", *argv]
    return subprocess.run(argv, capture_output=True, text=True)
