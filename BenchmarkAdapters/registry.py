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
    runtime_path: Path | None = None

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

    @property
    def terminal_ao_supported(self) -> bool:
        """False when no non-invasive Harness Engineering AO adaptation exists."""
        return not self.terminal_ao_backend.startswith("unsupported:")

    @property
    def execution_path(self) -> Path:
        return self.runtime_path or self.install_path


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
        "unsupported:Kaggle-shaped search engine; candidate success is decided by submission.csv production",
        "blocked:agent_adapters.mlevolve:MLEvolveTerminalAgent",
        "non-comparable direct solver blocked; MLEvolve does not participate in Terminal AO",
    ),
    "arbor": AgentSpec(
        "arbor",
        "Arbor",
        ROOT / "baselines/Arbor",
        (str(ROOT / "baselines/Arbor/.venv/bin/arbor"), "--version"),
        "unsupported:official Arbor MLE has no host dev-evaluator extension",
        "official-extension-thin-arbor-cli",
        "official-extension-thin-arbor-cli",
        "official-extension-thin-arbor-cli",
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
        "unsupported:official ML-Master workflow is MLE-specific",
        "unsupported:official ML-Master workflow is MLE-specific",
        "unsupported:Kaggle-shaped playground workspace; best-solution promotion is decided by submission.csv artifacts",
        "blocked:agent_adapters.ml_master_2:MLMaster2Agent",
        "native ML-Master 2 direct solver blocked; ML-Master 2.0 does not participate in Terminal AO",
    ),
    "ai-scientist": AgentSpec(
        "ai-scientist",
        "AweAI AiScientist",
        ROOT / "baselines/AiScientist",
        (str(ROOT / "baselines/AiScientist/.venv/bin/aisci"), "--help"),
        "native-mle",
        "unsupported:no official generic architecture entrypoint",
        "unsupported:no official optimizer-design entrypoint",
        "unsupported:no official generic terminal entrypoint",
        "agent_adapters.ai_scientist:AiScientistTerminalAgent",
        "native AiScientist Subagent bridge; locked Harbor profile",
        ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist",
    ),
}


# Runtime container images an Agent needs but upstream never published.
#
# AiScientist executes the code its Agent writes inside a Docker container, so a
# usable image is part of its runtime, not an optional extra. Upstream ships the
# code (MIT) but points `mle-default` at `hub.byted.org/your-team/aisci-mle`, an
# internal registry with a placeholder team name, and its Dockerfile's base image
# lived there too. Nothing pullable exists outside that network, so the cell died
# in preflight with "Runtime image ... is missing locally and this run would
# pull it".
#
# The image is therefore built locally from upstream's own docker/ assets via
# `bash docker/build_mle_image.sh` (see docs/adapters/mle-bench-lite.ai-scientist.md).
# `pull_policy=never` keeps a formal run from silently reaching the network: if
# the image is absent the cell fails loudly instead of pulling something
# unrecorded. Scorecards must note that this cell's runtime was built here rather
# than pulled from a published upstream release.
AGENT_RUNTIME_IMAGES = {
    "ai-scientist": ("aisci-mle:test", "never"),
}


TERMINAL_AO_UNSUPPORTED_REASONS = {
    "mlevolve": (
        "MLEvolve's search engine decides candidate success by whether a node produced "
        "submission.csv (baselines/MLEvolve/engine/execution.py:26-30) and manages its best "
        "solution by that same path (baselines/MLEvolve/engine/solution_manager.py:71,169; "
        "baselines/MLEvolve/agents/debug_agent.py:78). Terminal AO candidates are git diffs "
        "scored by dev pass rate and never produce a submission.csv, so no non-invasive "
        "adaptation exists. This is a task-shape mismatch, not an MLEvolve capability limit."
    ),
    "ml-master-2": (
        "ML-Master 2.0's playground hard-codes a Kaggle-shaped workspace "
        "(best_submission/best_solution/submission/working) and promotes its best solution by "
        "copying submission_<uid>.csv "
        "(baselines/EvoMaster/playground/ml_master_2/core/playground.py:107-113,212,300). "
        "Terminal AO has no such artifact, so no non-invasive adaptation exists. This is a "
        "task-shape mismatch, not an ML-Master 2.0 capability limit."
    ),
}


__all__ = ["AGENTS", "TERMINAL_AO_UNSUPPORTED_REASONS", "AgentSpec", "ROOT"]
