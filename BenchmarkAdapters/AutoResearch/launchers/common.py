"""Build per-Agent native commands for Autoresearch Architecture Design."""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Mapping

from ...contracts import AdapterError, CommandSpec
from ...autonomous_optimization import task_contract
from ...registry import AGENTS, ROOT


@dataclass(frozen=True)
class NativeLaunchRequest:
    agent: str
    workspace: Path
    output_dir: Path
    socket_path: Path
    token: str
    outer_seed: int
    timeout_seconds: int
    runtime_root: Path
    model_environment: Mapping[str, str] = field(default_factory=dict)
    native_step_limit: int | None = None
    runtime_executable: Path | None = None

    @property
    def state_path(self) -> Path:
        return task_contract(self.model_environment).state_path(self.workspace)

    def client_command(self, operation: str) -> str:
        contract = task_contract(self.model_environment)
        python = Path("/usr/bin/python3")
        argv = (
            str(python),
            str(self.runtime_root / "BenchmarkAdapters/AutoResearch/dev_client.py"),
            operation,
            "--socket",
            str(self.socket_path),
            "--token",
            self.token,
            "--train",
            str(contract.artifact_path(self.workspace)),
            "--state",
            str(self.state_path),
        )
        return shlex.join(argv)


def instruction(request: NativeLaunchRequest) -> str:
    contract = task_contract(request.model_environment)
    canonical = request.model_environment.get(
        "BENCHMARK_TASK_SPEC_TEXT", contract.task_instruction
    )
    return (
        f"Optimize the frozen {contract.task_name} task in this repository. "
        f"{canonical} Do not edit benchmark, data, evaluator, protocol, or "
        "capability files. Obtain host-owned development "
        f"feedback after each serious candidate with: {request.client_command('evaluate-current')}. "
        f"The metric is {contract.metric_name} and {contract.metric_direction} is better. Before "
        f"finishing, leave the chosen {contract.artifact_name} in "
        f"place and declare its evaluated revision with: {request.client_command('declare-current')}. "
        "Held-out seeds and final evaluation are unavailable during search."
    )


def _python_command(request: NativeLaunchRequest, module: str, python: Path, label: str) -> CommandSpec:
    source_root = {
        "ear": ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/agent-source",
        "mlevolve": ROOT
        / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/agent-source",
        "arbor": ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/agent-source",
        "ml-master-2": ROOT
        / "BenchmarkAdapters/environments/agents/ml-master-2-autoresearch/.venv/agent-source",
        "ai-scientist": ROOT
        / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/agent-source/src",
    }[request.agent]
    argv = [
            str(python),
            "-m",
            module,
            "--workspace",
            str(request.workspace),
            "--output-dir",
            str(request.output_dir),
            "--socket",
            str(request.socket_path),
            "--token",
            request.token,
            "--seed",
            str(request.outer_seed),
            "--timeout",
            str(request.timeout_seconds),
        ]
    if request.native_step_limit is not None:
        option = "--max-turns" if request.agent == "arbor" else "--max-steps"
        argv.extend((option, str(request.native_step_limit)))
    python_path = f"{request.runtime_root}:{source_root}"
    factory_root = request.model_environment.get("AUTORESEARCH_MODEL_FACTORY_ROOT")
    if factory_root:
        python_path = f"{factory_root}:{python_path}"
    environment = {
        **request.model_environment,
        "PYTHONPATH": python_path,
        "PYTHONHASHSEED": str(request.outer_seed),
        "AUTORESEARCH_MODEL_USAGE_PATH": str(request.output_dir / "model-usage.jsonl"),
    }
    if request.agent in {"ear", "mlevolve"}:
        environment.setdefault(
            "AUTORESEARCH_PROPOSER_COMMAND",
            shlex.join(
                (
                    str(python),
                    "-m",
                    "BenchmarkAdapters.AutoResearch.model_adapters",
                    "propose",
                )
            ),
        )
    elif request.agent == "arbor":
        environment.setdefault(
            "AUTORESEARCH_ARBOR_PROVIDER_FACTORY",
            "BenchmarkAdapters.AutoResearch.model_adapters:arbor_provider",
        )
    elif request.agent == "ml-master-2":
        environment.setdefault(
            "AUTORESEARCH_EVOMASTER_LLM_FACTORY",
            "BenchmarkAdapters.AutoResearch.model_adapters:evomaster_llm",
        )
    elif request.agent == "ai-scientist":
        environment.setdefault(
            "AUTORESEARCH_AISCIENTIST_LLM_FACTORY",
            "BenchmarkAdapters.AutoResearch.model_adapters:ai_scientist_llm",
        )
    return CommandSpec(
        argv=tuple(argv),
        cwd=request.workspace,
        env=environment,
        timeout_seconds=request.timeout_seconds,
        label=label,
    )


def build_native_command(request: NativeLaunchRequest) -> CommandSpec:
    if request.agent not in AGENTS:
        raise AdapterError(f"unknown Autoresearch Agent: {request.agent}")
    model = request.model_environment.get("AUTORESEARCH_MODEL", "").strip()
    if not model:
        raise AdapterError("Autoresearch launcher requires an explicit model")
    raw_parameters = request.model_environment.get("AUTORESEARCH_MODEL_PARAMETERS", "")
    if not raw_parameters:
        raise AdapterError("Autoresearch launcher requires explicit model parameters")
    try:
        model_parameters = json.loads(raw_parameters)
    except json.JSONDecodeError as exc:
        raise AdapterError("Autoresearch model parameters are invalid JSON") from exc
    if not isinstance(model_parameters, dict) or not model_parameters:
        raise AdapterError("Autoresearch model parameters must be a non-empty object")
    if request.agent == "codex":
        executable = str(request.runtime_executable or shutil.which("codex") or "codex")
        argv = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "workspace-write",
            "--json",
            "--model",
            model,
        ]
        for name, value in sorted(model_parameters.items()):
            argv.extend(("-c", f"{name}={json.dumps(value)}"))
        base_url = request.model_environment.get("AUTORESEARCH_CODEX_BASE_URL")
        if base_url:
            argv.extend(("-c", f"openai_base_url={json.dumps(base_url)}"))
        argv.append(instruction(request))
        return CommandSpec(
            argv=tuple(argv),
            cwd=request.workspace,
            env=request.model_environment,
            timeout_seconds=request.timeout_seconds,
            label="Codex native repository loop",
        )
    if request.agent == "claude-code":
        executable = str(request.runtime_executable or shutil.which("claude") or "claude")
        return CommandSpec(
            argv=(
                executable,
                "--print",
                "--bare",
                "--no-chrome",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--output-format",
                "stream-json",
                "--verbose",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                model,
                "--tools=Bash,Read,Edit,Write",
                instruction(request),
            ),
            cwd=request.workspace,
            env=request.model_environment,
            timeout_seconds=request.timeout_seconds,
            label="Claude Code native repository loop",
        )
    if request.agent == "arbor":
        return _python_command(
            request,
            "BenchmarkAdapters.AutoResearch.launchers.arbor",
            request.runtime_executable
            or ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/bin/python",
            "Arbor native coordinator/executor loop",
        )
    modules = {
        "ear": (
            "BenchmarkAdapters.AutoResearch.launchers.ear",
            ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/bin/python",
            "EAR native G3 KTS loop",
        ),
        "mlevolve": (
            "BenchmarkAdapters.AutoResearch.launchers.mlevolve",
            ROOT
            / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/bin/python",
            "MLEvolve native AgentSearch/UCT loop",
        ),
        "ml-master-2": (
            "BenchmarkAdapters.AutoResearch.launchers.ml_master_2",
            ROOT
            / "BenchmarkAdapters/environments/agents/ml-master-2-autoresearch/.venv/bin/python",
            "ML-Master 2 native EvoMaster workflow",
        ),
        "ai-scientist": (
            "BenchmarkAdapters.AutoResearch.launchers.ai_scientist",
            ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/bin/python",
            "AiScientist native Subagent loop",
        ),
    }
    module, python, label = modules[request.agent]
    if request.runtime_executable is not None:
        python = request.runtime_executable
    return _python_command(request, module, python, label)


__all__ = ["NativeLaunchRequest", "build_native_command", "instruction"]
