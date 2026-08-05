"""Path guard — block agent access to private test data directories."""

from __future__ import annotations

import os
import re

_BLOCKED_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:^|[/\\])private[/\\]data(?:[/\\]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[/\\])prepared[/\\]private(?:[/\\]|$)", re.IGNORECASE),
]


def check_path_allowed(path: str) -> str | None:
    """Return an error message if *path* resolves into a blocked directory, else None."""
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        resolved = path
    for pat in _BLOCKED_PATH_PATTERNS:
        if pat.search(resolved):
            return (
                "Access to private test data is blocked. "
                "Use only the public data in data/ for your experiments."
            )
    return None


def check_command_allowed(command: str) -> str | None:
    """Return an error message if *command* references a blocked data path, else None."""
    for pat in _BLOCKED_PATH_PATTERNS:
        if pat.search(command):
            return (
                "Access to private test data is blocked. "
                "Use only the public data in data/ for your experiments."
            )
    return None
