"""Registered baseline agents and their benchmark capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AgentSpec:
    key: str
    display_name: str
    install_path: Path
    version_command: tuple[str, ...]
    mle_mode: str
    terminal_mode: str
    terminal_implementation: str
    native_upstream_terminal_backend: bool


AGENTS = {
    "ear": AgentSpec(
        "ear",
        "Efficient Agent Research",
        ROOT / "mle-bench-agents/efficient-auto-research",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "shared-repository-agent",
        "shared-openai-repository-profile",
        False,
    ),
    "mlevolve": AgentSpec(
        "mlevolve",
        "MLEvolve",
        ROOT / "baselines/MLEvolve",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "shared-repository-agent",
        "shared-openai-repository-profile",
        False,
    ),
    "arbor": AgentSpec(
        "arbor",
        "Arbor",
        ROOT / "baselines/Arbor",
        (str(ROOT / "baselines/Arbor/.venv/bin/arbor"), "--version"),
        "native-docker",
        "native-repository-ao",
        "upstream-native-repository-ao",
        True,
    ),
    "codex": AgentSpec(
        "codex",
        "Codex CLI",
        ROOT / "baselines/Codex",
        ("codex", "--version"),
        "generic-mle-workspace",
        "native-repository-cli",
        "upstream-native-repository-cli",
        True,
    ),
    "claude-code": AgentSpec(
        "claude-code",
        "Claude Code",
        ROOT / "baselines/ClaudeCode",
        ("claude", "--version"),
        "generic-mle-workspace",
        "native-repository-cli",
        "upstream-native-repository-cli",
        True,
    ),
    "ml-master-2": AgentSpec(
        "ml-master-2",
        "ML-Master 2.0",
        ROOT / "baselines/EvoMaster",
        (str(ROOT / "baselines/EvoMaster/.venv/bin/python"), "--version"),
        "native-mle",
        "shared-repository-agent",
        "shared-openai-repository-profile",
        False,
    ),
    "ai-scientist": AgentSpec(
        "ai-scientist",
        "AweAI AiScientist",
        ROOT / "baselines/AiScientist",
        (str(ROOT / "baselines/AiScientist/.venv/bin/aisci"), "--help"),
        "native-mle",
        "shared-repository-agent",
        "shared-openai-repository-profile",
        False,
    ),
}


__all__ = ["AGENTS", "AgentSpec", "ROOT"]
