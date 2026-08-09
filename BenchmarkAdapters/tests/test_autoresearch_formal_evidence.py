from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from BenchmarkAdapters.AutoResearch.aggregate import aggregate_autoresearch
from BenchmarkAdapters.AutoResearch.baseline import BaselineManifest
from BenchmarkAdapters.AutoResearch.evaluator import CandidateEvaluation, EvaluationStatus
from BenchmarkAdapters.AutoResearch.seed_injection import SeedPolicy
from BenchmarkAdapters.AutoResearch.supervisor import build_run_manifest
from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.protocol import BenchmarkMode, canonical_json, sha256_file, write_json_exclusive
from BenchmarkAdapters.records import BenchmarkRunResult, RunStatus
from BenchmarkAdapters.task_specs import task_spec_digest


@dataclass(frozen=True)
class SyntheticAutoResearchProtocol:
    baseline_manifest_path: Path
    seed_policy_path: Path
    outer_seeds: tuple[int, ...]
    baseline_ready: bool = True
    protocol_id: str = "synthetic-autoresearch-v1"
    outer_wall_clock_seconds: int = 172800
    outer_model: str = "configured-by-model-track"
    reasoning_effort: str = "configured-by-model-track"
    temperature: float = 0.0
    gpu_policy: str = "one exclusive H100"
    cpu_policy: str = "recorded"
    memory_policy: str = "recorded"
    failure_policy: str = "fail closed"
    artifact_policy: str = "one final train.py"
    aggregation_policy: str = "two held-out records per outer run"

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_seeds)

    @property
    def digest(self) -> str:
        return "d" * 64

    def protocol_asset_digests(self) -> dict[str, str]:
        return {
            "task_spec": task_spec_digest("autoresearch-architecture"),
            "baseline_manifest": sha256_file(self.baseline_manifest_path),
            "seed_policy": sha256_file(self.seed_policy_path),
            "baseline_score_record": "1" * 64,
            "prepared_manifest": "2" * 64,
            "kernel_cache_manifest": "3" * 64,
            "evaluator_manifest": "4" * 64,
            "environment_lock": "5" * 64,
        }

    def validate(self, *_args, **_kwargs) -> None:
        if self.outer_repetitions not in {1, 3}:
            raise AdapterError("invalid synthetic outer repetitions")

    def require_formal_baseline(self) -> object:
        if not self.baseline_ready:
            raise AdapterError("formal Autoresearch baseline status is pending")
        return object()


def _model_config() -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-autoresearch-track",
        outer_model_id="synthetic-model-id",
        relay_base_url="http://relay.invalid/v1",
        model_parameters={"temperature": 0.0, "reasoning_effort": "medium"},
    )


def _protocol(tmp_path: Path, outer_repetitions: int) -> SyntheticAutoResearchProtocol:
    assets = tmp_path / "protocol-assets"
    source = tmp_path / "source"
    assets.mkdir()
    source.mkdir()
    train = source / "train.py"
    train.write_text("print('synthetic final')\n", encoding="utf-8")
    baseline = BaselineManifest(
        source_commit="c" * 40,
        source_files={"train.py": sha256_file(train)},
        editable_paths=("train.py",),
        protected_paths=(),
        baseline_train_sha256=sha256_file(train),
    )
    baseline_path = assets / "baseline_manifest.json"
    write_json_exclusive(
        baseline_path, {**baseline.to_dict(), "manifest_digest": baseline.digest}
    )
    seed_policy = SeedPolicy(
        protocol_id="synthetic-autoresearch-v1",
        dev_seed=100,
        held_out_seeds=(101, 102),
    )
    seed_path = assets / "seed_policy.json"
    write_json_exclusive(
        seed_path, {**seed_policy.to_dict(), "policy_digest": seed_policy.digest}
    )
    return SyntheticAutoResearchProtocol(
        baseline_manifest_path=baseline_path,
        seed_policy_path=seed_path,
        outer_seeds=tuple(range(outer_repetitions)),
    )


def _hardware() -> dict[str, object]:
    return {
        "gpu_name": "NVIDIA H100 80GB HBM3",
        "gpu_uuid": "GPU-SYNTHETIC",
        "gpu_memory_total_mb": 81559,
        "driver_version": "synthetic-driver",
        "compute_mode": "Default",
        "environment_python_sha256": "6" * 64,
        "evaluator_digest": "7" * 64,
        "evaluator_environment_digest": "8" * 64,
    }


def _write_outer_run(
    *,
    protocol: SyntheticAutoResearchProtocol,
    campaign: Path,
    outer_run_index: int,
    formal: bool = True,
) -> Path:
    outer_seed = protocol.outer_seeds[outer_run_index]
    run_dir = campaign / "ear" / f"run-{outer_run_index}"
    manifest = build_run_manifest(
        protocol=protocol,
        agent="ear",
        outer_seed=outer_seed,
        formal=formal,
        model_identity="openai-compatible:synthetic-model-id:endpoint-synthetic",
        hardware=_hardware(),
        model_config=_model_config() if formal else None,
        agent_variant="synthetic-ear-variant" if formal else "smoke",
    )
    artifact = run_dir / "artifacts/final/train.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("print('selected candidate')\n", encoding="utf-8")
    artifact_digest = sha256_file(artifact)
    values = (1.01 + outer_run_index * 0.01, 1.03 + outer_run_index * 0.01)
    for index, (seed, value) in enumerate(zip((101, 102), values), 1):
        evaluation_dir = run_dir / f"final-evaluations/held-out-{index}"
        workspace = evaluation_dir / "workspace"
        workspace.mkdir(parents=True)
        executed = workspace / "train.py"
        executed.write_bytes(artifact.read_bytes())
        stdout = evaluation_dir / "stdout.log"
        stderr = evaluation_dir / "stderr.log"
        stdout.write_text("synthetic evaluator output\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        evaluation = CandidateEvaluation(
            evaluation_id=f"held-out-{index}",
            revision_id="selected-revision",
            candidate_sha256=artifact_digest,
            seed=seed,
            status=EvaluationStatus.COMPLETED,
            score_valid=True,
            val_bpb=value,
            metrics={"val_bpb": value},
            return_code=0,
            timed_out=False,
            wall_clock_seconds=300.0,
            command=("synthetic-evaluator",),
            executed_train_sha256=sha256_file(executed),
            stdout_sha256=sha256_file(stdout),
            stderr_sha256=sha256_file(stderr),
            evaluator_digest="7" * 64,
            environment_digest="8" * 64,
            gpu_id="0",
            gpu_uuid="GPU-SYNTHETIC",
            protocol_digest=protocol.digest,
            benchmark_commit="c" * 40,
        )
        evaluation.validate()
        write_json_exclusive(
            evaluation_dir / "evaluation.json",
            {**evaluation.to_dict(), "evaluation_digest": evaluation.digest},
        )
    manifest.write(run_dir / "manifest.json")
    score = sum(values) / 2
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent="ear",
        task_id="architecture-design",
        seed=outer_seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=score,
        metrics={"claimed_precomputed_mean": score},
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
    formal: bool = True,
) -> tuple[SyntheticAutoResearchProtocol, Path]:
    protocol = _protocol(tmp_path, outer_repetitions)
    campaign = tmp_path / "campaign"
    monkeypatch.setattr(
        "BenchmarkAdapters.AutoResearch.supervisor._git_identity",
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
def test_autoresearch_producer_manifest_two_heldout_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_repetitions: int,
    label: str,
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=outer_repetitions
    )
    aggregate = aggregate_autoresearch(
        protocol=protocol, campaign_dir=campaign, agent="ear"
    )
    assert aggregate["reporting_label"] == label
    assert aggregate["held_out_evaluations_per_outer_run"] == 2
    assert aggregate["outer_runs"][0]["held_out_val_bpb"] == pytest.approx((1.01, 1.03))
    assert aggregate["metrics"]["held_out_final_val_bpb"]["mean"] == pytest.approx(
        1.02 if outer_repetitions == 1 else 1.03
    )
    if outer_repetitions == 1:
        assert aggregate["metrics"]["held_out_final_val_bpb"]["standard_deviation"] is None


@pytest.mark.parametrize("target", ("artifact", "evaluation", "manifest", "baseline"))
def test_autoresearch_aggregate_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "ear/run-0"
    if target == "artifact":
        (run_dir / "artifacts/final/train.py").write_text("tampered", encoding="utf-8")
    elif target == "evaluation":
        path = run_dir / "final-evaluations/held-out-1/evaluation.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["val_bpb"] = 999
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif target == "manifest":
        path = run_dir / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["agent_variant"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        protocol.baseline_manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(AdapterError):
        aggregate_autoresearch(protocol=protocol, campaign_dir=campaign, agent="ear")


def test_autoresearch_requires_exact_shared_asset_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "ear/run-0"
    manifest_path = run_dir / "manifest.json"
    result_path = run_dir / "result.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("manifest_digest")
    manifest["asset_digests"]["unreviewed-extra-asset"] = "9" * 64
    manifest_digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
    manifest_path.write_text(
        json.dumps({**manifest, "manifest_digest": manifest_digest}), encoding="utf-8"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["manifest_digest"] = manifest_digest
    result_path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(AdapterError, match="asset digests differ"):
        aggregate_autoresearch(protocol=protocol, campaign_dir=campaign, agent="ear")


def test_autoresearch_pending_baseline_and_missing_outer_run_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=3)
    pending = SyntheticAutoResearchProtocol(
        baseline_manifest_path=protocol.baseline_manifest_path,
        seed_policy_path=protocol.seed_policy_path,
        outer_seeds=protocol.outer_seeds,
        baseline_ready=False,
    )
    with pytest.raises(AdapterError, match="baseline status is pending"):
        aggregate_autoresearch(protocol=pending, campaign_dir=campaign, agent="ear")
    (campaign / "ear/run-2/result.json").unlink()
    with pytest.raises(AdapterError, match="missing configured outer run"):
        aggregate_autoresearch(protocol=protocol, campaign_dir=campaign, agent="ear")


def test_autoresearch_smoke_manifest_cannot_enter_formal_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=1, formal=False
    )
    with pytest.raises(AdapterError, match="formal policy"):
        aggregate_autoresearch(protocol=protocol, campaign_dir=campaign, agent="ear")
