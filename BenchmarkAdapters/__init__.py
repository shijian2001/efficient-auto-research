"""Shared benchmark adapters and supervisors for the baseline agents."""

from .agents import AgentAdapter, get_agent_adapter
from .contracts import AdapterError, CommandResult, CommandSpec, UnsupportedAdapterError
from .MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from .registry import AGENTS, AgentSpec
from .TerminalBench.adapter import TerminalAoAdapter, TerminalAoRequest

__all__ = [
    "AGENTS",
    "AdapterError",
    "AgentAdapter",
    "AgentSpec",
    "CommandResult",
    "CommandSpec",
    "MleLiteAdapter",
    "MleLiteRequest",
    "TerminalAoAdapter",
    "TerminalAoRequest",
    "UnsupportedAdapterError",
    "get_agent_adapter",
]
