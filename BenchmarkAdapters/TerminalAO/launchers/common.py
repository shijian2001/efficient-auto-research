"""Dispatch to per-Agent native repository optimization loops."""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...contracts import AdapterError, CommandSpec, require_file
from ...registry import AGENTS, ROOT


@dataclass(frozen=True)
class NativeAOLaunchRequest:
    agent: str
    candidate_dir: Path
    launcher_output_dir: Path
    dev_client_path: Path
    dev_socket: Path
    dev_token: str
    model: str
    seed: int
    timeout_seconds: int

    @property
    def dev_command(self) -> str:
        argv = (
            str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
            str(self.dev_client_path.resolve()),
            "--socket",
            str(self.dev_socket.resolve()),
            "--token",
            self.dev_token,
        )
        return shlex.join(argv)


def _instruction(request: NativeAOLaunchRequest) -> str:
    return (
        "Optimize this frozen Harbor terminus-2 harness for held-out Terminal-Bench performance. "
        "Only edit terminus_2.py, terminus_json_plain_parser.py, terminus_xml_plain_parser.py, "
        "tmux_session.py, or files under templates/. Run the following host-owned command whenever "
        f"you need structured DEV-only feedback: {request.dev_command}. The command returns only "
        "aggregate dev statistics. Hidden test tasks and test evaluation are unavailable during "
        "search. Keep the best replayable harness in this workspace; do not create symlinks."
    )


def _codex(request: NativeAOLaunchRequest) -> CommandSpec:
    executable = shutil.which("codex")
    if executable is None:
        raise AdapterError("Codex CLI is not installed")
    return CommandSpec(
        argv=(
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--model",
            request.model,
            "-c",
            "model_reasoning_effort=high",
            _instruction(request),
        ),
        cwd=request.candidate_dir,
        timeout_seconds=request.timeout_seconds,
        label="Codex native Terminal AO loop",
    )


def _claude(request: NativeAOLaunchRequest) -> CommandSpec:
    executable = shutil.which("claude")
    if executable is None:
        raise AdapterError("Claude Code CLI is not installed")
    return CommandSpec(
        argv=(
            executable,
            "--print",
            "--no-session-persistence",
            "--model",
            request.model,
            "--effort",
            "high",
            "--permission-mode",
            "bypassPermissions",
            _instruction(request),
        ),
        cwd=request.candidate_dir,
        timeout_seconds=request.timeout_seconds,
        label="Claude Code native Terminal AO loop",
    )


def _arbor(request: NativeAOLaunchRequest) -> CommandSpec:
    executable = require_file(AGENTS["arbor"].install_path / ".venv/bin/arbor", "Arbor CLI")
    return CommandSpec(
        argv=(
            str(executable),
            "run",
            _instruction(request),
            "--cwd",
            str(request.candidate_dir),
            "--yes",
            "--yes-cwd",
            str(request.candidate_dir),
            "--workspace-dir",
            str(request.launcher_output_dir / "arbor-session"),
            "--max-cycles",
            "24",
            "--max-turns",
            "96",
            "--interaction-mode",
            "auto",
            "--no-followup",
            "--no-webui",
        ),
        cwd=request.candidate_dir,
        env={"ARBOR_MODEL": request.model, "SEED": str(request.seed)},
        timeout_seconds=request.timeout_seconds,
        label="Arbor native coordinator Terminal AO loop",
    )


def _python_native(request: NativeAOLaunchRequest, module: str, python: Path, label: str) -> CommandSpec:
    source_roots = {
        "ear": AGENTS["ear"].install_path,
        "mlevolve": AGENTS["mlevolve"].install_path,
        "ml-master-2": AGENTS["ml-master-2"].install_path,
        "ai-scientist": AGENTS["ai-scientist"].install_path / "src",
    }
    python_path = f"{ROOT}:{source_roots[request.agent]}"
    return CommandSpec(
        argv=(
            str(require_file(python, f"{label} Python")),
            "-m",
            module,
            "--workspace",
            str(request.candidate_dir),
            "--output-dir",
            str(request.launcher_output_dir),
            "--dev-command",
            request.dev_command,
            "--model",
            request.model,
            "--seed",
            str(request.seed),
            "--timeout",
            str(request.timeout_seconds),
        ),
        cwd=ROOT,
        env={"PYTHONPATH": python_path, "PYTHONHASHSEED": str(request.seed)},
        timeout_seconds=request.timeout_seconds,
        label=label,
    )


def build_native_ao_command(request: NativeAOLaunchRequest) -> CommandSpec:
    if request.agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {request.agent}")
    if request.agent == "codex":
        return _codex(request)
    if request.agent == "claude-code":
        return _claude(request)
    if request.agent == "arbor":
        return _arbor(request)
    modules = {
        "ear": (
            "BenchmarkAdapters.TerminalAO.launchers.ear",
            ROOT / "BenchmarkAdapters/environments/mle/ear/.venv/bin/python",
            "EAR native graph/Thompson Terminal AO loop",
        ),
        "mlevolve": (
            "BenchmarkAdapters.TerminalAO.launchers.mlevolve",
            ROOT / "BenchmarkAdapters/environments/agents/mlevolve/.venv/bin/python",
            "MLEvolve native search Terminal AO loop",
        ),
        "ml-master-2": (
            "BenchmarkAdapters.TerminalAO.launchers.ml_master_2",
            ROOT / "baselines/EvoMaster/.venv/bin/python",
            "ML-Master 2 native EvoMaster Terminal AO loop",
        ),
        "ai-scientist": (
            "BenchmarkAdapters.TerminalAO.launchers.ai_scientist",
            ROOT / "baselines/AiScientist/.venv/bin/python",
            "AiScientist native subagent Terminal AO loop",
        ),
    }
    module, python, label = modules[request.agent]
    return _python_native(request, module, python, label)


__all__ = ["NativeAOLaunchRequest", "build_native_ao_command"]
