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
    mle_backend: str
    autoresearch_backend: str
    optimizer_design_backend: str
    terminal_ao_backend: str
    terminal_direct_smoke_backend: str | None
    terminal_direct_smoke_status: str
    terminal_project: Path | None = None

    @property
    def mle_mode(self) -> str:
        return self.mle_backend

    @property
    def terminal_agent(self) -> str | None:
        return self.terminal_direct_smoke_backend

    @property
    def terminal_supported(self) -> bool:
        return self.terminal_direct_smoke_backend is not None and not self.terminal_direct_smoke_backend.startswith("blocked:")

    @property
    def terminal_status(self) -> str:
        return self.terminal_direct_smoke_status


AGENTS = {
    "ear": AgentSpec(
        "ear",
        "Efficient Agent Research",
        ROOT / "mle-bench-agents/efficient-auto-research",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "native-ear-kts",
        "optimizer-design-ear-kts",
        "native-ear-repository",
        "blocked:agent_adapters.ear:EARTerminalAgent",
        "non-comparable direct solver blocked; formal AO uses native EAR KTS repository backend",
    ),
    "mlevolve": AgentSpec(
        "mlevolve",
        "MLEvolve",
        ROOT / "baselines/MLEvolve",
        ("git", "rev-parse", "HEAD"),
        "native-docker",
        "native-mlevolve-uct",
        "optimizer-design-mlevolve-uct",
        "native-mlevolve-repository",
        "blocked:agent_adapters.mlevolve:MLEvolveTerminalAgent",
        "non-comparable direct solver blocked; formal AO uses native MLEvolve UCT repository backend",
    ),
    "arbor": AgentSpec(
        "arbor",
        "Arbor",
        ROOT / "baselines/Arbor",
        (str(ROOT / "baselines/Arbor/.venv/bin/arbor"), "--version"),
        "native-docker",
        "native-arbor-coordinator",
        "optimizer-design-arbor-coordinator",
        "native-arbor-repository",
        "agent_adapters.arbor:ArborTerminalAgent",
        "native Arbor ReAct loop with Harbor-backed tools",
        ROOT / "BenchmarkAdapters/environments/terminal/arbor",
    ),
    "codex": AgentSpec(
        "codex",
        "Codex CLI",
        ROOT / "baselines/Codex",
        ("codex", "--version"),
        "generic-mle-workspace",
        "native-codex-cli",
        "optimizer-design-codex-cli",
        "native-codex-cli",
        "codex",
        "Harbor built-in Codex adapter",
    ),
    "claude-code": AgentSpec(
        "claude-code",
        "Claude Code",
        ROOT / "baselines/ClaudeCode",
        ("claude", "--version"),
        "generic-mle-workspace",
        "native-claude-cli",
        "optimizer-design-claude-cli",
        "native-claude-cli",
        "claude-code",
        "Harbor built-in Claude Code adapter",
    ),
    "ml-master-2": AgentSpec(
        "ml-master-2",
        "ML-Master 2.0",
        ROOT / "baselines/EvoMaster",
        (str(ROOT / "baselines/EvoMaster/.venv/bin/python"), "--version"),
        "native-mle",
        "native-ml-master-2-workflow",
        "optimizer-design-ml-master-2-workflow",
        "native-ml-master-2-repository",
        "blocked:agent_adapters.ml_master_2:MLMaster2Agent",
        "native ML-Master 2 direct solver blocked; formal AO uses native EvoMaster repository workflow",
    ),
    "ai-scientist": AgentSpec(
        "ai-scientist",
        "AweAI AiScientist",
        ROOT / "baselines/AiScientist",
        (str(ROOT / "baselines/AiScientist/.venv/bin/aisci"), "--help"),
        "native-mle",
        "native-ai-scientist-subagent",
        "optimizer-design-ai-scientist-subagent",
        "native-ai-scientist-subagent",
        "agent_adapters.ai_scientist:AiScientistTerminalAgent",
        "native AiScientist Subagent bridge; locked Harbor profile",
        ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist",
    ),
}


__all__ = ["AGENTS", "AgentSpec", "ROOT"]
