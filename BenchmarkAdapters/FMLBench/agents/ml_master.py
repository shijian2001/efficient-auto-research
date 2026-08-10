"""Canonical ML-Master 2 FML adapter: unsupported outside its MLE workflow."""

from __future__ import annotations

from pathlib import Path

from ...contracts import CommandSpec, UnsupportedAdapterError
from .base import FMLAgentAdapter, FMLAgentLaunchContext


class MLMasterFMLAdapter(FMLAgentAdapter):
    agent_id = "ml-master-2"
    native_entrypoint = "unsupported: official ML-Master 2 workflow is MLE-specific"

    def installation_executable(self) -> Path | None:
        return None

    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        del context, prompt
        raise UnsupportedAdapterError(
            "ml-master-2 has no official thin FML entrypoint; select "
            "ml-master-autoresearch-variant explicitly to use the staged BaseAgent workflow"
        )


__all__ = ["MLMasterFMLAdapter"]
