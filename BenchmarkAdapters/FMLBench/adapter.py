"""Thin FML launcher contract; upstream FML owns search and evaluation semantics."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError, CommandSpec
from ..formal_contract import ModelTrackConfig
from ..registry import AGENTS
from ..security import is_sensitive_name
from ..task_specs import task_spec_digest, task_spec_path, task_spec_text
from .protocol import FMLProtocol


@dataclass(frozen=True)
class FMLRunRequest:
    agent: str
    protocol: FMLProtocol
    model_config: ModelTrackConfig
    task_config: Path
    output_dir: Path
    outer_run_index: int
    agent_variant: str
    formal: bool = True
    credential_env_names: tuple[str, ...] = ()
    gpu_ids: tuple[str, ...] = ()
    execution_root: Path | None = None
    execution_task_config: Path | None = None


class FMLBenchmarkAdapter:
    def __init__(self, agent: str) -> None:
        if agent not in AGENTS:
            raise AdapterError(f"unknown FML Agent: {agent}")
        self.agent = agent

    def build_command(self, request: FMLRunRequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("FML request Agent differs from adapter")
        request.protocol.validate(formal=request.formal)
        request.model_config.validate(formal=request.formal)
        if not 0 <= request.outer_run_index < request.protocol.outer_repetitions:
            raise AdapterError("FML outer run index is outside protocol")
        if request.task_config.resolve() not in {
            path.resolve() for path in request.protocol.task_config_paths
        }:
            raise AdapterError("FML task config is outside the frozen protocol")
        command = request.protocol.launcher_commands[self.agent]
        execution_root = (request.execution_root or request.protocol.upstream_root).resolve()
        execution_task = (request.execution_task_config or request.task_config).resolve()
        if not execution_root.is_dir() or not execution_task.is_file():
            raise AdapterError("FML disposable execution source or task config is missing")
        replacements = {
            "agent_id": self.agent,
            "agent_variant": request.agent_variant,
            "model_id": request.model_config.outer_model_id,
            "model_track_id": request.model_config.model_track_id,
            "relay_base_url": request.model_config.relay_base_url,
            "task_config": str(execution_task),
            "task_spec": str(task_spec_path("fml-bench")),
            "output_dir": str(request.output_dir.resolve()),
            "outer_run_index": str(request.outer_run_index),
            "wall_clock_seconds": str(request.protocol.wall_clock_seconds),
            "fml_root": str(execution_root),
        }
        try:
            formatted = tuple(value.format(**replacements) for value in command)
        except KeyError as exc:
            raise AdapterError(f"FML launcher contains an unknown placeholder: {exc}") from exc
        argv_values: list[str] = []
        for value in formatted:
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                try:
                    relative = candidate.resolve().relative_to(
                        request.protocol.upstream_root.resolve()
                    )
                except ValueError:
                    pass
                else:
                    value = str(execution_root / relative)
            argv_values.append(value)
        argv = tuple(argv_values)
        environment = {
            "FML_MODEL_ID": request.model_config.outer_model_id,
            "FML_MODEL_TRACK_ID": request.model_config.model_track_id,
            "FML_MODEL_PARAMETERS": json.dumps(
                request.model_config.model_parameters, sort_keys=True
            ),
            "FML_RELAY_BASE_URL": request.model_config.relay_base_url,
            "FML_TASK_SPEC_PATH": str(task_spec_path("fml-bench")),
            "FML_TASK_SPEC_SHA256": task_spec_digest("fml-bench"),
            "FML_TASK_SPEC_TEXT": task_spec_text("fml-bench"),
            "FML_WALL_CLOCK_SECONDS": str(request.protocol.wall_clock_seconds),
            "CUDA_VISIBLE_DEVICES": ",".join(request.gpu_ids),
        }
        for name in request.credential_env_names:
            if not is_sensitive_name(name):
                raise AdapterError(
                    f"FML credential environment name is not recognized as sensitive: {name}"
                )
            value = os.environ.get(name)
            if not value:
                raise AdapterError(f"required FML credential environment variable is unset: {name}")
            environment[name] = value
        return CommandSpec(
            argv=argv,
            cwd=execution_root,
            env=environment,
            timeout_seconds=request.protocol.wall_clock_seconds,
            label=f"FML-Bench / {self.agent}",
            inherit_env=False,
        )


__all__ = ["FMLBenchmarkAdapter", "FMLRunRequest"]
