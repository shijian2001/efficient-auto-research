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


AGENTS = {
    "ear": AgentSpec(
        "ear",
        "Efficient Agent Research",
        ROOT / "mle-bench-agents/efficient-auto-research",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "blocked-native-backend",
    ),
    "mlevolve": AgentSpec(
        "mlevolve",
        "MLEvolve",
        ROOT / "baselines/MLEvolve",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "blocked-native-backend",
    ),
    "arbor": AgentSpec(
        "arbor",
        "Arbor",
        ROOT / "baselines/Arbor",
        (str(ROOT / "baselines/Arbor/.venv/bin/arbor"), "--version"),
        "native-docker",
        "native-repository-ao",
    ),
    "codex": AgentSpec(
        "codex",
        "Codex CLI",
        ROOT / "baselines/Codex",
        ("codex", "--version"),
        "generic-mle-workspace",
        "native-repository-cli",
    ),
    "claude-code": AgentSpec(
        "claude-code",
        "Claude Code",
        ROOT / "baselines/ClaudeCode",
        ("claude", "--version"),
        "generic-mle-workspace",
        "native-repository-cli",
    ),
    "ml-master-2": AgentSpec(
        "ml-master-2",
        "ML-Master 2.0",
        ROOT / "baselines/EvoMaster",
        (str(ROOT / "baselines/EvoMaster/.venv/bin/python"), "--version"),
        "native-mle",
        "evomaster-repository-playground",
    ),
    "ai-scientist": AgentSpec(
        "ai-scientist",
        "AweAI AiScientist",
        ROOT / "baselines/AiScientist",
        (str(ROOT / "baselines/AiScientist/.venv/bin/aisci"), "--help"),
        "native-mle",
        "blocked-native-backend",
    ),
}


__all__ = ["AGENTS", "AgentSpec", "ROOT"]
