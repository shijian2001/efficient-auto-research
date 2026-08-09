"""Claude Code CLI formal FML adapter."""

from __future__ import annotations

import shutil
from pathlib import Path

from ...contracts import AdapterError, CommandSpec
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class ClaudeCodeFMLAdapter(FMLAgentAdapter):
    agent_id = "claude-code"
    native_entrypoint = "claude --print"

    def installation_executable(self) -> Path | None:
        value = shutil.which("claude")
        return None if value is None else Path(value)

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        executable = self.installation_executable()
        if executable is None:
            raise AdapterError("Claude Code CLI is not installed")
        return CommandSpec(
            argv=(
                str(executable),
                "--print",
                "--bare",
                "--no-chrome",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--output-format",
                "stream-json",
                "--verbose",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                context.model_config.outer_model_id,
                "--tools=Bash,Read,Edit,Write",
                prompt,
            ),
            cwd=context.workspace.root,
            timeout_seconds=context.timeout_seconds,
            label="Claude Code native generic FML loop",
            inherit_env=False,
        )


__all__ = ["ClaudeCodeFMLAdapter"]
