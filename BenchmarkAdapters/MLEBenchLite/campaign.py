"""Frozen MLE-Bench Lite campaign construction, execution, and aggregation."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts import AdapterError
from ..protocol import BenchmarkMode, FormalProtocol, sha256_file
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from ..registry import AGENTS, ROOT
from .adapter import MleLiteAdapter, MleLiteRequest
from .aggregate import aggregate_seeds, calculate_seed_metrics
from .formal import FormalMleOutcome, run_formal_mle
from .grading import GRADER_WORKER
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
    model: str = "gpt-5.5",
    seeds: tuple[int, ...] = (0, 1, 2),
    wall_clock_seconds: int = 86400,
) -> FormalProtocol:
    return FormalProtocol(
        protocol_id="mle-bench-lite-official-22-v1",
        mode=BenchmarkMode.MLE,
        task_ids=load_lite_task_ids(),
        asset_digests={
            "data_manifest": data_manifest_digest(),
            "grader_worker": sha256_file(GRADER_WORKER),
            "mlebench_lock": sha256_file(ROOT / "mle-bench-lite/uv.lock"),
            "official_low_split": split_digest(),
        },
        model=model,
        reasoning_effort="high",
        temperature=1.0,
        wall_clock_seconds=wall_clock_seconds,
        seeds=seeds,
        retry_policy="no task retry; infrastructure retry keeps the same run identity",
        failure_policy="missing, invalid, timed-out, and failed tasks remain in the denominator as zero",
        artifact_policy="one explicit final submission.csv selected before host-owned grading",
        aggregation_policy="per-seed 22-task rates, then mean/std/SEM/95% CI across at least three seeds",
        hardware_policy="one declared GPU and fixed wall-clock budget per task",
    )


def validate_mle_protocol(protocol: FormalProtocol, data_root: Path) -> None:
    protocol.validate()
    if protocol.mode is not BenchmarkMode.MLE:
        raise AdapterError("MLE campaign requires an MLE protocol")
    expected = validate_lite_data_root(data_root)
    validate_mlebench_source_identity()
    if protocol.task_ids != expected:
        raise AdapterError("MLE protocol task set/order does not match the frozen official Lite split")
    if protocol.asset_digests.get("official_low_split") != split_digest():
        raise AdapterError("MLE protocol split digest does not match the frozen official Lite split")
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
            str(request.data_root / request.competition_id / "prepared/public"),
            "--staged-data-root",
            str(request.output_dir / "public-data"),
            "--workspace-dir",
            str(request.output_dir / "ml-master-workspace"),
            "--gpu-id",
            str(request.gpu_id),
            "--timeout",
            str(request.timeout_seconds),
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
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
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
) -> RunManifest:
    agent_commit, agent_dirty = _git_identity(AGENTS[cell.agent].install_path)
    adapter_commit, adapter_dirty = _git_identity(ROOT)
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
        model=protocol.model,
        reasoning_effort=protocol.reasoning_effort,
        temperature=protocol.temperature,
        wall_clock_seconds=protocol.wall_clock_seconds,
        asset_digests=protocol.asset_digests,
        hardware={"gpu_id": gpu_id},
        policies={
            "retry": protocol.retry_policy,
            "failure": protocol.failure_policy,
            "artifact": protocol.artifact_policy,
            "aggregation": protocol.aggregation_policy,
            "hardware": protocol.hardware_policy,
        },
        formal=formal,
    )


def run_campaign_cell(
    *,
    cell: MleCampaignCell,
    protocol: FormalProtocol,
    data_root: Path,
    gpu_id: int = 0,
    formal: bool = True,
) -> FormalMleOutcome | BenchmarkRunResult:
    validate_mle_protocol(protocol, data_root)
    verify_task_archive(data_root, cell.task_id)
    request = MleLiteRequest(
        agent=cell.agent,
        competition_id=cell.task_id,
        data_root=data_root,
        output_dir=cell.run_dir / "agent-output",
        gpu_id=gpu_id,
        steps=1000,
        seed=cell.seed,
        model=protocol.model,
        max_turns=1000,
        timeout_seconds=protocol.wall_clock_seconds,
    )
    started = time.monotonic()
    manifest = build_manifest(cell=cell, protocol=protocol, gpu_id=gpu_id, formal=formal)
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
    seed_metrics = []
    raw_scores: dict[str, dict[str, float]] = {}
    for seed in protocol.seeds:
        reports: dict[str, Mapping[str, Any] | None] = {}
        usage: dict[str, Mapping[str, float | int]] = {}
        raw_scores[str(seed)] = {}
        for task_id in protocol.task_ids:
            run_dir = campaign_dir / agent / f"seed-{seed}" / task_id
            report_path = run_dir / "grading/competition_report.json"
            result_path = run_dir / "result.json"
            if report_path.is_file() and result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if (
                    result.get("protocol_digest") == protocol.digest
                    and result.get("agent") == agent
                    and result.get("task_id") == task_id
                    and int(result.get("seed")) == seed
                    and result.get("status") == RunStatus.COMPLETED.value
                ):
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    reports[task_id] = report
                    raw_scores[str(seed)][task_id] = float(report["score"])
                    usage[task_id] = {
                        "tokens": sum(int(value) for value in result.get("tokens", {}).values()),
                        "cost": sum(float(value) for value in result.get("cost", {}).values()),
                    }
        seed_metrics.append(
            calculate_seed_metrics(seed=seed, task_ids=protocol.task_ids, reports=reports, usage=usage)
        )
    return {
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": BenchmarkMode.MLE.value,
        "agent": agent,
        "raw_scores_by_seed_and_task": raw_scores,
        **aggregate_seeds(seed_metrics),
    }


__all__ = [
    "MleCampaignCell",
    "aggregate_campaign",
    "build_manifest",
    "build_mle_protocol",
    "campaign_cells",
    "generate_ml_master_config",
    "run_campaign_cell",
    "validate_mle_protocol",
]
