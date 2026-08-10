"""Arbor FML adapter using the native coordinator CLI."""

from __future__ import annotations

import json
from pathlib import Path
import shlex

from ...arbor_thin import write_arbor_config
from ...contracts import AdapterError, CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from ...thin_registry import require_clean_upstream_source
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class ArborFMLAdapter(FMLAgentAdapter):
    agent_id = "arbor"
    native_entrypoint = "arbor run"

    def installation_executable(self) -> Path | None:
        return ROOT / "baselines/Arbor/.venv/bin/arbor"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        require_clean_upstream_source("arbor")
        return _build_arbor_command(
            self,
            context,
            _arbor_prompt(context),
            label="Arbor official CLI FML loop",
            benchmark_limits=False,
        )

    def generated_config_digests(
        self, context: FMLAgentLaunchContext
    ) -> dict[str, str]:
        return _generated_config_digests(context)


class ArborBenchmarkPatchedFMLAdapter(FMLAgentAdapter):
    agent_id = "arbor"
    native_entrypoint = "patched Arbor CoordinatorOrchestrator.run"

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/bin/arbor"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        return _build_arbor_command(
            self,
            context,
            prompt,
            label="Arbor benchmark-patched FML loop",
            benchmark_limits=True,
        )

    def generated_config_digests(
        self, context: FMLAgentLaunchContext
    ) -> dict[str, str]:
        return _generated_config_digests(context)


def _build_arbor_command(
    adapter: FMLAgentAdapter,
    context: FMLAgentLaunchContext,
    prompt: str,
    *,
    label: str,
    benchmark_limits: bool,
) -> CommandSpec:
    executable = adapter.installation_executable()
    if executable is None or not executable.is_file():
        raise AdapterError("Arbor FML runtime is not installed")
    generated = {
        "schema_version": 1,
        "model_track_digest": context.model_config.digest,
        "task_spec_digest": context.task.digest,
        "wall_clock_seconds": context.timeout_seconds,
    }
    if benchmark_limits:
        generated["max_cycles"] = context.task.max_agent_steps
    context.output_dir.mkdir(parents=True, exist_ok=True)
    config_path = context.output_dir / "arbor-fml-config.json"
    write_json_exclusive(config_path, generated)
    config_options: dict[str, object] = {}
    if not benchmark_limits:
        config_options = {
            "eval_command": _arbor_eval_command(context),
            "metric_direction": (
                "maximize" if context.task.metric_direction == "higher" else "minimize"
            ),
            "protected_paths": _protected_paths(context),
            "required_outputs": context.task.editable_paths,
        }
    cli_config_path = write_arbor_config(
        context.output_dir / "arbor-thin-config.yaml",
        model=context.model_config.outer_model_id,
        base_url=context.relay_base_url,
        model_parameters=context.model_config.model_parameters,
        **config_options,
    )
    argv = [
        str(executable),
        "run",
        prompt,
        "--cwd",
        str(context.workspace.root),
        "--yes",
        "--yes-cwd",
        str(context.workspace.root),
        "--workspace-dir",
        str(context.output_dir / "arbor-session"),
        "--config",
        str(cli_config_path),
        "--interaction-mode",
        "auto",
        "--no-followup",
        "--no-webui",
    ]
    if benchmark_limits:
        argv.extend(
            (
                "--max-cycles",
                str(context.task.max_agent_steps),
                "--max-turns",
                str(max(context.task.max_agent_steps, context.task.max_agent_steps * 4)),
            )
        )
    return CommandSpec(
        argv=tuple(argv),
        cwd=context.workspace.root,
        env={
            "FML_ARBOR_CONFIG_SHA256": __import__("hashlib").sha256(
                __import__("json").dumps(generated, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        timeout_seconds=context.timeout_seconds,
        label=label,
        inherit_env=False,
    )


def _generated_config_digests(context: FMLAgentLaunchContext) -> dict[str, str]:
    paths = (
        context.output_dir / "arbor-fml-config.json",
        context.output_dir / "arbor-thin-config.yaml",
        context.output_dir / "plugins/benchmark_dev.yaml",
    )
    return {
        path.name: __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def _arbor_eval_command(context: FMLAgentLaunchContext) -> str:
    socket_path = (
        Path("/capability/dev.sock")
        if context.formal
        else context.development_socket
    )
    argv = [
        "/usr/bin/python3",
        str(context.development_client_path),
        "--socket",
        str(socket_path),
        "--candidate-root",
        "{cwd}",
    ]
    for relative in context.task.editable_paths:
        argv.extend(("--editable", relative))
    return shlex.join(argv) + ' --token "$FML_DEVELOPMENT_TOKEN"'


def _arbor_prompt(context: FMLAgentLaunchContext) -> str:
    editable = ", ".join(context.task.editable_paths)
    return (
        f"Optimize the frozen FML task {context.task.task_id}. "
        f"{context.task.task_description} Edit only: {editable}. "
        f"The metric is {context.task.metric}; {context.task.metric_direction} is better. "
        "The official project plugin injects the host-owned B_dev evaluator into every "
        "executor worktree. Held-out evaluation is unavailable. Use Arbor's native tree, "
        "merge, promotion, selection, and stop behavior, and leave the Agent-selected "
        "final candidate on the trunk."
    )


def _protected_paths(context: FMLAgentLaunchContext) -> tuple[str, ...]:
    editable = tuple(path.rstrip("/") for path in context.task.editable_paths)
    return tuple(
        relative
        for relative in context.workspace.initial_manifest
        if not any(
            relative == allowed or relative.startswith(allowed + "/")
            for allowed in editable
        )
    )


__all__ = ["ArborBenchmarkPatchedFMLAdapter", "ArborFMLAdapter"]
