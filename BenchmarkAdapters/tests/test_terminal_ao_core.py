from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.protocol import sha256_file
from BenchmarkAdapters.registry import ROOT
from BenchmarkAdapters.TerminalAO.baseline import BaselineManifest, tree_digest
from BenchmarkAdapters.TerminalAO.broker import DevEvaluationBroker, DevEvaluationRequest
from BenchmarkAdapters.TerminalAO.evaluator import (
    EvaluationRecord,
    TaskEvaluation,
    aggregate_task_evaluations,
    build_harbor_evaluation_command,
    parse_harbor_evaluation,
)
from BenchmarkAdapters.TerminalAO.supervisor import archive_harness
from BenchmarkAdapters.TerminalAO.protocol import TerminalAOProtocol
from BenchmarkAdapters.TerminalAO.revisions import RevisionStore
from BenchmarkAdapters.TerminalAO.sealed import SealedTestGate
from BenchmarkAdapters.TerminalAO.split import (
    FrozenSplit,
    build_reconstruction_split,
    dataset_tree_digest,
    read_task_metadata,
)


ASSET_DIR = ROOT / "terminal-bench-2/ao_protocol"


def _protocol() -> TerminalAOProtocol:
    return TerminalAOProtocol.load(ASSET_DIR / "protocol.json")


def test_frozen_split_is_deterministic_disjoint_and_complete() -> None:
    protocol = _protocol()
    metadata = read_task_metadata(protocol.dataset_path)
    frozen = FrozenSplit.load(protocol.split_path, {task.name for task in metadata})
    rebuilt = build_reconstruction_split(
        metadata,
        dataset_digest=protocol.dataset_digest,
        seed=frozen.seed,
        protocol_id=frozen.protocol_id,
    )
    assert len(frozen.dev) == 36
    assert len(frozen.test) == 53
    assert not set(frozen.dev) & set(frozen.test)
    assert set(frozen.dev) | set(frozen.test) == {task.name for task in metadata}
    assert rebuilt.digest == frozen.digest
    assert rebuilt.dev == frozen.dev
    assert rebuilt.test == frozen.test


def test_protocol_verifies_real_harbor_and_terminus_baseline() -> None:
    protocol = _protocol()
    protocol.validate()
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    baseline.verify_source(protocol.baseline_source)
    assert baseline.source_tree_digest == tree_digest(protocol.baseline_source)
    assert protocol.harbor_version == "0.20.0"
    assert protocol.dataset_digest == dataset_tree_digest(protocol.dataset_path)
    assert protocol.split_digest == FrozenSplit.load(protocol.split_path).digest
    assert protocol.baseline_manifest_digest == baseline.digest
    assert protocol.harbor_lock_digest == sha256_file(protocol.harbor_lock_path)
    assert protocol.outer_wall_clock_seconds == 172800
    assert protocol.dev_concurrency == 8


def test_revision_store_isolates_siblings_and_replays_only_allowlisted_diff(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    store = RevisionStore(
        baseline_source=protocol.baseline_source,
        baseline_manifest=baseline,
        state_dir=tmp_path / "state",
    )
    first = store.checkout("baseline", "candidate-one")
    second = store.checkout("baseline", "candidate-two")
    marker = "\n# synthetic candidate marker\n"
    with (first / "terminus_2.py").open("a", encoding="utf-8") as handle:
        handle.write(marker)
    assert marker not in (second / "terminus_2.py").read_text(encoding="utf-8")
    revision = store.commit(first, parent_id="baseline", revision_id="candidate-1")
    assert revision.changed_files == ("terminus_2.py",)
    final = store.replay("candidate-1", tmp_path / "final")
    assert marker in (final / "terminus_2.py").read_text(encoding="utf-8")
    assert tree_digest(final) == revision.tree_digest


def test_revision_store_rejects_non_allowlisted_change_and_symlink(tmp_path: Path) -> None:
    protocol = _protocol()
    store = RevisionStore(
        baseline_source=protocol.baseline_source,
        baseline_manifest=BaselineManifest.load(protocol.baseline_manifest_path),
        state_dir=tmp_path / "state",
    )
    forbidden = store.checkout("baseline", "forbidden")
    (forbidden / "__init__.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="non-editable"):
        store.commit(forbidden, parent_id="baseline", revision_id="forbidden")
    linked = store.checkout("baseline", "linked")
    (linked / "templates/escape").symlink_to("/etc/passwd")
    with pytest.raises(AdapterError, match="symlink"):
        store.commit(linked, parent_id="baseline", revision_id="linked")


def test_dev_evaluation_on_disposable_copy_does_not_mutate_candidate(tmp_path: Path) -> None:
    protocol = _protocol()
    store = RevisionStore(
        baseline_source=protocol.baseline_source,
        baseline_manifest=BaselineManifest.load(protocol.baseline_manifest_path),
        state_dir=tmp_path / "state",
    )
    candidate = store.checkout("baseline", "candidate")
    before = tree_digest(candidate)
    disposable = tmp_path / "evaluation-copy"
    shutil.copytree(candidate, disposable)
    (disposable / "terminus_2.py").write_text("evaluation mutation\n", encoding="utf-8")
    assert tree_digest(candidate) == before
    assert tree_digest(disposable) != before


def test_dev_broker_has_no_test_capability_and_validates_candidate_digest() -> None:
    protocol = _protocol()
    split = FrozenSplit.load(protocol.split_path)
    candidate_digest = "b" * 64

    def evaluate(request: DevEvaluationRequest):
        return aggregate_task_evaluations(
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            split="dev",
            split_digest=split.digest,
            candidate_digest=request.candidate_digest,
            expected_task_ids=split.dev,
            evaluations={},
        )

    broker = DevEvaluationBroker(protocol, evaluate)
    assert not hasattr(broker, "evaluate_test")
    record = broker.evaluate(DevEvaluationRequest("candidate-1", candidate_digest))
    assert record.split == "dev"
    assert record.expected_tasks == 36
    assert record.pass_rate == 0.0
    assert record.missing_rewards == 36


def test_missing_rewards_and_errors_are_zero_in_fixed_denominator() -> None:
    expected = ("pass", "fail", "error", "missing")
    record = aggregate_task_evaluations(
        protocol_id="p",
        protocol_digest="a" * 64,
        split="test",
        split_digest="b" * 64,
        candidate_digest="c" * 64,
        expected_task_ids=expected,
        evaluations={
            "pass": TaskEvaluation("pass", 1.0, "completed"),
            "fail": TaskEvaluation("fail", 0.0, "completed"),
            "error": TaskEvaluation("error", None, "error", infrastructure_error=True),
        },
    )
    assert record.expected_tasks == 4
    assert record.passed == 1
    assert record.failed == 3
    assert record.pass_rate == 0.25
    assert record.errors == 1
    assert record.missing_rewards == 2


def test_sealed_test_can_be_consumed_only_once(tmp_path: Path) -> None:
    gate = SealedTestGate(tmp_path / "test-consumed.json")
    gate.consume(protocol_digest="a" * 64, harness_digest="b" * 64)
    payload = json.loads((tmp_path / "test-consumed.json").read_text())
    assert payload["test_consumed"] is True
    with pytest.raises(AdapterError, match="already been consumed"):
        gate.consume(protocol_digest="a" * 64, harness_digest="b" * 64)


def test_harbor_evaluator_uses_candidate_wrapper_and_only_requested_split(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    command = build_harbor_evaluation_command(
        protocol,
        split_name="dev",
        harness_dir=protocol.baseline_source,
        jobs_dir=tmp_path / "jobs",
    )
    text = " ".join(command.argv)
    split = FrozenSplit.load(protocol.split_path)
    assert "BenchmarkAdapters.TerminalAO.candidate_agent:CandidateTerminus2" in text
    assert len([item for item in command.argv if item == "--include-task-name"]) == 36
    assert all(task_id in command.argv for task_id in split.dev)
    assert all(task_id not in command.argv for task_id in split.test)
    assert "source_dir=" in text


def test_harbor_result_parser_uses_task_name_and_reward_with_fixed_denominator(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    split = FrozenSplit.load(protocol.split_path)
    job_dir = tmp_path / "jobs/ao-dev"
    passing = job_dir / "trial-one"
    errored = job_dir / "trial-two"
    passing.mkdir(parents=True)
    errored.mkdir(parents=True)
    (passing / "result.json").write_text(
        json.dumps(
            {
                "task_name": split.dev[0],
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": None,
            }
        )
    )
    (errored / "result.json").write_text(
        json.dumps(
            {
                "task_name": split.dev[1],
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": {
                    "exception_type": "InfrastructureError",
                    "exception_message": "synthetic",
                },
            }
        )
    )
    record = parse_harbor_evaluation(
        protocol=protocol,
        split_name="dev",
        candidate_digest="a" * 64,
        jobs_dir=tmp_path / "jobs",
    )
    assert record.expected_tasks == 36
    assert record.passed == 1
    assert record.failed == 35
    assert record.errors == 1
    assert record.missing_rewards == 34


def test_final_harness_archive_is_deterministic_and_hash_bound(tmp_path: Path) -> None:
    source = tmp_path / "harness"
    source.mkdir()
    (source / "terminus_2.py").write_text("print('candidate')\n")
    first = archive_harness(source, tmp_path / "first.tar")
    second = archive_harness(source, tmp_path / "second.tar")
    assert first.sha256 == second.sha256
    assert first.size_bytes > 0
    with pytest.raises(AdapterError, match="overwrite"):
        archive_harness(source, tmp_path / "first.tar")
