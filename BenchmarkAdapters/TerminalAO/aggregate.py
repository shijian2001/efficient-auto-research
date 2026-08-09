"""Strict evidence replay for Terminal-Bench AO held-out score aggregation."""

from __future__ import annotations

import hashlib
import json
import math
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..formal_contract import (
    hardware_comparison_fingerprint,
    repetition_summary,
    validate_gpu_attestation,
    verify_hashed_json,
)
from ..protocol import BenchmarkMode, canonical_json, sha256_file
from ..records import RunStatus
from ..registry import AGENTS
from ..task_specs import task_spec_digest
from .baseline import tree_digest
from .protocol import TerminalAOProtocol
from .split import FrozenSplit


@dataclass(frozen=True)
class TerminalAOSeedMetrics:
    outer_run_index: int
    replication_id: int
    pass_rate: float
    passed: int
    failed: int
    errors: int
    missing_rewards: int
    wall_clock_seconds: float
    total_tokens: int | None
    total_cost: float | None
    manifest_digest: str
    artifact_sha256: str
    model_config_digest: str
    model_track_id: str
    hardware_fingerprint: str
    adapter_commit: str
    agent_commit: str


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid Terminal AO JSON record: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Terminal AO record must be an object: {path}")
    return payload


def _manifest(
    path: Path,
    *,
    protocol: TerminalAOProtocol,
    agent: str,
    replication_id: int,
    outer_run_index: int,
    result_manifest_digest: str,
) -> tuple[dict[str, Any], str]:
    payload = _load_object(path)
    expected = payload.pop("manifest_digest", None)
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected != actual or expected != result_manifest_digest:
        raise AdapterError("Terminal AO result is not bound to its immutable manifest")
    if not (
        payload.get("schema_version") == 2
        and payload.get("protocol_id") == protocol.protocol_id
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("mode") == BenchmarkMode.TERMINAL_AO.value
        and payload.get("benchmark_id") == "terminal-bench-ao"
        and payload.get("agent") == agent
        and payload.get("seed") == replication_id
        and payload.get("outer_run_index") == outer_run_index
        and payload.get("outer_repetitions") == protocol.outer_repetitions
        and payload.get("formal") is True
        and payload.get("source_dirty") is False
        and payload.get("benchmark_commit") == protocol.benchmark_source_commit
        and payload.get("asset_digests") == protocol.protocol_asset_digests()
        and payload.get("task_spec_sha256") == task_spec_digest("terminal-bench-ao")
        and payload.get("terminal_inner_model_id")
        and payload.get("model_track_id")
        and payload.get("model_config_digest")
        and len(str(payload.get("agent_commit", ""))) == 40
        and len(str(payload.get("adapter_commit", ""))) == 40
        and payload.get("agent_variant") not in {None, "", "default"}
        and payload.get("gpus_per_evaluation") == 1
        and payload.get("max_concurrent_evaluations") == protocol.dev_concurrency
    ):
        raise AdapterError("Terminal AO formal manifest identity or policy is invalid")
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict):
        raise AdapterError("Terminal AO formal GPU attestation is missing")
    validate_gpu_attestation(
        hardware,
        expected_type="RTX 4090",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=protocol.dev_concurrency,
    )
    return payload, actual


def _archive_tree_digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise AdapterError("Terminal AO final harness archive is missing or unsafe")
    with tempfile.TemporaryDirectory(prefix="terminal-ao-aggregate-") as directory:
        root = Path(directory) / "harness"
        root.mkdir()
        with tarfile.open(path, "r") as archive:
            for member in archive.getmembers():
                target = (root / member.name).resolve()
                try:
                    target.relative_to(root.resolve())
                except ValueError as exc:
                    raise AdapterError("Terminal AO archive member escapes extraction root") from exc
                if not member.isfile():
                    raise AdapterError("Terminal AO archive contains a non-regular member")
            archive.extractall(root, filter="data")
        return tree_digest(root)


def _task_records(
    evaluation_path: Path,
    *,
    protocol: TerminalAOProtocol,
    manifest: dict[str, Any],
    candidate_digest: str,
) -> tuple[int, int, int, int, float, int | None, float | None]:
    payload = verify_hashed_json(evaluation_path, digest_field="evaluation_digest")
    split = FrozenSplit.load(protocol.split_path)
    tasks = payload.get("tasks")
    if not (
        payload.get("schema_version") == 2
        and payload.get("protocol_id") == protocol.protocol_id
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("split") == "test"
        and payload.get("split_digest") == split.digest
        and payload.get("candidate_digest") == candidate_digest
        and payload.get("benchmark_commit") == manifest.get("benchmark_commit")
        and payload.get("inner_model_track_digest") == manifest.get("model_config_digest")
        and payload.get("evaluator_version") == "terminal-ao-harbor-evaluator-v2"
        and payload.get("expected_tasks") == 53
        and isinstance(payload.get("completed_tasks"), int)
        and isinstance(tasks, list)
        and len(tasks) == 53
    ):
        raise AdapterError("Terminal AO sealed held-out evaluation identity is invalid")
    by_id: dict[str, dict[str, Any]] = {}
    for record in tasks:
        if not isinstance(record, dict):
            raise AdapterError("Terminal AO task record schema is invalid")
        task_id = str(record.get("task_id", ""))
        if task_id in by_id:
            raise AdapterError("Terminal AO held-out task records contain duplicates")
        by_id[task_id] = record
    if set(by_id) != set(split.test):
        raise AdapterError("Terminal AO held-out task denominator differs from frozen 53 tasks")
    passed = errors = missing = 0
    completed = 0
    for task_id in split.test:
        record = by_id[task_id]
        reward = record.get("reward")
        status = str(record.get("status", ""))
        if status not in {"completed", "error", "missing"}:
            raise AdapterError(f"Terminal AO task record has invalid status: {task_id}")
        if reward not in {None, 0, 0.0, 1, 1.0}:
            raise AdapterError(f"Terminal AO task record has a non-binary score: {task_id}")
        result_path = Path(str(record.get("result_path", "")))
        result_digest = str(record.get("result_sha256", ""))
        if status == "missing":
            if record.get("result_path") is not None or record.get("result_sha256") is not None:
                raise AdapterError(f"Terminal AO missing task has unexpected raw evidence: {task_id}")
        else:
            completed += 1
            if (
                not result_path.is_file()
                or result_path.is_symlink()
                or len(result_digest) != 64
                or sha256_file(result_path) != result_digest
            ):
                raise AdapterError(f"Terminal AO raw Harbor task record hash mismatch: {task_id}")
            raw = _load_object(result_path)
            raw_rewards = (raw.get("verifier_result") or {}).get("rewards") or {}
            raw_reward = raw_rewards.get("reward")
            raw_error = bool(raw.get("exception_info"))
            if (
                raw.get("task_name") != task_id
                or raw_reward != reward
                or status != ("error" if raw_error else "completed")
                or bool(record.get("infrastructure_error")) != raw_error
            ):
                raise AdapterError(
                    f"Terminal AO raw Harbor task record differs from evaluation: {task_id}"
                )
        infrastructure_error = bool(record.get("infrastructure_error"))
        if reward == 1 and not infrastructure_error:
            passed += 1
        errors += int(status == "error" or infrastructure_error)
        missing += int(reward is None)
    failed = 53 - passed
    pass_rate = passed / 53
    if not (
        payload.get("completed_tasks") == completed
        and payload.get("passed") == passed
        and payload.get("failed") == failed
        and payload.get("errors") == errors
        and payload.get("missing_rewards") == missing
        and math.isclose(float(payload.get("pass_rate", math.nan)), pass_rate, rel_tol=0, abs_tol=1e-12)
    ):
        raise AdapterError("Terminal AO held-out summary differs from task-record replay")
    token_fields = tuple(
        payload.get(name)
        for name in ("total_input_tokens", "total_cache_tokens", "total_output_tokens")
    )
    tokens = (
        sum(int(value) for value in token_fields)
        if all(value is not None for value in token_fields)
        else None
    )
    cost = float(payload.get("total_cost_usd")) if payload.get("total_cost_usd") is not None else None
    return passed, failed, errors, missing, pass_rate, tokens, cost


def _completed_outer_run(
    result_path: Path,
    *,
    protocol: TerminalAOProtocol,
    agent: str,
    replication_id: int,
    outer_run_index: int,
) -> TerminalAOSeedMetrics:
    result = _load_object(result_path)
    if not (
        result.get("protocol_id") == protocol.protocol_id
        and result.get("protocol_digest") == protocol.digest
        and result.get("mode") == BenchmarkMode.TERMINAL_AO.value
        and result.get("agent") == agent
        and result.get("seed") == replication_id
        and result.get("status") == RunStatus.COMPLETED.value
        and result.get("score_valid") is True
    ):
        raise AdapterError("Terminal AO result identity or completion status is invalid")
    manifest, manifest_digest = _manifest(
        result_path.parent / "manifest.json",
        protocol=protocol,
        agent=agent,
        replication_id=replication_id,
        outer_run_index=outer_run_index,
        result_manifest_digest=str(result.get("manifest_digest", "")),
    )
    artifact_path = Path(str(result.get("artifact_path", "")))
    artifact_digest = str(result.get("artifact_sha256", ""))
    expected_artifact = (result_path.parent / "artifacts/final/harness.tar").resolve()
    if (
        len(artifact_digest) != 64
        or artifact_path.resolve() != expected_artifact
        or not artifact_path.is_file()
        or artifact_path.is_symlink()
        or sha256_file(artifact_path) != artifact_digest
    ):
        raise AdapterError("Terminal AO final artifact hash mismatch")
    candidate_digest = _archive_tree_digest(artifact_path)
    gate = verify_hashed_json(
        result_path.parent / "sealed/test-consumed.json",
        digest_field="gate_digest",
    )
    if not (
        gate.get("test_consumed") is True
        and gate.get("protocol_digest") == protocol.digest
        and gate.get("split_digest") == protocol.split_digest
        and gate.get("harness_digest") == candidate_digest
        and gate.get("outer_run_index") == outer_run_index
    ):
        raise AdapterError("Terminal AO sealed test gate record is invalid")
    passed, failed, errors, missing, pass_rate, tokens, cost = _task_records(
        result_path.parent / "sealed/test-evaluation/evaluation.json",
        protocol=protocol,
        manifest=manifest,
        candidate_digest=candidate_digest,
    )
    if not math.isclose(float(result.get("score", math.nan)), pass_rate, rel_tol=0, abs_tol=1e-12):
        raise AdapterError("Terminal AO result score differs from 53-task replay")
    return TerminalAOSeedMetrics(
        outer_run_index=outer_run_index,
        replication_id=replication_id,
        pass_rate=pass_rate,
        passed=passed,
        failed=failed,
        errors=errors,
        missing_rewards=missing,
        wall_clock_seconds=float(result.get("wall_clock_seconds", 0.0)),
        total_tokens=tokens,
        total_cost=cost,
        manifest_digest=manifest_digest,
        artifact_sha256=artifact_digest,
        model_config_digest=str(manifest.get("model_config_digest", "")),
        model_track_id=str(manifest.get("model_track_id", "")),
        hardware_fingerprint=hardware_comparison_fingerprint(manifest.get("hardware", {})),
        adapter_commit=str(manifest.get("adapter_commit", "")),
        agent_commit=str(manifest.get("agent_commit", "")),
    )


def aggregate_terminal_ao(
    *, protocol: TerminalAOProtocol, campaign_dir: Path, agent: str
) -> dict[str, Any]:
    protocol.validate()
    protocol.require_formal_contract()
    if agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {agent}")
    outer_runs: list[TerminalAOSeedMetrics] = []
    for outer_run_index, replication_id in enumerate(protocol.seeds):
        legacy = campaign_dir.resolve() / agent / f"seed-{replication_id}" / "result.json"
        indexed = campaign_dir.resolve() / agent / f"run-{outer_run_index}" / "result.json"
        result_path = indexed if indexed.is_file() else legacy
        if not result_path.is_file() or result_path.is_symlink():
            raise AdapterError(
                f"Terminal AO aggregate is missing configured outer run {outer_run_index}: {result_path}"
            )
        outer_runs.append(
            _completed_outer_run(
                result_path,
                protocol=protocol,
                agent=agent,
                replication_id=replication_id,
                outer_run_index=outer_run_index,
            )
        )
    summary = repetition_summary(
        [record.pass_rate for record in outer_runs],
        outer_repetitions=protocol.outer_repetitions,
    )
    comparison_keys = {
        (
            record.model_config_digest,
            record.model_track_id,
            record.hardware_fingerprint,
            record.adapter_commit,
        )
        for record in outer_runs
    }
    if len(comparison_keys) != 1:
        raise AdapterError("Terminal AO outer runs mix model, hardware, or adapter tracks")
    if len({record.agent_commit for record in outer_runs}) != 1:
        raise AdapterError("Terminal AO outer runs mix Agent source commits")
    known_costs = [record.total_cost for record in outer_runs if record.total_cost is not None]
    return {
        "schema_version": 2,
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.TERMINAL_AO.value,
        "agent": agent,
        "primary_metric": "held_out_53_pass_rate",
        "metric_direction": "maximize",
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": summary["reporting_label"],
        "formal_score_valid": True,
        "formal_avg_at_3_valid": protocol.outer_repetitions == 3,
        "tasks_per_outer_run": 53,
        "metrics": {"held_out_53_pass_rate": summary},
        "outer_runs": [asdict(record) for record in outer_runs],
        "total_tokens": (
            sum(int(record.total_tokens) for record in outer_runs)
            if all(record.total_tokens is not None for record in outer_runs)
            else None
        ),
        "total_cost": sum(known_costs) if len(known_costs) == len(outer_runs) else None,
        "direct_89_scores_included": False,
        "composite_with_other_benchmarks": False,
        "comparison_key": list(next(iter(comparison_keys))),
    }


def terminal_ao_scorecard(
    *, protocol: TerminalAOProtocol, campaign_dir: Path
) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    ranking: list[dict[str, Any]] = []
    for agent in AGENTS:
        try:
            payload = aggregate_terminal_ao(
                protocol=protocol, campaign_dir=campaign_dir, agent=agent
            )
        except AdapterError as exc:
            agents[agent] = {"formal_score_valid": False, "failure_reason": str(exc)}
            continue
        agents[agent] = payload
        ranking.append(
            {
                "agent": agent,
                "score": payload["metrics"]["held_out_53_pass_rate"]["mean"],
            }
        )
    ranking.sort(key=lambda item: float(item["score"]), reverse=True)
    keys = {
        tuple(payload.get("comparison_key", ()))
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    complete = len(ranking) == len(AGENTS) and len(keys) == 1
    return {
        "schema_version": 2,
        "benchmark_id": "terminal-bench-ao",
        "protocol_digest": protocol.digest,
        "primary_metric": "held_out_53_pass_rate",
        "metric_direction": "maximize",
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": "single_run" if protocol.outer_repetitions == 1 else "avg_at_3",
        "agents": agents,
        "formal_ranking": ranking if complete else [],
        "same_model_hardware_track_valid": len(keys) == 1,
        "complete_seven_agent_comparison_valid": complete,
        "terminal_direct_smoke_included": False,
    }


__all__ = ["TerminalAOSeedMetrics", "aggregate_terminal_ao", "terminal_ao_scorecard"]
