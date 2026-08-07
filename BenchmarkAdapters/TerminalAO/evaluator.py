"""Structured Terminal AO evaluation records and Harbor command builder."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError, CommandSpec
from ..process import run_command
from ..protocol import sha256_file
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
    input_tokens: int = 0
    cache_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


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
    total_input_tokens: int = 0
    total_cache_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0


def aggregate_task_evaluations(
    *,
    protocol_id: str,
    protocol_digest: str,
    split: str,
    split_digest: str,
    candidate_digest: str,
    expected_task_ids: tuple[str, ...],
    evaluations: Mapping[str, TaskEvaluation],
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
        total_input_tokens=sum(item.input_tokens for item in ordered),
        total_cache_tokens=sum(item.cache_tokens for item in ordered),
        total_output_tokens=sum(item.output_tokens for item in ordered),
        total_cost_usd=sum(item.cost_usd for item in ordered),
    )


def build_harbor_evaluation_command(
    protocol: TerminalAOProtocol,
    *,
    split_name: str,
    harness_dir: Path,
    jobs_dir: Path,
) -> CommandSpec:
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
        protocol.inner_model,
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
    return CommandSpec(
        argv=tuple(argv),
        cwd=protocol.dataset_path.parent.parent,
        env={"PYTHONPATH": str(ROOT)},
        timeout_seconds=protocol.evaluator_timeout_seconds,
        label=f"Terminal AO {split_name} evaluator",
    )


def parse_harbor_evaluation(
    *,
    protocol: TerminalAOProtocol,
    split_name: str,
    candidate_digest: str,
    jobs_dir: Path,
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
                input_tokens=int(usage.get("n_input_tokens") or 0),
                cache_tokens=int(usage.get("n_cache_tokens") or 0),
                output_tokens=int(usage.get("n_output_tokens") or 0),
                cost_usd=float(usage.get("cost_usd") or 0.0),
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
    )


def evaluate_harness(
    protocol: TerminalAOProtocol,
    *,
    split_name: str,
    harness_dir: Path,
    evaluation_dir: Path,
    environment: Mapping[str, str] | None = None,
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
    command = build_harbor_evaluation_command(
        protocol,
        split_name=split_name,
        harness_dir=disposable,
        jobs_dir=jobs_dir,
    )
    if environment:
        command = CommandSpec(
            argv=command.argv,
            cwd=command.cwd,
            env=dict(environment),
            timeout_seconds=command.timeout_seconds,
            label=command.label,
            inherit_env=command.inherit_env,
        )
    result = run_command(command, log_path=evaluation_dir / "harbor.log")
    record = parse_harbor_evaluation(
        protocol=protocol,
        split_name=split_name,
        candidate_digest=candidate_digest,
        jobs_dir=jobs_dir,
    )
    if tree_digest(harness_dir) != candidate_digest:
        raise AdapterError("Terminal AO evaluation mutated the frozen candidate")
    write_evaluation(record, evaluation_dir / "evaluation.json")
    if result.return_code != 0 and record.errors == 0:
        raise AdapterError(f"Harbor evaluator exited {result.return_code} without structured errors")
    return record


def write_evaluation(record: EvaluationRecord, path: Path) -> None:
    if path.exists():
        raise AdapterError(f"refusing to overwrite evaluation record: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(record)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "EvaluationRecord",
    "TaskEvaluation",
    "aggregate_task_evaluations",
    "build_harbor_evaluation_command",
    "evaluate_harness",
    "parse_harbor_evaluation",
    "write_evaluation",
]
