"""Frozen MLE-Bench Lite campaign construction, execution, and aggregation."""

from __future__ import annotations

import json
import hashlib
import math
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts import AdapterError, require_formal_output_path
from ..formal_contract import (
    ModelTrackConfig,
    hardware_comparison_fingerprint,
    validate_gpu_attestation,
)
from ..gpu_locks import gpu_allocation
from ..protocol import BenchmarkMode, FormalProtocol, canonical_json, sha256_file
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from ..registry import AGENT_RUNTIME_IMAGES, AGENTS, ROOT
from ..task_specs import task_spec_digest
from ..thin_registry import require_clean_upstream_source
from .adapter import MleLiteAdapter, MleLiteRequest
from .aggregate import aggregate_seeds, calculate_seed_metrics
from .formal import FormalMleOutcome, run_formal_mle
from .grading import GRADER_WORKER, metric_is_lower_better
from .membership import (
    data_manifest_digest,
    load_lite_task_ids,
    split_digest,
    validate_lite_data_root,
    validate_mlebench_source_identity,
    verify_task_archive,
)


@dataclass(frozen=True)
class MleCampaignCell:
    agent: str
    task_id: str
    seed: int
    run_dir: Path

    @property
    def run_id(self) -> str:
        return f"mle-{self.agent}-{self.task_id}-seed-{self.seed}"


def build_mle_protocol(
    *,
    model: str = "configured-by-model-track",
    seeds: tuple[int, ...] = (0,),
    wall_clock_seconds: int = 86400,
) -> FormalProtocol:
    return FormalProtocol(
        protocol_id="mle-bench-lite-official-22-v1",
        mode=BenchmarkMode.MLE,
        task_ids=load_lite_task_ids(),
        asset_digests={
            "task_spec": task_spec_digest("mle-bench-lite"),
            "data_manifest": data_manifest_digest(),
            "grader_worker": sha256_file(GRADER_WORKER),
            "mlebench_lock": sha256_file(ROOT / "mle-bench-lite/uv.lock"),
            "official_low_split": split_digest(),
        },
        model=model,
        reasoning_effort="configured-by-model-track",
        temperature=None,
        wall_clock_seconds=wall_clock_seconds,
        seeds=seeds,
        retry_policy="no task retry; infrastructure retry keeps the same run identity",
        failure_policy="missing, invalid, timed-out, and failed tasks remain in the denominator as zero",
        artifact_policy="one explicit final submission.csv selected before host-owned grading",
        aggregation_policy="22-task rates per outer run; single_run for N=1 or Avg@3 for N=3",
        hardware_policy="one declared GPU and fixed wall-clock budget per task",
        schema_version=2,
    )


def require_mle_formal_contract(protocol: FormalProtocol) -> None:
    if (
        protocol.schema_version != 2
        or protocol.model != "configured-by-model-track"
        or protocol.reasoning_effort != "configured-by-model-track"
        or protocol.temperature is not None
    ):
        raise AdapterError("formal MLE requires a reviewed schema-v2 model-track protocol")


def validate_mle_protocol(protocol: FormalProtocol, data_root: Path) -> None:
    protocol.validate()
    if protocol.mode is not BenchmarkMode.MLE:
        raise AdapterError("MLE campaign requires an MLE protocol")
    require_mle_formal_contract(protocol)
    expected = validate_lite_data_root(data_root)
    validate_mlebench_source_identity()
    if protocol.task_ids != expected:
        raise AdapterError("MLE protocol task set/order does not match the frozen official Lite split")
    if protocol.asset_digests.get("official_low_split") != split_digest():
        raise AdapterError("MLE protocol split digest does not match the frozen official Lite split")
    if protocol.asset_digests.get("task_spec") != task_spec_digest("mle-bench-lite"):
        raise AdapterError("MLE protocol task specification differs from the canonical task")
    if protocol.asset_digests.get("data_manifest") != data_manifest_digest():
        raise AdapterError("MLE protocol data manifest digest does not match the frozen Lite assets")
    if protocol.asset_digests.get("grader_worker") != sha256_file(GRADER_WORKER):
        raise AdapterError("MLE protocol grader worker digest does not match the host grader")
    if protocol.asset_digests.get("mlebench_lock") != sha256_file(ROOT / "mle-bench-lite/uv.lock"):
        raise AdapterError("MLE protocol dependency lock digest does not match the host grader")


def campaign_cells(
    protocol: FormalProtocol,
    campaign_dir: Path,
    *,
    agents: Iterable[str] = tuple(AGENTS),
) -> tuple[MleCampaignCell, ...]:
    selected_agents = tuple(agents)
    unknown = set(selected_agents) - set(AGENTS)
    if unknown or not selected_agents or len(set(selected_agents)) != len(selected_agents):
        raise AdapterError(f"invalid MLE campaign agent set: {sorted(unknown)}")
    protocol.validate()
    return tuple(
        MleCampaignCell(agent, task_id, seed, campaign_dir / agent / f"seed-{seed}" / task_id)
        for agent in selected_agents
        for seed in protocol.seeds
        for task_id in protocol.task_ids
    )


def generate_ml_master_config(request: MleLiteRequest) -> MleLiteRequest:
    if request.agent != "ml-master-2" or request.config_path is not None:
        return request
    require_clean_upstream_source("ml-master-2")
    source_root = AGENTS["ml-master-2"].install_path
    python = source_root / ".venv/bin/python"
    worker = Path(__file__).with_name("ml_master_config_worker.py")
    destination = request.output_dir.resolve() / "ml-master-config.yaml"
    completed = subprocess.run(
        [
            str(python),
            str(worker),
            "--template",
            str(source_root / "configs/ml_master_2/deepseek-v3.2-example.yaml"),
            "--destination",
            str(destination),
            "--competition-id",
            request.competition_id,
            "--public-dir",
            # The worker runs with cwd=source_root (the ML-Master install dir),
            # so a relative --data-root would resolve against the wrong tree.
            str((request.data_root / request.competition_id / "prepared/public").resolve()),
            "--staged-data-root",
            str(request.output_dir / "public-data"),
            "--workspace-dir",
            str(request.output_dir / "workspace"),
            "--gpu-id",
            str(request.gpu_id),
            "--is-lower-better",
            "true" if metric_is_lower_better(
                competition_id=request.competition_id, data_root=request.data_root
            ) else "false",
            "--model",
            str(request.model),
            "--model-parameters-json",
            json.dumps(request.model_parameters, sort_keys=True),
        ],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AdapterError(f"ML-Master 2 config generation failed: {completed.stderr.strip()}")
    return replace(request, config_path=destination)


def _git_identity(path: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def build_manifest(
    *,
    cell: MleCampaignCell,
    protocol: FormalProtocol,
    gpu_id: int,
    formal: bool = True,
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
    hardware: Mapping[str, Any] | None = None,
) -> RunManifest:
    agent_commit, agent_dirty = _git_identity(AGENTS[cell.agent].install_path)
    adapter_commit, adapter_dirty = _git_identity(ROOT)
    if formal:
        if model_config is None:
            raise AdapterError("formal MLE requires an explicit model track config")
        model_config.validate(formal=True)
    data_manifest = __import__(
        "BenchmarkAdapters.MLEBenchLite.membership", fromlist=["load_data_manifest"]
    ).load_data_manifest()
    benchmark_commit = str(data_manifest["mlebench_source_commit"])
    outer_run_index = protocol.seeds.index(cell.seed)
    return RunManifest(
        run_id=cell.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        mode=BenchmarkMode.MLE,
        agent=cell.agent,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        source_dirty=agent_dirty or adapter_dirty,
        task_id=cell.task_id,
        seed=cell.seed,
        model=model_config.outer_model_id if model_config else protocol.model,
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
        wall_clock_seconds=protocol.wall_clock_seconds,
        asset_digests=protocol.asset_digests,
        hardware=hardware
        or {"gpu_id": gpu_id, "gpu_type": "RTX 4090", "gpu_count": 1},
        policies={
            "retry": protocol.retry_policy,
            "failure": protocol.failure_policy,
            "artifact": protocol.artifact_policy,
            "aggregation": protocol.aggregation_policy,
            "hardware": protocol.hardware_policy,
            "outer_run_identity": (
                "replication_id; Agent-internal deterministic seeding is not asserted"
            ),
        },
        formal=formal,
        schema_version=2,
        benchmark_id="mle-bench-lite",
        protocol_version=protocol.protocol_id,
        agent_variant=agent_variant,
        benchmark_commit=benchmark_commit,
        model_track_id=model_config.model_track_id if model_config else "non-formal",
        outer_model_id=model_config.outer_model_id if model_config else protocol.model,
        relay_base_url=model_config.relay_base_url if model_config else "non-formal://local",
        model_parameters=model_config.model_parameters if model_config else {},
        outer_repetitions=len(protocol.seeds),
        outer_run_index=outer_run_index,
        development_seeds=(),
        heldout_seeds=(),
        gpu_type="RTX 4090",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=1,
        task_spec_sha256=task_spec_digest("mle-bench-lite"),
        allowed_write_paths=("submission.csv",),
        model_config_digest=model_config.digest if model_config else None,
        non_comparable=not formal,
    )


def run_campaign_cell(
    *,
    cell: MleCampaignCell,
    protocol: FormalProtocol,
    data_root: Path,
    gpu_id: int = 0,
    formal: bool = True,
    model_config: ModelTrackConfig | None = None,
    agent_variant: str = "default",
    _hardware: Mapping[str, Any] | None = None,
) -> FormalMleOutcome | BenchmarkRunResult:
    validate_mle_protocol(protocol, data_root)
    verify_task_archive(data_root, cell.task_id, verify_hash=True)
    if formal and _hardware is None:
        with gpu_allocation(
            (str(gpu_id),),
            expected_type="RTX 4090",
            gpus_per_evaluation=1,
            max_concurrent_evaluations=1,
        ) as hardware:
            return run_campaign_cell(
                cell=cell,
                protocol=protocol,
                data_root=data_root,
                gpu_id=gpu_id,
                formal=formal,
                model_config=model_config,
                agent_variant=agent_variant,
                _hardware=hardware,
            )
    if formal:
        require_formal_output_path(cell.run_dir, ROOT)
    if model_config is None:
        raise AdapterError("MLE campaign cell requires an explicit model track config")
    model_config.validate(formal=formal)
    request = MleLiteRequest(
        agent=cell.agent,
        competition_id=cell.task_id,
        data_root=data_root,
        output_dir=cell.run_dir / "agent-output",
        gpu_id=gpu_id,
        steps=1000,
        seed=cell.seed,
        model=model_config.outer_model_id,
        upstream_base_url=model_config.relay_base_url,
        max_turns=1000,
        timeout_seconds=protocol.wall_clock_seconds,
        model_parameters=dict(model_config.model_parameters),
        request_timeout_seconds=model_config.request_timeout_seconds,
        retry_policy=dict(model_config.retry_policy),
        agent_variant=agent_variant,
        # Agents that run their work inside a container need one named here:
        # the campaign, not just adapter_smoke, has to be able to say which
        # image a formal cell may use. See registry.AGENT_RUNTIME_IMAGES.
        runtime_image=AGENT_RUNTIME_IMAGES.get(cell.agent, (None, None))[0],
        image_pull_policy=AGENT_RUNTIME_IMAGES.get(cell.agent, (None, None))[1],
    )
    started = time.monotonic()
    manifest = build_manifest(
        cell=cell,
        protocol=protocol,
        gpu_id=gpu_id,
        formal=formal,
        model_config=model_config,
        agent_variant=agent_variant,
        hardware=_hardware,
    )
    manifest.validate()
    try:
        request = generate_ml_master_config(request)
        return run_formal_mle(request=request, manifest=manifest, run_dir=cell.run_dir, log_path=cell.run_dir / "agent.log")
    except Exception as exc:
        if not (cell.run_dir / "manifest.json").exists():
            manifest.write(cell.run_dir / "manifest.json")
        artifact_path = cell.run_dir / "artifacts/final/submission.csv"
        artifact_sha256 = (
            sha256_file(artifact_path)
            if artifact_path.is_file() and not artifact_path.is_symlink()
            else None
        )
        report_path = cell.run_dir / "grading/competition_report.json"
        try:
            metrics = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            if report_path.is_file():
                metrics["grader_report_file_sha256"] = sha256_file(report_path)
        except json.JSONDecodeError:
            metrics = {}
        failure_text = f"{type(exc).__name__}: {exc}"
        if artifact_sha256 is not None and report_path.is_file():
            status = RunStatus.INVALID_ARTIFACT
        elif isinstance(exc, subprocess.TimeoutExpired) or "timed out" in failure_text.lower():
            status = RunStatus.TIMED_OUT
        else:
            status = RunStatus.FAILED
        result = BenchmarkRunResult(
            run_id=cell.run_id,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            manifest_digest=manifest.digest,
            mode=BenchmarkMode.MLE,
            agent=cell.agent,
            task_id=cell.task_id,
            seed=cell.seed,
            status=status,
            score_valid=False,
            score=None,
            metrics=metrics,
            artifact_path=str(artifact_path) if artifact_sha256 is not None else None,
            artifact_sha256=artifact_sha256,
            wall_clock_seconds=time.monotonic() - started,
            failure_reason=failure_text,
        )
        result.write(cell.run_dir / "result.json")
        return result


def aggregate_campaign(protocol: FormalProtocol, campaign_dir: Path, agent: str) -> dict[str, Any]:
    if agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {agent}")
    protocol.validate()
    require_mle_formal_contract(protocol)
    seed_metrics = []
    raw_scores: dict[str, dict[str, float]] = {}
    model_config_digests: set[str] = set()
    model_track_ids: set[str] = set()
    hardware_fingerprints: set[str] = set()
    adapter_commits: set[str] = set()
    agent_commits: set[str] = set()
    data_manifest = __import__(
        "BenchmarkAdapters.MLEBenchLite.membership", fromlist=["load_data_manifest"]
    ).load_data_manifest()
    benchmark_commit = str(data_manifest["mlebench_source_commit"])
    for outer_run_index, seed in enumerate(protocol.seeds):
        reports: dict[str, Mapping[str, Any] | None] = {}
        usage: dict[str, Mapping[str, float | int | None]] = {}
        raw_scores[str(seed)] = {}
        for task_id in protocol.task_ids:
            legacy = campaign_dir / agent / f"seed-{seed}" / task_id
            indexed = campaign_dir / agent / f"run-{outer_run_index}" / task_id
            run_dir = indexed if (indexed / "result.json").is_file() else legacy
            report_path = run_dir / "grading/competition_report.json"
            result_path = run_dir / "result.json"
            manifest_path = run_dir / "manifest.json"
            if not all(
                path.is_file() and not path.is_symlink()
                for path in (result_path, manifest_path)
            ):
                raise AdapterError(
                    f"MLE aggregate is missing formal evidence for outer run {outer_run_index}, task {task_id}"
                )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_digest = manifest.pop("manifest_digest", None)
            if manifest_digest != hashlib.sha256(canonical_json(manifest)).hexdigest():
                raise AdapterError(f"MLE immutable manifest digest mismatch: {task_id}")
            if not (
                result.get("protocol_id") == protocol.protocol_id
                and result.get("protocol_digest") == protocol.digest
                and result.get("manifest_digest") == manifest_digest
                and result.get("mode") == BenchmarkMode.MLE.value
                and result.get("agent") == agent
                and result.get("task_id") == task_id
                and int(result.get("seed")) == seed
                and manifest.get("schema_version") == 2
                and manifest.get("formal") is True
                and manifest.get("source_dirty") is False
                and manifest.get("benchmark_id") == "mle-bench-lite"
                and manifest.get("benchmark_commit") == benchmark_commit
                and len(str(manifest.get("agent_commit", ""))) == 40
                and len(str(manifest.get("adapter_commit", ""))) == 40
                and manifest.get("agent_variant") not in {None, "", "default"}
                and len(str(manifest.get("model_config_digest", ""))) == 64
                and bool(manifest.get("model_track_id"))
                and bool(manifest.get("outer_model_id"))
                and manifest.get("outer_repetitions") == len(protocol.seeds)
                and manifest.get("outer_run_index") == outer_run_index
                and manifest.get("wall_clock_seconds") == protocol.wall_clock_seconds
                and manifest.get("asset_digests") == dict(sorted(protocol.asset_digests.items()))
                and manifest.get("task_spec_sha256") == task_spec_digest("mle-bench-lite")
                and manifest.get("allowed_write_paths") == ["submission.csv"]
                and manifest.get("gpu_type") == "RTX 4090"
                and manifest.get("gpus_per_evaluation") == 1
                and manifest.get("max_concurrent_evaluations") == 1
            ):
                raise AdapterError(f"MLE formal result or manifest identity is invalid: {task_id}")
            hardware = manifest.get("hardware")
            if not isinstance(hardware, dict):
                raise AdapterError(f"MLE formal hardware attestation is missing: {task_id}")
            validate_gpu_attestation(
                hardware,
                expected_type="RTX 4090",
                gpus_per_evaluation=1,
                max_concurrent_evaluations=1,
            )
            model_config_digests.add(str(manifest.get("model_config_digest", "")))
            model_track_ids.add(str(manifest.get("model_track_id", "")))
            adapter_commits.add(str(manifest.get("adapter_commit", "")))
            agent_commits.add(str(manifest.get("agent_commit", "")))
            hardware_fingerprints.add(hardware_comparison_fingerprint(hardware))
            status = str(result.get("status", ""))
            if status != RunStatus.COMPLETED.value:
                if not (
                    status
                    in {
                        RunStatus.FAILED.value,
                        RunStatus.TIMED_OUT.value,
                        RunStatus.INVALID_ARTIFACT.value,
                        RunStatus.INFRASTRUCTURE_ERROR.value,
                    }
                    and result.get("score_valid") is False
                    and result.get("score") is None
                    and str(result.get("failure_reason", "")).strip()
                ):
                    raise AdapterError(f"MLE failed task evidence is invalid: {task_id}")
                artifact_value = result.get("artifact_path")
                artifact_digest = result.get("artifact_sha256")
                if artifact_value is not None or artifact_digest is not None:
                    artifact = Path(str(artifact_value or ""))
                    expected_artifact = (
                        run_dir / "artifacts/final/submission.csv"
                    ).resolve()
                    if (
                        artifact.resolve() != expected_artifact
                        or not artifact.is_file()
                        or artifact.is_symlink()
                        or len(str(artifact_digest or "")) != 64
                        or sha256_file(artifact) != artifact_digest
                    ):
                        raise AdapterError(f"MLE failed task artifact hash mismatch: {task_id}")
                if report_path.exists() or report_path.is_symlink():
                    if not report_path.is_file() or report_path.is_symlink():
                        raise AdapterError(f"MLE failed task grader evidence is unsafe: {task_id}")
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    report_digest = report.pop("grader_report_digest", None)
                    if report_digest != hashlib.sha256(canonical_json(report)).hexdigest():
                        raise AdapterError(f"MLE failed task grader report digest mismatch: {task_id}")
                    metrics = result.get("metrics")
                    if not (
                        report.get("schema_version") == 2
                        and report.get("competition_id") == task_id
                        and report.get("submission_sha256") == artifact_digest
                        and isinstance(metrics, dict)
                        and metrics.get("grader_report_digest") == report_digest
                        and metrics.get("grader_report_file_sha256") == sha256_file(report_path)
                    ):
                        raise AdapterError(
                            f"MLE failed task grader report is not bound to its artifact: {task_id}"
                        )
                reports[task_id] = None
                usage[task_id] = {"tokens": None, "cost": None}
                continue
            if result.get("score_valid") is not True:
                raise AdapterError(f"MLE completed task does not contain a valid score: {task_id}")
            if not report_path.is_file() or report_path.is_symlink():
                raise AdapterError(f"MLE completed task grader report is missing: {task_id}")
            artifact = Path(str(result.get("artifact_path", "")))
            artifact_digest = str(result.get("artifact_sha256", ""))
            expected_artifact = (run_dir / "artifacts/final/submission.csv").resolve()
            if (
                artifact.resolve() != expected_artifact
                or not artifact.is_file()
                or artifact.is_symlink()
                or len(artifact_digest) != 64
                or sha256_file(artifact) != artifact_digest
            ):
                raise AdapterError(f"MLE submission artifact hash mismatch: {task_id}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_digest = report.pop("grader_report_digest", None)
            if report_digest != hashlib.sha256(canonical_json(report)).hexdigest():
                raise AdapterError(f"MLE grader report digest mismatch: {task_id}")
            report["grader_report_digest"] = report_digest
            metrics = result.get("metrics")
            if not (
                report.get("schema_version") == 2
                and report.get("competition_id") == task_id
                and report.get("submission_sha256") == artifact_digest
                and report.get("valid_submission") is True
                and report.get("score") is not None
                and isinstance(metrics, dict)
                and metrics.get("grader_report_digest") == report_digest
                and metrics.get("grader_report_file_sha256") == sha256_file(report_path)
                and math.isclose(float(result.get("score")), float(report["score"]), rel_tol=0, abs_tol=1e-12)
            ):
                raise AdapterError(f"MLE grader report is not bound to the result artifact: {task_id}")
            reports[task_id] = report
            raw_scores[str(seed)][task_id] = float(report["score"])
            token_payload = result.get("tokens")
            cost_payload = result.get("cost")
            usage[task_id] = {
                "tokens": (
                    sum(int(value) for value in token_payload.values())
                    if isinstance(token_payload, dict)
                    and token_payload
                    and all(value is not None for value in token_payload.values())
                    else None
                ),
                "cost": (
                    sum(float(value) for value in cost_payload.values())
                    if isinstance(cost_payload, dict)
                    and cost_payload
                    and all(value is not None for value in cost_payload.values())
                    else None
                ),
            }
        if set(reports) != set(protocol.task_ids):
            raise AdapterError("MLE aggregate does not contain the complete frozen 22-task denominator")
        seed_metrics.append(
            calculate_seed_metrics(seed=seed, task_ids=protocol.task_ids, reports=reports, usage=usage)
        )
    if any(len(values) != 1 for values in (
        model_config_digests,
        model_track_ids,
        hardware_fingerprints,
        adapter_commits,
        agent_commits,
    )):
        raise AdapterError("MLE formal task cells mix model, hardware, or adapter tracks")
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.MLE.value,
        "agent": agent,
        "raw_scores_by_seed_and_task": raw_scores,
        "model_config_digest": next(iter(model_config_digests)),
        "model_track_id": next(iter(model_track_ids)),
        "hardware_fingerprint": next(iter(hardware_fingerprints)),
        "adapter_commit": next(iter(adapter_commits)),
        "agent_commit": next(iter(agent_commits)),
        **aggregate_seeds(seed_metrics, outer_repetitions=len(protocol.seeds)),
    }


def mle_scorecard(protocol: FormalProtocol, campaign_dir: Path) -> dict[str, Any]:
    agents: dict[str, Any] = {}
    ranking: list[dict[str, Any]] = []
    for agent in AGENTS:
        try:
            payload = aggregate_campaign(protocol, campaign_dir, agent)
        except AdapterError as exc:
            agents[agent] = {"formal_score_valid": False, "failure_reason": str(exc)}
            continue
        payload["formal_score_valid"] = True
        agents[agent] = payload
        ranking.append(
            {
                "agent": agent,
                "score": payload["metrics"]["any_medal_rate"]["mean"],
            }
        )
    ranking.sort(key=lambda item: float(item["score"]), reverse=True)
    comparison_keys = {
        (
            payload.get("model_config_digest"),
            payload.get("hardware_fingerprint"),
            payload.get("adapter_commit"),
        )
        for payload in agents.values()
        if payload.get("formal_score_valid")
    }
    complete = len(ranking) == len(AGENTS) and len(comparison_keys) == 1
    return {
        "schema_version": 2,
        "benchmark_id": "mle-bench-lite",
        "protocol_digest": protocol.digest,
        "primary_metric": "any_medal_rate",
        "metric_direction": "maximize",
        "outer_repetitions": len(protocol.seeds),
        "reporting_label": "single_run" if len(protocol.seeds) == 1 else "avg_at_3",
        "agents": agents,
        "formal_ranking": ranking if complete else [],
        "same_model_hardware_track_valid": len(comparison_keys) == 1,
        "complete_seven_agent_comparison_valid": complete,
    }


__all__ = [
    "MleCampaignCell",
    "aggregate_campaign",
    "build_manifest",
    "build_mle_protocol",
    "campaign_cells",
    "generate_ml_master_config",
    "mle_scorecard",
    "require_mle_formal_contract",
    "run_campaign_cell",
    "validate_mle_protocol",
]
