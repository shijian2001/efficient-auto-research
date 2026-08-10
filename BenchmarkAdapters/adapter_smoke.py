"""Low-cost real-entry smoke tests for the 7 Agent × 2 local Adapter cells.

This module never produces a Benchmark score.  A cell passes only after the
real Agent entrypoint, launched against real local Benchmark assets, completes
its first upstream model request through the repository Relay.  The Relay is
limited to one upstream call and the host supervisor then terminates the Agent.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator

from .LLMRelay import RelayProcess, route_command_through_relay
from .MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest, public_task_dir
from .TerminalAO.baseline import BaselineManifest, materialize_baseline
from .TerminalAO.launchers.common import NativeAOLaunchRequest, build_native_ao_command
from .TerminalAO.launchers.sandbox import sandbox_native_ao_command
from .TerminalAO.protocol import TerminalAOProtocol
from .TerminalAO.supervisor import initialize_candidate_repository
from .contracts import AdapterError, CommandSpec, protect_generated_output
from .registry import AGENTS, ROOT
from .thin_registry import UPSTREAM_REVISIONS


AGENT_ORDER = (
    "ear",
    "mlevolve",
    "arbor",
    "codex",
    "claude-code",
    "ml-master-2",
    "ai-scientist",
)

SMOKE_VARIANTS = {
    "mle-bench-lite": {
        "ear": "default",
        "mlevolve": "default",
        "arbor": "arbor-benchmark-patched",
        "codex": "default",
        "claude-code": "default",
        "ml-master-2": "ml-master-2@36a52bc6c42a6b9fd710a41c52f3c3bb948b9ac9",
        "ai-scientist": "ai-scientist@db8cce71cf9668a4946d1eace72290d9e3376164",
    },
    "terminal-bench-ao": {
        "ear": "default",
        "mlevolve": "default",
        "arbor": "arbor@65ffcc8fdf23a64a781940e6a3cfb6369d6d887e",
        "codex": "default",
        "claude-code": "default",
        "ml-master-2": "ml-master-autoresearch-variant",
        "ai-scientist": "ai-scientist-terminal-variant",
    },
}

_CLEAN_SOURCE_AGENTS = {
    "ear": "mle-bench-lite",
    "arbor": "terminal-bench-ao",
    "ai-scientist": "mle-bench-lite",
    "ml-master-2": "mle-bench-lite",
}

_NATIVE_DOCKER_NAMES = {
    "ear": "efficient-auto-research",
    "mlevolve": "MLEvolve",
    "arbor": "Arbor",
}


@dataclass(frozen=True)
class AdapterSmokeRecord:
    benchmark: str
    agent: str
    agent_variant: str
    status: str
    upstream_calls: int
    duration_seconds: float
    command_label: str | None
    process_return_code: int | None
    supervisor_terminated_after_first_request: bool
    score_valid: bool = False
    non_comparable: bool = True
    failure_reason: str | None = None
    telemetry: dict[str, object] | None = None


def _read_telemetry(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _terminate_process_group(process: subprocess.Popen, grace_seconds: float = 10) -> None:
    descendants: set[int] = set()
    parent_map: dict[int, int] = {}
    completed = subprocess.run(
        ["ps", "-eo", "pid=,ppid="],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    for line in completed.stdout.splitlines():
        try:
            pid_text, parent_text = line.split()
            parent_map[int(pid_text)] = int(parent_text)
        except (TypeError, ValueError):
            continue
    frontier = {process.pid}
    while frontier:
        children = {
            pid for pid, parent in parent_map.items() if parent in frontier
        } - descendants
        descendants.update(children)
        frontier = children
    if process.poll() is not None:
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    if descendants:
        time.sleep(0.2)
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_until_first_request(
    command: CommandSpec,
    *,
    token_log_path: Path,
    process_log_path: Path,
    timeout_seconds: int,
) -> tuple[bool, int | None, dict[str, object] | None, str | None]:
    process_log_path.parent.mkdir(parents=True, exist_ok=True)
    with process_log_path.open("x", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            list(command.argv),
            cwd=str(command.cwd),
            env=command.merged_env(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_seconds
        telemetry: dict[str, object] | None = None
        failure_reason = None
        try:
            while time.monotonic() < deadline:
                records = _read_telemetry(token_log_path)
                if records:
                    telemetry = records[0]
                    return_code = process.poll()
                    _terminate_process_group(process)
                    return True, return_code, telemetry, None
                return_code = process.poll()
                if return_code is not None:
                    failure_reason = f"Agent process exited before a model request: {return_code}"
                    return False, return_code, None, failure_reason
                time.sleep(0.2)
            failure_reason = f"No Agent model request within {timeout_seconds}s"
            return False, process.poll(), None, failure_reason
        finally:
            _terminate_process_group(process)


@contextmanager
def _reviewed_source(agent: str, temporary_root: Path) -> Iterator[None]:
    original_spec = AGENTS[agent]
    commit = UPSTREAM_REVISIONS.get(agent)
    if commit is None:
        completed = subprocess.run(
            ["git", "-C", str(original_spec.install_path), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        commit = completed.stdout.strip()
    worktree = temporary_root / f"clean-{agent}"
    exclude_path = temporary_root / f"exclude-{agent}"
    exclude_path.write_text(".venv\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "-C",
            str(original_spec.install_path),
            "worktree",
            "add",
            "--detach",
            str(worktree),
            commit,
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    original_venv = original_spec.install_path / ".venv"
    if original_venv.is_dir():
        (worktree / ".venv").mkdir()
    exclude_environment = "BENCHMARK_ADAPTERS_GIT_EXCLUDES_FILE"
    saved_excludes = os.environ.get(exclude_environment)
    os.environ[exclude_environment] = str(exclude_path)
    AGENTS[agent] = replace(
        original_spec,
        install_path=worktree,
        runtime_path=original_spec.execution_path,
    )
    try:
        yield
    finally:
        AGENTS[agent] = original_spec
        if saved_excludes is None:
            os.environ.pop(exclude_environment, None)
        else:
            os.environ[exclude_environment] = saved_excludes
        subprocess.run(
            [
                "git",
                "-C",
                str(original_spec.install_path),
                "worktree",
                "remove",
                "--force",
                str(worktree),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


@contextmanager
def _source_context(agent: str, benchmark: str, temporary_root: Path) -> Iterator[None]:
    if _CLEAN_SOURCE_AGENTS.get(agent) == benchmark:
        with _reviewed_source(agent, temporary_root):
            yield
    else:
        yield


def _write_ml_master_config(request: MleLiteRequest) -> Path:
    import copy
    import yaml

    template = AGENTS["ml-master-2"].install_path / "configs/ml_master_2/deepseek-v3.2-example.yaml"
    payload = yaml.safe_load(template.read_text(encoding="utf-8"))
    staged_root = request.output_dir / "staged-data"
    public_destination = staged_root / request.competition_id / "prepared/public"
    public_destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(public_task_dir(request), public_destination)
    workspace = request.output_dir / "workspace"
    model_parameters = dict(request.model_parameters)
    payload["competition_id"] = request.competition_id
    payload["data_root"] = str(staged_root.resolve())
    payload["llm"] = {"openai": copy.deepcopy(payload["llm"]["openai"]), "default": "openai"}
    payload["llm"]["openai"].update(
        {
            "model": request.model,
            "temperature": model_parameters.get("temperature", 1),
            "max_tokens": model_parameters.get("max_output_tokens", 1024),
            "timeout": request.request_timeout_seconds or 120,
            "max_retries": 1,
            "use_completion_api": True,
        }
    )
    for configured in payload.get("agents", {}).values():
        if not isinstance(configured, dict):
            continue
        if "llm" in configured:
            configured["llm"] = "openai"
        for prompt_key in ("system_prompt_file", "user_prompt_file"):
            prompt_value = configured.get(prompt_key)
            if isinstance(prompt_value, str) and not Path(prompt_value).is_absolute():
                configured[prompt_key] = str(
                    (
                        AGENTS["ml-master-2"].install_path
                        / "playground/ml_master_2"
                        / prompt_value
                    ).resolve()
                )
        wisdom_value = configured.get("wisdom_file")
        if isinstance(wisdom_value, str) and not Path(wisdom_value).is_absolute():
            configured["wisdom_file"] = str(
                (AGENTS["ml-master-2"].install_path / wisdom_value).resolve()
            )
    payload["session"]["local"].update(
        {
            "working_dir": str(workspace.resolve()),
            "workspace_path": str(workspace.resolve()),
            "gpu_devices": [str(request.gpu_id)],
            "cpu_devices": None,
            "symlinks": {str(public_destination.resolve()): "input"},
        }
    )
    destination = request.output_dir / "ml-master-smoke.yaml"
    destination.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return destination


def _native_docker_token_path(request: MleLiteRequest) -> Path:
    name = _NATIVE_DOCKER_NAMES[request.agent]
    return (
        request.output_dir
        / "relay-telemetry"
        / f"{name}_{request.competition_id}_gpu{request.gpu_id}.jsonl"
    )


def run_mle_smoke(
    agent: str,
    *,
    output_root: Path,
    data_root: Path,
    model: str,
    upstream_base_url: str,
    gpu_id: int,
    timeout_seconds: int,
) -> AdapterSmokeRecord:
    started = time.monotonic()
    variant = SMOKE_VARIANTS["mle-bench-lite"][agent]
    run_dir = protect_generated_output(output_root / "mle-bench-lite" / agent, ROOT)
    with tempfile.TemporaryDirectory(prefix=f"adapter-smoke-{agent}-") as temporary:
        with _source_context(agent, "mle-bench-lite", Path(temporary)):
            request = MleLiteRequest(
                agent=agent,
                competition_id="spooky-author-identification",
                data_root=data_root,
                output_dir=run_dir / "agent-output",
                gpu_id=gpu_id,
                steps=1,
                timeout_seconds=timeout_seconds,
                model=model,
                upstream_base_url=upstream_base_url,
                max_turns=1,
                seed=0,
                model_parameters={
                    "api_mode": "chat",
                    "use_completion_api": True,
                    "max_output_tokens": 1024,
                },
                request_timeout_seconds=timeout_seconds,
                retry_policy={"max_retries": 0},
                agent_variant=variant,
                runtime_image=(
                    "alexgshaw/fix-git:20251031" if agent == "ai-scientist" else None
                ),
                image_pull_policy="never" if agent == "ai-scientist" else None,
                official_llm_profile="gpt-5.4" if agent == "ai-scientist" else None,
            )
            request.output_dir.mkdir(parents=True, exist_ok=True)
            if agent == "ml-master-2":
                request = replace(request, config_path=_write_ml_master_config(request))
            if agent in _NATIVE_DOCKER_NAMES:
                command = MleLiteAdapter(agent).build_command(request)
                smoke_environment = {
                    **dict(command.env),
                    "LLM_MAX_UPSTREAM_CALLS": "1",
                    "LLM_SKIP_UPSTREAM_READY": "1",
                    "LLM_UPSTREAM_PROXY": "",
                }
                if agent == "arbor":
                    smoke_environment["CONTAINER_IMAGE"] = "alexgshaw/fix-git:20251031"
                command = replace(
                    command,
                    env=smoke_environment,
                )
                token_path = _native_docker_token_path(request)
            else:
                socket_path = Path(temporary) / "relay.sock"
                token_path = request.output_dir / "token_usage.jsonl"
                with RelayProcess(
                    agent=f"{agent}-mle-adapter-smoke",
                    log_path=request.output_dir / "relay.log",
                    token_log_path=token_path,
                    unix_socket=socket_path,
                    upstream_base_url=upstream_base_url,
                    model=model,
                    model_parameters=request.model_parameters,
                    retry_policy={"max_retries": 0},
                    check_upstream_ready=False,
                    max_upstream_calls=1,
                ):
                    command = MleLiteAdapter(agent).build_command(
                        replace(request, relay_socket=socket_path)
                    )
                    passed, return_code, telemetry, failure = _run_until_first_request(
                        command,
                        token_log_path=token_path,
                        process_log_path=run_dir / "agent.log",
                        timeout_seconds=timeout_seconds,
                    )
                return AdapterSmokeRecord(
                    benchmark="mle-bench-lite",
                    agent=agent,
                    agent_variant=variant,
                    status="passed" if passed else "failed",
                    upstream_calls=len(_read_telemetry(token_path)),
                    duration_seconds=time.monotonic() - started,
                    command_label=command.label,
                    process_return_code=return_code,
                    supervisor_terminated_after_first_request=passed,
                    failure_reason=failure,
                    telemetry=telemetry,
                )
            passed, return_code, telemetry, failure = _run_until_first_request(
                command,
                token_log_path=token_path,
                process_log_path=run_dir / "agent.log",
                timeout_seconds=timeout_seconds,
            )
            container_name = (
                f"mle-{_NATIVE_DOCKER_NAMES[agent]}-"
                f"{request.competition_id}-gpu{request.gpu_id}"
            )
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return AdapterSmokeRecord(
                benchmark="mle-bench-lite",
                agent=agent,
                agent_variant=variant,
                status="passed" if passed else "failed",
                upstream_calls=len(_read_telemetry(token_path)),
                duration_seconds=time.monotonic() - started,
                command_label=command.label,
                process_return_code=return_code,
                supervisor_terminated_after_first_request=passed,
                failure_reason=failure,
                telemetry=telemetry,
            )


class _CapabilityHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        request = json.loads(self.rfile.readline().decode("utf-8"))
        response = {
            "ok": True,
            "evaluation": {
                "candidate_id": "adapter-smoke-baseline",
                "candidate_digest": "0" * 64,
                "pass_rate": 0.0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "missing_rewards": 0,
                "expected_tasks": 0,
                "smoke_transport_only": True,
                "operation": request.get("operation"),
            },
        }
        self.wfile.write(json.dumps(response, sort_keys=True).encode() + b"\n")


@contextmanager
def _capability_server(socket_path: Path) -> Iterator[str]:
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    server = Server(str(socket_path), _CapabilityHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "adapter-smoke-capability-token"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        socket_path.unlink(missing_ok=True)


def run_terminal_smoke(
    agent: str,
    *,
    output_root: Path,
    protocol_path: Path,
    model: str,
    upstream_base_url: str,
    timeout_seconds: int,
) -> AdapterSmokeRecord:
    started = time.monotonic()
    variant = SMOKE_VARIANTS["terminal-bench-ao"][agent]
    run_dir = protect_generated_output(output_root / "terminal-bench-ao" / agent, ROOT)
    protocol = TerminalAOProtocol.load(protocol_path)
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    candidate = run_dir / "candidate"
    materialize_baseline(protocol.baseline_source, candidate, baseline)
    initialize_candidate_repository(candidate)
    launcher_dir = run_dir / "launcher"
    launcher_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f"terminal-smoke-{agent}-") as temporary:
        temporary_path = Path(temporary)
        with _source_context(agent, "terminal-bench-ao", temporary_path):
            dev_socket = temporary_path / "dev.sock"
            relay_socket = temporary_path / "llm.sock"
            token_path = launcher_dir / "token_usage.jsonl"
            resolver_path = temporary_path / "resolv.conf"
            resolver_path.write_text("nameserver 10.0.2.3\n", encoding="utf-8")
            with _capability_server(dev_socket) as token, RelayProcess(
                agent=f"{agent}-terminal-adapter-smoke",
                log_path=launcher_dir / "relay.log",
                token_log_path=token_path,
                unix_socket=relay_socket,
                upstream_base_url=upstream_base_url,
                model=model,
                model_parameters={
                    "api_mode": "chat",
                    "use_completion_api": True,
                    "max_output_tokens": 1024,
                },
                retry_policy={"max_retries": 0},
                check_upstream_ready=False,
                max_upstream_calls=1,
            ):
                request = NativeAOLaunchRequest(
                    agent=agent,
                    candidate_dir=candidate,
                    launcher_output_dir=launcher_dir,
                    dev_client_path=ROOT / "BenchmarkAdapters/TerminalAO/dev_client.py",
                    dev_socket=dev_socket,
                    dev_token=token,
                    model=model,
                    seed=0,
                    timeout_seconds=timeout_seconds,
                    model_parameters={
                        "api_mode": "chat",
                        "use_completion_api": True,
                        "max_output_tokens": 1024,
                    },
                    request_timeout_seconds=timeout_seconds,
                    retry_policy={
                        "max_retries": 1 if agent == "ml-master-2" else 0
                    },
                    agent_variant=variant,
                    model_base_url="http://127.0.0.1:6200/v1",
                    editable_paths=tuple(protocol.editable_paths),
                    protected_paths=tuple(
                        relative
                        for relative in baseline.files
                        if not any(
                            relative == editable
                            or relative.startswith(editable.rstrip("/") + "/")
                            for editable in protocol.editable_paths
                        )
                    ),
                    sandboxed=True,
                )
                command = route_command_through_relay(
                    build_native_ao_command(request),
                    base_url="http://127.0.0.1:6200/v1",
                    model=model,
                )
                command = sandbox_native_ao_command(
                    agent=agent,
                    command=command,
                    candidate_dir=candidate,
                    launcher_output_dir=launcher_dir,
                    host_dev_socket=dev_socket,
                    host_relay_socket=relay_socket,
                    resolver_path=resolver_path,
                )
                passed, return_code, telemetry, failure = _run_until_first_request(
                    command,
                    token_log_path=token_path,
                    process_log_path=launcher_dir / "agent.log",
                    timeout_seconds=timeout_seconds,
                )
    return AdapterSmokeRecord(
        benchmark="terminal-bench-ao",
        agent=agent,
        agent_variant=variant,
        status="passed" if passed else "failed",
        upstream_calls=len(_read_telemetry(token_path)),
        duration_seconds=time.monotonic() - started,
        command_label=command.label,
        process_return_code=return_code,
        supervisor_terminated_after_first_request=passed,
        failure_reason=failure,
        telemetry=telemetry,
    )


def _write_record(output_root: Path, record: AdapterSmokeRecord) -> None:
    path = output_root / record.benchmark / record.agent / "smoke-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("all", *SMOKE_VARIANTS), default="all")
    parser.add_argument("--agent", choices=("all", *AGENT_ORDER), default="all")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--upstream-base-url", required=True)
    parser.add_argument("--mle-data-root", type=Path, default=ROOT / "mle-bench-lite/data")
    parser.add_argument(
        "--terminal-protocol",
        type=Path,
        default=ROOT / "terminal-bench-2/ao_protocol/protocol.json",
    )
    parser.add_argument("--gpu-id", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    if args.timeout < 1:
        raise SystemExit("--timeout must be positive")
    output_root = protect_generated_output(args.output_root, ROOT)
    benchmarks = tuple(SMOKE_VARIANTS) if args.benchmark == "all" else (args.benchmark,)
    agents = AGENT_ORDER if args.agent == "all" else (args.agent,)
    records = []
    for benchmark in benchmarks:
        for agent in agents:
            try:
                if benchmark == "mle-bench-lite":
                    record = run_mle_smoke(
                        agent,
                        output_root=output_root,
                        data_root=args.mle_data_root,
                        model=args.model,
                        upstream_base_url=args.upstream_base_url,
                        gpu_id=args.gpu_id,
                        timeout_seconds=args.timeout,
                    )
                else:
                    record = run_terminal_smoke(
                        agent,
                        output_root=output_root,
                        protocol_path=args.terminal_protocol,
                        model=args.model,
                        upstream_base_url=args.upstream_base_url,
                        timeout_seconds=args.timeout,
                    )
            except Exception as exc:
                record = AdapterSmokeRecord(
                    benchmark=benchmark,
                    agent=agent,
                    agent_variant=SMOKE_VARIANTS[benchmark][agent],
                    status="failed",
                    upstream_calls=0,
                    duration_seconds=0.0,
                    command_label=None,
                    process_return_code=None,
                    supervisor_terminated_after_first_request=False,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            _write_record(output_root, record)
            records.append(record)
            print(json.dumps(asdict(record), sort_keys=True), flush=True)
    return 0 if all(record.status == "passed" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGENT_ORDER",
    "SMOKE_VARIANTS",
    "AdapterSmokeRecord",
    "run_mle_smoke",
    "run_terminal_smoke",
]
