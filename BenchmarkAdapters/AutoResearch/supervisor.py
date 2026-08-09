"""Host-owned outer supervisor for Autoresearch Architecture Design."""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path

from ..artifacts import PublishedArtifact, publish_artifact
from ..contracts import AdapterError, require_formal_output_path
from ..formal_contract import ModelTrackConfig
from ..protocol import BenchmarkMode, write_json_exclusive
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from ..registry import AGENTS, ROOT
from .baseline import BaselineManifest, KernelCacheManifest, PreparedAssetManifest
from .broker import CandidateDevBroker
from .evaluator import CandidateEvaluation, CandidateEvaluator, EvaluationStatus, EvaluatorManifest
from .protocol import AutoResearchProtocol
from .revisions import TrainRevisionStore
from .search import SearchContext, SearchOutcome, SearchRunner
from .seed_injection import SeedPolicy
from ..task_specs import task_spec_digest


def _git_identity(path: Path) -> tuple[str, bool]:
    path = path.resolve()
    top_level = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top_level.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    git_root = Path(top_level.stdout.strip()).resolve()
    relative = path.relative_to(git_root)
    commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(git_root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            relative.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    identity = commit.stdout.strip()
    if len(identity) != 40:
        raise AdapterError(f"invalid source commit identity for {path}")
    return identity, bool(dirty.stdout.strip())


def build_run_manifest(
    *,
    protocol: AutoResearchProtocol,
    agent: str,
    outer_seed: int,
    formal: bool,
    model_identity: str = "model-adapter-deferred",
    hardware: dict[str, object] | None = None,
    wall_clock_seconds: int | None = None,
    run_kind: str = "formal",
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
) -> RunManifest:
    if agent not in AGENTS:
        raise AdapterError(f"unknown Autoresearch Agent: {agent}")
    agent_commit, agent_dirty = _git_identity(AGENTS[agent].install_path)
    adapter_commit, adapter_dirty = _git_identity(ROOT)
    if formal:
        if model_config is None:
            raise AdapterError("formal Autoresearch requires an explicit model track config")
        model_config.validate(formal=True)
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    seed_policy = SeedPolicy.load(protocol.seed_policy_path)
    outer_run_index = protocol.outer_seeds.index(outer_seed)
    return RunManifest(
        run_id=f"autoresearch-{agent}-seed-{outer_seed}",
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent=agent,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        source_dirty=agent_dirty or adapter_dirty,
        task_id="architecture-design",
        seed=outer_seed,
        model=model_identity,
        reasoning_effort=(
            None
            if model_config is None or model_config.model_parameters.get("reasoning_effort") is None
            else str(model_config.model_parameters["reasoning_effort"])
        ),
        temperature=(
            None
            if model_config is None or model_config.model_parameters.get("temperature") is None
            else float(model_config.model_parameters["temperature"])
        ),
        wall_clock_seconds=wall_clock_seconds or protocol.outer_wall_clock_seconds,
        asset_digests=protocol.protocol_asset_digests(),
        hardware=hardware
        or {
            "gpu_policy": protocol.gpu_policy,
            "cpu_policy": protocol.cpu_policy,
            "memory_policy": protocol.memory_policy,
        },
        policies={
            "failure": protocol.failure_policy,
            "artifact": protocol.artifact_policy,
            "aggregation": protocol.aggregation_policy,
            "native_backend": AGENTS[agent].autoresearch_backend,
            "run_kind": run_kind,
        },
        formal=formal,
        schema_version=2,
        benchmark_id="autoresearch-architecture",
        protocol_version=protocol.protocol_id,
        agent_variant=agent_variant,
        benchmark_commit=baseline.source_commit,
        model_track_id=model_config.model_track_id if model_config else "non-formal",
        outer_model_id=model_config.outer_model_id if model_config else protocol.outer_model,
        relay_base_url=model_config.relay_base_url if model_config else "non-formal://local",
        model_parameters=model_config.model_parameters if model_config else {},
        outer_repetitions=protocol.outer_repetitions,
        outer_run_index=outer_run_index,
        development_seeds=(seed_policy.dev_seed,),
        heldout_seeds=seed_policy.held_out_seeds,
        gpu_type="H100",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=1,
        task_spec_sha256=task_spec_digest("autoresearch-architecture"),
        allowed_write_paths=("train.py",),
        model_config_digest=model_config.digest if model_config else None,
        non_comparable=not formal,
    )


def publish_final_train(source: Path, destination: Path) -> PublishedArtifact:
    artifact = publish_artifact(source, destination)
    digest_path = destination.with_suffix(destination.suffix + ".sha256")
    try:
        with digest_path.open("x", encoding="utf-8") as handle:
            handle.write(f"{artifact.sha256}  {destination.name}\n")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite final artifact digest: {digest_path}") from exc
    return artifact


def _failed_result(
    *,
    manifest: RunManifest,
    protocol: AutoResearchProtocol,
    agent: str,
    outer_seed: int,
    status: RunStatus,
    reason: str,
    started: float,
    metrics: dict[str, object],
    artifact: PublishedArtifact | None = None,
) -> BenchmarkRunResult:
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent=agent,
        task_id="architecture-design",
        seed=outer_seed,
        status=status,
        score_valid=False,
        score=None,
        metrics=metrics,
        artifact_path=str(artifact.path) if artifact else None,
        artifact_sha256=artifact.sha256 if artifact else None,
        wall_clock_seconds=time.monotonic() - started,
        failure_reason=reason,
    )
    return result


def _final_gate(
    *,
    protocol: AutoResearchProtocol,
    seed_policy: SeedPolicy,
    store: TrainRevisionStore,
    evaluator: CandidateEvaluator,
    revision_id: str,
    output_dir: Path,
    agent: str,
    outer_seed: int,
) -> tuple[tuple[CandidateEvaluation, ...], float | None]:
    records: list[CandidateEvaluation] = []
    for index, seed in enumerate(seed_policy.held_out_seeds, 1):
        evaluation = evaluator.evaluate(
            store=store,
            revision_id=revision_id,
            seed=seed,
            output_dir=output_dir / f"held-out-{index}",
            evaluation_id=f"held-out-{index}",
            agent=agent,
            outer_seed=outer_seed,
            candidate_sequence=index,
        )
        records.append(evaluation)
    valid = [record for record in records if record.score_valid and record.val_bpb is not None]
    if len(valid) != 2:
        return tuple(records), None
    return tuple(records), sum(float(record.val_bpb) for record in valid) / 2


def _search_tokens(search_outcome: SearchOutcome) -> dict[str, int]:
    metadata = search_outcome.metadata
    if not isinstance(metadata, dict):
        return {}
    payload = metadata.get("token_usage")
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): int(value)
        for name, value in payload.items()
        if isinstance(value, (int, float)) and value >= 0
    }


def _run_autoresearch_once(
    *,
    agent: str,
    protocol: AutoResearchProtocol,
    prepared_root: Path,
    output_dir: Path,
    outer_seed: int,
    search_runner: SearchRunner,
    evaluator: CandidateEvaluator,
    formal: bool = True,
    model_identity: str = "model-adapter-deferred",
    hardware: dict[str, object] | None = None,
    outer_wall_clock_seconds: int | None = None,
    run_kind: str = "formal",
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
) -> BenchmarkRunResult:
    """Run one Agent × outer-seed cell and enforce the sealed final score gate."""

    started = time.monotonic()
    run_budget = outer_wall_clock_seconds or protocol.outer_wall_clock_seconds
    if formal and (run_budget != protocol.outer_wall_clock_seconds or run_kind != "formal"):
        raise AdapterError("formal Autoresearch run must use the frozen 48-hour budget and run kind")
    if formal:
        protocol.require_formal_baseline()
    if not formal and (run_kind not in {"smoke", "pilot"} or not 1 <= run_budget < protocol.outer_wall_clock_seconds):
        raise AdapterError("non-formal Autoresearch run requires a reduced smoke or pilot budget")
    output_dir = output_dir.resolve()
    if formal:
        require_formal_output_path(output_dir, ROOT)
    if output_dir.exists() or output_dir.is_symlink():
        raise AdapterError(f"Autoresearch output already exists: {output_dir}")
    if agent not in AGENTS:
        raise AdapterError(f"unknown Autoresearch Agent: {agent}")
    if formal:
        if model_config is None:
            raise AdapterError("formal Autoresearch requires an explicit model track config")
        model_config.validate(formal=True)
        expected_model_prefix = f"openai-compatible:{model_config.outer_model_id}:endpoint-"
        if not model_identity.startswith(expected_model_prefix):
            raise AdapterError("formal Autoresearch model identity differs from model track")
    if outer_seed not in protocol.outer_seeds:
        raise AdapterError(f"outer seed is not registered by the Autoresearch protocol: {outer_seed}")
    protocol.validate(
        prepared_root if formal else None,
        evaluator.kernel_cache_root if formal else None,
    )
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    prepared_manifest = PreparedAssetManifest.load(protocol.prepared_manifest_path)
    kernel_cache_manifest = KernelCacheManifest.load(protocol.kernel_cache_manifest_path)
    seed_policy = SeedPolicy.load(protocol.seed_policy_path)
    if formal:
        if hardware is None:
            raise AdapterError("formal Autoresearch run requires verified hardware/environment metadata")
        if "H100" not in str(hardware.get("gpu_name", "")):
            raise AdapterError("formal Autoresearch run requires verified H100 hardware")
        if int(hardware.get("gpu_memory_total_mb", 0)) < 75000:
            raise AdapterError("formal Autoresearch run requires an H100 80GB memory policy")
        if not hardware.get("cpu_affinity") or int(hardware.get("cpu_count", 0)) < 1:
            raise AdapterError("formal Autoresearch run requires an enforced CPU affinity")
        if int(hardware.get("rlimit_as_bytes", 0)) < 64 * 1024**3:
            raise AdapterError("formal Autoresearch run requires an enforced RAM limit")
        if hardware.get("uv_lock_digest") != protocol.environment_lock_digest:
            raise AdapterError("formal Autoresearch environment record differs from the UV lock")
        if hardware.get("uv_locked_offline_dry_run") is not True:
            raise AdapterError("formal Autoresearch environment was not validated offline")
        if not str(hardware.get("environment_python_sha256", "")):
            raise AdapterError("formal Autoresearch environment Python digest is missing")
        if "verified no existing compute process" not in str(
            hardware.get("gpu_exclusivity", "")
        ):
            raise AdapterError("formal Autoresearch GPU exclusivity was not verified")
        if evaluator.prepared_root != prepared_root.resolve():
            raise AdapterError("formal Autoresearch evaluator prepared root differs from the run")
        if evaluator.manifest != EvaluatorManifest.load(protocol.evaluator_manifest_path):
            raise AdapterError("formal Autoresearch evaluator configuration differs from the protocol")
        if evaluator.protocol_digest != protocol.digest:
            raise AdapterError("formal Autoresearch evaluator is not bound to the protocol digest")
        if evaluator.benchmark_commit != baseline.source_commit:
            raise AdapterError("formal Autoresearch evaluator is not bound to the benchmark commit")
        if (
            not evaluator.sandbox
            or not evaluator.enforce_wall_clock_budget
            or not evaluator.attest_evaluate_bpb
        ):
            raise AdapterError(
                "formal Autoresearch evaluator requires sandbox, wall-clock enforcement, "
                "and host evaluate_bpb attestation"
            )
        if evaluator.prepared_manifest != prepared_manifest:
            raise AdapterError("formal Autoresearch evaluator prepared manifest differs from the protocol")
        if evaluator.kernel_cache_manifest != kernel_cache_manifest:
            raise AdapterError("formal Autoresearch evaluator kernel cache manifest differs from protocol")
    attested_hardware = dict(hardware or {})
    attested_hardware.update(
        {
            "evaluator_digest": evaluator.evaluator_digest,
            "evaluator_environment_digest": evaluator.environment_digest,
        }
    )
    manifest = build_run_manifest(
        protocol=protocol,
        agent=agent,
        outer_seed=outer_seed,
        formal=formal,
        model_identity=model_identity,
        hardware=attested_hardware,
        wall_clock_seconds=run_budget,
        run_kind=run_kind,
        model_config=model_config,
        agent_variant=agent_variant,
    )
    manifest.validate()
    output_dir.mkdir(parents=True)
    manifest.write(output_dir / "manifest.json")
    store = TrainRevisionStore(
        baseline_source=protocol.source_root,
        baseline_manifest=baseline,
        state_dir=output_dir / "revision-state",
    )
    broker = CandidateDevBroker(
        revision_store=store,
        evaluator=evaluator,
        dev_seed=seed_policy.dev_seed,
        output_dir=output_dir / "dev-evaluations",
        agent=agent,
        outer_seed=outer_seed,
    )
    search_outcome: SearchOutcome
    try:
        search_outcome = search_runner(
            SearchContext(
                agent=agent,
                native_backend=AGENTS[agent].autoresearch_backend,
                outer_seed=outer_seed,
                outer_deadline_monotonic=started + run_budget,
                candidate_training_seconds=protocol.candidate_training_seconds,
                program_path=protocol.source_root / "program.md",
                baseline_train_path=store.get("baseline").path / "train.py",
                output_dir=output_dir / "launcher",
                broker=broker,
            )
        )
    except Exception as exc:
        result = _failed_result(
            manifest=manifest,
            protocol=protocol,
            agent=agent,
            outer_seed=outer_seed,
            status=RunStatus.FAILED,
            reason=f"native search failed: {type(exc).__name__}: {exc}",
            started=started,
            metrics={
                "primary_metric": "held_out_final_val_bpb_mean",
                "held_out_evaluations_completed": 0,
                "dev_evaluations": len(broker.calls),
            },
        )
        result.write(output_dir / "result.json")
        return result
    best = broker.best
    declaration = search_outcome.declared_revision_id or broker.declared_revision_id
    if declaration is not None and broker.declared_revision_id is None:
        broker.declare_final(declaration)
    native_matches = search_outcome.native_component == AGENTS[agent].autoresearch_backend
    failure_reason = None
    if not native_matches:
        failure_reason = "native search component differs from the registered Agent backend"
    elif not search_outcome.completed:
        failure_reason = search_outcome.failure_reason or "native search did not complete"
    elif declaration is None:
        failure_reason = "native search did not declare a replayable final revision"
    elif best is None:
        failure_reason = "native search produced no valid dev-scored revision"
    elif time.monotonic() > started + run_budget and not search_outcome.timed_out:
        failure_reason = "native search exceeded the registered outer budget"
    if failure_reason is not None:
        status = RunStatus.TIMED_OUT if search_outcome.timed_out else RunStatus.FAILED
        result = _failed_result(
            manifest=manifest,
            protocol=protocol,
            agent=agent,
            outer_seed=outer_seed,
            status=status,
            reason=failure_reason,
            started=started,
            metrics={
                "primary_metric": "held_out_final_val_bpb_mean",
                "held_out_evaluations_completed": 0,
                "dev_evaluations": len(broker.calls),
                "native_component": search_outcome.native_component,
            },
        )
        result.write(output_dir / "result.json")
        return result

    selected = best
    replay = store.replay(selected.revision.revision_id, output_dir / "selected-replay")
    artifact = publish_final_train(replay / "train.py", output_dir / "artifacts/final/train.py")
    write_json_exclusive(
        output_dir / "selection.json",
        {
            "declared_revision_id": declaration,
            "selected_revision_id": selected.revision.revision_id,
            "selection_policy": "minimum valid development val_bpb",
            "selection_uses_held_out": False,
            "dev_val_bpb": selected.evaluation.val_bpb,
            "train_sha256": selected.revision.train_sha256,
            "published_sha256": artifact.sha256,
            "dev_evaluations": len(broker.calls),
        },
    )
    if artifact.sha256 != selected.revision.train_sha256:
        result = _failed_result(
            manifest=manifest,
            protocol=protocol,
            agent=agent,
            outer_seed=outer_seed,
            status=RunStatus.INVALID_ARTIFACT,
            reason="published train.py hash differs from selected revision",
            started=started,
            metrics={
                "primary_metric": "held_out_final_val_bpb_mean",
                "held_out_evaluations_completed": 0,
                "dev_selected_val_bpb": selected.evaluation.val_bpb,
            },
            artifact=artifact,
        )
        result.write(output_dir / "result.json")
        return result

    if formal:
        prepared_manifest.validate(prepared_root)
    final_records, final_mean = _final_gate(
        protocol=protocol,
        seed_policy=seed_policy,
        store=store,
        evaluator=evaluator,
        revision_id=selected.revision.revision_id,
        output_dir=output_dir / "final-evaluations",
        agent=agent,
        outer_seed=outer_seed,
    )
    final_values = [record.val_bpb for record in final_records]
    common_metrics: dict[str, object] = {
        "primary_metric": "held_out_final_val_bpb_mean",
        "dev_selected_val_bpb": selected.evaluation.val_bpb,
        "dev_evaluations": len(broker.calls),
        "valid_dev_evaluations": sum(item.evaluation.score_valid for item in broker.calls),
        "failed_dev_evaluations": sum(not item.evaluation.score_valid for item in broker.calls),
        "held_out_evaluations_required": 2,
        "held_out_evaluations_completed": len(final_records),
        "held_out_val_bpb": final_values,
        "held_out_statuses": [record.status.value for record in final_records],
        "native_component": search_outcome.native_component,
        "declared_revision_id": declaration,
        "selected_revision_id": selected.revision.revision_id,
        "candidate_gpu_seconds": sum(item.evaluation.wall_clock_seconds for item in broker.calls),
        "time_to_best_seconds": selected.completed_elapsed_seconds,
        "candidate_to_best": selected.evaluation_sequence,
        "run_kind": run_kind,
        "non_comparable": not formal,
        "outer_budget_seconds": run_budget,
    }
    if final_mean is None or not math.isfinite(final_mean):
        status = (
            RunStatus.INVALID_ARTIFACT
            if any(record.status is EvaluationStatus.INVALID_ARTIFACT for record in final_records)
            else RunStatus.FAILED
        )
        result = _failed_result(
            manifest=manifest,
            protocol=protocol,
            agent=agent,
            outer_seed=outer_seed,
            status=status,
            reason="both sealed held-out evaluations must be valid",
            started=started,
            metrics=common_metrics,
            artifact=artifact,
        )
        result.write(output_dir / "result.json")
        return result
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.AUTORESEARCH,
        agent=agent,
        task_id="architecture-design",
        seed=outer_seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=final_mean,
        metrics={**common_metrics, "final_val_bpb_mean": final_mean},
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
        wall_clock_seconds=time.monotonic() - started,
        tokens=_search_tokens(search_outcome),
    )
    result.write(output_dir / "result.json")
    return result


def run_autoresearch(
    *,
    agent: str,
    protocol: AutoResearchProtocol,
    prepared_root: Path,
    output_dir: Path,
    outer_seed: int,
    search_runner: SearchRunner,
    evaluator: CandidateEvaluator,
    formal: bool = True,
    model_identity: str = "model-adapter-deferred",
    hardware: dict[str, object] | None = None,
    outer_wall_clock_seconds: int | None = None,
    run_kind: str = "formal",
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
) -> BenchmarkRunResult:
    started = time.monotonic()
    try:
        return _run_autoresearch_once(
            agent=agent,
            protocol=protocol,
            prepared_root=prepared_root,
            output_dir=output_dir,
            outer_seed=outer_seed,
            search_runner=search_runner,
            evaluator=evaluator,
            formal=formal,
            model_identity=model_identity,
            hardware=hardware,
            outer_wall_clock_seconds=outer_wall_clock_seconds,
            run_kind=run_kind,
            model_config=model_config,
            agent_variant=agent_variant,
        )
    except Exception as exc:
        output_dir = output_dir.resolve()
        manifest_path = output_dir / "manifest.json"
        result_path = output_dir / "result.json"
        if not manifest_path.is_file() or result_path.exists():
            raise
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact_path = output_dir / "artifacts/final/train.py"
        artifact_sha256 = None
        if artifact_path.is_file() and not artifact_path.is_symlink():
            from ..protocol import sha256_file

            artifact_sha256 = sha256_file(artifact_path)
        result = BenchmarkRunResult(
            run_id=str(manifest_payload["run_id"]),
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            manifest_digest=str(manifest_payload["manifest_digest"]),
            mode=BenchmarkMode.AUTORESEARCH,
            agent=agent,
            task_id="architecture-design",
            seed=outer_seed,
            status=RunStatus.INFRASTRUCTURE_ERROR,
            score_valid=False,
            score=None,
            metrics={
                "primary_metric": "held_out_final_val_bpb_mean",
                "held_out_evaluations_completed": len(
                    tuple((output_dir / "final-evaluations").glob("held-out-*/evaluation.json"))
                ),
                "dev_scores_used_as_primary": False,
            },
            artifact_path=str(artifact_path) if artifact_sha256 else None,
            artifact_sha256=artifact_sha256,
            wall_clock_seconds=time.monotonic() - started,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )
        result.write(result_path)
        return result


__all__ = [
    "build_run_manifest",
    "_run_autoresearch_once",
    "publish_final_train",
    "run_autoresearch",
]
