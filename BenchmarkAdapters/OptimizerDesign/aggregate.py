"""Failure-preserving three-seed aggregation for Optimizer Design."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..formal_contract import repetition_summary
from ..protocol import BenchmarkMode, canonical_json, sha256_file
from ..records import RunStatus
from ..registry import AGENTS
from .evaluator import score_validation_trajectories
from .protocol import OptimizerDesignProtocol


@dataclass(frozen=True)
class OptimizerDesignSeedCell:
    seed: int
    state: str
    score_valid: bool
    held_out_common_significant_step: float | None
    development_score_steps: int | None
    result_path: str
    failure_reason: str | None
    manifest_digest: str | None
    hardware_fingerprint: str | None
    agent_commit: str | None
    adapter_commit: str | None
    model_config_digest: str | None


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid Optimizer Design JSON record: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Optimizer Design record must be a JSON object: {path}")
    return payload


def _manifest(
    path: Path,
    *,
    protocol: OptimizerDesignProtocol,
    agent: str,
    seed: int,
    result_manifest_digest: str,
) -> tuple[str, str, str, str, str, tuple[str, ...]]:
    payload = _load_object(path)
    expected = payload.pop("manifest_digest", None)
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected != actual or expected != result_manifest_digest:
        raise AdapterError("Optimizer Design result is not bound to its immutable run manifest")
    if not (
        payload.get("protocol_id") == protocol.protocol_id
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("mode") == BenchmarkMode.OPTIMIZER_DESIGN.value
        and payload.get("agent") == agent
        and payload.get("task_id") == "track-3-optimizer-design"
        and payload.get("seed") == seed
        and payload.get("schema_version") == 2
        and payload.get("benchmark_id") == "optimizer-design"
        and payload.get("benchmark_commit")
        == __import__(
            "BenchmarkAdapters.OptimizerDesign.protocol", fromlist=["SourceManifest"]
        ).SourceManifest.load(protocol.source_manifest_path).source_commit
        and payload.get("formal") is True
        and payload.get("source_dirty") is False
        and payload.get("wall_clock_seconds") == protocol.outer_wall_clock_seconds
        and payload.get("outer_repetitions") == protocol.outer_repetitions
        and payload.get("outer_run_index") == protocol.outer_seeds.index(seed)
        and payload.get("gpus_per_evaluation") == 4
        and payload.get("max_concurrent_evaluations") == 1
        and payload.get("model_track_id")
        and payload.get("outer_model_id")
        and payload.get("task_spec_sha256")
        == __import__("BenchmarkAdapters.task_specs", fromlist=["task_spec_digest"]).task_spec_digest(
            "optimizer-design"
        )
        and payload.get("model_config_digest")
        and payload.get("agent_variant") not in {None, "", "default"}
        and len(str(payload.get("agent_commit", ""))) == 40
        and len(str(payload.get("adapter_commit", ""))) == 40
        and str(payload.get("model", "")).startswith(
            f"openai-compatible:{payload.get('outer_model_id')}:"
        )
    ):
        raise AdapterError("Optimizer Design run manifest identity or formal policy is invalid")
    expected_assets = protocol.protocol_asset_digests()
    if payload.get("asset_digests") != expected_assets:
        raise AdapterError("Optimizer Design run manifest assets differ from the frozen protocol")
    policies = payload.get("policies")
    if not isinstance(policies, dict) or not (
        policies.get("native_backend") == AGENTS[agent].optimizer_design_backend
        and policies.get("run_kind") == "formal"
        and policies.get("editable") == protocol.editable_paths[0]
    ):
        raise AdapterError("Optimizer Design run manifest Agent backend policy is invalid")
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict):
        raise AdapterError("Optimizer Design run manifest hardware record is missing")
    gpus = hardware.get("gpus")
    if not isinstance(gpus, list) or len(gpus) != protocol.gpu_count:
        raise AdapterError("Optimizer Design formal run does not attest four GPUs")
    comparable_gpus: list[dict[str, object]] = []
    allocated_gpu_ids = tuple(str(value) for value in hardware.get("gpu_ids", ()))
    if len(allocated_gpu_ids) != 4 or len(set(allocated_gpu_ids)) != 4:
        raise AdapterError("Optimizer Design manifest GPU allocation is invalid")
    for gpu in gpus:
        if not isinstance(gpu, dict):
            raise AdapterError("Optimizer Design GPU attestation is invalid")
        if "H100" not in str(gpu.get("gpu_name")) or int(gpu.get("gpu_memory_total_mb") or 0) < 75000:
            raise AdapterError("Optimizer Design formal run used a non-H100-80GB GPU")
        comparable_gpus.append(
            {
                "gpu_name": gpu.get("gpu_name"),
                "gpu_memory_total_mb": gpu.get("gpu_memory_total_mb"),
                "driver_version": gpu.get("driver_version"),
                "compute_mode": gpu.get("compute_mode"),
            }
        )
    if (
        hardware.get("gpu_exclusivity") != "verified-and-host-locked"
        or hardware.get("uv_lock_digest") != protocol.environment_lock_digest
        or not hardware.get("environment_python_sha256")
        or hardware.get("python_version") != "3.10.20"
        or hardware.get("torch_version") != "2.11.0+cu128"
        or hardware.get("cuda_runtime") != "12.8"
        or not hardware.get("environment_sha256")
        or not hardware.get("environment_package_fingerprint")
    ):
        raise AdapterError("Optimizer Design formal hardware/runtime attestation is incomplete")
    from .runtime import AgentRuntimeManifest

    runtime_manifest = AgentRuntimeManifest.load(protocol.agent_runtime_manifest_path)
    if hardware.get("agent_runtime_fingerprint") != runtime_manifest.agents[agent].fingerprint:
        raise AdapterError("Optimizer Design Agent runtime attestation differs from protocol")
    comparable = {
        "gpus": comparable_gpus,
        "environment_python_sha256": hardware["environment_python_sha256"],
        "environment_sha256": hardware["environment_sha256"],
        "environment_package_fingerprint": hardware["environment_package_fingerprint"],
        "python_version": hardware["python_version"],
        "torch_version": hardware["torch_version"],
        "cuda_runtime": hardware["cuda_runtime"],
        "uv_lock_digest": hardware["uv_lock_digest"],
        "cpu_affinity": hardware.get("cpu_affinity"),
        "cpu_count": hardware.get("cpu_count"),
        "memory_limit_gib": hardware.get("memory_limit_gib"),
        "rlimit_as_bytes": hardware.get("rlimit_as_bytes"),
        "model": payload["model"],
        "model_parameters": payload.get("model_parameters"),
        "model_track_id": payload.get("model_track_id"),
    }
    return (
        actual,
        hashlib.sha256(canonical_json(comparable)).hexdigest(),
        str(payload.get("agent_commit", "")),
        str(payload.get("adapter_commit", "")),
        str(payload.get("model_config_digest", "")),
        allocated_gpu_ids,
    )


def _evaluation_trajectory(
    path: Path,
    *,
    expected_seed: int,
    artifact_digest: str,
    protocol: OptimizerDesignProtocol,
    expected_gpu_ids: tuple[str, ...],
) -> tuple[tuple[int, float], ...]:
    payload = _load_object(path)
    expected_digest = payload.pop("evaluation_digest", None)
    if expected_digest != hashlib.sha256(canonical_json(payload)).hexdigest():
        raise AdapterError("Optimizer Design held-out evaluation digest mismatch")
    if not (
        payload.get("status") == "completed"
        and payload.get("schema_version") == 2
        and payload.get("score_valid") is True
        and payload.get("seed") == expected_seed
        and payload.get("candidate_sha256") == artifact_digest
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("benchmark_commit")
        == __import__(
            "BenchmarkAdapters.OptimizerDesign.protocol", fromlist=["SourceManifest"]
        ).SourceManifest.load(protocol.source_manifest_path).source_commit
        and payload.get("evaluator_digest") == protocol.evaluator_manifest_digest
        and payload.get("environment_digest") == protocol.environment_manifest_digest
        and payload.get("gpu_ids") == list(expected_gpu_ids)
    ):
        raise AdapterError("Optimizer Design held-out evaluation identity is invalid")
    raw = payload.get("validation_trajectory")
    if not isinstance(raw, list) or not raw:
        raise AdapterError("Optimizer Design held-out trajectory is missing")
    trajectory: list[tuple[int, float]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise AdapterError("Optimizer Design held-out trajectory schema is invalid")
        step, loss = int(item[0]), float(item[1])
        if step < 0 or not math.isfinite(loss) or loss <= 0:
            raise AdapterError("Optimizer Design held-out trajectory value is invalid")
        trajectory.append((step, loss))
    if [step for step, _loss in trajectory] != sorted({step for step, _loss in trajectory}):
        raise AdapterError("Optimizer Design held-out trajectory steps are invalid")
    candidate = path.parent / "candidate.py"
    stdout = path.parent / "stdout.log"
    if (
        not candidate.is_file()
        or candidate.is_symlink()
        or sha256_file(candidate) != artifact_digest
        or not stdout.is_file()
        or stdout.is_symlink()
        or sha256_file(stdout) != payload.get("stdout_sha256")
    ):
        raise AdapterError("Optimizer Design held-out evaluation artifacts differ from their hashes")
    return tuple(trajectory)


def _completed_cell(
    result_path: Path,
    *,
    protocol: OptimizerDesignProtocol,
    agent: str,
    seed: int,
) -> OptimizerDesignSeedCell:
    payload = _load_object(result_path)
    if not (
        payload.get("protocol_id") == protocol.protocol_id
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("mode") == BenchmarkMode.OPTIMIZER_DESIGN.value
        and payload.get("agent") == agent
        and payload.get("task_id") == "track-3-optimizer-design"
        and payload.get("seed") == seed
    ):
        raise AdapterError("Optimizer Design result identity or protocol binding is invalid")
    (
        manifest_digest,
        hardware_fingerprint,
        agent_commit,
        adapter_commit,
        model_config_digest,
        allocated_gpu_ids,
    ) = _manifest(
        result_path.parent / "manifest.json",
        protocol=protocol,
        agent=agent,
        seed=seed,
        result_manifest_digest=str(payload.get("manifest_digest", "")),
    )
    if payload.get("status") != RunStatus.COMPLETED.value or payload.get("score_valid") is not True:
        return OptimizerDesignSeedCell(
            seed=seed,
            state="failed",
            score_valid=False,
            held_out_common_significant_step=None,
            development_score_steps=None,
            result_path=str(result_path),
            failure_reason=str(payload.get("failure_reason") or "non-completed result"),
            manifest_digest=manifest_digest,
            hardware_fingerprint=hardware_fingerprint,
            agent_commit=agent_commit,
            adapter_commit=adapter_commit,
            model_config_digest=model_config_digest,
        )
    score = payload.get("score")
    metrics = payload.get("metrics")
    artifact_path = Path(str(payload.get("artifact_path", "")))
    artifact_digest = str(payload.get("artifact_sha256", ""))
    expected_artifact = (
        result_path.parent / "artifacts/final/train_gpt_simple.py"
    ).resolve()
    if (
        not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 1 <= float(score) <= protocol.failure_penalty_steps
        or not isinstance(metrics, dict)
        or metrics.get("primary_metric") != "held_out_common_significant_step"
        or artifact_path.resolve() != expected_artifact
        or not artifact_path.is_file()
        or artifact_path.is_symlink()
        or len(artifact_digest) != 64
        or sha256_file(artifact_path) != artifact_digest
    ):
        raise AdapterError("Optimizer Design completed result schema or artifact is invalid")
    evaluation_paths = [
        result_path.parent / f"final-evaluations/held-out-{index}/evaluation.json"
        for index in range(1, len(protocol.held_out_seeds) + 1)
    ]
    trajectories = tuple(
        _evaluation_trajectory(
            path,
            expected_seed=held_out_seed,
            artifact_digest=artifact_digest,
            protocol=protocol,
            expected_gpu_ids=allocated_gpu_ids,
        )
        for path, held_out_seed in zip(evaluation_paths, protocol.held_out_seeds)
    )
    recomputed, _mean_loss = score_validation_trajectories(
        trajectories,
        target=protocol.target_val_loss,
        significance_margin=protocol.significance_margin,
        penalty=protocol.failure_penalty_steps,
    )
    if not math.isclose(float(score), float(recomputed), rel_tol=0.0, abs_tol=1e-12):
        raise AdapterError("Optimizer Design result score differs from held-out trajectory replay")
    development_score = metrics.get("development_score_steps")
    if not isinstance(development_score, int):
        raise AdapterError("Optimizer Design development selection score is missing")
    return OptimizerDesignSeedCell(
        seed=seed,
        state="completed",
        score_valid=True,
        held_out_common_significant_step=float(score),
        development_score_steps=development_score,
        result_path=str(result_path),
        failure_reason=None,
        manifest_digest=manifest_digest,
        hardware_fingerprint=hardware_fingerprint,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        model_config_digest=model_config_digest,
    )


def aggregate_optimizer_design(
    *,
    protocol: OptimizerDesignProtocol,
    campaign_dir: Path,
    agent: str,
) -> dict[str, Any]:
    protocol.validate()
    require_formal_contract = getattr(protocol, "require_formal_contract", None)
    if callable(require_formal_contract):
        require_formal_contract()
    if agent not in AGENTS:
        raise AdapterError(f"unknown Optimizer Design Agent: {agent}")
    cells: list[OptimizerDesignSeedCell] = []
    for outer_run_index, seed in enumerate(protocol.outer_seeds):
        legacy = campaign_dir.resolve() / agent / f"seed-{seed}" / "result.json"
        indexed = campaign_dir.resolve() / agent / f"run-{outer_run_index}" / "result.json"
        result_path = indexed if indexed.is_file() else legacy
        if not result_path.is_file() or result_path.is_symlink():
            cells.append(
                OptimizerDesignSeedCell(
                    seed=seed,
                    state="missing",
                    score_valid=False,
                    held_out_common_significant_step=None,
                    development_score_steps=None,
                    result_path=str(result_path),
                    failure_reason="result is missing",
                    manifest_digest=None,
                    hardware_fingerprint=None,
                    agent_commit=None,
                    adapter_commit=None,
                    model_config_digest=None,
                )
            )
            continue
        try:
            cells.append(
                _completed_cell(
                    result_path,
                    protocol=protocol,
                    agent=agent,
                    seed=seed,
                )
            )
        except AdapterError as exc:
            cells.append(
                OptimizerDesignSeedCell(
                    seed=seed,
                    state="invalid",
                    score_valid=False,
                    held_out_common_significant_step=None,
                    development_score_steps=None,
                    result_path=str(result_path),
                    failure_reason=str(exc),
                    manifest_digest=None,
                    hardware_fingerprint=None,
                    agent_commit=None,
                    adapter_commit=None,
                    model_config_digest=None,
                )
            )
    if len(cells) != protocol.outer_repetitions:
        raise AdapterError("Optimizer Design aggregate outer-run cardinality is invalid")
    scores = [
        float(cell.held_out_common_significant_step)
        for cell in cells
        if cell.score_valid and cell.held_out_common_significant_step is not None
    ]
    fingerprints = {
        cell.hardware_fingerprint
        for cell in cells
        if cell.score_valid and cell.hardware_fingerprint is not None
    }
    hardware_consistent = len(fingerprints) == 1
    agent_commit_consistent = len(
        {cell.agent_commit for cell in cells if cell.score_valid and cell.agent_commit}
    ) == 1
    adapter_commit_consistent = len(
        {cell.adapter_commit for cell in cells if cell.score_valid and cell.adapter_commit}
    ) == 1
    model_config_consistent = len(
        {
            cell.model_config_digest
            for cell in cells
            if cell.score_valid and cell.model_config_digest
        }
    ) == 1
    formal_valid = (
        protocol.formal_baseline_ready
        and len(scores) == protocol.outer_repetitions
        and hardware_consistent
        and agent_commit_consistent
        and adapter_commit_consistent
        and model_config_consistent
    )
    if not formal_valid:
        missing = [cell.seed for cell in cells if not cell.score_valid]
        if missing:
            raise AdapterError(
                "Optimizer Design aggregate is missing or has invalid configured outer runs: "
                + ", ".join(str(seed) for seed in missing)
            )
        if not protocol.formal_baseline_ready:
            raise AdapterError("Optimizer Design formal aggregate is blocked by pending baseline")
        raise AdapterError("Optimizer Design outer runs are not comparison-consistent")
    summary = repetition_summary(scores, outer_repetitions=protocol.outer_repetitions)
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.OPTIMIZER_DESIGN.value,
        "agent": agent,
        "primary_metric": "held_out_common_significant_step",
        "metric_direction": "minimize",
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": summary["reporting_label"],
        "num_registered_seeds": protocol.outer_repetitions,
        "num_valid_seeds": len(scores),
        "formal_score_valid": True,
        "formal_avg_at_3_valid": protocol.outer_repetitions == 3,
        "hardware_consistent": hardware_consistent,
        "agent_commit_consistent": agent_commit_consistent,
        "adapter_commit_consistent": adapter_commit_consistent,
        "model_config_consistent": model_config_consistent,
        "formal_hardware_fingerprint": (
            next(iter(fingerprints)) if formal_valid else None
        ),
        "formal_adapter_commit": (
            next(
                cell.adapter_commit
                for cell in cells
                if cell.score_valid and cell.adapter_commit is not None
            )
            if formal_valid
            else None
        ),
        "model_config_digest": (
            next(
                cell.model_config_digest
                for cell in cells
                if cell.score_valid and cell.model_config_digest is not None
            )
            if formal_valid
            else None
        ),
        "metrics": {
            "held_out_common_significant_step": summary
        },
        "seed_cells": [asdict(cell) for cell in cells],
        "failed_cells": sum(cell.state in {"failed", "invalid"} for cell in cells),
        "missing_cells": sum(cell.state == "missing" for cell in cells),
        "failure_policy": "aggregate rejects unless every configured formal outer run validates",
        "development_scores_used_in_primary_metric": False,
        "external_arbor_4xa100_scores_included": False,
    }


def optimizer_design_scorecard(
    *,
    protocol: OptimizerDesignProtocol,
    campaign_dir: Path,
) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    for agent in AGENTS:
        try:
            agents[agent] = aggregate_optimizer_design(
                protocol=protocol,
                campaign_dir=campaign_dir,
                agent=agent,
            )
        except AdapterError as exc:
            agents[agent] = {
                "formal_score_valid": False,
                "formal_avg_at_3_valid": False,
                "failure_reason": str(exc),
            }
    ranked = sorted(
        (
            {
                "agent": agent,
                "score": payload["metrics"]["held_out_common_significant_step"]["mean"],
            }
            for agent, payload in agents.items()
            if payload.get("formal_score_valid")
        ),
        key=lambda item: float(item["score"]),
    )
    formal_fingerprints = {
        (
            payload["formal_hardware_fingerprint"],
            payload["formal_adapter_commit"],
            payload["model_config_digest"],
        )
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    comparison_set_consistent = len(formal_fingerprints) <= 1
    complete = comparison_set_consistent and len(ranked) == len(AGENTS)
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.OPTIMIZER_DESIGN.value,
        "primary_metric": "held_out_common_significant_step",
        "metric_direction": "minimize",
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": "single_run" if protocol.outer_repetitions == 1 else "avg_at_3",
        "agents": agents,
        "formal_ranking": ranked if complete else [],
        "comparison_set_consistent": comparison_set_consistent,
        "complete_seven_agent_comparison_valid": complete,
        "unranked_agents": [
            agent for agent, payload in agents.items() if not payload.get("formal_score_valid")
        ],
        "comparison_policy": {
            "requires_same_protocol_digest": True,
            "requires_all_configured_outer_runs": True,
            "requires_two_non_cherry_picked_held_out_seeds_per_outer_run": True,
            "requires_identical_hardware_runtime_model_fingerprint": True,
            "requires_one_agent_commit_per_three_seed_cell": True,
            "requires_one_adapter_commit_across_scorecard": True,
            "requires_one_model_track_across_scorecard": True,
            "failed_and_missing_cells_preserved": True,
            "development_scores_excluded": True,
            "external_arbor_4xa100_numbers_excluded": True,
        },
    }


__all__ = [
    "OptimizerDesignSeedCell",
    "aggregate_optimizer_design",
    "optimizer_design_scorecard",
]
