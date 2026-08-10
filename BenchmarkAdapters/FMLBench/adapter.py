"""Benchmark-level FML adapter delegating native behavior to Agent adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..contracts import AdapterError, CommandSpec
from ..formal_contract import ModelTrackConfig
from ..protocol import canonical_json
from ..registry import AGENTS
from .agents import get_fml_agent_adapter
from .agents.base import FMLAgentLaunchContext
from .protocol import FMLProtocol
from .task import load_fml_task
from .workspace import FMLWorkspace, tree_manifest


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
    runtime_executable: Path | None = None
    evaluator_environment: Mapping[str, str] | None = None
    execution_root: Path | None = None


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
        task = load_fml_task(request.protocol, request.task_config)
        config = json.loads(
            (
                request.protocol.upstream_root
                / "ml_tasks"
                / task.upstream_task_name
                / "config.json"
            ).read_text(encoding="utf-8")
        )
        source = request.protocol.upstream_root / str(config["repo_dir"])
        workspace = FMLWorkspace(
            root=source.resolve(),
            initial_manifest=tree_manifest(source),
            initial_digest=__import__("hashlib").sha256(
                canonical_json(tree_manifest(source))
            ).hexdigest(),
        )
        context = FMLAgentLaunchContext(
            agent_id=request.agent,
            agent_variant=request.agent_variant,
            task=task,
            workspace=workspace,
            output_dir=request.output_dir.resolve() / "agent-output",
            development_socket=request.output_dir.resolve() / "capability/dev.sock",
            development_token="dry-run-token",
            development_client_path=Path(__file__).resolve().parent / "dev_client.py",
            model_config=request.model_config,
            outer_run_id=request.protocol.outer_run_ids[request.outer_run_index],
            timeout_seconds=request.protocol.wall_clock_seconds,
            credential_env_names=request.credential_env_names,
            relay_base_url="http://127.0.0.1:6200/v1",
            runtime_executable=request.runtime_executable,
            formal=request.formal,
        )
        return get_fml_agent_adapter(
            self.agent, request.agent_variant
        ).build_launch_command(context)[0]


__all__ = ["FMLBenchmarkAdapter", "FMLRunRequest"]
