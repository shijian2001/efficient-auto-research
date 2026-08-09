"""Arbor FML adapter using the native coordinator CLI."""

from __future__ import annotations

import json
from pathlib import Path

from ...contracts import AdapterError, CommandSpec
from ...protocol import write_json_exclusive
from ...registry import ROOT
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class ArborFMLAdapter(FMLAgentAdapter):
    agent_id = "arbor"
    native_entrypoint = "arbor run / CoordinatorOrchestrator.run"

    def installation_executable(self) -> Path | None:
        return ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/bin/arbor"

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        executable = self.installation_executable()
        if executable is None or not executable.is_file():
            raise AdapterError("Arbor FML runtime is not installed")
        generated = {
            "schema_version": 1,
            "model_track_digest": context.model_config.digest,
            "task_spec_digest": context.task.digest,
            "wall_clock_seconds": context.timeout_seconds,
            "max_cycles": context.task.max_agent_steps,
        }
        context.output_dir.mkdir(parents=True, exist_ok=True)
        config_path = context.output_dir / "arbor-fml-config.json"
        write_json_exclusive(config_path, generated)
        return CommandSpec(
            argv=(
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
                "--max-cycles",
                str(context.task.max_agent_steps),
                "--max-turns",
                str(max(context.task.max_agent_steps, context.task.max_agent_steps * 4)),
                "--interaction-mode",
                "auto",
                "--no-followup",
                "--no-webui",
            ),
            cwd=context.workspace.root,
            env={
                "ARBOR_MODEL": context.model_config.outer_model_id,
                "FML_ARBOR_CONFIG_SHA256": __import__("hashlib").sha256(
                    __import__("json").dumps(generated, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            },
            timeout_seconds=context.timeout_seconds,
            label="Arbor native coordinator FML loop",
            inherit_env=False,
        )

    def generated_config_digests(
        self, context: FMLAgentLaunchContext
    ) -> dict[str, str]:
        path = context.output_dir / "arbor-fml-config.json"
        if not path.is_file():
            return {}
        return {"arbor-fml-config.json": __import__("hashlib").sha256(path.read_bytes()).hexdigest()}


__all__ = ["ArborFMLAdapter"]
