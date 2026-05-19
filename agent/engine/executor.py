"""Code executor: runs Python scripts in a subprocess with timeout and capture."""

from __future__ import annotations

import os
import signal
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
    """Runs Python scripts in subprocess with timeout. Kills entire process group on timeout."""

    def __init__(self, work_dir: Path, timeout: int = 3600) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._step = 0

    def run(self, code: str, filename: str | None = None) -> ExecutionResult:
        """Write code to a file and execute it. Kills all child processes on timeout."""
        if filename is None:
            filename = f"step_{self._step:03d}.py"
        self._step += 1

        script = self.work_dir / filename
        script.write_text(code)

        start = time.time()
        try:
            proc = subprocess.Popen(
                ["python", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.work_dir),
                preexec_fn=os.setsid,
            )
            stdout, stderr = proc.communicate(timeout=self.timeout)
            return ExecutionResult(
                stdout=stdout[-8000:] if stdout else "",
                stderr=stderr[-8000:] if stderr else "",
                returncode=proc.returncode,
                exec_time=time.time() - start,
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            return ExecutionResult(
                stdout="",
                stderr=f"TimeoutError: Execution exceeded {self.timeout}s",
                returncode=-1,
                exec_time=time.time() - start,
                timed_out=True,
            )
