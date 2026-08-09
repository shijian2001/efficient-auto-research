"""Agent facade over the benchmark-specific adapter packages."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AdapterError, CommandSpec
from .MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from .OptimizerDesign.adapter import OptimizerDesignBenchmarkAdapter
from .registry import AGENTS, AgentSpec
from .TerminalBench.adapter import HarborTerminalAdapter, HarborTerminalRequest
from .TerminalAO.adapter import TerminalAOAdapter, TerminalAORequest


@dataclass(frozen=True)
class AgentAdapter:
    """Facade exposing both benchmark contracts for one registered Agent."""

    spec: AgentSpec

    @property
    def mle_lite(self) -> MleLiteAdapter:
        return MleLiteAdapter(self.spec.key)

    @property
    def terminal(self) -> HarborTerminalAdapter:
        return HarborTerminalAdapter(self.spec.key)

    @property
    def terminal_direct_smoke(self) -> HarborTerminalAdapter:
        return HarborTerminalAdapter(self.spec.key)

    @property
    def terminal_ao(self) -> TerminalAOAdapter:
        return TerminalAOAdapter(self.spec.key)

    @property
    def optimizer_design(self) -> OptimizerDesignBenchmarkAdapter:
        return OptimizerDesignBenchmarkAdapter(self.spec.key)

    def build_mle_command(self, request: MleLiteRequest) -> CommandSpec:
        return self.mle_lite.build_command(request)

    def build_terminal_command(self, request: HarborTerminalRequest) -> CommandSpec:
        return self.terminal.build_command(request)

    def build_terminal_ao_command(self, request: TerminalAORequest) -> CommandSpec:
        return self.terminal_ao.build_command(request)


def get_agent_adapter(agent: str) -> AgentAdapter:
    try:
        return AgentAdapter(AGENTS[agent])
    except KeyError as exc:
        raise AdapterError(f"unknown baseline agent: {agent}") from exc


__all__ = ["AgentAdapter", "get_agent_adapter"]
