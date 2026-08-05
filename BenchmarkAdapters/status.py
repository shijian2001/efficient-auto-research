"""Baseline installation status command."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .registry import AGENTS


def _command_available(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or "/" in command:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def collect_status() -> list[dict[str, object]]:
    records = []
    for spec in AGENTS.values():
        executable = spec.version_command[0]
        installed = spec.install_path.exists() and _command_available(executable)
        version = None
        error = None
        if installed:
            try:
                result = subprocess.run(
                    spec.version_command,
                    cwd=spec.install_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                lines = (result.stdout or result.stderr).strip().splitlines()
                version = lines[0] if lines else None
                if result.returncode:
                    error = f"version command exited {result.returncode}"
            except (OSError, subprocess.SubprocessError) as exc:
                error = str(exc)
        records.append(
            {
                "agent": spec.key,
                "display_name": spec.display_name,
                "installed": installed,
                "install_path": str(spec.install_path),
                "version": version,
                "mle_mode": spec.mle_mode,
                "terminal_mode": spec.terminal_mode,
                "error": error,
            }
        )
    return records


def main() -> None:
    print(json.dumps(collect_status(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
