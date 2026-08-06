"""Shared modified Terminal-Bench AO adapter contract."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..contracts import (
    AdapterError,
    CommandSpec,
    UnsupportedAdapterError,
    require_directory,
    require_file,
)
from ..process import DEFAULT_PROXY, DEFAULT_RELAY_BASE_URL, relay_client_env, run_command
from ..registry import AGENTS, ROOT


@dataclass(frozen=True)
class TerminalAoRequest:
    agent: str
    harness_dir: Path
    eval_script: Path
    dev_data: Path
    test_data: Path
    output_dir: Path
    model: str = "gpt-5.5"
    upstream_base_url: str = DEFAULT_RELAY_BASE_URL
    proxy: str = DEFAULT_PROXY
    concurrency: int = 8
    timeout_seconds: int = 3600
    python_executable: str = "auto"
    instruction: str | None = None
    candidates: int = 3
    max_turns: int = 12
    command_timeout_seconds: int = 120


class TerminalAoAdapter:
    """Shared evaluator and repository-optimization adapter for Terminal-Bench AO."""

    def __init__(self, agent: str):
        if agent not in AGENTS:
            raise AdapterError(f"unknown baseline agent: {agent}")
        self.agent = agent

    def build_eval_command(self, request: TerminalAoRequest, split: str) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        if split not in {"dev", "test"}:
            raise AdapterError("Terminal-Bench AO split must be dev or test")
        harness_dir = require_directory(request.harness_dir, "Terminal-Bench harness")
        eval_script = require_file(request.eval_script, "Terminal-Bench evaluator")
        python_executable = resolve_python_executable(request.python_executable, harness_dir)
        data_path = require_file(
            request.dev_data if split == "dev" else request.test_data,
            f"Terminal-Bench {split} data",
        )
        return CommandSpec(
            argv=(
                sys.executable,
                "-m",
                "BenchmarkAdapters.RepositoryAgent.evaluate",
                "--repository",
                str(harness_dir),
                "--evaluator",
                str(eval_script),
                "--data",
                str(data_path),
                "--python-executable",
                python_executable,
                "--concurrency",
                str(request.concurrency),
                "--timeout",
                str(request.timeout_seconds),
            ),
            cwd=ROOT,
            env={},
            timeout_seconds=request.timeout_seconds,
            label=f"Terminal-Bench AO {split} evaluator",
        )

    def build_optimizer_command(self, request: TerminalAoRequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        harness_dir = require_directory(request.harness_dir, "Terminal-Bench harness")
        python_executable = resolve_python_executable(request.python_executable, harness_dir)
        request.output_dir.resolve().mkdir(parents=True, exist_ok=True)
        instruction = request.instruction or (
            "Improve the terminus-2 harness in this repository for the declared "
            "Terminal-Bench development split. Keep the evaluator and split data immutable, "
            "and leave the best harness changes in the current repository."
        )
        environment = relay_client_env(
            base_url=request.upstream_base_url,
            proxy=request.proxy,
            model=request.model,
        )
        mode = AGENTS[self.agent].terminal_mode
        if mode == "native-repository-cli" and self.agent == "codex":
            argv = (
                "codex",
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--model",
                request.model,
                instruction,
            )
        elif mode == "native-repository-cli" and self.agent == "claude-code":
            argv = (
                "claude",
                "--print",
                "--bare",
                "--no-session-persistence",
                "--model",
                request.model,
                "--permission-mode",
                "bypassPermissions",
                instruction,
            )
        elif mode == "native-repository-ao" and self.agent == "arbor":
            argv = (
                str(ROOT / "baselines" / "Arbor" / ".venv" / "bin" / "arbor"),
                "run",
                instruction,
                "--yes",
                "--yes-cwd",
                str(harness_dir),
                "--interaction-mode",
                "auto",
                "--no-dashboard-input",
                "--no-followup",
                "--no-webui",
            )
        elif mode == "shared-repository-agent":
            eval_script = require_file(request.eval_script, "Terminal-Bench evaluator")
            dev_data = require_file(request.dev_data, "Terminal-Bench dev data")
            test_data = require_file(request.test_data, "Terminal-Bench held-out test data")
            launcher = {
                "ear": "BenchmarkAdapters.RepositoryAgent.launchers.ear",
                "mlevolve": "BenchmarkAdapters.RepositoryAgent.launchers.mlevolve",
                "ml-master-2": "BenchmarkAdapters.RepositoryAgent.launchers.ml_master_2",
                "ai-scientist": "BenchmarkAdapters.RepositoryAgent.launchers.ai_scientist",
            }[self.agent]
            argv = (
                sys.executable,
                "-m",
                launcher,
                "--repository",
                str(harness_dir),
                "--evaluator",
                str(eval_script),
                "--dev-data",
                str(dev_data),
                "--protected-path",
                str(test_data),
                "--output-dir",
                str(request.output_dir.resolve()),
                "--instruction",
                instruction,
                "--model",
                request.model,
                "--base-url",
                request.upstream_base_url,
                "--proxy",
                request.proxy,
                "--candidates",
                str(request.candidates),
                "--max-turns",
                str(request.max_turns),
                "--timeout",
                str(request.timeout_seconds),
                "--command-timeout",
                str(request.command_timeout_seconds),
                "--evaluator-concurrency",
                str(request.concurrency),
                "--python-executable",
                python_executable,
            )
        else:
            raise UnsupportedAdapterError(
                f"{self.agent} has terminal mode {mode!r}, but no native repository "
                "tool backend; do not label a direct Harbor solver as Terminal-Bench AO"
            )
        return CommandSpec(
            argv=argv,
            cwd=ROOT if mode == "shared-repository-agent" else harness_dir,
            env=environment,
            timeout_seconds=request.timeout_seconds,
            label=(
                f"{self.agent} shared-profile Terminal-Bench AO optimizer"
                if mode == "shared-repository-agent"
                else f"{self.agent} Terminal-Bench AO optimizer"
            ),
        )

    def run_eval(self, request: TerminalAoRequest, split: str) -> float:
        result = run_command(self.build_eval_command(request, split))
        if not result.succeeded:
            raise AdapterError(
                f"Terminal-Bench {split} evaluator exited with code {result.return_code}"
            )
        return parse_pass_rate(result.stdout)


def parse_pass_rate(output: str) -> float:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise AdapterError("could not parse a pass rate from empty evaluator output")
    final_line = lines[-1]
    ratio = re.fullmatch(
        r"(?:(?:passed|pass[_ -]?rate|reward)\s*[:=]\s*)?(\d+)\s*/\s*(\d+)",
        final_line,
        flags=re.IGNORECASE,
    )
    if ratio:
        passed_text, total_text = ratio.groups()
        passed = int(passed_text)
        total = int(total_text)
        if total > 0 and passed <= total:
            return passed / total
    reward = re.fullmatch(
        r"(?:reward|pass(?:ed)?[_ -]?rate)\s*[:=]\s*([01](?:\.\d+)?)",
        final_line,
        flags=re.IGNORECASE,
    )
    if reward:
        return float(reward.group(1))
    raise AdapterError(
        "Terminal-Bench evaluator final line must be passed/total or pass_rate: <0..1>"
    )


def resolve_python_executable(value: str, harness_dir: Path) -> str:
    if value == "auto":
        candidates = (
            harness_dir / ".venv/bin/python",
            ROOT / "terminal-bench-2/.venv/bin/python",
        )
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_mode & 0o111:
                return str(candidate.absolute())
        executable = shutil.which("python3")
        if executable:
            return executable
        raise AdapterError("could not find an evaluator Python; pass --python-executable")
    path = Path(value)
    if "/" in value and not path.is_absolute():
        path = (harness_dir / path).absolute()
    if path.is_absolute():
        if not path.is_file() or not path.stat().st_mode & 0o111:
            raise AdapterError(f"evaluator Python is not executable: {path}")
        return str(path)
    executable = shutil.which(value)
    if executable is None:
        raise AdapterError(f"evaluator Python executable not found: {value}")
    return executable


__all__ = [
    "TerminalAoAdapter",
    "TerminalAoRequest",
    "parse_pass_rate",
    "resolve_python_executable",
]
