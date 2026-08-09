"""Benchmark-owned Optimizer Design adapter composed with thin Agent adapters."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from ..artifacts import publish_artifact
from ..AutoResearch.launchers import NativeCommandSearchRunner, NativeLaunchRequest
from ..AutoResearch.model_adapters import model_identity
from ..AutoResearch.search import SearchContext, SearchRunner
from ..contracts import AdapterError, CommandSpec, require_formal_output_path
from ..formal_contract import ModelTrackConfig
from ..protocol import BenchmarkMode, write_json_exclusive
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from ..registry import AGENTS, ROOT
from ..task_specs import task_spec_digest, task_spec_text
from .agents import get_optimizer_design_agent_adapter
from .broker import OptimizerDesignBrokerServer, OptimizerDesignDevBroker
from .evaluator import OptimizerDesignEvaluator, score_validation_trajectories
from .protocol import (
    DEFAULT_DATA_ROOT,
    DEFAULT_ENVIRONMENT_PYTHON,
    DEFAULT_SOURCE_ROOT,
    OptimizerDesignProtocol,
    SourceManifest,
)
from .revisions import OptimizerRevisionStore
from .resource import optimizer_design_resource_lease
from .runtime import AgentRuntimeManifest


@dataclass(frozen=True)
class OptimizerDesignRequest:
    agent: str
    protocol_path: Path
    output_dir: Path
    outer_seed: int
    source_root: Path = DEFAULT_SOURCE_ROOT
    data_root: Path = DEFAULT_DATA_ROOT
    environment_python: Path = DEFAULT_ENVIRONMENT_PYTHON
    gpu_ids: tuple[str, ...] = ()
    cpu_set: str | None = None
    memory_limit_gib: int | None = None
    outer_budget_seconds: int = 1800
    run_kind: str = "smoke"
    native_step_limit: int | None = None
    model_environment: Mapping[str, str] = field(default_factory=dict)
    hardware: Mapping[str, Any] = field(default_factory=dict)
    model_identity: str = "model-adapter-deferred"
    sandbox: bool = True
    dry_run: bool = False
    model_config: ModelTrackConfig | None = None
    agent_variant: str = "default"


def _git_identity(path: Path) -> tuple[str, bool]:
    path = path.resolve()
    top = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    root = Path(top.stdout.strip()).resolve()
    relative = path.relative_to(root)
    identity = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(root),
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
    if identity.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve source identity for {path}")
    return identity.stdout.strip(), bool(dirty.stdout.strip())


def _program(protocol: OptimizerDesignProtocol) -> str:
    return f"""# modded-NanoGPT Track 3 Optimizer Design

This is `{protocol.protocol_id}`, a reconstruction frozen to the current official Track 3
commit, not a bit-for-bit reproduction of the Arbor paper's private workspace.

Edit only `train_gpt_simple.py`. Minimize `score_steps`: the first validation step whose
`val_loss <= {protocol.single_run_threshold}`. A run that does not reach the threshold scores
`{protocol.failure_penalty_steps}`. Keep the dataset, batch size, architecture, distributed
setup, validation loop, and one-forward-backward-per-step rule unchanged. You may change only
optimizer implementation, optimizer hyperparameters and schedules, model initialization, and
the literal `train_steps` bound permitted by the host policy validator.

Use only the host development capability during search. Held-out seeds are unavailable until
the final artifact has been frozen.
"""


class OptimizerDesignBenchmarkAdapter:
    """Owns protocol, policy, scoring, revision, and held-out evaluation semantics."""

    def __init__(self, agent: str) -> None:
        if agent not in AGENTS:
            raise AdapterError(f"unknown Optimizer Design Agent: {agent}")
        self.agent = agent
        self.agent_adapter = get_optimizer_design_agent_adapter(agent)

    def _load_protocol(self, request: OptimizerDesignRequest) -> OptimizerDesignProtocol:
        return OptimizerDesignProtocol.load(
            request.protocol_path,
            source_root=None if request.dry_run else request.source_root,
            data_root=None if request.dry_run else request.data_root,
            environment_python=None if request.dry_run else request.environment_python,
        )

    def build_command(self, request: OptimizerDesignRequest) -> CommandSpec:
        protocol = self._load_protocol(request)
        if request.outer_seed not in protocol.outer_seeds:
            raise AdapterError("Optimizer Design outer seed is outside the frozen protocol")
        model_environment = self.agent_adapter.task_environment(request.model_environment)
        launch_request = NativeLaunchRequest(
            agent=request.agent,
            workspace=request.output_dir.resolve() / "launcher/workspace",
            output_dir=request.output_dir.resolve() / "launcher/native",
            socket_path=request.output_dir.resolve() / "launcher/capability/dev.sock",
            token="dry-run-capability-token",
            outer_seed=request.outer_seed,
            timeout_seconds=request.outer_budget_seconds,
            runtime_root=request.output_dir.resolve() / "launcher/runtime",
            model_environment=model_environment,
            native_step_limit=request.native_step_limit,
        )
        return self.agent_adapter.build_command(launch_request)

    def run(
        self,
        request: OptimizerDesignRequest,
        *,
        evaluator: OptimizerDesignEvaluator | None = None,
        search_runner: SearchRunner | None = None,
    ) -> BenchmarkRunResult:
        protocol = OptimizerDesignProtocol.load(request.protocol_path)
        if request.dry_run:
            raise AdapterError("use build_command for Optimizer Design dry-run inspection")
        if request.run_kind == "formal":
            protocol.require_formal_ready()
            if evaluator is not None or search_runner is not None:
                raise AdapterError(
                    "formal Optimizer Design runs require the protected evaluator and native runner"
                )
        runtime_manifest = AgentRuntimeManifest.load(protocol.agent_runtime_manifest_path)
        runtime_manifest.validate(request.agent)
        if not request.sandbox:
            raise AdapterError("Optimizer Design runs require Agent and evaluator sandboxing")
        allowed_model_environment = {
            "ALL_PROXY",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "AUTORESEARCH_CODEX_BASE_URL",
            "AUTORESEARCH_MODEL",
            "CODEX_API_KEY",
            "GPT_BASE_URL",
            "GPT_CHAT_MODEL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "MODEL",
            "NO_PROXY",
            "OPENAI_API_BASE",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "UPSTREAM_API_KEY",
            "all_proxy",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        unexpected_environment = sorted(set(request.model_environment) - allowed_model_environment)
        if unexpected_environment:
            raise AdapterError(
                "Optimizer Design rejects model environment override: "
                f"{unexpected_environment[0]}"
            )
        configured_model = request.model_environment.get("AUTORESEARCH_MODEL")
        base_url = request.model_environment.get("OPENAI_BASE_URL", "")
        if request.model_config is None:
            raise AdapterError("Optimizer Design requires an explicit model track config")
        request.model_config.validate(formal=request.run_kind == "formal")
        if (
            configured_model != request.model_config.outer_model_id
            or not base_url
            or request.model_identity
            != model_identity(request.model_config.outer_model_id, base_url)
            or not (
                request.model_environment.get("OPENAI_API_KEY")
                or request.model_environment.get("UPSTREAM_API_KEY")
            )
        ):
            raise AdapterError("Optimizer Design run model identity differs from the frozen protocol")
        with optimizer_design_resource_lease(
            protocol=protocol,
            gpu_ids=request.gpu_ids,
            environment_python=request.environment_python,
            cpu_set=request.cpu_set,
            memory_limit_gib=request.memory_limit_gib,
        ) as hardware:
            runtime_record = runtime_manifest.agents[request.agent]
            hardware["agent_runtime_fingerprint"] = runtime_record.fingerprint
            return self._run_attested(
                replace(request, hardware=hardware),
                evaluator=evaluator,
                search_runner=search_runner,
            )

    def _run_attested(
        self,
        request: OptimizerDesignRequest,
        *,
        evaluator: OptimizerDesignEvaluator | None = None,
        search_runner: SearchRunner | None = None,
    ) -> BenchmarkRunResult:
        protocol = OptimizerDesignProtocol.load(request.protocol_path)
        if request.dry_run:
            raise AdapterError("use build_command for Optimizer Design dry-run inspection")
        if request.run_kind not in {"smoke", "pilot", "formal"}:
            raise AdapterError("Optimizer Design run kind must be smoke, pilot, or formal")
        formal = request.run_kind == "formal"
        if formal:
            protocol.require_formal_ready()
            if request.outer_budget_seconds != protocol.outer_wall_clock_seconds:
                raise AdapterError("formal Optimizer Design runs require the frozen 48-hour budget")
            if request.hardware.get("gpu_exclusivity") != "verified-and-host-locked":
                raise AdapterError("formal Optimizer Design runs require attested exclusive GPUs")
        elif not 1 <= request.outer_budget_seconds < protocol.outer_wall_clock_seconds:
            raise AdapterError("Optimizer Design smoke/pilot requires a reduced positive outer budget")
        if request.outer_seed not in protocol.outer_seeds:
            raise AdapterError("Optimizer Design outer seed is outside the frozen protocol")
        protocol.validate(request.source_root, request.data_root)
        agent_commit, agent_dirty = _git_identity(AGENTS[request.agent].install_path)
        adapter_commit, adapter_dirty = _git_identity(ROOT)
        if formal and (agent_dirty or adapter_dirty):
            raise AdapterError("formal Optimizer Design runs require clean Agent and Adapter sources")
        output_dir = request.output_dir.resolve()
        if formal:
            require_formal_output_path(output_dir, ROOT)
        if output_dir.exists() or output_dir.is_symlink():
            raise AdapterError(f"Optimizer Design output already exists: {output_dir}")
        started = time.monotonic()
        source_manifest = SourceManifest.load(protocol.source_manifest_path)
        baseline_path = request.source_root.resolve() / source_manifest.editable_path
        store = OptimizerRevisionStore(
            baseline_path=baseline_path,
            state_dir=output_dir / "revision-state",
        )
        program_path = output_dir / "benchmark-program.md"
        program_path.write_text(_program(protocol), encoding="utf-8")
        evaluator = evaluator or OptimizerDesignEvaluator(
            protocol=protocol,
            source_root=request.source_root,
            data_root=request.data_root,
            environment_python=request.environment_python,
            gpu_ids=request.gpu_ids,
            timeout_seconds=protocol.candidate_timeout_seconds,
            validate_assets=False,
        )
        broker = OptimizerDesignDevBroker(
            revision_store=store,
            evaluator=evaluator,
            development_seed=protocol.development_seed,
            output_dir=output_dir / "dev-evaluations",
            agent=request.agent,
        outer_seed=request.outer_seed,
            outer_deadline_monotonic=started + request.outer_budget_seconds,
        )
        manifest = RunManifest(
            run_id=f"optimizer-design-{request.agent}-seed-{request.outer_seed}",
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            mode=BenchmarkMode.OPTIMIZER_DESIGN,
            agent=request.agent,
            agent_commit=agent_commit,
            adapter_commit=adapter_commit,
            source_dirty=agent_dirty or adapter_dirty,
            task_id="track-3-optimizer-design",
            seed=request.outer_seed,
            model=request.model_identity,
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
            wall_clock_seconds=request.outer_budget_seconds,
            asset_digests=protocol.protocol_asset_digests(),
            hardware={
                **dict(request.hardware),
                "gpu_ids": list(request.gpu_ids),
                "hardware_policy": protocol.hardware_policy,
            },
            policies={
                "editable": source_manifest.editable_path,
                "score": "earliest common two-seed significant step; failure penalty retained",
                "native_backend": self.agent_adapter.native_component,
                "run_kind": request.run_kind,
                "cpu_policy": protocol.cpu_policy,
                "memory_policy": protocol.memory_policy,
            },
            formal=formal,
            schema_version=2,
            benchmark_id="optimizer-design",
            protocol_version=protocol.protocol_id,
            agent_variant=request.agent_variant,
            benchmark_commit=source_manifest.source_commit,
            model_track_id=request.model_config.model_track_id,
            outer_model_id=request.model_config.outer_model_id,
            relay_base_url=request.model_config.relay_base_url,
            model_parameters=request.model_config.model_parameters,
            outer_repetitions=protocol.outer_repetitions,
            outer_run_index=protocol.outer_seeds.index(request.outer_seed),
            development_seeds=(protocol.development_seed,),
            heldout_seeds=protocol.held_out_seeds,
            gpu_type="H100",
            gpus_per_evaluation=4,
            max_concurrent_evaluations=1,
            task_spec_sha256=task_spec_digest("optimizer-design"),
            allowed_write_paths=protocol.editable_paths,
            model_config_digest=request.model_config.digest,
            non_comparable=not formal,
        )
        manifest.write(output_dir / "manifest.json")
        model_environment = self.agent_adapter.task_environment(request.model_environment)
        runner = search_runner or NativeCommandSearchRunner(
            sandbox=request.sandbox,
            model_environment=model_environment,
            native_step_limit=request.native_step_limit,
            command_builder=self.agent_adapter.build_command,
            broker_server_factory=OptimizerDesignBrokerServer,
        )
        try:
            outcome = runner(
                SearchContext(
                    agent=request.agent,
                    native_backend=self.agent_adapter.native_component,
                    outer_seed=request.outer_seed,
                    outer_deadline_monotonic=started + request.outer_budget_seconds,
                    candidate_training_seconds=0,
                    program_path=program_path,
                    baseline_train_path=store.get("baseline").path,
                    output_dir=output_dir / "launcher",
                    broker=broker,
                )
            )
        except Exception as exc:
            result = BenchmarkRunResult(
                run_id=manifest.run_id,
                protocol_id=protocol.protocol_id,
                protocol_digest=protocol.digest,
                manifest_digest=manifest.digest,
                mode=BenchmarkMode.OPTIMIZER_DESIGN,
                agent=request.agent,
                task_id=manifest.task_id,
                seed=request.outer_seed,
                status=RunStatus.FAILED,
                score_valid=False,
                score=None,
                metrics={"dev_evaluations": len(broker.calls)},
                artifact_path=None,
                artifact_sha256=None,
                wall_clock_seconds=time.monotonic() - started,
                failure_reason=f"native search failed: {type(exc).__name__}: {exc}",
            )
            result.write(output_dir / "result.json")
            return result
        best = broker.best
        if not outcome.completed or best is None:
            result = BenchmarkRunResult(
                run_id=manifest.run_id,
                protocol_id=protocol.protocol_id,
                protocol_digest=protocol.digest,
                manifest_digest=manifest.digest,
                mode=BenchmarkMode.OPTIMIZER_DESIGN,
                agent=request.agent,
                task_id=manifest.task_id,
                seed=request.outer_seed,
                status=RunStatus.TIMED_OUT if outcome.timed_out else RunStatus.FAILED,
                score_valid=False,
                score=None,
                metrics={"dev_evaluations": len(broker.calls)},
                artifact_path=None,
                artifact_sha256=None,
                wall_clock_seconds=time.monotonic() - started,
                failure_reason=outcome.failure_reason or "no valid development candidate",
            )
            result.write(output_dir / "result.json")
            return result
        replay = store.replay(
            best.revision.revision_id,
            output_dir / "selected-replay/train_gpt_simple.py",
        )
        artifact = publish_artifact(
            replay,
            output_dir / "artifacts/final/train_gpt_simple.py",
        )
        final_records = [
            evaluator.evaluate(
                replay,
                seed=seed,
                output_dir=output_dir / f"final-evaluations/held-out-{index}",
                evaluation_id=f"held-out-{index}",
            )
            for index, seed in enumerate(protocol.held_out_seeds, 1)
        ]
        score_valid = all(record.score_valid for record in final_records)
        score = None
        common_mean_loss = None
        if score_valid:
            score_steps, common_mean_loss = score_validation_trajectories(
                tuple(record.validation_trajectory for record in final_records),
                target=protocol.target_val_loss,
                significance_margin=protocol.significance_margin,
                penalty=protocol.failure_penalty_steps,
            )
            score = float(score_steps)
        write_json_exclusive(
            output_dir / "selection.json",
            {
                "selected_revision_id": best.revision.revision_id,
                "selection_policy": "minimum valid development score_steps",
                "development_score_steps": best.evaluation.score_steps,
                "selection_uses_held_out": False,
                "artifact_sha256": artifact.sha256,
            },
        )
        token_usage = {}
        if isinstance(outcome.metadata, Mapping):
            raw_usage = outcome.metadata.get("token_usage")
            if isinstance(raw_usage, Mapping):
                token_usage = {
                    str(name): int(value)
                    for name, value in raw_usage.items()
                    if isinstance(value, (int, float)) and value >= 0
                }
        result = BenchmarkRunResult(
            run_id=manifest.run_id,
            protocol_id=protocol.protocol_id,
            protocol_digest=protocol.digest,
            manifest_digest=manifest.digest,
            mode=BenchmarkMode.OPTIMIZER_DESIGN,
            agent=request.agent,
            task_id=manifest.task_id,
            seed=request.outer_seed,
            status=RunStatus.COMPLETED if score_valid else RunStatus.FAILED,
            score_valid=score_valid,
            score=score,
            metrics={
                "primary_metric": "held_out_common_significant_step",
                "development_score_steps": best.evaluation.score_steps,
                "held_out_score_steps": [record.score_steps for record in final_records],
                "held_out_val_loss": [record.val_loss for record in final_records],
                "held_out_common_significant_step": score,
                "held_out_common_mean_val_loss": common_mean_loss,
                "dev_evaluations": len(broker.calls),
                "non_comparable_to_arbor_4xa100": True,
                "run_kind": request.run_kind,
            },
            artifact_path=str(artifact.path),
            artifact_sha256=artifact.sha256,
            wall_clock_seconds=time.monotonic() - started,
            tokens=token_usage,
            failure_reason=None if score_valid else "one or more held-out evaluations failed",
        )
        result.write(output_dir / "result.json")
        write_json_exclusive(
            output_dir / "final-evaluations.json",
            {"evaluations": [asdict(record) for record in final_records]},
        )
        return result


__all__ = ["OptimizerDesignBenchmarkAdapter", "OptimizerDesignRequest"]
