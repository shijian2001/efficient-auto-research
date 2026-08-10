"""Canonical AiScientist FML adapter: unsupported without an official entrypoint."""

from __future__ import annotations

from pathlib import Path

from ...contracts import CommandSpec, UnsupportedAdapterError
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class AiScientistFMLAdapter(FMLAgentAdapter):
    agent_id = "ai-scientist"
    native_entrypoint = "unsupported: no official generic AiScientist repository entrypoint"

    def installation_executable(self) -> Path | None:
        return None

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        del context, prompt
        raise UnsupportedAdapterError(
            "ai-scientist has no official thin FML entrypoint; select "
            "ai-scientist-terminal-variant explicitly to use TerminalTaskSubagent"
        )


__all__ = ["AiScientistFMLAdapter"]
