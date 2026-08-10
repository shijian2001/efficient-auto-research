"""Codex CLI formal FML adapter."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ...contracts import AdapterError, CommandSpec
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class CodexFMLAdapter(FMLAgentAdapter):
    agent_id = "codex"
    native_entrypoint = "codex exec"

    def installation_executable(self) -> Path | None:
        value = shutil.which("codex")
        return None if value is None else Path(value)

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        executable = self.installation_executable()
        if executable is None:
            raise AdapterError("Codex CLI is not installed")
        argv = [
            str(executable),
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "workspace-write",
            "--json",
            "--model",
            context.model_config.outer_model_id,
        ]
        for name, value in sorted(context.model_config.model_parameters.items()):
            argv.extend(("-c", f"{name}={json.dumps(value)}"))
        argv.extend(
            (
                "-c",
                f"openai_base_url={json.dumps(context.relay_base_url)}",
                prompt,
            )
        )
        return CommandSpec(
            argv=tuple(argv),
            cwd=context.workspace.root,
            timeout_seconds=context.timeout_seconds,
            label="Codex native generic FML loop",
            inherit_env=False,
        )


__all__ = ["CodexFMLAdapter"]
