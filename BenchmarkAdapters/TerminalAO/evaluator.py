"""Structured Terminal AO evaluation records and Harbor command builder."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError, CommandSpec
from ..formal_contract import ModelTrackConfig
from ..process import run_command
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..LLMRelay import RelayProcess, relay_agent_environment
from ..registry import ROOT
from .baseline import tree_digest
from .protocol import TerminalAOProtocol
from .split import FrozenSplit


@dataclass(frozen=True)
class TaskEvaluation:
    task_id: str
    reward: float | None
    status: str
    infrastructure_error: bool = False
    error: str | None = None
    result_path: str | None = None
    input_tokens: int | None = None
    cache_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    result_sha256: str | None = None


@dataclass(frozen=True)
class EvaluationRecord:
    protocol_id: str
    protocol_digest: str
    split: str
    split_digest: str
    candidate_digest: str
    expected_tasks: int
    completed_tasks: int
    passed: int
    failed: int
    errors: int
    missing_rewards: int
    pass_rate: float
    tasks: tuple[TaskEvaluation, ...]
    total_input_tokens: int | None = None
    total_cache_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cost_usd: float | None = None
    schema_version: int = 2
    benchmark_commit: str | None = None
    evaluator_version: str = "terminal-ao-harbor-evaluator-v2"
    inner_model_track_digest: str | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()


def aggregate_task_evaluations(
    *,
    protocol_id: str,
    protocol_digest: str,
    split: str,
    split_digest: str,
    candidate_digest: str,
    expected_task_ids: tuple[str, ...],
    evaluations: Mapping[str, TaskEvaluation],
    benchmark_commit: str | None = None,
    inner_model_track_digest: str | None = None,
) -> EvaluationRecord:
    if split not in {"dev", "test"}:
        raise AdapterError("Terminal AO evaluation split must be dev or test")
    unknown = set(evaluations) - set(expected_task_ids)
    if unknown:
        raise AdapterError(f"evaluation contains tasks outside frozen split: {sorted(unknown)}")
    ordered: list[TaskEvaluation] = []
    passed = failed = errors = missing = 0
    for task_id in expected_task_ids:
        item = evaluations.get(task_id)
        if item is None:
            item = TaskEvaluation(task_id, None, "missing", error="missing terminal state")
        ordered.append(item)
        if item.reward == 1 and not item.infrastructure_error:
            passed += 1
        else:
            failed += 1
        errors += int(item.status == "error" or item.infrastructure_error)
        missing += int(item.reward is None)
    expected = len(expected_task_ids)
    return EvaluationRecord(
        protocol_id=protocol_id,
        protocol_digest=protocol_digest,
        split=split,
        split_digest=split_digest,
        candidate_digest=candidate_digest,
        expected_tasks=expected,
        completed_tasks=len(evaluations),
        passed=passed,
        failed=failed,
        errors=errors,
        missing_rewards=missing,
        pass_rate=passed / expected,
        tasks=tuple(ordered),
        total_input_tokens=(
            sum(int(item.input_tokens) for item in ordered)
            if all(item.input_tokens is not None for item in ordered)
            else None
        ),
        total_cache_tokens=(
            sum(int(item.cache_tokens) for item in ordered)
            if all(item.cache_tokens is not None for item in ordered)
            else None
        ),
        total_output_tokens=(
            sum(int(item.output_tokens) for item in ordered)
            if all(item.output_tokens is not None for item in ordered)
            else None
        ),
        total_cost_usd=(
            sum(float(item.cost_usd) for item in ordered)
            if all(item.cost_usd is not None for item in ordered)
            else None
        ),
        benchmark_commit=benchmark_commit,
        inner_model_track_digest=inner_model_track_digest,
    )


def build_harbor_evaluation_command(
    protocol: TerminalAOProtocol,
    *,
    split_name: str,
    harness_dir: Path,
    jobs_dir: Path,
    model_config: ModelTrackConfig,
    gpu_ids: tuple[str, ...] = (),
) -> CommandSpec:
    model_config.validate(formal=True, require_terminal_inner=True)
    split = FrozenSplit.load(protocol.split_path)
    task_ids = split.dev if split_name == "dev" else split.test
    if split_name not in {"dev", "test"}:
        raise AdapterError("Terminal AO split must be dev or test")
    argv = [
        str(protocol.harbor_executable),
        "run",
        "--path",
        str(protocol.dataset_path.resolve()),
        "--agent",
        "BenchmarkAdapters.TerminalAO.candidate_agent:CandidateTerminus2",
        "--model",
        str(model_config.terminal_inner_model_id),
        "--jobs-dir",
        str(jobs_dir.resolve()),
        "--job-name",
        f"ao-{split_name}",
        "--n-attempts",
        "1",
        "--n-concurrent",
        str(protocol.dev_concurrency),
        "--agent-kwarg",
        f"source_dir={harness_dir.resolve()}",
        "--yes",
    ]
    for task_id in task_ids:
        argv.extend(["--include-task-name", task_id])
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "DOCKER_HOST",
        }
    }
    environment.update(
        {
            "PYTHONPATH": str(ROOT),
            "OPENAI_BASE_URL": model_config.relay_base_url.rstrip("/"),
            "OPENAI_API_BASE": model_config.relay_base_url.rstrip("/"),
            "OPENAI_API_KEY": "proxy",
            "ANTHROPIC_API_KEY": "proxy",
            "TERMINAL_INNER_MODEL_ID": str(model_config.terminal_inner_model_id),
            "TERMINAL_INNER_MODEL_PARAMETERS": json.dumps(
                model_config.terminal_inner_parameters, sort_keys=True
            ),
            "TERMINAL_INNER_REQUEST_TIMEOUT_SECONDS": str(
                model_config.request_timeout_seconds or protocol.evaluator_timeout_seconds
            ),
            "TERMINAL_INNER_RETRY_POLICY": json.dumps(
                model_config.retry_policy, sort_keys=True
            ),
            "CUDA_VISIBLE_DEVICES": ",".join(gpu_ids),
        }
    )
    return CommandSpec(
        argv=tuple(argv),
        cwd=protocol.dataset_path.parent.parent,
        env=environment,
        timeout_seconds=protocol.evaluator_timeout_seconds,
        label=f"Terminal AO {split_name} evaluator",
        inherit_env=False,
    )


def parse_harbor_evaluation(
    *,
    protocol: TerminalAOProtocol,
    split_name: str,
    candidate_digest: str,
    jobs_dir: Path,
    model_config: ModelTrackConfig,
) -> EvaluationRecord:
    split = FrozenSplit.load(protocol.split_path)
    expected_task_ids = split.dev if split_name == "dev" else split.test
    if split_name not in {"dev", "test"}:
        raise AdapterError("Terminal AO split must be dev or test")
    job_dir = jobs_dir.resolve() / f"ao-{split_name}"
    evaluations: dict[str, TaskEvaluation] = {}
    result_paths = sorted(job_dir.glob("*/result.json")) if job_dir.is_dir() else []
    for result_path in result_paths:
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            task_id = str(payload["task_name"])
            if task_id in evaluations:
                raise AdapterError(f"Harbor produced duplicate Terminal AO result for {task_id}")
            rewards = (payload.get("verifier_result") or {}).get("rewards") or {}
            reward_value = rewards.get("reward")
            reward = float(reward_value) if isinstance(reward_value, (int, float)) else None
            exception = payload.get("exception_info")
            usage = payload.get("agent_result") or {}
            evaluations[task_id] = TaskEvaluation(
                task_id=task_id,
                reward=reward,
                status="error" if exception else "completed",
                infrastructure_error=bool(exception),
                error=(
                    f"{exception.get('exception_type')}: {exception.get('exception_message')}"
                    if exception
                    else None
                ),
                result_path=str(result_path),
                input_tokens=(
                    int(usage["n_input_tokens"])
                    if usage.get("n_input_tokens") is not None
                    else None
                ),
                cache_tokens=(
                    int(usage["n_cache_tokens"])
                    if usage.get("n_cache_tokens") is not None
                    else None
                ),
                output_tokens=(
                    int(usage["n_output_tokens"])
                    if usage.get("n_output_tokens") is not None
                    else None
                ),
                cost_usd=(
                    float(usage["cost_usd"])
                    if usage.get("cost_usd") is not None
                    else None
                ),
                result_sha256=sha256_file(result_path),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid Harbor trial result: {result_path}: {exc}") from exc
    return aggregate_task_evaluations(
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        split=split_name,
        split_digest=split.digest,
        candidate_digest=candidate_digest,
        expected_task_ids=expected_task_ids,
        evaluations=evaluations,
        benchmark_commit=protocol.benchmark_source_commit,
        inner_model_track_digest=model_config.digest,
    )


def evaluate_harness(
    protocol: TerminalAOProtocol,
    *,
    split_name: str,
    harness_dir: Path,
    evaluation_dir: Path,
    environment: Mapping[str, str] | None = None,
    model_config: ModelTrackConfig | None = None,
    gpu_ids: tuple[str, ...] = (),
) -> EvaluationRecord:
    protocol.validate()
    harness_dir = harness_dir.resolve()
    candidate_digest = tree_digest(harness_dir)
    if evaluation_dir.exists() or evaluation_dir.is_symlink():
        raise AdapterError(f"evaluation directory already exists: {evaluation_dir.resolve()}")
    disposable = evaluation_dir.resolve() / "candidate"
    jobs_dir = evaluation_dir.resolve() / "jobs"
    disposable.parent.mkdir(parents=True)
    shutil.copytree(harness_dir, disposable, symlinks=False)
    if model_config is None:
        raise AdapterError("Terminal AO evaluator requires an explicit inner model track")
    command = build_harbor_evaluation_command(
        protocol,
        split_name=split_name,
        harness_dir=disposable,
        jobs_dir=jobs_dir,
        model_config=model_config,
        gpu_ids=gpu_ids,
    )
    if environment:
        command = CommandSpec(
            argv=command.argv,
            cwd=command.cwd,
            env={**command.env, **dict(environment)},
            timeout_seconds=command.timeout_seconds,
            label=command.label,
            inherit_env=False,
        )
    with RelayProcess(
        agent="terminal-ao-inner",
        log_path=evaluation_dir / "inner-relay.log",
        token_log_path=evaluation_dir / "inner-token-usage.jsonl",
        upstream_base_url=model_config.relay_base_url,
        model=model_config.terminal_inner_model_id,
        model_parameters=model_config.terminal_inner_parameters,
        request_timeout_seconds=model_config.request_timeout_seconds,
        retry_policy=model_config.retry_policy,
    ) as relay:
        safe_environment = relay_agent_environment(
            base_url=relay.base_url,
            model=model_config.terminal_inner_model_id,
            environment=command.env,
        )
        command = CommandSpec(
            argv=command.argv,
            cwd=command.cwd,
            env=safe_environment,
            timeout_seconds=command.timeout_seconds,
            label=command.label,
            inherit_env=False,
        )
        result = run_command(command, log_path=evaluation_dir / "harbor.log")
    record = parse_harbor_evaluation(
        protocol=protocol,
        split_name=split_name,
        candidate_digest=candidate_digest,
        jobs_dir=jobs_dir,
        model_config=model_config,
    )
    if tree_digest(harness_dir) != candidate_digest:
        raise AdapterError("Terminal AO evaluation mutated the frozen candidate")
    write_evaluation(record, evaluation_dir / "evaluation.json")
    if result.return_code != 0 and record.errors == 0:
        raise AdapterError(f"Harbor evaluator exited {result.return_code} without structured errors")
    return record


def write_evaluation(record: EvaluationRecord, path: Path) -> None:
    write_json_exclusive(path, {**asdict(record), "evaluation_digest": record.digest})


__all__ = [
    "EvaluationRecord",
    "TaskEvaluation",
    "aggregate_task_evaluations",
    "build_harbor_evaluation_command",
    "evaluate_harness",
    "parse_harbor_evaluation",
    "write_evaluation",
]
