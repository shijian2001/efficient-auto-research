"""Run one FML cell through the shared benchmark and concrete Agent layers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts import AdapterError, require_formal_output_path
from ..formal_contract import write_hashed_json
from ..gpu_locks import gpu_allocation
from ..protocol import BenchmarkMode, canonical_json, sha256_file
from ..records import RunManifest
from ..relay import RelayProcess
from ..registry import AGENTS, ROOT
from ..task_specs import task_spec_digest
from .adapter import FMLRunRequest
from .agents import get_fml_agent_adapter
from .agents.base import FMLAgentLaunchContext, FMLAgentRunResult
from .broker import fml_dev_broker
from .evaluator import FMLSharedEvaluator
from .records import FMLTaskRecord
from .sandbox import sandbox_fml_command
from .task import FMLTaskSpec, load_fml_task
from .workspace import FMLWorkspace


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
    request: FMLRunRequest,
    *,
    task: FMLTaskSpec,
    agent_identity_digest: str,
    rendered_prompt_digest: str,
    initial_workspace_digest: str,
    hardware: dict[str, object] | None = None,
) -> RunManifest:
    agent_commit, agent_dirty = _git_commit(AGENTS[request.agent].install_path)
    adapter_commit, adapter_dirty = _git_commit(ROOT)
    return RunManifest(
        run_id=f"fml-{request.agent}-run-{request.outer_run_index}-{task.task_id}",
        protocol_id=request.protocol.protocol_version,
        protocol_digest=request.protocol.digest,
        mode=BenchmarkMode.FML,
        agent=request.agent,
        agent_commit=agent_commit,
        adapter_commit=adapter_commit,
        source_dirty=agent_dirty or adapter_dirty,
        task_id=task.task_id,
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
        asset_digests={
            **request.protocol.protocol_asset_digests,
            "canonical_task": task.digest,
            "task_config": task.task_config_sha256,
            "initial_workspace": initial_workspace_digest,
            "rendered_prompt": rendered_prompt_digest,
            "agent_identity": agent_identity_digest,
        },
        hardware=hardware
        or {
            "gpu_type": request.protocol.gpu_type,
            "gpus_per_evaluation": request.protocol.gpus_per_evaluation,
            "max_concurrent_evaluations": request.protocol.max_concurrent_evaluations,
        },
        policies={
            "rounds": request.protocol.internal_round_policy,
            "proposals": request.protocol.internal_proposal_policy,
            "adapter": "shared-FML-plus-concrete-native-Agent",
            "evaluator": "host-owned-identical-development-and-heldout",
            "heldout_selection": "one-shot-after-agent-exit",
            "dependencies": request.protocol.allowed_dependency_policy,
            "max_agent_steps": request.protocol.max_agent_steps,
            "max_evaluator_calls": request.protocol.max_evaluator_calls,
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
        allowed_write_paths=task.editable_paths,
        model_config_digest=request.model_config.digest,
        non_comparable=not request.formal,
    )


def _failure_record(
    *,
    request: FMLRunRequest,
    task: FMLTaskSpec,
    manifest_digest: str,
    agent_identity_digest: str,
    prompt_digest: str,
    initial_workspace_digest: str,
    status: str,
    failure: str,
    output_dir: Path,
    development_calls: int,
) -> FMLTaskRecord:
    record = FMLTaskRecord(
        schema_version=2,
        protocol_digest=request.protocol.digest,
        upstream_commit=request.protocol.upstream_commit,
        agent_id=request.agent,
        agent_identity_digest=agent_identity_digest,
        task_id=task.task_id,
        task_config_digest=task.task_config_sha256,
        canonical_task_digest=task.digest,
        rendered_prompt_digest=prompt_digest,
        initial_workspace_digest=initial_workspace_digest,
        outer_run_index=request.outer_run_index,
        status=status,
        score_valid=False,
        raw_test_metric=None,
        displayed_test_metric=None,
        normalized_improvement=None,
        win=None,
        artifact_path=str(output_dir / "final-artifact.tar"),
        artifact_sha256="",
        agent_result_path=str(output_dir / "agent-result.json"),
        agent_result_sha256="",
        evaluation_record_path=str(output_dir / "evaluations/heldout-0001/evaluation-record.json"),
        evaluation_record_sha256="",
        evaluator_digest=task.evaluator_digest,
        manifest_digest=manifest_digest,
        internal_rounds_completed=0,
        internal_proposals_completed=0,
        development_evaluator_calls=development_calls,
        stdout_path=str(output_dir / "stdout.log"),
        stderr_path=str(output_dir / "stderr.log"),
        failure_reason=failure,
    )
    record.write(output_dir / "task-record.json")
    return record


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
    if not 0 <= request.outer_run_index < request.protocol.outer_repetitions:
        raise AdapterError("FML outer run index is outside protocol")
    if request.task_config.resolve() not in {
        path.resolve() for path in request.protocol.task_config_paths
    }:
        raise AdapterError("FML task config is outside the frozen protocol")
    if request.formal and _hardware is None:
        with gpu_allocation(
            request.gpu_ids,
            expected_type=request.protocol.gpu_type,
            gpus_per_evaluation=request.protocol.gpus_per_evaluation,
            max_concurrent_evaluations=request.protocol.max_concurrent_evaluations,
        ) as hardware:
            return run_fml_task(request, _hardware=hardware)
    output_dir.mkdir(parents=True)
    task = load_fml_task(request.protocol, request.task_config)
    canonical_path = output_dir / "canonical-task.json"
    write_hashed_json(
        canonical_path, task.to_dict(), digest_field="canonical_task_record_digest"
    )
    workspace = FMLWorkspace.create(
        upstream_root=request.protocol.upstream_root,
        task=task,
        destination=output_dir / "workspace",
    )
    from .agents.helpers import initialize_repository

    initialize_repository(workspace.root)
    agent_output = output_dir / "agent-output"
    agent_output.mkdir()
    evaluator = FMLSharedEvaluator(
        task=task,
        workspace=workspace,
        evidence_dir=output_dir / "evaluations",
        upstream_root=request.protocol.upstream_root,
        max_calls=request.protocol.max_evaluator_calls,
        environment=request.evaluator_environment,
        direct_execution=request.runtime_executable is not None,
    )
    adapter = get_fml_agent_adapter(request.agent)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stderr_path.write_text("", encoding="utf-8")
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    relay_socket = Path("/tmp") / (
        "fml-relay-"
        + hashlib.sha256(str(output_dir).encode("utf-8")).hexdigest()[:24]
        + ".sock"
    )
    relay_context = (
        RelayProcess(
            agent=request.agent,
            log_path=output_dir / "relay.log",
            token_log_path=output_dir / "relay-token-usage.jsonl",
            unix_socket=relay_socket,
            upstream_base_url=request.model_config.relay_base_url,
            model=request.model_config.outer_model_id,
            model_parameters=request.model_config.model_parameters,
            request_timeout_seconds=request.model_config.request_timeout_seconds,
            retry_policy=request.model_config.retry_policy,
        )
        if request.formal and request.runtime_executable is None
        else nullcontext(None)
    )
    with relay_context as relay, fml_dev_broker(
        evaluator, output_dir / "capability/dev.sock"
    ) as capability:
        context = FMLAgentLaunchContext(
            agent_id=request.agent,
            agent_variant=request.agent_variant,
            task=task,
            workspace=workspace,
            output_dir=agent_output,
            development_socket=capability.socket_path,
            development_token=capability.token,
            development_client_path=Path(__file__).resolve().parent / "dev_client.py",
            model_config=request.model_config,
            outer_run_id=request.protocol.outer_run_ids[request.outer_run_index],
            timeout_seconds=request.protocol.wall_clock_seconds,
            credential_env_names=request.credential_env_names,
            runtime_executable=request.runtime_executable,
            formal=request.formal,
        )
        identity = adapter.identity(context)
        command, prompt_digest = adapter.build_launch_command(context)
        prompt = adapter.render_auditable_task_input(context)
        (output_dir / "rendered-task.md").write_text(prompt, encoding="utf-8")
        manifest = build_fml_manifest(
            request,
            task=task,
            agent_identity_digest=identity.digest,
            rendered_prompt_digest=prompt_digest,
            initial_workspace_digest=workspace.initial_digest,
            hardware=_hardware,
        )
        manifest.validate()
        manifest.write(output_dir / "manifest.json")
        if request.formal and request.runtime_executable is None:
            command = sandbox_fml_command(
                agent_id=request.agent,
                command=command,
                workspace=workspace.root,
                agent_output_dir=agent_output,
                development_socket=capability.socket_path,
                relay_socket=relay.unix_socket,
                editable_paths=task.editable_paths,
            )
        try:
            from ..process import run_command

            command_result = run_command(command, log_path=stdout_path)
            if not context.trajectory_path.exists():
                from ..protocol import write_json_exclusive

                write_json_exclusive(
                    context.trajectory_path,
                    {
                        "native_entrypoint": adapter.native_entrypoint,
                        "return_code": command_result.return_code,
                        "output": command_result.stdout,
                    },
                )
            status, failure_kind = adapter.normalize_exit_status(command_result)
        except AdapterError as exc:
            status = "infrastructure_error"
            failure_kind = f"{type(exc).__name__}: {exc}"
            return _failure_record(
                request=request,
                task=task,
                manifest_digest=manifest.digest,
                agent_identity_digest=identity.digest,
                prompt_digest=prompt_digest,
                initial_workspace_digest=workspace.initial_digest,
                status=status,
                failure=failure_kind,
                output_dir=output_dir,
                development_calls=evaluator.development_calls,
            )
    trajectory_path, trajectory = adapter.collect_trajectory(context)
    token_usage = {
        str(name): int(value)
        for name, value in dict(trajectory.get("token_usage", {})).items()
        if isinstance(value, int) and value >= 0
    }
    adapter.locate_artifact(context)
    if status != "completed":
        return _failure_record(
            request=request,
            task=task,
            manifest_digest=manifest.digest,
            agent_identity_digest=identity.digest,
            prompt_digest=prompt_digest,
            initial_workspace_digest=workspace.initial_digest,
            status=status,
            failure=failure_kind or "agent_failed",
            output_dir=output_dir,
            development_calls=evaluator.development_calls,
        )
    artifact, artifact_digest, _changed = workspace.create_artifact(
        task, output_dir / "final-artifact.tar"
    )
    final_evaluation = evaluator.evaluate_final()
    if final_evaluation.status != "completed":
        return _failure_record(
            request=request,
            task=task,
            manifest_digest=manifest.digest,
            agent_identity_digest=identity.digest,
            prompt_digest=prompt_digest,
            initial_workspace_digest=workspace.initial_digest,
            status="failed",
            failure=final_evaluation.failure_reason or "heldout_evaluation_failed",
            output_dir=output_dir,
            development_calls=evaluator.development_calls,
        )
    finished_at = datetime.now(timezone.utc).isoformat()
    agent_result = FMLAgentRunResult(
        schema_version=1,
        agent_id=request.agent,
        agent_identity={**identity.__dict__, "identity_digest": identity.digest},
        task_id=task.task_id,
        run_id=manifest.run_id,
        outer_run_index=request.outer_run_index,
        status="completed",
        started_at=started_at,
        finished_at=finished_at,
        wall_clock_seconds=time.monotonic() - started,
        artifact_paths=(str(artifact),),
        artifact_sha256=artifact_digest,
        trajectory_path=str(trajectory_path) if trajectory_path else None,
        trajectory_sha256=sha256_file(trajectory_path) if trajectory_path else None,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        exit_code=0,
        failure_kind=None,
        model_track_digest=request.model_config.digest,
        rendered_prompt_digest=prompt_digest,
        canonical_task_digest=task.digest,
        development_evaluator_calls=evaluator.development_calls,
        generated_config_digests=adapter.generated_config_digests(context),
        token_usage=token_usage,
        request_count=trajectory.get("request_count"),
        cost=trajectory.get("cost"),
    )
    agent_result_path = output_dir / "agent-result.json"
    agent_result.write(agent_result_path)
    evaluation_record_path = (
        output_dir / "evaluations/heldout-0001/evaluation-record.json"
    )
    record = FMLTaskRecord(
        schema_version=2,
        protocol_digest=request.protocol.digest,
        upstream_commit=request.protocol.upstream_commit,
        agent_id=request.agent,
        agent_identity_digest=identity.digest,
        task_id=task.task_id,
        task_config_digest=task.task_config_sha256,
        canonical_task_digest=task.digest,
        rendered_prompt_digest=prompt_digest,
        initial_workspace_digest=workspace.initial_digest,
        outer_run_index=request.outer_run_index,
        status="completed",
        score_valid=True,
        raw_test_metric=final_evaluation.raw_metric,
        displayed_test_metric=final_evaluation.displayed_metric,
        normalized_improvement=final_evaluation.normalized_improvement,
        win=final_evaluation.win,
        artifact_path=str(artifact),
        artifact_sha256=artifact_digest,
        agent_result_path=str(agent_result_path),
        agent_result_sha256=sha256_file(agent_result_path),
        evaluation_record_path=str(evaluation_record_path),
        evaluation_record_sha256=sha256_file(evaluation_record_path),
        evaluator_digest=task.evaluator_digest,
        manifest_digest=manifest.digest,
        internal_rounds_completed=1,
        internal_proposals_completed=evaluator.development_calls,
        development_evaluator_calls=evaluator.development_calls,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        token_usage=token_usage,
        request_count=agent_result.request_count,
        cost=agent_result.cost,
    )
    record.write(output_dir / "task-record.json")
    return record


__all__ = ["build_fml_manifest", "run_fml_task"]
