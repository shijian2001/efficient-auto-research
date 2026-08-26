from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.OptimizerDesign.aggregate import aggregate_optimizer_design
from BenchmarkAdapters.OptimizerDesign.evaluator import OptimizerDesignEvaluation
from BenchmarkAdapters.protocol import BenchmarkMode, sha256_file, write_json_exclusive
from BenchmarkAdapters.records import BenchmarkRunResult, RunManifest, RunStatus
from BenchmarkAdapters.registry import AGENTS
from BenchmarkAdapters.task_specs import task_spec_digest


HELD_OUT_SEEDS = (314159, 271828)


@dataclass(frozen=True)
class SyntheticOptimizerProtocol:
    outer_seeds: tuple[int, ...]
    formal_baseline_ready: bool = True
    protocol_id: str = "synthetic-optimizer-design-v1"
    outer_wall_clock_seconds: int = 172800
    held_out_seeds: tuple[int, int] = HELD_OUT_SEEDS
    target_val_loss: float = 3.28
    significance_margin: float = 0.004
    failure_penalty_steps: int = 3801
    evaluator_manifest_digest: str = "7" * 64
    environment_manifest_digest: str = "8" * 64
    environment_lock_digest: str = "9" * 64
    agent_runtime_manifest_path: Path = Path("/synthetic/agent-runtime.json")
    source_manifest_path: Path = Path("/synthetic/source-manifest.json")
    editable_paths: tuple[str, ...] = ("train_gpt_simple.py",)
    gpu_count: int = 4

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_seeds)

    @property
    def digest(self) -> str:
        return "d" * 64

    def validate(self, *_args, **_kwargs) -> None:
        if self.outer_repetitions not in {1, 3}:
            raise AdapterError("invalid synthetic outer repetitions")

    def protocol_asset_digests(self) -> dict[str, str]:
        return {
            "task_spec": task_spec_digest("optimizer-design"),
            "source_manifest": "1" * 64,
            "data_manifest": "2" * 64,
            "environment_manifest": self.environment_manifest_digest,
            "environment_lock": self.environment_lock_digest,
            "evaluator_manifest": self.evaluator_manifest_digest,
            "agent_runtime_manifest": "3" * 64,
            "baseline_score_record": "4" * 64,
        }


def _model_config() -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-optimizer-track",
        outer_model_id="synthetic-model-id",
        relay_base_url="http://relay.invalid/v1",
        model_parameters={"temperature": 0.0, "reasoning_effort": "medium"},
    )


def _hardware() -> dict[str, object]:
    return {
        "gpu_ids": ["0", "1", "2", "3"],
        "gpus": [
            {
                "gpu_id": str(index),
                "gpu_name": "NVIDIA H100 80GB HBM3",
                "gpu_uuid": f"GPU-SYNTHETIC-{index}",
                "gpu_memory_total_mb": 81559,
                "driver_version": "synthetic-driver",
                "compute_mode": "Default",
            }
            for index in range(4)
        ],
        "gpu_exclusivity": "verified-and-host-locked",
        "uv_lock_digest": "9" * 64,
        "environment_python_sha256": "a" * 64,
        "environment_sha256": "b" * 64,
        "environment_package_fingerprint": "c" * 64,
        "python_version": "3.10.20",
        "torch_version": "2.11.0+cu128",
        "cuda_runtime": "12.8",
        "cpu_affinity": [0, 1],
        "cpu_count": 2,
        "memory_limit_gib": 64,
        "rlimit_as_bytes": 64 * 1024**3,
        "agent_runtime_fingerprint": "e" * 64,
    }


def _write_outer_run(
    *,
    protocol: SyntheticOptimizerProtocol,
    campaign: Path,
    outer_run_index: int,
    formal: bool = True,
) -> Path:
    seed = protocol.outer_seeds[outer_run_index]
    run_dir = campaign / "ear" / f"run-{outer_run_index}"
    model_config = _model_config()
    manifest = RunManifest(
        run_id=f"optimizer-design-ear-run-{outer_run_index}",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.OPTIMIZER_DESIGN,
        agent="ear",
        agent_commit="a" * 40,
        adapter_commit="b" * 40,
        source_dirty=False,
        task_id="track-3-optimizer-design",
        seed=seed,
        model="openai-compatible:synthetic-model-id:endpoint-synthetic",
        reasoning_effort="medium",
        temperature=0.0,
        wall_clock_seconds=protocol.outer_wall_clock_seconds,
        asset_digests=protocol.protocol_asset_digests(),
        hardware=_hardware(),
        policies={
            "native_backend": AGENTS["ear"].optimizer_design_backend,
            "run_kind": "formal" if formal else "smoke",
            "editable": "train_gpt_simple.py",
        },
        formal=formal,
        schema_version=2,
        benchmark_id="optimizer-design",
        protocol_version=protocol.protocol_id,
        agent_variant="synthetic-ear-variant" if formal else "smoke",
        benchmark_commit="f" * 40,
        model_track_id=model_config.model_track_id if formal else "non-formal",
        outer_model_id=model_config.outer_model_id,
        relay_base_url=model_config.relay_base_url,
        model_parameters=model_config.model_parameters,
        outer_repetitions=protocol.outer_repetitions,
        outer_run_index=outer_run_index,
        development_seeds=(42,),
        heldout_seeds=protocol.held_out_seeds,
        gpu_type="H100",
        gpus_per_evaluation=4,
        max_concurrent_evaluations=1,
        task_spec_sha256=task_spec_digest("optimizer-design"),
        allowed_write_paths=protocol.editable_paths,
        model_config_digest=model_config.digest,
        non_comparable=not formal,
    )
    artifact = run_dir / "artifacts/final/train_gpt_simple.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("print('synthetic optimizer candidate')\n", encoding="utf-8")
    artifact_digest = sha256_file(artifact)
    trajectories = (
        ((0, 4.0), (50, 3.275), (100, 3.27)),
        ((0, 4.0), (50, 3.277), (100, 3.27)),
    )
    for index, (held_out_seed, trajectory) in enumerate(
        zip(protocol.held_out_seeds, trajectories), 1
    ):
        evaluation_dir = run_dir / f"final-evaluations/held-out-{index}"
        evaluation_dir.mkdir(parents=True)
        candidate = evaluation_dir / "candidate.py"
        candidate.write_bytes(artifact.read_bytes())
        stdout = evaluation_dir / "stdout.log"
        stdout.write_text("synthetic evaluator output\n", encoding="utf-8")
        evaluation = OptimizerDesignEvaluation(
            evaluation_id=f"held-out-{index}",
            status="completed",
            score_valid=True,
            score_steps=50,
            target_reached=True,
            val_loss=trajectory[1][1],
            train_steps=100,
            seed=held_out_seed,
            candidate_sha256=artifact_digest,
            stdout_sha256=sha256_file(stdout),
            wall_clock_seconds=1.0,
            failure_reason=None,
            validation_trajectory=trajectory,
            protocol_digest=protocol.digest,
            benchmark_commit="f" * 40,
            evaluator_digest=protocol.evaluator_manifest_digest,
            environment_digest=protocol.environment_manifest_digest,
            gpu_ids=("0", "1", "2", "3"),
        )
        write_json_exclusive(
            evaluation_dir / "evaluation.json",
            {**asdict(evaluation), "evaluation_digest": evaluation.digest},
        )
    manifest.write(run_dir / "manifest.json")
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.OPTIMIZER_DESIGN,
        agent="ear",
        task_id="track-3-optimizer-design",
        seed=seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=50.0,
        metrics={
            "primary_metric": "held_out_common_significant_step",
            "development_score_steps": 75,
            "selection_policy": "agent-declared",
        },
        artifact_path=str(artifact.resolve()),
        artifact_sha256=artifact_digest,
        wall_clock_seconds=1.0,
    )
    result.write(run_dir / "result.json")
    (run_dir / "selection.json").write_text(
        json.dumps(
            {
                "declared_revision_id": "candidate-0001",
                "selected_revision_id": "candidate-0001",
                "selection_policy": "agent-declared final artifact",
                "selection_policy_id": "agent-declared",
                "harness_selected_among_candidates": False,
                "development_score_steps": 75,
                "selection_uses_held_out": False,
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
    baseline_ready: bool = True,
) -> tuple[SyntheticOptimizerProtocol, Path]:
    protocol = SyntheticOptimizerProtocol(
        outer_seeds=tuple(range(outer_repetitions)),
        formal_baseline_ready=baseline_ready,
    )
    campaign = tmp_path / "campaign"
    monkeypatch.setattr(
        "BenchmarkAdapters.OptimizerDesign.protocol.SourceManifest.load",
        lambda _path: SimpleNamespace(source_commit="f" * 40),
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.OptimizerDesign.runtime.AgentRuntimeManifest.load",
        lambda _path: SimpleNamespace(
            agents={"ear": SimpleNamespace(fingerprint="e" * 64)}
        ),
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
def test_optimizer_four_h100_two_heldout_trajectory_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_repetitions: int,
    label: str,
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=outer_repetitions
    )
    aggregate = aggregate_optimizer_design(
        protocol=protocol, campaign_dir=campaign, agent="ear"
    )
    assert aggregate["reporting_label"] == label
    assert aggregate["metrics"]["held_out_common_significant_step"]["mean"] == 50.0
    assert aggregate["num_valid_seeds"] == outer_repetitions
    if outer_repetitions == 1:
        assert (
            aggregate["metrics"]["held_out_common_significant_step"][
                "standard_deviation"
            ]
            is None
        )


@pytest.mark.parametrize("target", ("artifact", "evaluation", "manifest", "stdout"))
def test_optimizer_aggregate_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    protocol, campaign = _campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "ear/run-0"
    paths = {
        "artifact": run_dir / "artifacts/final/train_gpt_simple.py",
        "evaluation": run_dir / "final-evaluations/held-out-1/evaluation.json",
        "manifest": run_dir / "manifest.json",
        "stdout": run_dir / "final-evaluations/held-out-1/stdout.log",
    }
    paths[target].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(AdapterError):
        aggregate_optimizer_design(protocol=protocol, campaign_dir=campaign, agent="ear")


def test_optimizer_pending_baseline_and_missing_outer_run_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=1, baseline_ready=False
    )
    with pytest.raises(AdapterError, match="pending baseline"):
        aggregate_optimizer_design(protocol=pending, campaign_dir=campaign, agent="ear")
    protocol, campaign = _campaign(
        tmp_path / "missing", monkeypatch, outer_repetitions=3
    )
    (campaign / "ear/run-2/result.json").unlink()
    with pytest.raises(AdapterError, match="missing or has invalid configured outer runs"):
        aggregate_optimizer_design(protocol=protocol, campaign_dir=campaign, agent="ear")


def test_optimizer_smoke_manifest_cannot_enter_formal_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _campaign(
        tmp_path, monkeypatch, outer_repetitions=1, formal=False
    )
    with pytest.raises(AdapterError, match="missing or has invalid configured outer runs"):
        aggregate_optimizer_design(protocol=protocol, campaign_dir=campaign, agent="ear")
