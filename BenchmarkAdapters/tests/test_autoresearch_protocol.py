from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from BenchmarkAdapters.AutoResearch.baseline import (
    BaselineManifest,
    KernelCacheManifest,
    PreparedAssetManifest,
)
from BenchmarkAdapters.AutoResearch.broker import CandidateDevBroker
from BenchmarkAdapters.AutoResearch.evaluator import (
    CandidateEvaluator,
    EvaluationStatus,
    EvaluatorManifest,
    parse_attested_val_bpb,
    parse_metrics,
)
from BenchmarkAdapters.AutoResearch.protocol import AutoResearchProtocol, build_protocol
from BenchmarkAdapters.AutoResearch.revisions import TrainRevisionStore
from BenchmarkAdapters.AutoResearch.search import SearchOutcome
from BenchmarkAdapters.AutoResearch.seed_injection import (
    SeedPolicy,
    inject_seed,
    validate_candidate_policy,
)
from BenchmarkAdapters.AutoResearch.supervisor import run_autoresearch
from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.protocol import sha256_file
from BenchmarkAdapters.records import BenchmarkRunResult, RunManifest, RunStatus
from BenchmarkAdapters.protocol import BenchmarkMode


ROOT = Path(__file__).resolve().parents[2]


def _synthetic_train(score: float, *, duplicate: bool = False) -> str:
    duplicate_line = f"print('val_bpb: {score}')\n" if duplicate else ""
    return f"""class Cuda:
    def manual_seed(self, seed):
        pass
class Torch:
    cuda = Cuda()
    def manual_seed(self, seed):
        pass
torch = Torch()
torch.manual_seed(42)
torch.cuda.manual_seed(42)
print('val_bpb: {score}')
{duplicate_line}print('training_seconds: 300.0')
print('total_seconds: 301.0')
print('peak_vram_mb: 10.0')
print('mfu_percent: 1.0')
print('total_tokens_M: 1.0')
print('num_steps: 11')
print('num_params_M: 1.0')
print('depth: 1')
"""


def _revision_store(tmp_path: Path) -> TrainRevisionStore:
    protocol = build_protocol()
    return TrainRevisionStore(
        baseline_source=protocol.source_root,
        baseline_manifest=BaselineManifest.load(protocol.baseline_manifest_path),
        state_dir=tmp_path / "revision-state",
    )


def _synthetic_evaluator(tmp_path: Path) -> CandidateEvaluator:
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    return CandidateEvaluator(
        manifest=EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json"),
        prepared_root=prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=10,
    )


def _attested_store(tmp_path: Path, printed_score: float) -> TrainRevisionStore:
    source = tmp_path / "attested-source"
    source.mkdir()
    prepare = source / "prepare.py"
    train = source / "train.py"
    prepare.write_text("def evaluate_bpb(*args, **kwargs):\n    return 1.02\n", encoding="utf-8")
    train.write_text(
        """import torch
from prepare import evaluate_bpb
torch.manual_seed(42)
torch.cuda.manual_seed(42)
val_bpb = evaluate_bpb(None)
print(f'val_bpb:          PRINTED_SCORE')
print('training_seconds: 300.0')
print('total_seconds:    301.0')
print('peak_vram_mb:     10.0')
print('mfu_percent:      1.0')
print('total_tokens_M:   1.0')
print('num_steps:        11')
print('num_params_M:     1.0')
print('depth:            1')
""".replace("PRINTED_SCORE", str(printed_score)),
        encoding="utf-8",
    )
    manifest = BaselineManifest(
        source_commit="a" * 40,
        source_files={
            "prepare.py": sha256_file(prepare),
            "train.py": sha256_file(train),
        },
        editable_paths=("train.py",),
        protected_paths=("prepare.py",),
        baseline_train_sha256=sha256_file(train),
    )
    return TrainRevisionStore(
        baseline_source=source,
        baseline_manifest=manifest,
        state_dir=tmp_path / "attested-state",
    )


def _copy_prepared_assets(tmp_path: Path) -> Path:
    manifest = PreparedAssetManifest.load(ROOT / "autoresearch/protocol/prepared_manifest.json")
    root = tmp_path / "prepared"
    for relative in manifest.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    return root


def test_frozen_protocol_validates_all_static_assets() -> None:
    protocol = build_protocol()
    protocol.validate()
    assert protocol.candidate_training_seconds == 300
    assert protocol.outer_wall_clock_seconds == 172800
    assert protocol.outer_seeds == (0,)
    assert protocol.outer_model == "configured-by-model-track"
    assert protocol.reasoning_effort == "configured-by-model-track"
    assert protocol.temperature is None
    assert protocol.editable_paths == ("train.py",)
    assert "H100" in protocol.gpu_policy
    assert protocol.formal_baseline_ready is False
    with pytest.raises(AdapterError, match="completed frozen baseline"):
        protocol.require_formal_baseline()


def test_protocol_and_manifest_drift_fail_closed(tmp_path: Path) -> None:
    protocol_path = tmp_path / "protocol.json"
    protocol = build_protocol()
    protocol.write(protocol_path)
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        protocol.write(protocol_path)

    baseline_path = tmp_path / "baseline.json"
    shutil.copy2(protocol.baseline_manifest_path, baseline_path)
    payload = protocol.to_dict()
    payload["baseline_manifest_path"] = str(baseline_path)
    payload["protocol_digest"] = protocol.digest
    mutated_protocol = tmp_path / "mutated-protocol.json"
    mutated_protocol.write_text(json.dumps(payload), encoding="utf-8")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["source_files"]["train.py"] = "0" * 64
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(AdapterError):
        AutoResearchProtocol.load(mutated_protocol)


def test_baseline_source_drift_is_rejected(tmp_path: Path) -> None:
    manifest = BaselineManifest.load(ROOT / "autoresearch/protocol/baseline_manifest.json")
    source = tmp_path / "source"
    shutil.copytree(ROOT / "autoresearch", source, ignore=shutil.ignore_patterns("protocol", "docs", "scripts", "config", "SOURCE.md", ".source-revision"))
    manifest.validate(source)
    (source / "prepare.py").write_text("# drift\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="source drift"):
        manifest.validate(source)


def test_prepared_asset_drift_is_rejected(tmp_path: Path) -> None:
    source_manifest = PreparedAssetManifest.load(ROOT / "autoresearch/protocol/prepared_manifest.json")
    root = tmp_path / "prepared"
    files = {}
    for relative in source_manifest.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
        files[relative] = sha256_file(path)
    manifest = PreparedAssetManifest(source_manifest.protocol_id, "test", files)
    manifest.validate(root)
    (root / "tokenizer/tokenizer.pkl").write_bytes(b"drift")
    with pytest.raises(AdapterError, match="prepared asset drift"):
        manifest.validate(root)


def test_kernel_cache_tree_and_symlink_drift_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "kernel-cache"
    blob = root / "blobs/kernel"
    reference = root / "refs/main"
    module = root / "snapshots" / ("a" * 40) / "build/test-variant/module.so"
    blob.parent.mkdir(parents=True)
    reference.parent.mkdir(parents=True)
    module.parent.mkdir(parents=True)
    blob.write_bytes(b"kernel")
    reference.write_text("a" * 40, encoding="utf-8")
    module.symlink_to("../../../../blobs/kernel")
    manifest = KernelCacheManifest(
        protocol_id="autoresearch-architecture-reconstruction-v1",
        repository="varunneal/flash-attention-3",
        revision="a" * 40,
        variant="torch29-cxx11-cu128-test-variant",
        files={
            "blobs/kernel": sha256_file(blob),
            "refs/main": sha256_file(reference),
        },
        symlinks={
            f"snapshots/{'a' * 40}/build/test-variant/module.so": "../../../../blobs/kernel",
        },
    )
    manifest.validate(root)
    (root / "unexpected").write_text("drift", encoding="utf-8")
    with pytest.raises(AdapterError, match="tree differs"):
        manifest.validate(root)
    (root / "unexpected").unlink()
    module.unlink()
    module.symlink_to("/etc/passwd")
    with pytest.raises(AdapterError, match="symlink drift"):
        manifest.validate(root)


def test_seed_policy_injects_without_mutating_artifact() -> None:
    source = "import torch\ntorch.manual_seed(42)\ntorch.cuda.manual_seed(42)\n"
    original = source.encode("utf-8")
    transformed = inject_seed(source, 314159)
    assert "manual_seed(314159)" in transformed
    assert source.encode("utf-8") == original
    with pytest.raises(AdapterError, match="removed required seed hooks"):
        inject_seed("import torch\ntorch.manual_seed(42)\n", 1)
    with pytest.raises(AdapterError, match="unsupported seed override"):
        inject_seed(source + "random.seed(42)\n", 1)
    with pytest.raises(AdapterError, match="replace evaluator seed hooks"):
        inject_seed(source + "torch.manual_seed = lambda value: None\n", 1)
    with pytest.raises(AdapterError, match="unsupported seed override"):
        inject_seed(source + "torch.seed()\n", 1)
    with pytest.raises(AdapterError, match="import alternate seed hooks"):
        inject_seed(source + "from torch import manual_seed\n", 1)
    valid_candidate = (
        source
        + "from prepare import evaluate_bpb\n"
        + "val_bpb = evaluate_bpb(None)\n"
    )
    validate_candidate_policy(valid_candidate)
    with pytest.raises(AdapterError, match="forbidden reflection"):
        validate_candidate_policy(valid_candidate + "print(evaluate_bpb.__globals__)\n")
    with pytest.raises(AdapterError, match="forbidden module"):
        validate_candidate_policy(valid_candidate + "import inspect\n")
    with pytest.raises(AdapterError, match="replace evaluate_bpb"):
        validate_candidate_policy(valid_candidate + "evaluate_bpb = lambda model: 0.1\n")
    with pytest.raises(AdapterError, match="host attestation markers"):
        validate_candidate_policy(valid_candidate + "print('AUTORESEARCH_HOST_VAL_BPB:0.1')\n")
    with pytest.raises(AdapterError, match="forbidden reflection call"):
        validate_candidate_policy(valid_candidate + "import os\nos._exit(0)\n")
    with pytest.raises(AdapterError, match="forbidden reflection"):
        validate_candidate_policy(valid_candidate + "import os\nprint(os.sys.modules)\n")
    with pytest.raises(AdapterError, match="forbidden reflection helper"):
        validate_candidate_policy(valid_candidate + "from os import _exit as bye\nbye(0)\n")
    policy = SeedPolicy.load(ROOT / "autoresearch/protocol/seed_policy.json")
    assert policy.held_out_seeds == (314159, 271828)


def test_formal_autoresearch_records_reject_dirty_and_invalid_success(tmp_path: Path) -> None:
    protocol = build_protocol()
    manifest = RunManifest(
        run_id="autoresearch-ear-seed-0",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent="ear",
        agent_commit="a" * 40,
        adapter_commit="b" * 40,
        source_dirty=True,
        task_id="architecture-design",
        seed=0,
        model="synthetic-model-v1",
        reasoning_effort="synthetic-effort",
        temperature=None,
        wall_clock_seconds=172800,
        asset_digests={"baseline": protocol.baseline_manifest_digest},
        hardware={"gpu": "H100"},
        policies={"failure": protocol.failure_policy},
    )
    with pytest.raises(AdapterError, match="clean source"):
        manifest.validate()

    invalid = BenchmarkRunResult(
        run_id="autoresearch-ear-seed-0",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest="c" * 64,
        mode=BenchmarkMode.AUTORESEARCH,
        agent="ear",
        task_id="architecture-design",
        seed=0,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=1.0,
        metrics={"dev_val_bpb": 0.9},
        artifact_path=None,
        artifact_sha256=None,
        wall_clock_seconds=1.0,
    )
    with pytest.raises(AdapterError, match="hashed final artifact"):
        invalid.validate()


def test_train_revision_replays_from_baseline_and_rejects_illegal_trees(tmp_path: Path) -> None:
    store = _revision_store(tmp_path)
    revision = store.commit_train_source(
        _synthetic_train(1.05), parent_id="baseline", revision_id="candidate-1"
    )
    assert revision.changed_files == ("train.py",)
    assert "--- baseline/train.py" in revision.unified_diff
    assert "+++ candidate-1/train.py" in revision.unified_diff
    assert revision.created_at
    final = store.replay(revision.revision_id, tmp_path / "final")
    assert sha256_file(final / "train.py") == revision.train_sha256
    baseline = BaselineManifest.load(ROOT / "autoresearch/protocol/baseline_manifest.json")
    assert sha256_file(final / "prepare.py") == baseline.source_files["prepare.py"]

    forbidden = store.checkout("baseline", "forbidden")
    (forbidden / "prepare.py").write_text("drift\n", encoding="utf-8")
    with pytest.raises(AdapterError, match="non-editable"):
        store.commit(forbidden, parent_id="baseline", revision_id="candidate-2")
    linked = store.checkout("baseline", "linked")
    (linked / "escape").symlink_to("/etc/passwd")
    with pytest.raises(AdapterError, match="symlink"):
        store.commit(linked, parent_id="baseline", revision_id="candidate-3")


def test_metric_parser_rejects_duplicate_missing_and_nonfinite_values() -> None:
    manifest = EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json")
    valid_source = "\n".join(
        line.removeprefix("print('").removesuffix("')")
        for line in _synthetic_train(1.01).splitlines()
        if line.startswith("print('")
    )
    assert parse_metrics(valid_source, manifest)["val_bpb"] == 1.01
    with pytest.raises(AdapterError, match="duplicate"):
        parse_metrics(valid_source + "\nval_bpb: 1.02\n", manifest)
    with pytest.raises(AdapterError, match="missing"):
        parse_metrics("val_bpb: 1.0\n", manifest)
    with pytest.raises(AdapterError, match="non-finite"):
        parse_metrics(valid_source.replace("val_bpb: 1.01", "val_bpb: nan"), manifest)
    assert parse_attested_val_bpb("AUTORESEARCH_HOST_VAL_BPB:1.012345\n") == 1.012345
    with pytest.raises(AdapterError, match="exactly one"):
        parse_attested_val_bpb("")
    with pytest.raises(AdapterError, match="exactly one"):
        parse_attested_val_bpb(
            "AUTORESEARCH_HOST_VAL_BPB:1.0\nAUTORESEARCH_HOST_VAL_BPB:1.0\n"
        )


def test_evaluator_executes_disposable_candidate_and_broker_minimizes(tmp_path: Path) -> None:
    store = _revision_store(tmp_path)
    evaluator = _synthetic_evaluator(tmp_path)
    broker = CandidateDevBroker(
        revision_store=store,
        evaluator=evaluator,
        dev_seed=42,
        output_dir=tmp_path / "dev",
        agent="ear",
        outer_seed=0,
    )
    first = broker.create_candidate(_synthetic_train(1.08))
    second = broker.create_candidate(_synthetic_train(1.03))
    first_score = broker.evaluate(first.revision_id)
    second_score = broker.evaluate(second.revision_id)
    assert first_score.evaluation.status is EvaluationStatus.COMPLETED
    assert second_score.evaluation.val_bpb == 1.03
    assert broker.best is not None
    assert broker.best.revision.revision_id == second.revision_id
    assert second.creation_metadata == {
        "agent": "ear",
        "outer_seed": 0,
        "candidate_sequence": 2,
    }
    assert second_score.evaluation.agent == "ear"
    assert second_score.evaluation.outer_seed == 0
    assert second_score.evaluation.candidate_sequence == 2
    assert second_score.evaluation.evaluator_digest
    assert second_score.evaluation.environment_digest
    assert second_score.evaluation.started_at
    assert second_score.evaluation.finished_at
    assert sha256_file(second.path / "train.py") == second.train_sha256
    broker.declare_final(second.revision_id)
    assert broker.declared_revision_id == second.revision_id


def test_evaluator_sandbox_executes_without_network_or_protocol_visibility(
    tmp_path: Path,
) -> None:
    store = _revision_store(tmp_path)
    candidate = store.commit_train_source(
        _synthetic_train(1.02),
        parent_id="baseline",
        revision_id="candidate-1",
    )
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    evaluator = CandidateEvaluator(
        manifest=EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json"),
        prepared_root=prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=10,
        sandbox=True,
        gpu_id="0",
    )
    result = evaluator.evaluate(
        store=store,
        revision_id=candidate.revision_id,
        seed=42,
        output_dir=tmp_path / "sandbox-evaluation",
        evaluation_id="dev-sandbox",
    )
    assert result.score_valid is True
    assert result.val_bpb == 1.02
    assert result.executed_train_sha256 is not None
    assert result.stdout_sha256 is not None
    assert "--unshare-net" in result.command


def test_evaluator_returns_structured_invalid_metrics(tmp_path: Path) -> None:
    store = _revision_store(tmp_path)
    candidate = store.commit_train_source(
        _synthetic_train(1.0, duplicate=True),
        parent_id="baseline",
        revision_id="candidate-1",
    )
    result = _synthetic_evaluator(tmp_path).evaluate(
        store=store,
        revision_id=candidate.revision_id,
        seed=42,
        output_dir=tmp_path / "evaluation",
        evaluation_id="dev-0001",
    )
    assert result.status is EvaluationStatus.INVALID_METRICS
    assert result.score_valid is False
    assert "duplicate" in (result.failure_reason or "")
    assert result.evaluator_digest
    assert result.environment_digest
    assert result.started_at
    assert result.finished_at


def test_evaluator_classifies_timeout_and_oom(tmp_path: Path) -> None:
    timeout_store = _revision_store(tmp_path / "timeout")
    timeout_candidate = timeout_store.commit_train_source(
        "import time\nclass Cuda:\n    def manual_seed(self, seed): pass\n"
        "class Torch:\n    cuda = Cuda()\n    def manual_seed(self, seed): pass\n"
        "torch = Torch()\ntorch.manual_seed(42)\ntorch.cuda.manual_seed(42)\ntime.sleep(5)\n",
        parent_id="baseline",
        revision_id="candidate-timeout",
    )
    prepared = tmp_path / "timeout-prepared"
    prepared.mkdir()
    timeout_evaluator = CandidateEvaluator(
        manifest=EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json"),
        prepared_root=prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=1,
    )
    timeout_result = timeout_evaluator.evaluate(
        store=timeout_store,
        revision_id=timeout_candidate.revision_id,
        seed=42,
        output_dir=tmp_path / "timeout-evaluation",
        evaluation_id="timeout-evaluation",
    )
    assert timeout_result.status is EvaluationStatus.TIMED_OUT
    assert timeout_result.timed_out is True

    oom_store = _revision_store(tmp_path / "oom")
    oom_candidate = oom_store.commit_train_source(
        "import sys\nclass Cuda:\n    def manual_seed(self, seed): pass\n"
        "class Torch:\n    cuda = Cuda()\n    def manual_seed(self, seed): pass\n"
        "torch = Torch()\ntorch.manual_seed(42)\ntorch.cuda.manual_seed(42)\n"
        "print('CUDA out of memory', file=sys.stderr)\nraise SystemExit(1)\n",
        parent_id="baseline",
        revision_id="candidate-oom",
    )
    oom_prepared = tmp_path / "oom-prepared"
    oom_prepared.mkdir()
    oom_result = CandidateEvaluator(
        manifest=EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json"),
        prepared_root=oom_prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=10,
    ).evaluate(
        store=oom_store,
        revision_id=oom_candidate.revision_id,
        seed=42,
        output_dir=tmp_path / "oom-evaluation",
        evaluation_id="oom-evaluation",
    )
    assert oom_result.status is EvaluationStatus.OOM


@pytest.mark.parametrize(
    ("printed_score", "score_valid"),
    ((1.02, True), (0.5, False)),
)
def test_host_runner_attests_evaluate_bpb_in_actual_subprocess(
    tmp_path: Path,
    printed_score: float,
    score_valid: bool,
) -> None:
    store = _attested_store(tmp_path, printed_score)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    evaluator = CandidateEvaluator(
        manifest=EvaluatorManifest.load(ROOT / "autoresearch/protocol/evaluator_manifest.json"),
        prepared_root=prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=10,
        attest_evaluate_bpb=True,
    )
    result = evaluator.evaluate(
        store=store,
        revision_id="baseline",
        seed=42,
        output_dir=tmp_path / "attested-evaluation",
        evaluation_id="attested-evaluation",
    )
    assert result.score_valid is score_valid
    if score_valid:
        assert result.val_bpb == 1.02
    else:
        assert "host-attested" in (result.failure_reason or "")


def test_supervisor_scores_the_agent_declared_revision_not_the_dev_best(
    tmp_path: Path,
) -> None:
    """The Agent's own final choice is scored, even when a better dev candidate exists.

    Recognising which of its candidates is best is part of the Agent's ability, so
    the harness must replay the declared revision (1.08) rather than substituting
    the minimum-dev-score one (1.03).
    """
    protocol = build_protocol()

    def native_runner(context):
        worse = context.broker.create_candidate(_synthetic_train(1.08))
        better = context.broker.create_candidate(_synthetic_train(1.03))
        context.broker.evaluate(worse.revision_id)
        context.broker.evaluate(better.revision_id)
        context.broker.declare_final(worse.revision_id)
        return SearchOutcome(
            native_component="native-ear-kts",
            declared_revision_id=worse.revision_id,
            completed=True,
        )

    result = run_autoresearch(
        agent="ear",
        protocol=protocol,
        prepared_root=tmp_path / "prepared",
        output_dir=tmp_path / "run",
        outer_seed=0,
        search_runner=native_runner,
        evaluator=_synthetic_evaluator(tmp_path),
        formal=False,
        outer_wall_clock_seconds=30,
        run_kind="smoke",
    )
    assert result.score_valid is True
    assert result.score == 1.08
    assert result.metrics["declared_revision_id"] == result.metrics["selected_revision_id"]
    assert result.metrics["selection_policy"] == "agent-declared"
    assert result.metrics["held_out_evaluations_completed"] == 2
    selection = json.loads((tmp_path / "run/selection.json").read_text(encoding="utf-8"))
    assert selection["selection_uses_held_out"] is False
    assert selection["selection_policy_id"] == "agent-declared"
    assert selection["harness_selected_among_candidates"] is False
    assert sha256_file(tmp_path / "run/artifacts/final/train.py") == result.artifact_sha256


def test_supervisor_withholds_score_when_one_held_out_evaluation_fails(
    tmp_path: Path,
) -> None:
    protocol = build_protocol()
    evaluator = _synthetic_evaluator(tmp_path)

    class FailSecondFinal:
        def __init__(self):
            self.calls = 0

        def evaluate(self, **kwargs):
            self.calls += 1
            if self.calls == 3:
                store = kwargs["store"]
                broken = store.commit_train_source(
                    _synthetic_train(1.0, duplicate=True),
                    parent_id=kwargs["revision_id"],
                    revision_id="broken-final",
                )
                kwargs["revision_id"] = broken.revision_id
            return evaluator.evaluate(**kwargs)

    def native_runner(context):
        candidate = context.broker.create_candidate(_synthetic_train(1.04))
        context.broker.evaluate(candidate.revision_id)
        context.broker.declare_final(candidate.revision_id)
        return SearchOutcome("native-ear-kts", candidate.revision_id, True)

    result = run_autoresearch(
        agent="ear",
        protocol=protocol,
        prepared_root=tmp_path / "prepared",
        output_dir=tmp_path / "run",
        outer_seed=0,
        search_runner=native_runner,
        evaluator=FailSecondFinal(),
        formal=False,
        outer_wall_clock_seconds=30,
        run_kind="smoke",
    )
    assert result.score_valid is False
    assert result.score is None
    assert result.status is RunStatus.FAILED
    assert result.metrics["held_out_val_bpb"] == [1.04, None]
