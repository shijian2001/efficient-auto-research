"""Task-record replay aggregation for first-class FML-Bench campaigns."""

from __future__ import annotations

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
from ..protocol import sha256_file
from ..protocol import canonical_json
from ..registry import AGENTS
from .protocol import FMLProtocol


def _outer_score(
    *,
    protocol: FMLProtocol,
    campaign_dir: Path,
    agent: str,
    outer_run_index: int,
) -> tuple[float, list[dict[str, Any]], tuple[str, str, str], str]:
    records: list[dict[str, Any]] = []
    comparison_keys: set[tuple[str, str, str]] = set()
    agent_commits: set[str] = set()
    for task_path in protocol.task_config_paths:
        task_id = task_path.stem
        record_path = (
            campaign_dir.resolve()
            / agent
            / f"run-{outer_run_index}"
            / task_id
            / "task-record.json"
        )
        if not record_path.is_file() or record_path.is_symlink():
            raise AdapterError(
                f"FML aggregate is missing task record for run {outer_run_index}: {task_id}"
            )
        payload = verify_hashed_json(record_path, digest_field="task_record_digest")
        manifest_path = record_path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_digest = manifest.pop("manifest_digest", None)
        if manifest_digest != __import__("hashlib").sha256(canonical_json(manifest)).hexdigest():
            raise AdapterError(f"FML immutable manifest digest mismatch: {task_id}")
        artifact = Path(str(payload.get("artifact_path", "")))
        upstream_result = Path(str(payload.get("upstream_result_path", "")))
        expected_artifact = (record_path.parent / protocol.artifact_relative_path).resolve()
        expected_upstream_result = (
            record_path.parent / protocol.upstream_result_relative_path
        ).resolve()
        upstream_payload = (
            json.loads(upstream_result.read_text(encoding="utf-8"))
            if upstream_result.is_file()
            else {}
        )
        upstream_token_usage = upstream_payload.get("token_usage")
        normalized_token_usage = (
            {str(name): int(value) for name, value in upstream_token_usage.items()}
            if isinstance(upstream_token_usage, dict)
            else None
        )
        upstream_request_count = upstream_payload.get("request_count")
        upstream_cost = upstream_payload.get("cost")
        if not (
            payload.get("schema_version") == 1
            and payload.get("protocol_digest") == protocol.digest
            and payload.get("upstream_commit") == protocol.upstream_commit
            and payload.get("task_id") == task_id
            and payload.get("outer_run_index") == outer_run_index
            and payload.get("status") == "completed"
            and payload.get("score_valid") is True
            and isinstance(payload.get("raw_score"), (int, float))
            and artifact.resolve() == expected_artifact
            and artifact.is_file()
            and not artifact.is_symlink()
            and sha256_file(artifact) == payload.get("artifact_sha256")
            and upstream_result.resolve() == expected_upstream_result
            and upstream_result.is_file()
            and not upstream_result.is_symlink()
            and sha256_file(upstream_result) == payload.get("upstream_result_sha256")
            and isinstance(upstream_payload.get(protocol.primary_metric_name), (int, float))
            and float(upstream_payload[protocol.primary_metric_name])
            == float(payload.get("raw_score"))
            and payload.get("internal_rounds_completed")
            == int(upstream_payload.get("internal_rounds_completed", 0))
            and payload.get("internal_proposals_completed")
            == int(upstream_payload.get("internal_proposals_completed", 0))
            and payload.get("token_usage") == normalized_token_usage
            and payload.get("request_count")
            == (int(upstream_request_count) if upstream_request_count is not None else None)
            and payload.get("cost")
            == (float(upstream_cost) if upstream_cost is not None else None)
            and payload.get("evaluator_digest") == protocol.evaluator_digest
            and payload.get("manifest_digest") == manifest_digest
            and manifest.get("schema_version") == 2
            and manifest.get("formal") is True
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
            and manifest.get("asset_digests") == protocol.protocol_asset_digests
            and manifest.get("task_id") == task_id
            and manifest.get("outer_repetitions") == protocol.outer_repetitions
            and manifest.get("outer_run_index") == outer_run_index
            and manifest.get("wall_clock_seconds") == protocol.wall_clock_seconds
            and manifest.get("task_spec_sha256")
            == protocol.protocol_asset_digests["task_spec"]
            and manifest.get("allowed_write_paths") == list(protocol.allowed_write_paths)
            and manifest.get("gpus_per_evaluation") == protocol.gpus_per_evaluation
            and manifest.get("max_concurrent_evaluations")
            == protocol.max_concurrent_evaluations
            and manifest.get("gpu_type") == protocol.gpu_type
            and isinstance(manifest.get("model_track_id"), str)
            and bool(manifest.get("model_track_id"))
            and isinstance(manifest.get("outer_model_id"), str)
            and bool(manifest.get("outer_model_id"))
            and isinstance(manifest.get("model_config_digest"), str)
            and len(manifest.get("model_config_digest")) == 64
        ):
            raise AdapterError(f"FML formal task record is invalid: {task_id}")
        hardware = manifest.get("hardware")
        if not isinstance(hardware, dict):
            raise AdapterError(f"FML formal GPU attestation is missing: {task_id}")
        validate_gpu_attestation(
            hardware,
            expected_type=protocol.gpu_type,
            gpus_per_evaluation=protocol.gpus_per_evaluation,
            max_concurrent_evaluations=protocol.max_concurrent_evaluations,
        )
        records.append(payload)
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
        sum(float(record["raw_score"]) for record in records) / len(records),
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
    outer_scores: list[float] = []
    task_records: dict[str, list[dict[str, Any]]] = {}
    comparison_keys: set[tuple[str, str, str]] = set()
    agent_commits: set[str] = set()
    for outer_run_index in range(protocol.outer_repetitions):
        score, records, comparison_key, agent_commit = _outer_score(
            protocol=protocol,
            campaign_dir=campaign_dir,
            agent=agent,
            outer_run_index=outer_run_index,
        )
        outer_scores.append(score)
        task_records[str(outer_run_index)] = records
        comparison_keys.add(comparison_key)
        agent_commits.add(agent_commit)
    if len(comparison_keys) != 1:
        raise AdapterError("FML outer runs mix model, hardware, or adapter tracks")
    if len(agent_commits) != 1:
        raise AdapterError("FML outer runs mix Agent source commits")
    summary = repetition_summary(outer_scores, outer_repetitions=protocol.outer_repetitions)
    return {
        "schema_version": 1,
        "benchmark_id": protocol.benchmark_id,
        "protocol_version": protocol.protocol_version,
        "protocol_digest": protocol.digest,
        "agent": agent,
        "metric_direction": protocol.metric_direction,
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": summary["reporting_label"],
        "formal_score_valid": True,
        "formal_avg_at_3_valid": protocol.outer_repetitions == 3,
        "metrics": {"mean_task_score": summary},
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
            payload = aggregate_fml(protocol=protocol, campaign_dir=campaign_dir, agent=agent)
        except AdapterError as exc:
            agents[agent] = {"formal_score_valid": False, "failure_reason": str(exc)}
            continue
        agents[agent] = payload
        ranked.append({"agent": agent, "score": payload["metrics"]["mean_task_score"]["mean"]})
    ranked.sort(
        key=lambda item: float(item["score"]),
        reverse=protocol.metric_direction == "maximize",
    )
    comparison_keys = {
        tuple(payload.get("comparison_key", ()))
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    complete = len(ranked) == len(AGENTS) and len(comparison_keys) == 1
    return {
        "schema_version": 1,
        "benchmark_id": protocol.benchmark_id,
        "protocol_digest": protocol.digest,
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": "single_run" if protocol.outer_repetitions == 1 else "avg_at_3",
        "agents": agents,
        "formal_ranking": ranked if complete else [],
        "complete_seven_agent_comparison_valid": complete,
        "same_model_hardware_track_valid": len(comparison_keys) == 1,
    }


__all__ = ["aggregate_fml", "fml_scorecard"]
