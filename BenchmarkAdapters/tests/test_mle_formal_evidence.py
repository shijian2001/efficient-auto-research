from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.MLEBenchLite.campaign import (
    MleCampaignCell,
    aggregate_campaign,
    build_manifest,
)
from BenchmarkAdapters.MLEBenchLite.membership import (
    validate_lite_data_root,
    verify_task_archive,
)
from BenchmarkAdapters.protocol import (
    BenchmarkMode,
    FormalProtocol,
    canonical_json,
    sha256_file,
    write_json_exclusive,
)
from BenchmarkAdapters.records import BenchmarkRunResult, RunStatus
from BenchmarkAdapters.task_specs import task_spec_digest


TASKS = tuple(f"synthetic-task-{index:02d}" for index in range(22))
BENCHMARK_COMMIT = "b" * 40


def _model_config() -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-mle-track",
        outer_model_id="synthetic-model-id",
        relay_base_url="http://relay.invalid/v1",
        model_parameters={"temperature": 0.0, "reasoning_effort": "medium"},
    )


def _protocol(outer_repetitions: int) -> FormalProtocol:
    return FormalProtocol(
        schema_version=2,
        protocol_id="synthetic-mle-lite-v1",
        mode=BenchmarkMode.MLE,
        task_ids=TASKS,
        asset_digests={
            "task_spec": task_spec_digest("mle-bench-lite"),
            "data_manifest": "1" * 64,
            "grader_worker": "2" * 64,
            "dependency_lock": "3" * 64,
            "official_low_split": "4" * 64,
        },
        model="configured-by-model-track",
        reasoning_effort="configured-by-model-track",
        temperature=None,
        wall_clock_seconds=60,
        seeds=tuple(range(outer_repetitions)),
        retry_policy="no automatic retry",
        failure_policy="failed tasks remain in the denominator",
        artifact_policy="one immutable submission",
        aggregation_policy="fixed 22-task denominator",
        hardware_policy="one exclusive RTX 4090",
    )


def _manifest_data() -> dict[str, object]:
    return {
        "schema_version": 2,
        "mlebench_source_commit": BENCHMARK_COMMIT,
        "split_digest": "4" * 64,
        "tasks": [{"task_id": task} for task in TASKS],
        "manifest_digest": "1" * 64,
    }


def _write_cell(
    *,
    protocol: FormalProtocol,
    campaign: Path,
    outer_run_index: int,
    task_id: str,
) -> Path:
    run_dir = campaign / "codex" / f"run-{outer_run_index}" / task_id
    cell = MleCampaignCell(
        agent="codex",
        task_id=task_id,
        seed=protocol.seeds[outer_run_index],
        run_dir=run_dir,
    )
    manifest = build_manifest(
        cell=cell,
        protocol=protocol,
        gpu_id=0,
        formal=True,
        model_config=_model_config(),
        agent_variant="synthetic-codex-contract",
        hardware={
            "gpu_type": "RTX 4090",
            "gpu_ids": ["0"],
            "gpus": [
                {
                    "gpu_id": "0",
                    "gpu_name": "NVIDIA GeForce RTX 4090",
                    "gpu_uuid": "GPU-SYNTHETIC",
                    "gpu_memory_total_mb": 24564,
                }
            ],
            "gpu_count": 1,
            "gpus_per_evaluation": 1,
            "max_concurrent_evaluations": 1,
            "gpu_exclusivity": "verified-and-host-locked",
        },
    )
    artifact = run_dir / "artifacts/final/submission.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("id,target\n1,0.5\n", encoding="utf-8")
    artifact_digest = sha256_file(artifact)
    report_payload = {
        "schema_version": 2,
        "competition_id": task_id,
        "submission_sha256": artifact_digest,
        "valid_submission": True,
        "score": 0.5,
        "above_median": True,
        "any_medal": True,
        "gold_medal": False,
    }
    report_digest = hashlib.sha256(canonical_json(report_payload)).hexdigest()
    report_path = run_dir / "grading/competition_report.json"
    write_json_exclusive(
        report_path,
        {**report_payload, "grader_report_digest": report_digest},
    )
    manifest.write(run_dir / "manifest.json")
    result = BenchmarkRunResult(
        run_id=cell.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.MLE,
        agent="codex",
        task_id=task_id,
        seed=cell.seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=0.5,
        metrics={
            "grader_report_digest": report_digest,
            "grader_report_file_sha256": sha256_file(report_path),
        },
        artifact_path=str(artifact.resolve()),
        artifact_sha256=artifact_digest,
        wall_clock_seconds=1.0,
    )
    result.write(run_dir / "result.json")
    return run_dir


def _campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outer_repetitions: int,
) -> tuple[FormalProtocol, Path]:
    protocol = _protocol(outer_repetitions)
    campaign = tmp_path / "campaign"
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.campaign._git_identity",
        lambda _path: ("a" * 40, False),
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.membership.load_data_manifest",
        lambda: _manifest_data(),
    )
    for outer_run_index in range(outer_repetitions):
        for task_id in TASKS:
            _write_cell(
                protocol=protocol,
                campaign=campaign,
                outer_run_index=outer_run_index,
                task_id=task_id,
            )
    return protocol, campaign


@pytest.mark.parametrize(
    ("outer_repetitions", "label"), ((1, "single_run"), (3, "avg_at_3"))
)
def test_mle_manifest_grader_record_aggregate_replays_22_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_repetitions: int,
    label: str,
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=outer_repetitions
    )
    aggregate = aggregate_campaign(protocol, campaign, "codex")
    assert aggregate["reporting_label"] == label
    assert aggregate["tasks_per_seed"] == 22
    assert aggregate["metrics"]["valid_rate"]["mean"] == 1.0
    assert aggregate["metrics"]["any_medal_rate"]["mean"] == 1.0
    assert aggregate["total_tokens"] is None
    assert aggregate["total_cost"] is None
    if outer_repetitions == 1:
        assert aggregate["metrics"]["valid_rate"]["standard_deviation"] is None


@pytest.mark.parametrize("target", ("artifact", "report", "manifest", "result"))
def test_mle_aggregate_rejects_bound_evidence_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "codex/run-0" / TASKS[0]
    paths = {
        "artifact": run_dir / "artifacts/final/submission.csv",
        "report": run_dir / "grading/competition_report.json",
        "manifest": run_dir / "manifest.json",
        "result": run_dir / "result.json",
    }
    paths[target].write_text("tampered\n", encoding="utf-8")
    with pytest.raises((AdapterError, json.JSONDecodeError)):
        aggregate_campaign(protocol, campaign, "codex")


def test_mle_aggregate_rejects_incomplete_22_task_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    (campaign / "codex/run-0" / TASKS[-1] / "result.json").unlink()
    with pytest.raises(AdapterError, match="missing formal evidence"):
        aggregate_campaign(protocol, campaign, "codex")


def test_mle_hashed_failed_task_remains_zero_in_22_task_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "codex/run-0" / TASKS[0]
    (run_dir / "grading/competition_report.json").unlink()
    artifact = run_dir / "artifacts/final/submission.csv"
    artifact.unlink()
    result_path = run_dir / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.update(
        {
            "status": "failed",
            "score_valid": False,
            "score": None,
            "metrics": {},
            "artifact_path": None,
            "artifact_sha256": None,
            "failure_reason": "synthetic Agent failure",
        }
    )
    result_path.write_text(json.dumps(result), encoding="utf-8")
    aggregate = aggregate_campaign(protocol, campaign, "codex")
    assert aggregate["tasks_per_seed"] == 22
    assert aggregate["total_failures"] == 1
    assert aggregate["metrics"]["valid_rate"]["mean"] == pytest.approx(21 / 22)
    assert TASKS[0] not in aggregate["raw_scores_by_seed_and_task"]["0"]


def test_mle_data_root_structure_and_archive_hashes_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    records = []
    for task_id in TASKS:
        task_root = data_root / task_id
        public = task_root / "prepared/public"
        private = task_root / "prepared/private"
        public.mkdir(parents=True)
        private.mkdir(parents=True)
        (public / "input.txt").write_text(task_id, encoding="utf-8")
        (private / "labels.txt").write_text("label", encoding="utf-8")
        archive = task_root / f"{task_id}.zip"
        archive.write_bytes(("archive:" + task_id).encode())
        records.append(
            {
                "task_id": task_id,
                "archive_size_bytes": archive.stat().st_size,
                "archive_sha256": sha256_file(archive),
                "prepared_public_files": {"input.txt": sha256_file(public / "input.txt")},
                "prepared_private_files": {"labels.txt": sha256_file(private / "labels.txt")},
            }
        )
    manifest = {
        "schema_version": 2,
        "mlebench_source_commit": BENCHMARK_COMMIT,
        "split_digest": "4" * 64,
        "tasks": records,
        "manifest_digest": "1" * 64,
    }
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.membership.load_lite_task_ids",
        lambda _path=None: TASKS,
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.membership.load_data_manifest", lambda: manifest
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.membership.upstream_checksums_path",
        lambda task_id: data_root / task_id / "checksums.yaml",
    )
    for task_id in TASKS:
        (data_root / task_id / "checksums.yaml").write_text("zip: x\n", encoding="utf-8")
    assert validate_lite_data_root(data_root) == TASKS
    verify_task_archive(data_root, TASKS[0])

    # Content drift inside a prepared tree is no longer this function's job:
    # upstream mlebench prepare owns per-file checksums, and re-hashing 135 GB
    # before every cell cost more than it caught. What must still fail closed is
    # structural: data that is missing, empty, or has no upstream checksum record.
    public_dir = data_root / TASKS[0] / "prepared/public"
    (public_dir / "input.txt").unlink()
    with pytest.raises(AdapterError, match="prepared public data is empty"):
        validate_lite_data_root(data_root)
    (public_dir / "input.txt").write_text(TASKS[0], encoding="utf-8")

    (data_root / TASKS[0] / "checksums.yaml").unlink()
    with pytest.raises(AdapterError, match="upstream mle-bench checksums are missing"):
        validate_lite_data_root(data_root)
    (data_root / TASKS[0] / "checksums.yaml").write_text("zip: x\n", encoding="utf-8")

    # The per-cell source archive is still verified by content.
    archive = data_root / TASKS[0] / f"{TASKS[0]}.zip"
    archive.write_bytes(b"same-size-tamper".ljust(archive.stat().st_size, b"x"))
    with pytest.raises(AdapterError, match="archive"):
        verify_task_archive(data_root, TASKS[0])
