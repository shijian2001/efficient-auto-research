from __future__ import annotations

import json
from pathlib import Path

import pytest

from BenchmarkAdapters.AutoResearch.aggregate import aggregate_autoresearch, autoresearch_scorecard
from BenchmarkAdapters.AutoResearch.protocol import build_protocol
from BenchmarkAdapters.protocol import BenchmarkMode
from BenchmarkAdapters.protocol import sha256_file
from BenchmarkAdapters.records import BenchmarkRunResult, RunManifest, RunStatus
from BenchmarkAdapters.registry import AGENTS


def _write_result(
    campaign: Path,
    *,
    agent: str,
    seed: int,
    score: float | None,
    status: RunStatus,
    failure_reason: str | None = None,
) -> None:
    protocol = build_protocol()
    run_dir = campaign / agent / f"seed-{seed}"
    run_dir.mkdir(parents=True, exist_ok=False)
    artifact_path = run_dir / "artifacts/final/train.py"
    artifact_sha = None
    if score is not None:
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("print('final')\n", encoding="utf-8")
        artifact_sha = sha256_file(artifact_path)
    raw = [score - 0.01, score + 0.01] if score is not None else []
    manifest = RunManifest(
        run_id=f"autoresearch-{agent}-seed-{seed}",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent=agent,
        agent_commit="a" * 40,
        adapter_commit="b" * 40,
        source_dirty=False,
        task_id="architecture-design",
        seed=seed,
        model="openai-compatible:synthetic-model-v1:endpoint-0123456789abcdef",
        reasoning_effort=protocol.reasoning_effort,
        temperature=protocol.temperature,
        wall_clock_seconds=protocol.outer_wall_clock_seconds,
        asset_digests={
            "baseline_manifest": protocol.baseline_manifest_digest,
            "prepared_manifest": protocol.prepared_manifest_digest,
            "kernel_cache_manifest": protocol.kernel_cache_manifest_digest,
            "evaluator_manifest": protocol.evaluator_manifest_digest,
            "seed_policy": protocol.seed_policy_digest,
            "environment_lock": protocol.environment_lock_digest,
        },
        hardware={
            "gpu_name": "NVIDIA H100 PCIe",
            "gpu_memory_total_mb": 81559,
            "driver_version": "550.90.07",
            "compute_mode": "Default",
            "cpu_affinity": [0, 1],
            "cpu_count": 2,
            "memory_total_kib": 1024,
            "cgroup_memory_max": "max",
            "rlimit_as_bytes": 128 * 1024**3,
            "environment_python_sha256": "c" * 64,
            "python_version": "3.10.20",
            "uv_lock_digest": protocol.environment_lock_digest,
            "gpu_exclusivity": "verified no existing compute process and guarded by host lock",
        },
        policies={"failure": protocol.failure_policy},
        formal=True,
    )
    manifest.write(run_dir / "manifest.json")
    result = BenchmarkRunResult(
        run_id=f"autoresearch-{agent}-seed-{seed}",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent=agent,
        task_id="architecture-design",
        seed=seed,
        status=status,
        score_valid=score is not None,
        score=score,
        metrics={
            "primary_metric": "held_out_final_val_bpb_mean",
            "held_out_val_bpb": raw,
            "dev_selected_val_bpb": 0.5 if score is not None else None,
        },
        artifact_path=str(artifact_path) if score is not None else None,
        artifact_sha256=artifact_sha,
        wall_clock_seconds=1.0,
        failure_reason=failure_reason,
    )
    result.write(run_dir / "result.json")


def test_avg_at_three_uses_only_held_out_final_means(tmp_path: Path) -> None:
    protocol = build_protocol(outer_repetitions=3)
    for seed, score in zip(protocol.outer_seeds, (1.02, 1.04, 1.06)):
        _write_result(
            tmp_path,
            agent="ear",
            seed=seed,
            score=score,
            status=RunStatus.COMPLETED,
        )
    aggregate = aggregate_autoresearch(protocol=protocol, campaign_dir=tmp_path, agent="ear")
    assert aggregate["formal_avg_at_3_valid"] is True
    assert aggregate["metrics"]["held_out_final_val_bpb"]["mean"] == pytest.approx(1.04)
    assert aggregate["dev_scores_used_in_primary_metric"] is False


def test_failure_and_missing_cells_remain_explicit_and_withhold_avg_at_three(
    tmp_path: Path,
) -> None:
    protocol = build_protocol(outer_repetitions=3)
    _write_result(
        tmp_path,
        agent="codex",
        seed=0,
        score=1.01,
        status=RunStatus.COMPLETED,
    )
    _write_result(
        tmp_path,
        agent="codex",
        seed=1,
        score=None,
        status=RunStatus.FAILED,
        failure_reason="synthetic final evaluation failure",
    )
    aggregate = aggregate_autoresearch(protocol=protocol, campaign_dir=tmp_path, agent="codex")
    assert aggregate["formal_avg_at_3_valid"] is False
    assert aggregate["metrics"]["held_out_final_val_bpb"] is None
    assert [cell["state"] for cell in aggregate["seed_cells"]] == [
        "completed",
        "failed",
        "missing",
    ]
    assert aggregate["failed_cells"] == 1
    assert aggregate["missing_cells"] == 1


def test_scorecard_contains_all_seven_agents_without_external_fill(tmp_path: Path) -> None:
    protocol = build_protocol()
    card = autoresearch_scorecard(protocol=protocol, campaign_dir=tmp_path)
    assert tuple(card["agents"]) == tuple(AGENTS)
    assert card["formal_ranking"] == []
    assert set(card["unranked_agents"]) == set(AGENTS)
    assert card["comparison_policy"]["failed_and_missing_cells_preserved"] is True
    serialized = json.dumps(card, sort_keys=True)
    assert "1.028" not in serialized


def test_aggregate_rejects_tampered_formal_manifest(tmp_path: Path) -> None:
    protocol = build_protocol()
    _write_result(
        tmp_path,
        agent="ear",
        seed=0,
        score=1.02,
        status=RunStatus.COMPLETED,
    )
    manifest_path = tmp_path / "ear/seed-0/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hardware"]["gpu_name"] = "unverified accelerator"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    aggregate = aggregate_autoresearch(protocol=protocol, campaign_dir=tmp_path, agent="ear")
    assert aggregate["formal_avg_at_3_valid"] is False
    assert aggregate["seed_cells"][0]["state"] == "invalid"
    assert "immutable run manifest" in aggregate["seed_cells"][0]["failure_reason"]
