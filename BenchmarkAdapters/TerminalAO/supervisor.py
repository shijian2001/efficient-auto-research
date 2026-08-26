"""Outer supervisor for the frozen Terminal-Bench 36/53 AO protocol."""

from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from ..artifacts import PublishedArtifact
from ..contracts import AdapterError, require_formal_output_path
from ..formal_contract import ModelTrackConfig
from ..gpu_locks import gpu_allocation
from ..process import run_command
from ..protocol import BenchmarkMode, sha256_file, write_json_exclusive
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from ..registry import AGENTS, ROOT
from ..task_specs import task_spec_digest
from ..LLMRelay import RelayProcess, route_command_through_relay
from .baseline import BaselineManifest, tree_digest
from .dev_server import CandidateDevBroker
from .evaluator import evaluate_harness
from .launchers import (
    NativeAOLaunchRequest,
    build_native_ao_command,
    sandbox_native_ao_command,
)
from .protocol import TerminalAOProtocol
from .revisions import RevisionStore
from .sealed import SealedTestGate
from .split import FrozenSplit


def _git_identity(path: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def build_ao_manifest(
    *,
    protocol: TerminalAOProtocol,
    agent: str,
    seed: int,
    formal: bool = True,
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
    hardware: dict[str, object] | None = None,
) -> RunManifest:
    agent_commit, agent_dirty = _git_identity(AGENTS[agent].install_path)
    adapter_commit, adapter_dirty = _git_identity(ROOT)
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    if formal:
        protocol.require_formal_contract()
        if model_config is None:
            raise AdapterError("formal Terminal AO requires an explicit model track config")
        model_config.validate(formal=True, require_terminal_inner=True)
    outer_run_index = protocol.seeds.index(seed)
    return RunManifest(
        run_id=f"terminal-ao-{agent}-seed-{seed}",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.TERMINAL_AO,
        agent=agent,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        source_dirty=agent_dirty or adapter_dirty,
        task_id="held-out-53",
        seed=seed,
        model=model_config.outer_model_id if model_config else protocol.inner_model,
        reasoning_effort=(
            None
            if model_config is None or model_config.model_parameters.get("reasoning_effort") is None
            else str(model_config.model_parameters["reasoning_effort"])
        ),
        temperature=(
            None
            if model_config is None or model_config.model_parameters.get("temperature") is None
            else float(model_config.model_parameters["temperature"])
        ),
        wall_clock_seconds=protocol.outer_wall_clock_seconds,
        asset_digests=protocol.protocol_asset_digests(),
        hardware=hardware
        or {
            "gpu_type": "RTX 4090",
            "gpus_per_evaluation": 1,
            "max_concurrent_evaluations": protocol.dev_concurrency,
            "dev_concurrency": protocol.dev_concurrency,
        },
        policies={
            "retry": protocol.retry_policy,
            "failure": protocol.failure_policy,
            "artifact": "one dev-selected allowlisted harness revision replayed from frozen baseline",
            "aggregation": "53-task held-out pass rate; missing/error reward is zero",
            "hardware": "48-hour outer wall clock with dev evaluator concurrency 8",
        },
        formal=formal,
        schema_version=2,
        benchmark_id="terminal-bench-ao",
        protocol_version=protocol.protocol_id,
        agent_variant=agent_variant,
        benchmark_commit=protocol.benchmark_source_commit or "",
        model_track_id=model_config.model_track_id if model_config else "non-formal",
        outer_model_id=model_config.outer_model_id if model_config else protocol.inner_model,
        terminal_inner_model_id=(
            model_config.terminal_inner_model_id if model_config else protocol.inner_model
        ),
        relay_base_url=model_config.relay_base_url if model_config else "non-formal://local",
        model_parameters=model_config.model_parameters if model_config else {},
        outer_repetitions=protocol.outer_repetitions,
        outer_run_index=outer_run_index,
        development_seeds=(FrozenSplit.load(protocol.split_path).seed,),
        heldout_seeds=(),
        gpu_type="RTX 4090",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=protocol.dev_concurrency,
        task_spec_sha256=task_spec_digest("terminal-bench-ao"),
        allowed_write_paths=protocol.editable_paths,
        model_config_digest=model_config.digest if model_config else None,
        non_comparable=not formal,
    )


def archive_harness(source: Path, destination: Path) -> PublishedArtifact:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise AdapterError(f"refusing to overwrite final harness archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as raw_handle:
        with tarfile.open(fileobj=raw_handle, mode="w") as archive:
            for path in sorted(source.rglob("*")):
                if path.is_dir() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                    continue
                if not path.is_file() or path.is_symlink():
                    raise AdapterError(f"invalid file in final harness archive: {path}")
                relative = path.relative_to(source).as_posix()
                info = tarfile.TarInfo(relative)
                info.size = path.stat().st_size
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with path.open("rb") as input_handle:
                    archive.addfile(info, input_handle)
    return PublishedArtifact(
        path=destination,
        sha256=sha256_file(destination),
        size_bytes=destination.stat().st_size,
        source_path=source,
    )


def initialize_candidate_repository(candidate: Path) -> None:
    commands = (
        ("git", "init", "-q"),
        ("git", "config", "user.name", "Terminal AO Supervisor"),
        ("git", "config", "user.email", "terminal-ao@invalid.local"),
        ("git", "add", "."),
        ("git", "commit", "-q", "-m", "Frozen terminus-2 baseline"),
    )
    for command in commands:
        completed = subprocess.run(command, cwd=candidate, capture_output=True, text=True, check=False)
        if completed.returncode:
            raise AdapterError(
                f"could not initialize candidate repository ({' '.join(command)}): "
                f"{completed.stderr.strip()}"
            )


def summarize_token_log(path: Path) -> dict[str, int | None]:
    totals = {
        "input": 0,
        "output": 0,
        "cache": 0,
        "reasoning": 0,
        "requests": 0,
        "retries": 0,
    }
    if not path.is_file():
        return {name: None for name in totals}
    complete = {name: True for name in ("input", "output", "cache", "reasoning")}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            for total_name, record_name in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("cache", "cache_tokens"),
                ("reasoning", "reasoning_tokens"),
            ):
                value = record.get(record_name)
                if value is None:
                    complete[total_name] = False
                else:
                    totals[total_name] += int(value)
            totals["requests"] += 1
            totals["retries"] += int(record.get("retries", 0))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid relay token log at line {line_number}: {path}") from exc
    return {
        name: (value if name not in complete or complete[name] else None)
        for name, value in totals.items()
    }


def _run_terminal_ao_once(
    *,
    agent: str,
    protocol: TerminalAOProtocol,
    output_dir: Path,
    seed: int,
    model: str,
    timeout_seconds: int,
    upstream_base_url: str | None = None,
    proxy: str | None = None,
    formal: bool = True,
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
    gpu_ids: tuple[str, ...] = (),
    _hardware: dict[str, object] | None = None,
) -> BenchmarkRunResult:
    if agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {agent}")
    protocol.validate()
    if formal:
        protocol.require_formal_contract()
    if seed not in protocol.seeds:
        raise AdapterError(f"seed {seed} is not registered by the Terminal AO protocol")
    if model_config is None:
        raise AdapterError("Terminal AO requires an explicit model track config")
    model_config.validate(formal=formal, require_terminal_inner=True)
    if upstream_base_url not in {None, model_config.relay_base_url}:
        raise AdapterError("Terminal AO upstream relay override differs from the model track")
    if formal and _hardware is None:
        with gpu_allocation(
            gpu_ids,
            expected_type="RTX 4090",
            gpus_per_evaluation=1,
            max_concurrent_evaluations=protocol.dev_concurrency,
        ) as hardware:
            return _run_terminal_ao_once(
                agent=agent,
                protocol=protocol,
                output_dir=output_dir,
                seed=seed,
                model=model,
                timeout_seconds=timeout_seconds,
                upstream_base_url=upstream_base_url,
                proxy=proxy,
                formal=formal,
                model_config=model_config,
                agent_variant=agent_variant,
                gpu_ids=gpu_ids,
                _hardware=hardware,
            )
    if model != model_config.outer_model_id:
        raise AdapterError("Terminal AO outer model differs from the model track")
    if timeout_seconds != protocol.outer_wall_clock_seconds:
        raise AdapterError("Terminal AO outer timeout differs from the frozen 48-hour budget")
    output_dir = output_dir.resolve()
    if formal:
        require_formal_output_path(output_dir, ROOT)
    if output_dir.exists() or output_dir.is_symlink():
        raise AdapterError(f"Terminal AO output already exists: {output_dir}")
    manifest = build_ao_manifest(
        protocol=protocol,
        agent=agent,
        seed=seed,
        formal=formal,
        model_config=model_config,
        agent_variant=agent_variant,
        hardware=_hardware,
    )
    manifest.validate()
    output_dir.mkdir(parents=True)
    manifest.write(output_dir / "manifest.json")
    started = time.monotonic()
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    store = RevisionStore(
        baseline_source=protocol.baseline_source,
        baseline_manifest=baseline,
        state_dir=output_dir / "revision-state",
    )
    candidate = store.checkout("baseline", "candidate-active")
    initialize_candidate_repository(candidate)
    launcher_result = None
    launcher_error = None
    launcher_timed_out = False
    with tempfile.TemporaryDirectory(prefix="terminal-ao-dev-") as socket_directory:
        broker = CandidateDevBroker(
            protocol=protocol,
            revision_store=store,
            candidate_dir=candidate,
            output_dir=output_dir,
            socket_path=Path(socket_directory) / "broker.sock",
            model_config=model_config,
            gpu_ids=gpu_ids,
        )
        with broker:
            broker.evaluate_current()
            remaining_seconds = max(1, timeout_seconds - int(time.monotonic() - started))
            try:
                relay_socket = (
                    Path(socket_directory) / "llm.sock" if formal else None
                )
                relay = RelayProcess(
                    agent=agent,
                    log_path=output_dir / "launcher/relay.log",
                    token_log_path=output_dir / "launcher/token_usage.jsonl",
                    unix_socket=relay_socket,
                    upstream_base_url=model_config.relay_base_url,
                    upstream_proxy=proxy,
                    model=model_config.outer_model_id,
                    model_parameters=model_config.model_parameters,
                    request_timeout_seconds=model_config.request_timeout_seconds,
                    retry_policy=model_config.retry_policy,
                )
                with relay:
                    local_base_url = (
                        "http://127.0.0.1:6200/v1" if formal else relay.base_url
                    )
                    request = NativeAOLaunchRequest(
                        agent=agent,
                        candidate_dir=candidate,
                        launcher_output_dir=output_dir / "launcher",
                        dev_client_path=Path(__file__).with_name("dev_client.py"),
                        dev_socket=broker.socket_path,
                        dev_token=broker.token,
                        model=model,
                        seed=seed,
                        timeout_seconds=remaining_seconds,
                        model_parameters=model_config.model_parameters,
                        request_timeout_seconds=model_config.request_timeout_seconds,
                        retry_policy=model_config.retry_policy,
                        agent_variant=agent_variant,
                        model_base_url=local_base_url,
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
                        sandboxed=formal,
                    )
                    command = route_command_through_relay(
                        build_native_ao_command(request),
                        base_url=local_base_url,
                        model=model_config.outer_model_id,
                    )
                    if formal:
                        resolver_path = Path(socket_directory) / "resolv.conf"
                        resolver_path.write_text(
                            "nameserver 10.0.2.3\n", encoding="utf-8"
                        )
                        command = sandbox_native_ao_command(
                            agent=agent,
                            command=command,
                            candidate_dir=candidate,
                            launcher_output_dir=output_dir / "launcher",
                            host_dev_socket=broker.socket_path,
                            host_relay_socket=relay_socket,
                            resolver_path=resolver_path,
                        )
                    launcher_result = run_command(
                        command, log_path=output_dir / "launcher/agent.log"
                    )
            except (AdapterError, RuntimeError) as exc:
                if "timed out" in str(exc).lower():
                    launcher_timed_out = True
                else:
                    launcher_error = str(exc)
            try:
                broker.declare_current()
            except AdapterError as exc:
                launcher_error = f"{launcher_error}; {exc}" if launcher_error else str(exc)
    best = broker.declared
    if best is None:
        raise AdapterError(
            "Terminal AO search produced no Agent-declared final harness"
        )
    final_harness = store.replay(best.revision.revision_id, output_dir / "final-harness")
    harness_digest = tree_digest(final_harness)
    if launcher_result is not None and launcher_result.return_code == 124:
        launcher_timed_out = True
    write_json_exclusive(
        output_dir / "selection.json",
        {
            "revision_id": best.revision.revision_id,
            "harness_digest": harness_digest,
            "dev_pass_rate": best.evaluation.pass_rate,
            "dev_evaluations": len(broker.calls),
            "launcher_return_code": launcher_result.return_code if launcher_result else None,
            "launcher_error": launcher_error,
            "launcher_timed_out": launcher_timed_out,
            "selection_policy": "agent-declared final artifact",
            "selection_policy_id": "agent-declared",
            "harness_selected_among_candidates": False,
            "selection_uses_test": False,
        },
    )
    launcher_failed = launcher_error is not None or (
        launcher_result is not None and launcher_result.return_code not in {0, 124}
    )
    if launcher_failed:
        token_totals = summarize_token_log(output_dir / "launcher/token_usage.jsonl")
        status = (
            RunStatus.TIMED_OUT
            if launcher_error and "timed out" in launcher_error.lower()
            else RunStatus.FAILED
        )
        result = BenchmarkRunResult(
            run_id=manifest.run_id,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            manifest_digest=manifest.digest,
            mode=BenchmarkMode.TERMINAL_AO,
            agent=agent,
            task_id="held-out-53",
            seed=seed,
            status=status,
            score_valid=False,
            score=None,
            metrics={
                "primary_metric": "held_out_53_pass_rate",
                "held_out_test_consumed": False,
                "dev_selected_pass_rate": best.evaluation.pass_rate,
                "selection_policy": "agent-declared",
                "direct_89_score_used": False,
            },
            artifact_path=None,
            artifact_sha256=None,
            wall_clock_seconds=time.monotonic() - started,
            tokens=token_totals,
            cost={"reported_usd": 0.0},
            failure_reason=launcher_error
            or f"native launcher exited with code {launcher_result.return_code}",
        )
        result.write(output_dir / "result.json")
        return result
    gate = SealedTestGate(output_dir / "sealed/test-consumed.json")
    gate.consume(
        protocol_digest=protocol.digest,
        split_digest=protocol.split_digest,
        harness_digest=harness_digest,
        outer_run_index=protocol.seeds.index(seed),
    )
    test_record = evaluate_harness(
        protocol,
        split_name="test",
        harness_dir=final_harness,
        evaluation_dir=output_dir / "sealed/test-evaluation",
        model_config=model_config,
    )
    artifact = archive_harness(final_harness, output_dir / "artifacts/final/harness.tar")
    token_totals = summarize_token_log(output_dir / "launcher/token_usage.jsonl")
    def optional_sum(values):
        collected = tuple(values)
        return sum(collected) if all(value is not None for value in collected) else None

    dev_input = optional_sum(item.evaluation.total_input_tokens for item in broker.calls)
    dev_cache = optional_sum(item.evaluation.total_cache_tokens for item in broker.calls)
    dev_output = optional_sum(item.evaluation.total_output_tokens for item in broker.calls)
    dev_cost = optional_sum(item.evaluation.total_cost_usd for item in broker.calls)
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.TERMINAL_AO,
        agent=agent,
        task_id="held-out-53",
        seed=seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=test_record.pass_rate,
        metrics={
            "primary_metric": "held_out_53_pass_rate",
            "held_out_tasks": test_record.expected_tasks,
            "passed": test_record.passed,
            "failed": test_record.failed,
            "errors": test_record.errors,
            "missing_rewards": test_record.missing_rewards,
            "dev_selected_pass_rate": best.evaluation.pass_rate,
            "selection_policy": "agent-declared",
            "launcher_timed_out_at_budget": launcher_timed_out,
            "direct_89_score_used": False,
        },
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
        wall_clock_seconds=time.monotonic() - started,
        tokens={
            "outer_input": token_totals["input"],
            "outer_output": token_totals["output"],
            "outer_cache": token_totals["cache"],
            "inner_dev_input": dev_input,
            "inner_dev_output": dev_output,
            "inner_dev_cache": dev_cache,
            "inner_test_input": test_record.total_input_tokens,
            "inner_test_output": test_record.total_output_tokens,
            "inner_test_cache": test_record.total_cache_tokens,
        },
        cost={
            "outer_reported_usd": None,
            "inner_dev_usd": dev_cost,
            "inner_test_usd": test_record.total_cost_usd,
        },
    )
    result.write(output_dir / "result.json")
    return result


def run_terminal_ao(
    *,
    agent: str,
    protocol: TerminalAOProtocol,
    output_dir: Path,
    seed: int,
    model: str,
    timeout_seconds: int,
    upstream_base_url: str | None = None,
    proxy: str | None = None,
    formal: bool = True,
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
    gpu_ids: tuple[str, ...] = (),
) -> BenchmarkRunResult:
    started = time.monotonic()
    try:
        return _run_terminal_ao_once(
            agent=agent,
            protocol=protocol,
            output_dir=output_dir,
            seed=seed,
            model=model,
            timeout_seconds=timeout_seconds,
            upstream_base_url=upstream_base_url,
            proxy=proxy,
            formal=formal,
            model_config=model_config,
            agent_variant=agent_variant,
            gpu_ids=gpu_ids,
        )
    except Exception as exc:
        manifest_path = output_dir.resolve() / "manifest.json"
        result_path = output_dir.resolve() / "result.json"
        if not manifest_path.is_file() or result_path.exists():
            raise
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = BenchmarkRunResult(
            run_id=str(manifest_payload["run_id"]),
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            manifest_digest=str(manifest_payload["manifest_digest"]),
            mode=BenchmarkMode.TERMINAL_AO,
            agent=agent,
            task_id="held-out-53",
            seed=seed,
            status=RunStatus.INFRASTRUCTURE_ERROR,
            score_valid=False,
            score=None,
            metrics={
                "primary_metric": "held_out_53_pass_rate",
                "held_out_test_consumed": (output_dir / "sealed/test-consumed.json").is_file(),
                "direct_89_score_used": False,
            },
            artifact_path=None,
            artifact_sha256=None,
            wall_clock_seconds=time.monotonic() - started,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
        result.write(result_path)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--agent-variant", required=True)
    parser.add_argument("--gpu-id", action="append", default=[])
    parser.add_argument("--upstream-base-url")
    parser.add_argument("--proxy")
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_terminal_ao(
            agent=args.agent,
            protocol=TerminalAOProtocol.load(args.protocol),
            output_dir=args.output_dir,
            seed=args.seed,
            model=args.model,
            timeout_seconds=args.timeout,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            model_config=ModelTrackConfig.load(
                args.model_config,
                formal=True,
                require_terminal_inner=True,
            ),
            agent_variant=args.agent_variant,
            gpu_ids=tuple(args.gpu_id),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "score_valid": False,
                    "error_type": type(exc).__name__,
                    "failure_reason": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if result.score_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "archive_harness",
    "build_ao_manifest",
    "initialize_candidate_repository",
    "_run_terminal_ao_once",
    "run_terminal_ao",
    "summarize_token_log",
]
