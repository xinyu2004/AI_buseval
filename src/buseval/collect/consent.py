"""Ask before an administrator command that will require a password.

Nothing is stored. Each time a password will be asked, the caller shows
the notice again. A live sudo ticket never reaches this function.
"""
from __future__ import annotations

import sys


def confirm(text: str) -> bool:
    """Print `text` and ask. Decline, EOF, or a non-interactive stdin is no."""
    print(text, file=sys.stderr)
    if not sys.stdin.isatty():
        return False
    try:
        answer = input("Continue and enter the administrator password? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}
