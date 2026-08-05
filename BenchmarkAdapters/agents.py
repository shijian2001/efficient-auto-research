"""Agent facade over the benchmark-specific adapter packages."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AdapterError, CommandSpec
from .MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from .registry import AGENTS, AgentSpec
from .TerminalBench.adapter import TerminalAoAdapter, TerminalAoRequest


@dataclass(frozen=True)
class AgentAdapter:
    """Facade exposing both benchmark contracts for one registered Agent."""

    spec: AgentSpec

    @property
    def mle_lite(self) -> MleLiteAdapter:
        return MleLiteAdapter(self.spec.key)

    @property
    def terminal_ao(self) -> TerminalAoAdapter:
        return TerminalAoAdapter(self.spec.key)

    def build_mle_command(self, request: MleLiteRequest) -> CommandSpec:
        return self.mle_lite.build_command(request)

    def build_terminal_optimizer_command(self, request: TerminalAoRequest) -> CommandSpec:
        return self.terminal_ao.build_optimizer_command(request)


def get_agent_adapter(agent: str) -> AgentAdapter:
    try:
        return AgentAdapter(AGENTS[agent])
    except KeyError as exc:
        raise AdapterError(f"unknown baseline agent: {agent}") from exc


__all__ = ["AgentAdapter", "get_agent_adapter"]
