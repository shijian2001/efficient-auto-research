"""Canonical registry for the seven concrete formal FML Agent adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ...contracts import AdapterError
from ...protocol import canonical_json, sha256_file
from ...registry import AGENTS
from .ai_scientist import AiScientistFMLAdapter
from .arbor import ArborFMLAdapter
from .base import FMLAgentAdapter
from .claude_code import ClaudeCodeFMLAdapter
from .codex import CodexFMLAdapter
from .ear import EARFMLAdapter
from .ml_master import MLMasterFMLAdapter
from .mlevolve import MLEvolveFMLAdapter


FML_AGENT_ADAPTERS: dict[str, type[FMLAgentAdapter]] = {
    "ear": EARFMLAdapter,
    "mlevolve": MLEvolveFMLAdapter,
    "arbor": ArborFMLAdapter,
    "codex": CodexFMLAdapter,
    "claude-code": ClaudeCodeFMLAdapter,
    "ml-master-2": MLMasterFMLAdapter,
    "ai-scientist": AiScientistFMLAdapter,
}

if set(FML_AGENT_ADAPTERS) != set(AGENTS):
    raise RuntimeError("FML Agent adapter registry differs from the canonical seven Agents")


def get_fml_agent_adapter(agent_id: str) -> FMLAgentAdapter:
    try:
        return FML_AGENT_ADAPTERS[agent_id]()
    except KeyError as exc:
        raise AdapterError(f"unknown FML Agent: {agent_id}") from exc


def adapter_registry_digest() -> str:
    root = Path(__file__).resolve().parent
    files = {
        path.name: sha256_file(path)
        for path in sorted(root.glob("*.py"))
        if path.name != "__pycache__"
    }
    return hashlib.sha256(canonical_json(files)).hexdigest()


__all__ = [
    "FML_AGENT_ADAPTERS",
    "FMLAgentAdapter",
    "adapter_registry_digest",
    "get_fml_agent_adapter",
]
