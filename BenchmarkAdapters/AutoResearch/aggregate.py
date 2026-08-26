"""Evidence-replaying aggregation for Autoresearch Architecture Design."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..formal_contract import hardware_comparison_fingerprint, repetition_summary
from ..protocol import BenchmarkMode, canonical_json, sha256_file
from ..records import RunStatus
from ..registry import AGENTS
from ..task_specs import task_spec_digest
from .baseline import BaselineManifest
from .evaluator import EvaluationStatus
from .protocol import AutoResearchProtocol
from .seed_injection import SeedPolicy


@dataclass(frozen=True)
class AutoResearchSeedCell:
    outer_run_index: int
    replication_id: int
    state: str
    score_valid: bool
    final_val_bpb_mean: float
    held_out_val_bpb: tuple[float, float]
    result_path: str
    selection_policy: str
    manifest_digest: str
    hardware_fingerprint: str
    model_track_id: str
    model_config_digest: str
    agent_commit: str
    adapter_commit: str


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid Autoresearch JSON record: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Autoresearch record must be an object: {path}")
    return payload


def _validated_manifest(
    path: Path,
    *,
    protocol: AutoResearchProtocol,
    agent: str,
    replication_id: int,
    outer_run_index: int,
    result_manifest_digest: str,
) -> tuple[dict[str, Any], str, str]:
    payload = _load_object(path)
    expected_digest = payload.pop("manifest_digest", None)
    actual_digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected_digest != actual_digest or expected_digest != result_manifest_digest:
        raise AdapterError("Autoresearch result is not bound to its immutable run manifest")
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    if not (
        payload.get("schema_version") == 2
        and payload.get("protocol_id") == protocol.protocol_id
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("mode") == BenchmarkMode.AUTORESEARCH.value
        and payload.get("benchmark_id") == "autoresearch-architecture"
        and payload.get("agent") == agent
        and payload.get("task_id") == "architecture-design"
        and payload.get("seed") == replication_id
        and payload.get("outer_run_index") == outer_run_index
        and payload.get("outer_repetitions") == protocol.outer_repetitions
        and payload.get("formal") is True
        and payload.get("source_dirty") is False
        and payload.get("benchmark_commit") == baseline.source_commit
        and payload.get("wall_clock_seconds") == protocol.outer_wall_clock_seconds
        and payload.get("task_spec_sha256") == task_spec_digest("autoresearch-architecture")
        and payload.get("model_config_digest")
        and len(str(payload.get("agent_commit", ""))) == 40
        and len(str(payload.get("adapter_commit", ""))) == 40
        and payload.get("agent_variant") not in {None, "", "default"}
        and payload.get("gpus_per_evaluation") == 1
        and payload.get("max_concurrent_evaluations") == 1
    ):
        raise AdapterError("Autoresearch run manifest identity or formal policy is invalid")
    if payload.get("asset_digests") != protocol.protocol_asset_digests():
        raise AdapterError("Autoresearch run manifest asset digests differ from protocol")
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict) or not all(
        hardware.get(name)
        for name in (
            "gpu_uuid",
            "evaluator_digest",
            "evaluator_environment_digest",
            "environment_python_sha256",
        )
    ):
        raise AdapterError("Autoresearch formal hardware/evaluator attestation is incomplete")
    return payload, actual_digest, hardware_comparison_fingerprint(hardware)


def _held_out_value(
    path: Path,
    *,
    protocol: AutoResearchProtocol,
    manifest: dict[str, Any],
    expected_seed: int,
    artifact_digest: str,
) -> float:
    payload = _load_object(path)
    expected_digest = payload.pop("evaluation_digest", None)
    actual_digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected_digest != actual_digest:
        raise AdapterError("Autoresearch held-out evaluation digest mismatch")
    hardware = manifest["hardware"]
    if not (
        payload.get("schema_version") == 2
        and payload.get("protocol_digest") == protocol.digest
        and payload.get("benchmark_commit") == manifest.get("benchmark_commit")
        and payload.get("candidate_sha256") == artifact_digest
        and payload.get("seed") == expected_seed
        and payload.get("status") == EvaluationStatus.COMPLETED.value
        and payload.get("score_valid") is True
        and payload.get("evaluator_digest") == hardware.get("evaluator_digest")
        and payload.get("environment_digest") == hardware.get("evaluator_environment_digest")
        and payload.get("gpu_uuid") == hardware.get("gpu_uuid")
        and payload.get("evaluator_version") == "autoresearch-evaluator-v2"
    ):
        raise AdapterError("Autoresearch held-out evaluation identity or attestation is invalid")
    value = payload.get("val_bpb")
    metrics = payload.get("metrics")
    if (
        not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
        or not isinstance(metrics, dict)
        or not math.isclose(
            float(metrics.get("val_bpb", math.nan)),
            float(value),
            rel_tol=0,
            abs_tol=1e-12,
        )
    ):
        raise AdapterError("Autoresearch held-out evaluation raw val_bpb is invalid")
    executed = path.parent / "workspace/train.py"
    if not executed.is_file() or executed.is_symlink():
        raise AdapterError("Autoresearch held-out executed artifact is missing")
    if sha256_file(executed) != str(payload.get("executed_train_sha256", "")):
        raise AdapterError("Autoresearch held-out executed artifact differs from its record")
    for filename, digest_field in (
        ("stdout.log", "stdout_sha256"),
        ("stderr.log", "stderr_sha256"),
    ):
        log_path = path.parent / filename
        digest = str(payload.get(digest_field, ""))
        if (
            not log_path.is_file()
            or log_path.is_symlink()
            or len(digest) != 64
            or sha256_file(log_path) != digest
        ):
            raise AdapterError("Autoresearch held-out evaluator log differs from its record")
    return float(value)


def _selection_policy(path: Path, *, result: dict[str, Any]) -> str:
    """Replay the per-run final-artifact selection policy.

    The policy is uniform across Agents: each Agent declares its own final
    revision and the host never substitutes a different candidate. Surfacing it
    on the aggregate makes any future divergence between Agents visible instead
    of silent.
    """
    if not path.is_file() or path.is_symlink():
        raise AdapterError("Autoresearch selection record is missing")
    payload = _load_object(path)
    policy = str(payload.get("selection_policy_id", ""))
    metrics = result.get("metrics")
    recorded = (
        str(metrics.get("selection_policy", "")) if isinstance(metrics, dict) else ""
    )
    if (
        policy != "agent-declared"
        or recorded != policy
        or payload.get("harness_selected_among_candidates") is not False
        or payload.get("selection_uses_held_out") is not False
        or payload.get("declared_revision_id") != payload.get("selected_revision_id")
    ):
        raise AdapterError(
            "Autoresearch final revision was not Agent-declared under a held-out-free policy"
        )
    return policy


def _completed_cell(
    result_path: Path,
    *,
    protocol: AutoResearchProtocol,
    agent: str,
    replication_id: int,
    outer_run_index: int,
) -> AutoResearchSeedCell:
    result = _load_object(result_path)
    if not (
        result.get("protocol_id") == protocol.protocol_id
        and result.get("protocol_digest") == protocol.digest
        and result.get("mode") == BenchmarkMode.AUTORESEARCH.value
        and result.get("agent") == agent
        and result.get("seed") == replication_id
        and result.get("status") == RunStatus.COMPLETED.value
        and result.get("score_valid") is True
    ):
        raise AdapterError("Autoresearch result identity or completion status is invalid")
    manifest, manifest_digest, hardware_fingerprint = _validated_manifest(
        result_path.parent / "manifest.json",
        protocol=protocol,
        agent=agent,
        replication_id=replication_id,
        outer_run_index=outer_run_index,
        result_manifest_digest=str(result.get("manifest_digest", "")),
    )
    artifact_path = Path(str(result.get("artifact_path", "")))
    artifact_digest = str(result.get("artifact_sha256", ""))
    expected_artifact = (result_path.parent / "artifacts/final/train.py").resolve()
    if (
        artifact_path.resolve() != expected_artifact
        or not artifact_path.is_file()
        or artifact_path.is_symlink()
        or sha256_file(artifact_path) != artifact_digest
    ):
        raise AdapterError("Autoresearch final artifact differs from result hash")
    selection_policy = _selection_policy(
        result_path.parent / "selection.json", result=result
    )
    seed_policy = SeedPolicy.load(protocol.seed_policy_path)
    values = tuple(
        _held_out_value(
            result_path.parent / f"final-evaluations/held-out-{index}/evaluation.json",
            protocol=protocol,
            manifest=manifest,
            expected_seed=seed,
            artifact_digest=artifact_digest,
        )
        for index, seed in enumerate(seed_policy.held_out_seeds, 1)
    )
    if len(values) != 2:
        raise AdapterError("Autoresearch requires exactly two held-out evaluations per outer run")
    score = sum(values) / 2
    if not math.isclose(float(result.get("score", math.nan)), score, rel_tol=0, abs_tol=1e-12):
        raise AdapterError("Autoresearch result score differs from held-out record replay")
    return AutoResearchSeedCell(
        outer_run_index=outer_run_index,
        replication_id=replication_id,
        state="completed",
        score_valid=True,
        final_val_bpb_mean=score,
        held_out_val_bpb=(values[0], values[1]),
        result_path=str(result_path),
        selection_policy=selection_policy,
        manifest_digest=manifest_digest,
        hardware_fingerprint=hardware_fingerprint,
        model_track_id=str(manifest.get("model_track_id", "")),
        model_config_digest=str(manifest.get("model_config_digest", "")),
        agent_commit=str(manifest.get("agent_commit", "")),
        adapter_commit=str(manifest.get("adapter_commit", "")),
    )


def aggregate_autoresearch(
    *, protocol: AutoResearchProtocol, campaign_dir: Path, agent: str
) -> dict[str, Any]:
    protocol.validate()
    protocol.require_formal_baseline()
    if agent not in AGENTS:
        raise AdapterError(f"unknown Autoresearch Agent: {agent}")
    cells: list[AutoResearchSeedCell] = []
    for outer_run_index, replication_id in enumerate(protocol.outer_seeds):
        legacy = campaign_dir.resolve() / agent / f"seed-{replication_id}" / "result.json"
        indexed = campaign_dir.resolve() / agent / f"run-{outer_run_index}" / "result.json"
        result_path = indexed if indexed.is_file() else legacy
        if not result_path.is_file() or result_path.is_symlink():
            raise AdapterError(
                f"Autoresearch aggregate is missing configured outer run {outer_run_index}: {result_path}"
            )
        cells.append(
            _completed_cell(
                result_path,
                protocol=protocol,
                agent=agent,
                replication_id=replication_id,
                outer_run_index=outer_run_index,
            )
        )
    fingerprints = {cell.hardware_fingerprint for cell in cells}
    if len(fingerprints) != 1:
        raise AdapterError("Autoresearch outer runs use inconsistent hardware/model environments")
    track_ids = {cell.model_track_id for cell in cells}
    model_config_digests = {cell.model_config_digest for cell in cells}
    if len(track_ids) != 1 or len(model_config_digests) != 1:
        raise AdapterError("Autoresearch outer runs mix model tracks")
    if len({cell.agent_commit for cell in cells}) != 1:
        raise AdapterError("Autoresearch outer runs mix Agent source commits")
    if len({cell.adapter_commit for cell in cells}) != 1:
        raise AdapterError("Autoresearch outer runs mix Adapter source commits")
    selection_policies = {cell.selection_policy for cell in cells}
    if len(selection_policies) != 1:
        raise AdapterError("Autoresearch outer runs mix final-artifact selection policies")
    summary = repetition_summary(
        [cell.final_val_bpb_mean for cell in cells],
        outer_repetitions=protocol.outer_repetitions,
    )
    return {
        "schema_version": 2,
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.AUTORESEARCH.value,
        "agent": agent,
        "primary_metric": "held_out_final_val_bpb",
        "metric_direction": "minimize",
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": summary["reporting_label"],
        "formal_score_valid": True,
        "formal_avg_at_3_valid": protocol.outer_repetitions == 3,
        "model_track_id": next(iter(track_ids)),
        "model_config_digest": next(iter(model_config_digests)),
        "comparison_fingerprint": next(iter(fingerprints)),
        "adapter_commit": cells[0].adapter_commit,
        "selection_policy": next(iter(selection_policies)),
        "metrics": {"held_out_final_val_bpb": summary},
        "outer_runs": [asdict(cell) for cell in cells],
        "held_out_evaluations_per_outer_run": 2,
        "development_scores_used_in_primary_metric": False,
    }


def autoresearch_scorecard(
    *, protocol: AutoResearchProtocol, campaign_dir: Path
) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    ranking: list[dict[str, Any]] = []
    for agent in AGENTS:
        try:
            payload = aggregate_autoresearch(
                protocol=protocol, campaign_dir=campaign_dir, agent=agent
            )
        except AdapterError as exc:
            agents[agent] = {"formal_score_valid": False, "failure_reason": str(exc)}
            continue
        agents[agent] = payload
        ranking.append(
            {
                "agent": agent,
                "score": payload["metrics"]["held_out_final_val_bpb"]["mean"],
                "selection_policy": payload["selection_policy"],
            }
        )
    ranking.sort(key=lambda item: float(item["score"]))
    ranked_agents = {item["agent"] for item in ranking}
    comparison_fingerprints = {
        payload.get("comparison_fingerprint")
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    model_config_digests = {
        payload.get("model_config_digest")
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    adapter_commits = {
        payload.get("adapter_commit")
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    complete = (
        len(ranking) == len(AGENTS)
        and len(comparison_fingerprints) == 1
        and len(model_config_digests) == 1
        and len(adapter_commits) == 1
    )
    selection_policies = {
        agent: payload["selection_policy"]
        for agent, payload in agents.items()
        if payload.get("formal_score_valid")
    }
    return {
        "schema_version": 2,
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.AUTORESEARCH.value,
        "outer_repetitions": protocol.outer_repetitions,
        "reporting_label": (
            "single_run" if protocol.outer_repetitions == 1 else "avg_at_3"
        ),
        "agents": agents,
        "formal_ranking": ranking if complete else [],
        "complete_seven_agent_comparison_valid": complete,
        "selection_policy_by_agent": selection_policies,
        "uniform_selection_policy_valid": len(set(selection_policies.values())) <= 1,
        "same_model_track_valid": len(model_config_digests) == 1,
        "comparison_environment_consistent": len(comparison_fingerprints) == 1,
        "adapter_commit_consistent": len(adapter_commits) == 1,
        "unranked_agents": [agent for agent in AGENTS if agent not in ranked_agents],
    }


__all__ = ["AutoResearchSeedCell", "aggregate_autoresearch", "autoresearch_scorecard"]
