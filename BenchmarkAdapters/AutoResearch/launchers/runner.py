"""Execute one registered native Agent command against the dev-only broker."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping

from ...contracts import AdapterError, CommandResult, CommandSpec
from ...process import redact_process_output, redact_sensitive_payload
from ...registry import AGENTS
from ..broker import DevBrokerServer
from ..dev_client import declare_current
from ..search import SearchContext, SearchOutcome
from .common import NativeLaunchRequest, build_native_command
from .sandbox import sandbox_native_command


_MODEL_ENVIRONMENT_ALLOWLIST = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "AUTORESEARCH_AISCIENTIST_LLM_FACTORY",
    "AUTORESEARCH_ARBOR_PROVIDER_FACTORY",
    "AUTORESEARCH_CODEX_BASE_URL",
    "AUTORESEARCH_EVOMASTER_LLM_FACTORY",
    "AUTORESEARCH_MODEL",
    "AUTORESEARCH_MODEL_PARAMETERS",
    "AUTORESEARCH_REQUEST_TIMEOUT_SECONDS",
    "AUTORESEARCH_RETRY_POLICY",
    "AUTORESEARCH_MODEL_FACTORY_ROOT",
    "AUTORESEARCH_PROPOSER_COMMAND",
    "CODEX_API_KEY",
    "ALL_PROXY",
    "GPT_BASE_URL",
    "GPT_CHAT_MODEL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "MODEL",
    "NO_PROXY",
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "UPSTREAM_API_KEY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "OPTIMIZATION_ARTIFACT_NAME",
    "OPTIMIZATION_METRIC_DIRECTION",
    "OPTIMIZATION_METRIC_NAME",
    "OPTIMIZATION_NATIVE_COMPONENT",
    "OPTIMIZATION_PROGRAM_NAME",
    "OPTIMIZATION_STATE_NAME",
    "OPTIMIZATION_TASK_INSTRUCTION",
    "OPTIMIZATION_TASK_NAME",
    "BENCHMARK_TASK_SPEC_SHA256",
    "BENCHMARK_TASK_SPEC_TEXT",
}


def _merge_usage(target: dict[str, int], source: Mapping[str, object], *, maximum: bool) -> None:
    aliases = {
        "input": ("input", "input_tokens", "prompt_tokens"),
        "cached_input": ("cached_input", "cached_input_tokens", "cache_read_input_tokens"),
        "cache_write_input": ("cache_write_input", "cache_write_input_tokens"),
        "output": ("output", "output_tokens", "completion_tokens"),
        "reasoning": ("reasoning", "reasoning_tokens", "reasoning_output_tokens"),
    }
    for normalized, names in aliases.items():
        raw = next((source[name] for name in names if name in source), None)
        if not isinstance(raw, (int, float)) or raw < 0:
            continue
        value = int(raw)
        target[normalized] = max(target.get(normalized, 0), value) if maximum else target.get(normalized, 0) + value


def _usage_mappings(value: object):
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            yield usage
        token_usage = value.get("token_usage")
        if isinstance(token_usage, dict):
            yield token_usage
        for child in value.values():
            yield from _usage_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _usage_mappings(child)


def collect_model_usage(
    *,
    stdout: str,
    usage_path: Path,
    native_result: object,
) -> dict[str, int]:
    usage: dict[str, int] = {}
    if usage_path.is_file() and not usage_path.is_symlink():
        for line in usage_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(record, dict):
                _merge_usage(usage, record, maximum=False)
    for mapping in _usage_mappings(native_result):
        _merge_usage(usage, mapping, maximum=True)
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        for mapping in _usage_mappings(payload):
            _merge_usage(usage, mapping, maximum=True)
    return usage


def execute_native_command(command: CommandSpec) -> CommandResult:
    try:
        process = subprocess.Popen(
            command.argv,
            cwd=command.cwd,
            env=command.merged_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(timeout=command.timeout_seconds)
            return_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            remaining, _ = process.communicate()
            stdout = (exc.stdout or "") + remaining
            return_code = 124
    except FileNotFoundError as exc:
        raise AdapterError(f"native Agent executable not found: {command.argv[0]}") from exc
    except OSError as exc:
        raise AdapterError(f"could not run {command.label}: {exc}") from exc
    return CommandResult(
        command=command,
        return_code=return_code,
        stdout=redact_process_output(stdout or "", command.merged_env()),
    )


def _initialize_workspace(
    context: SearchContext,
    workspace: Path,
    contract,
) -> None:
    workspace.mkdir(parents=True, exist_ok=False)
    shutil.copy2(context.baseline_train_path, contract.artifact_path(workspace))
    shutil.copy2(context.program_path, contract.program_path(workspace))
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Autoresearch Supervisor"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "autoresearch-supervisor@invalid.local"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "add", contract.artifact_name, contract.program_name],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "Frozen Autoresearch baseline"], cwd=workspace, check=True)


def _stage_runtime(destination: Path) -> Path:
    package = destination / "BenchmarkAdapters/AutoResearch"
    launchers = package / "launchers"
    launchers.mkdir(parents=True, exist_ok=False)
    for init_path in (destination / "BenchmarkAdapters/__init__.py", package / "__init__.py", launchers / "__init__.py"):
        init_path.parent.mkdir(parents=True, exist_ok=True)
        init_path.write_text("\n", encoding="utf-8")
    source_root = Path(__file__).resolve().parents[1]
    shutil.copy2(source_root.parent / "autonomous_optimization.py", destination / "BenchmarkAdapters/autonomous_optimization.py")
    shutil.copy2(source_root.parent / "contracts.py", destination / "BenchmarkAdapters/contracts.py")
    shutil.copy2(source_root / "dev_client.py", package / "dev_client.py")
    shutil.copy2(source_root / "model_adapters.py", package / "model_adapters.py")
    for name in (
        "proposal.py",
        "ear.py",
        "mlevolve.py",
        "arbor.py",
        "ml_master_2.py",
        "ai_scientist.py",
    ):
        shutil.copy2(source_root / "launchers" / name, launchers / name)
    return destination


class NativeCommandSearchRunner:
    def __init__(
        self,
        *,
        sandbox: bool = False,
        model_environment: Mapping[str, str] | None = None,
        native_step_limit: int | None = None,
        runtime_executables: Mapping[str, Path] | None = None,
        command_builder: Callable[[NativeLaunchRequest], CommandSpec] = build_native_command,
        broker_server_factory: Callable[[object, Path], object] = DevBrokerServer,
    ) -> None:
        self.sandbox = sandbox
        self.model_environment = (
            {key: value for key, value in os.environ.items() if key in _MODEL_ENVIRONMENT_ALLOWLIST}
            if model_environment is None
            else dict(model_environment)
        )
        unexpected = sorted(set(self.model_environment) - _MODEL_ENVIRONMENT_ALLOWLIST)
        if unexpected:
            raise AdapterError(
                f"unsupported Autoresearch model-boundary environment key: {unexpected[0]}"
            )
        if native_step_limit is not None and native_step_limit <= 0:
            raise AdapterError("Autoresearch native step limit must be positive")
        self.native_step_limit = native_step_limit
        self.runtime_executables = {
            agent: path.resolve() for agent, path in dict(runtime_executables or {}).items()
        }
        unexpected_agents = sorted(set(self.runtime_executables) - set(AGENTS))
        if unexpected_agents:
            raise AdapterError(
                f"unknown Autoresearch runtime override Agent: {unexpected_agents[0]}"
            )
        for path in self.runtime_executables.values():
            if not path.is_file() or not os.access(path, os.X_OK):
                raise AdapterError(f"Autoresearch runtime override is not executable: {path}")
        self.command_builder = command_builder
        self.broker_server_factory = broker_server_factory

    def __call__(self, context: SearchContext) -> SearchOutcome:
        from ...autonomous_optimization import task_contract

        context.output_dir.mkdir(parents=True, exist_ok=False)
        workspace = context.output_dir / "workspace"
        contract = task_contract(self.model_environment)
        _initialize_workspace(context, workspace, contract)
        runtime_root = _stage_runtime(context.output_dir / "runtime")
        socket_path = context.output_dir / "capability/dev.sock"
        with self.broker_server_factory(context.broker, socket_path) as server:
            remaining = max(1, int(context.outer_deadline_monotonic - time.monotonic()))
            request = NativeLaunchRequest(
                agent=context.agent,
                workspace=workspace,
                output_dir=context.output_dir / "native",
                socket_path=socket_path,
                token=server.token,
                outer_seed=context.outer_seed,
                timeout_seconds=remaining,
                runtime_root=runtime_root,
                model_environment=self.model_environment,
                native_step_limit=self.native_step_limit,
                runtime_executable=self.runtime_executables.get(context.agent),
            )
            command = self.command_builder(request)
            if self.sandbox:
                command = sandbox_native_command(
                    agent=context.agent,
                    command=command,
                    workspace=workspace,
                    output_dir=request.output_dir,
                    runtime_root=runtime_root,
                    host_socket=socket_path,
                )
            try:
                result = execute_native_command(command)
            except AdapterError as exc:
                if "timed out" not in str(exc).lower():
                    raise
                result = CommandResult(command=command, return_code=124, stdout=str(exc))
            redacted_stdout = result.stdout.replace(server.token, "<capability-token>")
            with (context.output_dir / "native-agent.log").open("x", encoding="utf-8") as handle:
                handle.write(redacted_stdout)
            if context.broker.declared_revision_id is None and request.state_path.is_file():
                declare_current(str(socket_path), server.token, request.state_path)
        native_result_path = request.output_dir / "native-result.json"
        native_result = None
        if native_result_path.is_file():
            native_result = json.loads(native_result_path.read_text(encoding="utf-8"))
            if native_result.get("native_component") != context.native_backend:
                raise AdapterError("native Agent result component differs from registered backend")
            native_result = redact_sensitive_payload(native_result, command.merged_env())
        token_usage = collect_model_usage(
            stdout=result.stdout,
            usage_path=request.output_dir / "model-usage.jsonl",
            native_result=native_result,
        )
        payload = {
            "native_component": context.native_backend,
            "command": [
                redact_process_output(
                    value.replace(server.token, "<capability-token>"),
                    command.merged_env(),
                )
                for value in command.argv
            ],
            "label": command.label,
            "return_code": result.return_code,
            "declared_revision_id": context.broker.declared_revision_id,
            "native_process_executed": True,
            "native_result": native_result,
            "token_usage": token_usage,
        }
        result_path = context.output_dir / "native-dispatch.json"
        with result_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
        timed_out = result.return_code == 124
        return SearchOutcome(
            native_component=context.native_backend,
            declared_revision_id=context.broker.declared_revision_id,
            completed=result.return_code in {0, 124} and context.broker.declared_revision_id is not None,
            timed_out=timed_out,
            failure_reason=(
                None
                if result.return_code in {0, 124}
                else f"native Agent command exited with code {result.return_code}"
            ),
            metadata=payload,
        )


__all__ = ["NativeCommandSearchRunner", "collect_model_usage", "execute_native_command"]
