"""Code executor: runs Python scripts in a subprocess with timeout and capture."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecutionResult:
    """Result of executing a Python script."""

    stdout: str
    stderr: str
    returncode: int
    exec_time: float
    timed_out: bool

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Executor:
    """Runs Python scripts in subprocess with timeout."""

    def __init__(self, work_dir: Path, timeout: int = 3600) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._step = 0

    def run(self, code: str, filename: str | None = None) -> ExecutionResult:
        """Write code to a file and execute it."""
        if filename is None:
            filename = f"step_{self._step:03d}.py"
        self._step += 1

        script = self.work_dir / filename
        script.write_text(code)

        start = time.time()
        try:
            proc = subprocess.run(
                ["python", str(script)],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(self.work_dir),
            )
            return ExecutionResult(
                stdout=proc.stdout[-8000:] if proc.stdout else "",
                stderr=proc.stderr[-8000:] if proc.stderr else "",
                returncode=proc.returncode,
                exec_time=time.time() - start,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                stdout="",
                stderr=f"TimeoutError: Execution exceeded {self.timeout}s",
                returncode=-1,
                exec_time=time.time() - start,
                timed_out=True,
            )
