"""Replay formal FML records and compute both upstream leaderboard metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..formal_contract import (
    hardware_comparison_fingerprint,
    repetition_summary,
    validate_gpu_attestation,
    verify_hashed_json,
)
from ..protocol import canonical_json, sha256_file
from ..registry import AGENTS
from .evaluator import verify_evaluation_record
from .protocol import FMLProtocol
from .task import load_fml_task, normalized_improvement, task_win


def _manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid FML immutable manifest: {path}") from exc
    expected = payload.pop("manifest_digest", None)
    if expected != hashlib.sha256(canonical_json(payload)).hexdigest():
        raise AdapterError(f"FML immutable manifest digest mismatch: {path}")
    payload["manifest_digest"] = expected
    return payload


def _verify_file(path_value: object, digest: object, description: str) -> Path:
    path = Path(str(path_value))
    if (
        not path.is_file()
        or path.is_symlink()
        or not isinstance(digest, str)
        or sha256_file(path) != digest
    ):
        raise AdapterError(f"FML {description} differs from its hash")
    return path


def _outer_metrics(
    *, protocol: FMLProtocol, campaign_dir: Path, agent: str, outer_run_index: int
) -> tuple[dict[str, float], list[dict[str, Any]], tuple[str, str, str], str]:
    records: list[dict[str, Any]] = []
    comparison_keys: set[tuple[str, str, str]] = set()
    agent_commits: set[str] = set()
    improvements: list[float] = []
    wins: list[float] = []
    for task_path in protocol.task_config_paths:
        task = load_fml_task(protocol, task_path)
        run_dir = (
            campaign_dir.resolve()
            / agent
            / f"run-{outer_run_index}"
            / task.task_id
        )
        record_path = run_dir / "task-record.json"
        if not record_path.is_file() or record_path.is_symlink():
            raise AdapterError(
                f"FML aggregate is missing task record for run {outer_run_index}: {task.task_id}"
            )
        record = verify_hashed_json(record_path, digest_field="task_record_digest")
        manifest = _manifest(run_dir / "manifest.json")
        artifact = _verify_file(record.get("artifact_path"), record.get("artifact_sha256"), "artifact")
        agent_result = _verify_file(
            record.get("agent_result_path"), record.get("agent_result_sha256"), "Agent result"
        )
        evaluation_path = _verify_file(
            record.get("evaluation_record_path"),
            record.get("evaluation_record_sha256"),
            "evaluation record",
        )
        evaluation = verify_evaluation_record(evaluation_path, task)
        raw_metric = evaluation.get("raw_metric")
        if not isinstance(raw_metric, (int, float)):
            raise AdapterError(f"FML held-out evaluation omitted raw metric: {task.task_id}")
        replayed_improvement = normalized_improvement(task, float(raw_metric))
        replayed_win = task_win(task, float(raw_metric))
        expected_assets = {
            **protocol.protocol_asset_digests,
            "canonical_task": task.digest,
            "task_config": task.task_config_sha256,
            "initial_workspace": record.get("initial_workspace_digest"),
            "rendered_prompt": record.get("rendered_prompt_digest"),
            "agent_identity": record.get("agent_identity_digest"),
        }
        if not (
            record.get("schema_version") == 2
            and record.get("protocol_digest") == protocol.digest
            and record.get("upstream_commit") == protocol.upstream_commit
            and record.get("agent_id") == agent
            and record.get("task_id") == task.task_id
            and record.get("task_config_digest") == task.task_config_sha256
            and record.get("canonical_task_digest") == task.digest
            and record.get("outer_run_index") == outer_run_index
            and record.get("status") == "completed"
            and record.get("score_valid") is True
            and float(record.get("raw_test_metric")) == float(raw_metric)
            and float(record.get("normalized_improvement")) == replayed_improvement
            and record.get("win") is replayed_win
            and record.get("evaluator_digest") == task.evaluator_digest
            and record.get("manifest_digest") == manifest.get("manifest_digest")
            and evaluation.get("phase") == "heldout"
            and float(evaluation.get("normalized_improvement")) == replayed_improvement
            and evaluation.get("win") is replayed_win
            and manifest.get("schema_version") == 2
            and manifest.get("formal") is True
            and manifest.get("non_formal") is False
            and manifest.get("non_comparable") is False
            and manifest.get("source_dirty") is False
            and manifest.get("benchmark_id") == "fml-bench"
            and manifest.get("protocol_version") == protocol.protocol_version
            and manifest.get("protocol_digest") == protocol.digest
            and manifest.get("benchmark_commit") == protocol.upstream_commit
            and manifest.get("agent") == agent
            and isinstance(manifest.get("agent_variant"), str)
            and manifest.get("agent_variant") not in {"", "default"}
            and isinstance(manifest.get("agent_commit"), str)
            and len(manifest.get("agent_commit")) == 40
            and isinstance(manifest.get("adapter_commit"), str)
            and len(manifest.get("adapter_commit")) == 40
            and manifest.get("asset_digests") == expected_assets
            and manifest.get("task_id") == task.task_id
            and manifest.get("outer_repetitions") == protocol.outer_repetitions
            and manifest.get("outer_run_index") == outer_run_index
            and manifest.get("wall_clock_seconds") == protocol.wall_clock_seconds
            and manifest.get("allowed_write_paths") == list(task.editable_paths)
            and manifest.get("model_config_digest")
            == json.loads(agent_result.read_text(encoding="utf-8")).get("model_track_digest")
        ):
            raise AdapterError(f"FML formal task record is invalid: {task.task_id}")
        hardware = manifest.get("hardware")
        if not isinstance(hardware, dict):
            raise AdapterError(f"FML formal GPU attestation is missing: {task.task_id}")
        validate_gpu_attestation(
            hardware,
            expected_type=protocol.gpu_type,
            gpus_per_evaluation=protocol.gpus_per_evaluation,
            max_concurrent_evaluations=protocol.max_concurrent_evaluations,
        )
        improvements.append(replayed_improvement)
        wins.append(1.0 if replayed_win else 0.0)
        records.append(record)
        comparison_keys.add(
            (
                str(manifest.get("model_config_digest", "")),
                str(manifest.get("adapter_commit", "")),
                hardware_comparison_fingerprint(hardware),
            )
        )
        agent_commits.add(str(manifest.get("agent_commit", "")))
    if len(records) != len(protocol.task_config_paths):
        raise AdapterError("FML task denominator is incomplete")
    if len(comparison_keys) != 1:
        raise AdapterError("FML task cells mix model, hardware, or adapter tracks")
    if len(agent_commits) != 1:
        raise AdapterError("FML task cells mix Agent source commits")
    return (
        {
            "normalized_improvement": sum(improvements) / len(improvements),
            "win_rate": sum(wins) / len(wins),
        },
        records,
        next(iter(comparison_keys)),
        next(iter(agent_commits)),
    )


def aggregate_fml(
    *, protocol: FMLProtocol, campaign_dir: Path, agent: str
) -> dict[str, Any]:
    protocol.validate(formal=True)
    if agent not in AGENTS:
        raise AdapterError(f"unknown FML Agent: {agent}")
    outer_values = {"average_improvement": [], "win_rate": []}
    task_records: dict[str, list[dict[str, Any]]] = {}
    comparison_keys: set[tuple[str, str, str]] = set()
    agent_commits: set[str] = set()
    for outer_run_index in range(protocol.outer_repetitions):
        metrics, records, comparison_key, agent_commit = _outer_metrics(
            protocol=protocol,
            campaign_dir=campaign_dir,
            agent=agent,
            outer_run_index=outer_run_index,
        )
        outer_values["average_improvement"].append(metrics["normalized_improvement"])
        outer_values["win_rate"].append(metrics["win_rate"])
        task_records[str(outer_run_index)] = records
        comparison_keys.add(comparison_key)
        agent_commits.add(agent_commit)
    if len(comparison_keys) != 1 or len(agent_commits) != 1:
        raise AdapterError("FML outer runs mix comparison tracks")
    metric_summaries = {
        name: repetition_summary(values, outer_repetitions=protocol.outer_repetitions)
        for name, values in outer_values.items()
    }
    return {
        "schema_version": 2,
        "benchmark_id": protocol.benchmark_id,
        "protocol_version": protocol.protocol_version,
        "protocol_digest": protocol.digest,
        "agent": agent,
        "primary_metric": protocol.primary_metric_name,
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": metric_summaries["average_improvement"]["reporting_label"],
        "formal_score_valid": True,
        "formal_avg_at_3_valid": protocol.outer_repetitions == 3,
        "metrics": metric_summaries,
        "metric_definition": {
            "average_improvement": "mean upstream normalized improvement over the complete frozen task set",
            "win_rate": "fraction of frozen tasks whose held-out metric strictly improves over baseline",
        },
        "tasks_per_outer_run": len(protocol.task_config_paths),
        "task_records": task_records,
        "upstream_internal_rounds_are_outer_repetitions": False,
        "comparison_key": list(next(iter(comparison_keys))),
    }


def fml_scorecard(*, protocol: FMLProtocol, campaign_dir: Path) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    ranked: list[dict[str, Any]] = []
    for agent in AGENTS:
        try:
            payload = aggregate_fml(
                protocol=protocol, campaign_dir=campaign_dir, agent=agent
            )
        except AdapterError as exc:
            agents[agent] = {
                "formal_score_valid": False,
                "failure_reason": str(exc),
            }
            continue
        agents[agent] = payload
        ranked.append(
            {
                "agent": agent,
                "score": payload["metrics"][protocol.primary_metric_name]["mean"],
            }
        )
    ranked.sort(key=lambda item: float(item["score"]), reverse=True)
    comparison_keys = {
        tuple(payload.get("comparison_key", ()))
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    complete = len(ranked) == len(AGENTS) and len(comparison_keys) == 1
    return {
        "schema_version": 2,
        "benchmark_id": protocol.benchmark_id,
        "protocol_digest": protocol.digest,
        "primary_metric": protocol.primary_metric_name,
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": (
            "single_run" if protocol.outer_repetitions == 1 else "avg_at_3"
        ),
        "agents": agents,
        "formal_ranking": ranked if complete else [],
        "complete_seven_agent_comparison_valid": complete,
        "same_model_hardware_track_valid": len(comparison_keys) == 1,
    }


__all__ = ["aggregate_fml", "fml_scorecard"]
