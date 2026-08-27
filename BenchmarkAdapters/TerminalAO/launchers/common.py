"""Dispatch to per-Agent native repository optimization loops."""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ...arbor_thin import write_arbor_config
from ...contracts import AdapterError, CommandSpec, UnsupportedAdapterError, require_file
from ...registry import AGENTS, ROOT, TERMINAL_AO_UNSUPPORTED_REASONS
from ...task_specs import task_spec_text
from ...thin_registry import (
    require_clean_upstream_source,
    require_thin_support,
    terminal_ao_agents,
)


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
    model_parameters: Mapping[str, object]
    request_timeout_seconds: int | None
    retry_policy: Mapping[str, object]
    agent_variant: str = "default"
    model_base_url: str = "http://127.0.0.1:6200/v1"
    editable_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    sandboxed: bool = False

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
    return task_spec_text("terminal-bench-ao") + "\n\nDEV capability: " + request.dev_command


def _arbor_instruction() -> str:
    return (
        task_spec_text("terminal-bench-ao")
        + "\n\nThe official project plugin injects the host-owned B_dev evaluator "
        "into each executor worktree. Held-out evaluation is unavailable. Use Arbor's "
        "native tree, merge, promotion, selection, and stop behavior, and leave the "
        "Agent-selected final candidate on the trunk."
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
            # Formal AO wraps this command in the host's Bubblewrap jail, where
            # bwrap itself is not reachable, so Codex's own sandbox cannot start
            # and every command fails before execution. Documented flag for
            # externally sandboxed environments; the outer jail still limits the
            # candidate, its runtime, the dev socket and the relay socket.
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            request.model,
            "-c",
            'model_provider="benchmark_relay"',
            "-c",
            'model_providers.benchmark_relay.name="Benchmark relay"',
            "-c",
            f'model_providers.benchmark_relay.base_url="{request.model_base_url}"',
            "-c",
            'model_providers.benchmark_relay.wire_api="responses"',
            "-c",
            "model_providers.benchmark_relay.requires_openai_auth=true",
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
            "--permission-mode",
            "bypassPermissions",
            _instruction(request),
        ),
        cwd=request.candidate_dir,
        timeout_seconds=request.timeout_seconds,
        label="Claude Code native Terminal AO loop",
    )


def _arbor(
    request: NativeAOLaunchRequest,
    *,
    require_upstream: bool,
) -> CommandSpec:
    if require_upstream:
        require_clean_upstream_source("arbor")
    executable = require_file(AGENTS["arbor"].execution_path / ".venv/bin/arbor", "Arbor CLI")
    socket_path = Path("/capability/dev.sock") if request.sandboxed else request.dev_socket
    eval_argv = [
        "/usr/bin/python3",
        str(request.dev_client_path.resolve()),
        "--socket",
        str(socket_path),
        "--operation",
        "evaluate-dev",
        "--candidate-root",
        "{cwd}",
    ]
    for relative in request.editable_paths:
        eval_argv.extend(("--editable", relative))
    config_path = write_arbor_config(
        request.launcher_output_dir / "arbor-thin-config.yaml",
        model=request.model,
        base_url=request.model_base_url,
        model_parameters=request.model_parameters,
        eval_command=shlex.join(eval_argv) + ' --token "$TERMINAL_AO_DEV_TOKEN"',
        metric_direction="maximize",
        protected_paths=request.protected_paths,
        required_outputs=request.editable_paths,
    )
    argv = [
        str(executable),
        "run",
        _arbor_instruction(),
        "--cwd",
        str(request.candidate_dir),
        "--yes",
        "--yes-cwd",
        str(request.candidate_dir),
        "--workspace-dir",
        str(request.launcher_output_dir / "arbor-session"),
        "--config",
        str(config_path),
        "--interaction-mode",
        "auto",
        "--no-followup",
        "--no-webui",
    ]
    if not require_upstream:
        argv.extend(("--max-cycles", "24", "--max-turns", "96"))
    return CommandSpec(
        argv=tuple(argv),
        cwd=request.candidate_dir,
        env={
            "PYTHONPATH": str(AGENTS["arbor"].install_path / "src"),
            "SEED": str(request.seed),
            "TERMINAL_AO_DEV_TOKEN": request.dev_token,
        },
        timeout_seconds=request.timeout_seconds,
        label="Arbor native coordinator Terminal AO loop",
    )


def _python_native(request: NativeAOLaunchRequest, module: str, python: Path, label: str) -> CommandSpec:
    source_roots = {
        "ear": AGENTS["ear"].install_path,
        "ai-scientist": AGENTS["ai-scientist"].install_path / "src",
    }
    python_path = f"{ROOT}:{source_roots[request.agent]}"
    require_file(python, f"{label} Python")
    return CommandSpec(
        argv=(
            str(python),
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
        env={
            "PYTHONPATH": python_path,
            "PYTHONHASHSEED": str(request.seed),
            "TERMINAL_OUTER_MODEL_PARAMETERS": json.dumps(
                request.model_parameters, sort_keys=True
            ),
            "TERMINAL_OUTER_REQUEST_TIMEOUT_SECONDS": (
                ""
                if request.request_timeout_seconds is None
                else str(request.request_timeout_seconds)
            ),
            "TERMINAL_OUTER_RETRY_POLICY": json.dumps(
                request.retry_policy, sort_keys=True
            ),
        },
        timeout_seconds=request.timeout_seconds,
        label=label,
    )


def build_native_ao_command(request: NativeAOLaunchRequest) -> CommandSpec:
    if request.agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {request.agent}")
    if request.agent not in terminal_ao_agents():
        raise UnsupportedAdapterError(
            f"{request.agent} does not participate in Terminal-Bench AO: "
            f"{TERMINAL_AO_UNSUPPORTED_REASONS[request.agent]}"
        )
    variant = require_thin_support(
        request.agent, "terminal-bench-ao", request.agent_variant
    )
    if request.agent == "codex":
        return _codex(request)
    if request.agent == "claude-code":
        return _claude(request)
    if request.agent == "arbor":
        if variant is not None and variant.key != "arbor-benchmark-patched":
            raise AdapterError(f"unexpected Arbor variant: {variant.key}")
        command = _arbor(request, require_upstream=variant is None)
        if variant is None:
            return command
        return CommandSpec(
            argv=command.argv,
            cwd=command.cwd,
            env=command.env,
            timeout_seconds=command.timeout_seconds,
            label="Arbor benchmark-patched Terminal AO loop",
        )
    modules = {
        "ear": (
            "BenchmarkAdapters.TerminalAO.launchers.ear",
            ROOT / "BenchmarkAdapters/environments/mle/ear/.venv/bin/python",
            "EAR native graph/Thompson Terminal AO loop",
        ),
        "ai-scientist": (
            "BenchmarkAdapters.TerminalAO.launchers.ai_scientist",
            ROOT / "baselines/AiScientist/.venv/bin/python",
            "AiScientist native subagent Terminal AO loop",
        ),
    }
    if request.agent == "ai-scientist" and variant is None:
        raise AdapterError(f"unreachable original {request.agent} Terminal AO dispatch")
    module, python, label = modules[request.agent]
    if variant is not None:
        label = {
            "ai-scientist-terminal-variant": (
                "AiScientist TerminalTaskSubagent variant"
            ),
        }.get(variant.key, label)
    return _python_native(request, module, python, label)


__all__ = ["NativeAOLaunchRequest", "build_native_ao_command"]
