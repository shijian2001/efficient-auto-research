"""Harbor 0.20 adapter for the local Terminal-Bench 2 task dataset."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..contracts import (
    AdapterError,
    CommandSpec,
    UnsupportedAdapterError,
    protect_generated_output,
    require_directory,
)
from ..process import DEFAULT_PROXY, relay_client_env
from ..registry import AGENTS, ROOT
from ..security import contains_sensitive_name


TERMINAL_ROOT = ROOT / "terminal-bench-2"
DEFAULT_DATASET_DIR = TERMINAL_ROOT / "datasets" / "terminal-bench-2"
DEFAULT_JOBS_DIR = TERMINAL_ROOT / "jobs"


@dataclass(frozen=True)
class HarborTerminalRequest:
    agent: str
    dataset_dir: Path = DEFAULT_DATASET_DIR
    jobs_dir: Path = DEFAULT_JOBS_DIR
    model: str | None = None
    upstream_base_url: str = ""
    proxy: str = DEFAULT_PROXY
    attempts: int = 1
    concurrency: int = 1
    agent_concurrency: int | None = None
    task_names: tuple[str, ...] = ()
    exclude_task_names: tuple[str, ...] = ()
    agent_kwargs: tuple[str, ...] = ()
    job_name: str | None = None
    max_retries: int = 0
    timeout_multiplier: float = 1.0
    force_build: bool = False
    dry_run: bool = False


class HarborTerminalAdapter:
    """Build a real Harbor job command for one registered Agent."""

    def __init__(self, agent: str):
        if agent not in AGENTS:
            raise AdapterError(f"unknown baseline agent: {agent}")
        self.agent = agent

    def build_command(self, request: HarborTerminalRequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        if not (request.model or "").strip() or not request.upstream_base_url.strip():
            raise AdapterError("Terminal direct smoke requires an explicit model and relay URL")
        if request.attempts < 1 or request.concurrency < 1:
            raise AdapterError("attempts and concurrency must be positive")
        if request.agent_concurrency is not None and not (
            1 <= request.agent_concurrency <= request.concurrency
        ):
            raise AdapterError("agent_concurrency must be between 1 and concurrency")
        if request.max_retries < 0 or request.timeout_multiplier <= 0:
            raise AdapterError("max_retries must be non-negative and timeout_multiplier positive")
        for value in request.agent_kwargs:
            if contains_sensitive_name(value):
                raise AdapterError(
                    "sensitive Agent values must use environment variables, not --agent-kwarg"
                )

        dataset_dir = require_directory(request.dataset_dir, "Terminal-Bench dataset")
        jobs_dir = protect_generated_output(
            request.jobs_dir,
            ROOT,
            create=not request.dry_run,
        )
        spec = AGENTS[self.agent]
        direct_backend = spec.terminal_direct_smoke_backend
        if direct_backend is None or direct_backend.startswith("blocked:"):
            raise UnsupportedAdapterError(
                f"{self.agent} Terminal-Bench native adapter is not ready: "
                f"{spec.terminal_status}"
            )

        harbor_project = spec.terminal_project or TERMINAL_ROOT
        harbor_executable = harbor_project / ".venv" / "bin" / "harbor"
        if not harbor_executable.is_file() or not os.access(harbor_executable, os.X_OK):
            raise AdapterError(f"Harbor executable is not installed: {harbor_executable}")
        if not request.dry_run:
            version = subprocess.run(
                [str(harbor_executable), "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if version.returncode or version.stdout.strip() != "0.20.0":
                raise AdapterError(
                    "Terminal-Bench requires Harbor 0.20.0, got "
                    f"{version.stdout.strip() or version.stderr.strip()}"
                )
        argv = [
            str(harbor_executable),
            "run",
            "--path",
            str(dataset_dir),
            "--agent",
            direct_backend,
            "--jobs-dir",
            str(jobs_dir),
            "--n-attempts",
            str(request.attempts),
            "--n-concurrent",
            str(request.concurrency),
            "--max-retries",
            str(request.max_retries),
            "--timeout-multiplier",
            str(request.timeout_multiplier),
            "--yes",
        ]
        if request.model:
            argv.extend(["--model", request.model])
        if request.agent_concurrency is not None:
            argv.extend(["--n-concurrent-agents", str(request.agent_concurrency)])
        if request.job_name:
            argv.extend(["--job-name", request.job_name])
        if request.force_build:
            argv.append("--force-build")
        for task_name in request.task_names:
            argv.extend(["--include-task-name", task_name])
        for task_name in request.exclude_task_names:
            argv.extend(["--exclude-task-name", task_name])
        for value in request.agent_kwargs:
            argv.extend(["--agent-kwarg", value])

        environment = relay_client_env(
            base_url=request.upstream_base_url,
            proxy=request.proxy,
            model=request.model,
        )
        environment.update(
            {
                "TB2_ROOT": str(TERMINAL_ROOT),
                "HARBOR_VIEWER_JOBS_DIR": str(jobs_dir),
                "PYTHONPATH": str(TERMINAL_ROOT),
            }
        )
        return CommandSpec(
            argv=tuple(argv),
            cwd=TERMINAL_ROOT,
            env=environment,
            label=f"{self.agent} Harbor Terminal-Bench job",
        )


__all__ = [
    "DEFAULT_DATASET_DIR",
    "DEFAULT_JOBS_DIR",
    "HarborTerminalAdapter",
    "HarborTerminalRequest",
    "TERMINAL_ROOT",
    "TerminalDirectSmokeAdapter",
    "TerminalDirectSmokeRequest",
]


TerminalDirectSmokeAdapter = HarborTerminalAdapter
TerminalDirectSmokeRequest = HarborTerminalRequest
