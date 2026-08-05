"""Shared modified Terminal-Bench AO adapter contract."""

from __future__ import annotations

import re
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
    python_executable: str = "python3"
    instruction: str | None = None


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
        data_path = require_file(
            request.dev_data if split == "dev" else request.test_data,
            f"Terminal-Bench {split} data",
        )
        return CommandSpec(
            argv=(
                request.python_executable,
                str(eval_script),
                "--data",
                str(data_path),
            ),
            cwd=harness_dir,
            env={"HARBOR_N_CONCURRENT": str(request.concurrency)},
            timeout_seconds=request.timeout_seconds,
            label=f"Terminal-Bench AO {split} evaluator",
        )

    def build_optimizer_command(self, request: TerminalAoRequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        harness_dir = require_directory(request.harness_dir, "Terminal-Bench harness")
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
        else:
            raise UnsupportedAdapterError(
                f"{self.agent} has terminal mode {mode!r}, but no native repository "
                "tool backend; do not label a direct Harbor solver as Terminal-Bench AO"
            )
        return CommandSpec(
            argv=argv,
            cwd=harness_dir,
            env=environment,
            timeout_seconds=request.timeout_seconds,
            label=f"{self.agent} Terminal-Bench AO optimizer",
        )

    def run_eval(self, request: TerminalAoRequest, split: str) -> float:
        result = run_command(self.build_eval_command(request, split))
        if not result.succeeded:
            raise AdapterError(
                f"Terminal-Bench {split} evaluator exited with code {result.return_code}"
            )
        return parse_pass_rate(result.stdout)


def parse_pass_rate(output: str) -> float:
    ratios = re.findall(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)", output)
    for passed_text, total_text in reversed(ratios):
        passed = int(passed_text)
        total = int(total_text)
        if total > 0 and passed <= total:
            return passed / total

    reward = re.findall(
        r"(?:reward|pass(?:ed)?[_ -]?rate)\s*[:=]\s*"
        r"([01](?:\.\d+)?)",
        output,
        flags=re.IGNORECASE,
    )
    if reward:
        return float(reward[-1])
    raise AdapterError("could not parse a pass rate from Terminal-Bench evaluator output")


__all__ = ["TerminalAoAdapter", "TerminalAoRequest", "parse_pass_rate"]
