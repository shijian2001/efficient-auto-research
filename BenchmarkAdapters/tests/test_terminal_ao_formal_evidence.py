from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.protocol import BenchmarkMode, sha256_file
from BenchmarkAdapters.records import BenchmarkRunResult, RunStatus
from BenchmarkAdapters.TerminalAO.aggregate import aggregate_terminal_ao
from BenchmarkAdapters.TerminalAO.baseline import (
    EDITABLE_PATHS,
    BaselineManifest,
    tree_digest,
    tree_manifest,
)
from BenchmarkAdapters.TerminalAO.evaluator import (
    TaskEvaluation,
    aggregate_task_evaluations,
    write_evaluation,
)
from BenchmarkAdapters.TerminalAO.protocol import TerminalAOProtocol, build_protocol_candidate
from BenchmarkAdapters.TerminalAO.sealed import SealedTestGate
from BenchmarkAdapters.TerminalAO.split import FrozenSplit, dataset_tree_digest
from BenchmarkAdapters.TerminalAO.supervisor import (
    archive_harness,
    build_ao_manifest,
    run_terminal_ao,
)
from BenchmarkAdapters.protocol import write_json_exclusive
from BenchmarkAdapters.registry import AGENTS


DEV_TASKS = tuple(f"dev-{index:02d}" for index in range(36))
TEST_TASKS = tuple(f"test-{index:02d}" for index in range(53))


def _model_config() -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-terminal-track",
        outer_model_id="synthetic-outer-model",
        terminal_inner_model_id="synthetic-inner-model",
        relay_base_url="http://relay.invalid/v1",
        model_parameters={"temperature": 0.0, "reasoning_effort": "medium"},
        terminal_inner_parameters={"temperature": 0.0, "max_output_tokens": 128},
        request_timeout_seconds=30,
        retry_policy={"max_attempts": 1},
    )


def _protocol(tmp_path: Path, outer_repetitions: int) -> TerminalAOProtocol:
    dataset = tmp_path / "dataset"
    for task_id in (*DEV_TASKS, *TEST_TASKS):
        task_dir = dataset / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.toml").write_text(
            '[metadata]\ndifficulty = "synthetic"\ncategory = "harness"\n',
            encoding="utf-8",
        )
    dataset_digest = dataset_tree_digest(dataset)
    split = FrozenSplit(
        protocol_id="synthetic-terminal-ao-v1",
        dataset_digest=dataset_digest,
        seed=20260806,
        dev=DEV_TASKS,
        test=TEST_TASKS,
    )
    split_path = tmp_path / "split.json"
    write_json_exclusive(split_path, {**split.to_dict(), "split_digest": split.digest})
    baseline_source = tmp_path / "baseline"
    baseline_source.mkdir()
    (baseline_source / "terminus_2.py").write_text(
        "print('synthetic terminus')\n", encoding="utf-8"
    )
    baseline = BaselineManifest(
        source_identifier="synthetic-terminus",
        harbor_version="0.20.0",
        source_tree_digest=tree_digest(baseline_source),
        files=tree_manifest(baseline_source),
        editable_paths=EDITABLE_PATHS,
    )
    baseline_path = tmp_path / "baseline_manifest.json"
    write_json_exclusive(
        baseline_path,
        {
            "source_identifier": baseline.source_identifier,
            "harbor_version": baseline.harbor_version,
            "source_tree_digest": baseline.source_tree_digest,
            "files": baseline.files,
            "editable_paths": list(baseline.editable_paths),
            "baseline_manifest_digest": baseline.digest,
        },
    )
    lock_path = tmp_path / "harbor.lock"
    lock_path.write_text("synthetic-harbor-lock\n", encoding="utf-8")
    protocol = TerminalAOProtocol(
        schema_version=2,
        protocol_id=split.protocol_id,
        dataset_path=dataset,
        dataset_digest=dataset_digest,
        split_path=split_path,
        split_digest=split.digest,
        baseline_source=baseline_source,
        baseline_manifest_path=baseline_path,
        baseline_manifest_digest=baseline.digest,
        harbor_executable=Path(sys.executable),
        harbor_version="0.20.0",
        harbor_lock_path=lock_path,
        harbor_lock_digest=sha256_file(lock_path),
        inner_model="configured-by-model-track",
        outer_wall_clock_seconds=172800,
        dev_concurrency=8,
        seeds=tuple(range(outer_repetitions)),
        retry_policy="no automatic result-based retry",
        failure_policy="missing and error rewards remain zero in denominator",
        editable_paths=EDITABLE_PATHS,
        benchmark_source_commit="c" * 40,
        evaluator_timeout_seconds=60,
    )
    protocol.validate()
    return protocol


def _hardware() -> dict[str, object]:
    return {
        "gpu_type": "RTX 4090",
        "gpu_ids": [str(index) for index in range(8)],
        "gpus": [
            {
                "gpu_id": str(index),
                "gpu_name": "NVIDIA GeForce RTX 4090",
                "gpu_uuid": f"GPU-SYNTHETIC-{index}",
                "gpu_memory_total_mb": 24564,
            }
            for index in range(8)
        ],
        "gpu_count": 8,
        "gpus_per_evaluation": 1,
        "max_concurrent_evaluations": 8,
        "gpu_exclusivity": "verified-and-host-locked",
    }


def _write_outer_run(
    *,
    protocol: TerminalAOProtocol,
    campaign: Path,
    outer_run_index: int,
    formal: bool = True,
) -> Path:
    seed = protocol.seeds[outer_run_index]
    run_dir = campaign / "codex" / f"run-{outer_run_index}"
    model_config = _model_config()
    manifest = build_ao_manifest(
        protocol=protocol,
        agent="codex",
        seed=seed,
        formal=formal,
        model_config=model_config if formal else None,
        agent_variant="synthetic-codex-contract" if formal else "smoke",
        hardware=_hardware(),
    )
    final_harness = run_dir / "final-harness"
    final_harness.mkdir(parents=True)
    (final_harness / "terminus_2.py").write_text(
        f"print('candidate {outer_run_index}')\n", encoding="utf-8"
    )
    candidate_digest = tree_digest(final_harness)
    evaluations: dict[str, TaskEvaluation] = {}
    raw_root = run_dir / "sealed/test-evaluation/raw"
    for index, task_id in enumerate(TEST_TASKS):
        raw_path = raw_root / task_id / "result.json"
        raw_path.parent.mkdir(parents=True)
        reward = 1.0 if index < 27 else 0.0
        raw = {
            "task_name": task_id,
            "verifier_result": {"rewards": {"reward": reward}},
            "exception_info": None,
        }
        raw_path.write_text(json.dumps(raw), encoding="utf-8")
        evaluations[task_id] = TaskEvaluation(
            task_id=task_id,
            reward=reward,
            status="completed",
            result_path=str(raw_path.resolve()),
            result_sha256=sha256_file(raw_path),
        )
    evaluation = aggregate_task_evaluations(
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        split="test",
        split_digest=protocol.split_digest,
        candidate_digest=candidate_digest,
        expected_task_ids=TEST_TASKS,
        evaluations=evaluations,
        benchmark_commit=protocol.benchmark_source_commit,
        inner_model_track_digest=(model_config.digest if formal else None),
    )
    write_evaluation(
        evaluation, run_dir / "sealed/test-evaluation/evaluation.json"
    )
    SealedTestGate(run_dir / "sealed/test-consumed.json").consume(
        protocol_digest=protocol.digest,
        split_digest=protocol.split_digest,
        harness_digest=candidate_digest,
        outer_run_index=outer_run_index,
    )
    artifact = archive_harness(
        final_harness, run_dir / "artifacts/final/harness.tar"
    )
    manifest.write(run_dir / "manifest.json")
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.TERMINAL_AO,
        agent="codex",
        task_id="held-out-53",
        seed=seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=evaluation.pass_rate,
        metrics={
            "claimed_pass_rate": evaluation.pass_rate,
            "selection_policy": "agent-declared",
        },
        artifact_path=str(artifact.path.resolve()),
        artifact_sha256=artifact.sha256,
        wall_clock_seconds=1.0,
    )
    result.write(run_dir / "result.json")
    (run_dir / "selection.json").write_text(
        json.dumps(
            {
                "revision_id": "candidate-0001",
                "harness_digest": candidate_digest,
                "selection_policy": "agent-declared final artifact",
                "selection_policy_id": "agent-declared",
                "harness_selected_among_candidates": False,
                "selection_uses_test": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return run_dir


def _campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outer_repetitions: int,
    formal: bool = True,
) -> tuple[TerminalAOProtocol, Path]:
    protocol = _protocol(tmp_path, outer_repetitions)
    campaign = tmp_path / "campaign"
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor._git_identity",
        lambda _path: ("a" * 40, False),
    )
    for outer_run_index in range(outer_repetitions):
        _write_outer_run(
            protocol=protocol,
            campaign=campaign,
            outer_run_index=outer_run_index,
            formal=formal,
        )
    return protocol, campaign


@pytest.mark.parametrize(
    ("outer_repetitions", "label"), ((1, "single_run"), (3, "avg_at_3"))
)
def test_terminal_ao_producer_sealed_53_records_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_repetitions: int,
    label: str,
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=outer_repetitions
    )
    aggregate = aggregate_terminal_ao(
        protocol=protocol, campaign_dir=campaign, agent="codex"
    )
    assert aggregate["reporting_label"] == label
    assert aggregate["tasks_per_outer_run"] == 53
    assert aggregate["metrics"]["held_out_53_pass_rate"]["mean"] == pytest.approx(
        27 / 53
    )
    assert aggregate["total_tokens"] is None
    assert aggregate["total_cost"] is None
    if outer_repetitions == 1:
        assert aggregate["metrics"]["held_out_53_pass_rate"]["standard_deviation"] is None


def test_terminal_ao_wrapper_forwards_all_gpu_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path, 1)
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return "synthetic-result"

    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor._run_terminal_ao_once", fake_run
    )
    result = run_terminal_ao(
        agent="codex",
        protocol=protocol,
        output_dir=tmp_path / "output",
        seed=0,
        model=_model_config().outer_model_id,
        timeout_seconds=protocol.outer_wall_clock_seconds,
        formal=True,
        model_config=_model_config(),
        agent_variant="synthetic-codex-contract",
        gpu_ids=tuple(str(index) for index in range(8)),
    )
    assert result == "synthetic-result"
    assert captured["gpu_ids"] == tuple(str(index) for index in range(8))


@pytest.mark.parametrize(
    "target", ("artifact", "raw-task", "evaluation", "manifest", "gate")
)
def test_terminal_ao_aggregate_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "codex/run-0"
    paths = {
        "artifact": run_dir / "artifacts/final/harness.tar",
        "raw-task": run_dir / f"sealed/test-evaluation/raw/{TEST_TASKS[0]}/result.json",
        "evaluation": run_dir / "sealed/test-evaluation/evaluation.json",
        "manifest": run_dir / "manifest.json",
        "gate": run_dir / "sealed/test-consumed.json",
    }
    paths[target].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(AdapterError):
        aggregate_terminal_ao(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_terminal_ao_rejects_incomplete_denominator_and_missing_outer_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=3)
    evaluation_path = campaign / "codex/run-0/sealed/test-evaluation/evaluation.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["tasks"].pop()
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    with pytest.raises(AdapterError):
        aggregate_terminal_ao(protocol=protocol, campaign_dir=campaign, agent="codex")
    protocol, campaign = _campaign(
        tmp_path / "missing", monkeypatch, outer_repetitions=3
    )
    (campaign / "codex/run-2/result.json").unlink()
    with pytest.raises(AdapterError, match="missing configured outer run"):
        aggregate_terminal_ao(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_terminal_dataset_digest_drift_reports_expected_and_actual(
    tmp_path: Path,
) -> None:
    protocol = _protocol(tmp_path, 1)
    (protocol.dataset_path / DEV_TASKS[0] / "task.toml").write_text(
        "[metadata]\ndifficulty='tampered'\ncategory='harness'\n", encoding="utf-8"
    )
    with pytest.raises(AdapterError, match=r"expected=.*actual="):
        protocol.validate()


def test_terminal_protocol_candidate_rebinds_explicitly_reviewed_split(
    tmp_path: Path,
) -> None:
    protocol = _protocol(tmp_path, 1)
    source_path = tmp_path / "source-protocol.json"
    protocol.write(source_path)
    reviewed_split = FrozenSplit(
        protocol_id=protocol.protocol_id,
        dataset_digest=protocol.dataset_digest,
        seed=20260807,
        dev=tuple(reversed(DEV_TASKS)),
        test=tuple(reversed(TEST_TASKS)),
    )
    protocol.split_path.write_text(
        json.dumps({**reviewed_split.to_dict(), "split_digest": reviewed_split.digest}),
        encoding="utf-8",
    )
    with pytest.raises(AdapterError):
        TerminalAOProtocol.load(source_path)
    candidate = build_protocol_candidate(
        source_path=source_path,
        benchmark_source_commit="d" * 40,
        outer_repetitions=1,
    )
    assert candidate.schema_version == 2
    assert candidate.split_digest == reviewed_split.digest
    assert candidate.dataset_digest == reviewed_split.dataset_digest
    assert candidate.inner_model == "configured-by-model-track"


def test_terminal_smoke_manifest_cannot_enter_formal_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=1, formal=False
    )
    with pytest.raises(AdapterError, match="formal manifest"):
        aggregate_terminal_ao(protocol=protocol, campaign_dir=campaign, agent="codex")


def _ear_launcher_module():
    sys.path.insert(0, str(AGENTS["ear"].install_path))
    from BenchmarkAdapters.TerminalAO.launchers import ear

    return ear


def _prepare_ear_exception_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[object, object]:
    """Arm the EAR launcher for a single failing step.

    The launcher itself no longer owns a search loop — it delegates to EAR's
    native repository mode — so the failure seams live in EAR's own
    `agent.engine.repo_domain`. Both git and the embedder are stubbed so the
    test needs neither a real repository nor the sentence-encoder download.
    """
    ear = _ear_launcher_module()
    from agent.engine import repo_domain

    monkeypatch.setenv(
        "TERMINAL_OUTER_MODEL_PARAMETERS",
        json.dumps({"temperature": 0.0}),
    )

    class _Completed:
        stdout = "a" * 40

    monkeypatch.setattr(repo_domain, "git", lambda *_args, **_kwargs: _Completed())
    monkeypatch.setattr(repo_domain, "embed_diff", lambda _diff, plan="": None)
    return ear, repo_domain


def test_terminal_ear_proposal_failure_preserves_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ear, repo_domain = _prepare_ear_exception_test(tmp_path, monkeypatch)

    def fail_proposal(*_args, **_kwargs):
        raise RuntimeError("synthetic proposal failure")

    monkeypatch.setattr(repo_domain, "llm_query", fail_proposal)
    payload = ear.run_native_loop(
        workspace=tmp_path,
        output_dir=tmp_path / "proposal-output",
        dev_command="synthetic-dev",
        model="synthetic-model",
        seed=0,
        timeout=60,
        max_steps=1,
    )
    error = payload["attempts"][0]["error"]
    assert "RuntimeError: synthetic proposal failure" in error
    assert "NameError" not in error


def test_terminal_ear_evaluation_failure_preserves_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ear, repo_domain = _prepare_ear_exception_test(tmp_path, monkeypatch)
    diff_text = (
        "--- a/terminus_2.py\n+++ b/terminus_2.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    monkeypatch.setattr(
        repo_domain,
        "llm_query",
        lambda *_args, **_kwargs: (f"```diff\n{diff_text}```", 1, 2),
    )

    def fail_evaluation(*_args, **_kwargs):
        raise RuntimeError("synthetic evaluation failure")

    monkeypatch.setattr(ear, "evaluate_dev", fail_evaluation)
    payload = ear.run_native_loop(
        workspace=tmp_path,
        output_dir=tmp_path / "evaluation-output",
        dev_command="synthetic-dev",
        model="synthetic-model",
        seed=0,
        timeout=60,
        max_steps=1,
    )
    error = payload["attempts"][0]["error"]
    assert "RuntimeError: synthetic evaluation failure" in error
    assert "NameError" not in error
