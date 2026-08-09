"""Seven thin native Agent adapters for Optimizer Design."""

from __future__ import annotations

from ...contracts import AdapterError
from .ai_scientist import ADAPTER as AI_SCIENTIST
from .arbor import ADAPTER as ARBOR
from .claude_code import ADAPTER as CLAUDE_CODE
from .codex import ADAPTER as CODEX
from .ear import ADAPTER as EAR
from .ml_master_2 import ADAPTER as ML_MASTER_2
from .mlevolve import ADAPTER as MLEVOLVE


AGENT_ADAPTERS = {
    adapter.agent: adapter
    for adapter in (
        EAR,
        MLEVOLVE,
        ARBOR,
        CODEX,
        CLAUDE_CODE,
        ML_MASTER_2,
        AI_SCIENTIST,
    )
}


def get_optimizer_design_agent_adapter(agent: str):
    try:
        return AGENT_ADAPTERS[agent]
    except KeyError as exc:
        raise AdapterError(f"unknown Optimizer Design Agent: {agent}") from exc


__all__ = ["AGENT_ADAPTERS", "get_optimizer_design_agent_adapter"]
