"""Shared contracts for benchmark-specific adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class AdapterError(RuntimeError):
    """Raised when a benchmark adapter cannot satisfy its contract."""


class UnsupportedAdapterError(AdapterError):
    """Raised when an agent has no native backend for a benchmark mode."""


@dataclass(frozen=True)
class CommandSpec:
    """A reproducible command plus only the environment overrides it needs."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None
    label: str = ""

    def merged_env(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(self.env)
        return environment

    def subprocess_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "cwd": str(self.cwd),
            "env": self.merged_env(),
            "text": True,
        }
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        return kwargs


@dataclass(frozen=True)
class CommandResult:
    """The normalized result returned by a shared adapter process."""

    command: CommandSpec
    return_code: int
    stdout: str

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0


def require_directory(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise AdapterError(f"{description} does not exist: {resolved}")
    return resolved


def require_file(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise AdapterError(f"{description} does not exist: {resolved}")
    return resolved


def raise_for_result(result: CommandResult) -> None:
    if not result.succeeded:
        label = result.command.label or "adapter command"
        raise AdapterError(f"{label} exited with code {result.return_code}")


__all__ = [
    "AdapterError",
    "CommandResult",
    "CommandSpec",
    "UnsupportedAdapterError",
    "raise_for_result",
    "require_directory",
    "require_file",
]
