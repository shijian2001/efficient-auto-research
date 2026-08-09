"""Run one thin FML task cell and normalize upstream output into immutable records."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from ..contracts import AdapterError, require_formal_output_path
from ..formal_contract import ModelTrackConfig
from ..gpu_locks import gpu_allocation
from ..process import run_command
from ..protocol import BenchmarkMode, sha256_file, write_json_exclusive
from ..records import RunManifest
from ..registry import AGENTS, ROOT
from ..task_specs import task_spec_digest
from .adapter import FMLBenchmarkAdapter, FMLRunRequest
from .protocol import FMLProtocol
from .records import FMLTaskRecord


def _git_commit(path: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve FML source identity: {path}")
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def build_fml_manifest(
    request: FMLRunRequest, *, hardware: dict[str, object] | None = None
) -> RunManifest:
    agent_commit, agent_dirty = _git_commit(AGENTS[request.agent].install_path)
    adapter_commit, adapter_dirty = _git_commit(ROOT)
    task_id = request.task_config.stem
    return RunManifest(
        run_id=f"fml-{request.agent}-run-{request.outer_run_index}-{task_id}",
        protocol_id=request.protocol.protocol_version,
        protocol_digest=request.protocol.digest,
        mode=BenchmarkMode.FML,
        agent=request.agent,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        source_dirty=agent_dirty or adapter_dirty,
        task_id=task_id,
        seed=request.protocol.outer_run_ids[request.outer_run_index],
        model=request.model_config.outer_model_id,
        reasoning_effort=(
            None
            if request.model_config.model_parameters.get("reasoning_effort") is None
            else str(request.model_config.model_parameters["reasoning_effort"])
        ),
        temperature=(
            None
            if request.model_config.model_parameters.get("temperature") is None
            else float(request.model_config.model_parameters["temperature"])
        ),
        wall_clock_seconds=request.protocol.wall_clock_seconds,
        asset_digests=request.protocol.protocol_asset_digests,
        hardware=hardware
        or {
            "gpu_type": request.protocol.gpu_type,
            "gpus_per_evaluation": request.protocol.gpus_per_evaluation,
            "max_concurrent_evaluations": request.protocol.max_concurrent_evaluations,
        },
        policies={
            "rounds": request.protocol.internal_round_policy,
            "proposals": request.protocol.internal_proposal_policy,
            "adapter": "thin-upstream-FML",
        },
        formal=request.formal,
        schema_version=2,
        benchmark_id="fml-bench",
        protocol_version=request.protocol.protocol_version,
        agent_variant=request.agent_variant,
        benchmark_commit=request.protocol.upstream_commit,
        model_track_id=request.model_config.model_track_id,
        outer_model_id=request.model_config.outer_model_id,
        relay_base_url=request.model_config.relay_base_url,
        model_parameters=request.model_config.model_parameters,
        outer_repetitions=request.protocol.outer_repetitions,
        outer_run_index=request.outer_run_index,
        development_seeds=(),
        heldout_seeds=(),
        gpu_type=request.protocol.gpu_type,
        gpus_per_evaluation=request.protocol.gpus_per_evaluation,
        max_concurrent_evaluations=request.protocol.max_concurrent_evaluations,
        task_spec_sha256=task_spec_digest("fml-bench"),
        allowed_write_paths=request.protocol.allowed_write_paths,
        model_config_digest=request.model_config.digest,
        non_comparable=not request.formal,
    )


def run_fml_task(
    request: FMLRunRequest, *, _hardware: dict[str, object] | None = None
) -> FMLTaskRecord:
    output_dir = request.output_dir.resolve()
    if request.formal:
        require_formal_output_path(output_dir, ROOT)
    if output_dir.exists() or output_dir.is_symlink():
        raise AdapterError(f"FML output already exists: {output_dir}")
    request.protocol.validate(formal=request.formal)
    request.model_config.validate(formal=request.formal)
    if request.formal and _hardware is None:
        with gpu_allocation(
            request.gpu_ids,
            expected_type=request.protocol.gpu_type,
            gpus_per_evaluation=request.protocol.gpus_per_evaluation,
            max_concurrent_evaluations=request.protocol.max_concurrent_evaluations,
        ) as hardware:
            return run_fml_task(request, _hardware=hardware)
    manifest = build_fml_manifest(request, hardware=_hardware)
    manifest.validate()
    output_dir.mkdir(parents=True)
    manifest.write(output_dir / "manifest.json")
    execution_root = output_dir / "upstream-runtime"
    shutil.copytree(
        request.protocol.upstream_root,
        execution_root,
        symlinks=False,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo"),
    )
    task_relative = request.task_config.resolve().relative_to(
        request.protocol.upstream_root.resolve()
    )
    execution_request = __import__("dataclasses").replace(
        request,
        execution_root=execution_root,
        execution_task_config=execution_root / task_relative,
    )
    command = FMLBenchmarkAdapter(request.agent).build_command(execution_request)
    started = time.monotonic()
    try:
        result = run_command(command)
    except AdapterError as exc:
        failed_record = FMLTaskRecord(
            schema_version=1,
            protocol_digest=request.protocol.digest,
            upstream_commit=request.protocol.upstream_commit,
            task_id=request.task_config.stem,
            outer_run_index=request.outer_run_index,
            status="infrastructure_error",
            score_valid=False,
            raw_score=None,
            artifact_path=str(output_dir / request.protocol.artifact_relative_path),
            artifact_sha256="",
            upstream_result_path=str(
                output_dir / request.protocol.upstream_result_relative_path
            ),
            upstream_result_sha256="",
            evaluator_digest=request.protocol.evaluator_digest,
            manifest_digest=manifest.digest,
            internal_rounds_completed=0,
            internal_proposals_completed=0,
            failure_reason=f"{type(exc).__name__}: {exc}",
            token_usage=None,
            request_count=None,
            cost=None,
        )
        failed_record.write(output_dir / "task-record.json")
        raise
    write_json_exclusive(
        output_dir / "command-result.json",
        {
            "return_code": result.return_code,
            "wall_clock_seconds": time.monotonic() - started,
            "stdout": result.stdout,
            "non_formal": not request.formal,
        },
    )
    upstream_result = output_dir / request.protocol.upstream_result_relative_path
    artifact = output_dir / request.protocol.artifact_relative_path
    if result.return_code != 0 or not upstream_result.is_file() or not artifact.is_file():
        record = FMLTaskRecord(
            schema_version=1,
            protocol_digest=request.protocol.digest,
            upstream_commit=request.protocol.upstream_commit,
            task_id=request.task_config.stem,
            outer_run_index=request.outer_run_index,
            status="failed",
            score_valid=False,
            raw_score=None,
            artifact_path=str(artifact),
            artifact_sha256="",
            upstream_result_path=str(upstream_result),
            upstream_result_sha256="",
            evaluator_digest=request.protocol.evaluator_digest,
            manifest_digest=manifest.digest,
            internal_rounds_completed=0,
            internal_proposals_completed=0,
            failure_reason=f"upstream FML command exited {result.return_code} or omitted outputs",
            token_usage=None,
            request_count=None,
            cost=None,
        )
        record.write(output_dir / "task-record.json")
        return record
    payload = json.loads(upstream_result.read_text(encoding="utf-8"))
    score = payload.get(request.protocol.primary_metric_name)
    if not isinstance(score, (int, float)):
        raise AdapterError("FML upstream result omitted the configured primary metric")
    record = FMLTaskRecord(
        schema_version=1,
        protocol_digest=request.protocol.digest,
        upstream_commit=request.protocol.upstream_commit,
        task_id=request.task_config.stem,
        outer_run_index=request.outer_run_index,
        status="completed",
        score_valid=True,
        raw_score=float(score),
        artifact_path=str(artifact.resolve()),
        artifact_sha256=sha256_file(artifact),
        upstream_result_path=str(upstream_result.resolve()),
        upstream_result_sha256=sha256_file(upstream_result),
        evaluator_digest=request.protocol.evaluator_digest,
        manifest_digest=manifest.digest,
        internal_rounds_completed=int(payload.get("internal_rounds_completed", 0)),
        internal_proposals_completed=int(payload.get("internal_proposals_completed", 0)),
        token_usage=(
            {
                str(name): int(value)
                for name, value in payload["token_usage"].items()
            }
            if isinstance(payload.get("token_usage"), dict)
            else None
        ),
        request_count=(
            int(payload["request_count"])
            if payload.get("request_count") is not None
            else None
        ),
        cost=float(payload["cost"]) if payload.get("cost") is not None else None,
    )
    record.write(output_dir / "task-record.json")
    return record


__all__ = ["build_fml_manifest", "run_fml_task"]
