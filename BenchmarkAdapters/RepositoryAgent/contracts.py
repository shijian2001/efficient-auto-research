"""Contracts used by the shared repository-agent backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentProfile:
    key: str
    display_name: str
    system_prompt: str
    candidate_prompts: tuple[str, ...]

    def prompt_for(self, candidate_index: int, best_score: float) -> str:
        prompt = self.candidate_prompts[(candidate_index - 1) % len(self.candidate_prompts)]
        return prompt.format(candidate_index=candidate_index, best_score=best_score)


@dataclass(frozen=True)
class RepositoryAgentRequest:
    agent: str
    repository: Path
    evaluator: Path
    dev_data: Path
    output_dir: Path
    instruction: str
    protected_paths: tuple[Path, ...] = ()
    model: str = "gpt-5.5"
    base_url: str = "https://relay.shuai-ederson-clow.xyz/v1"
    proxy: str = "http://127.0.0.1:17892"
    candidates: int = 3
    max_turns: int = 12
    timeout_seconds: int = 3600
    command_timeout_seconds: int = 120
    evaluator_concurrency: int = 8
    python_executable: str = "python3"
    apply_best: bool = True


@dataclass(frozen=True)
class EvaluationResult:
    score: float
    return_code: int
    output: str


@dataclass
class CandidateRecord:
    index: int
    score: float | None = None
    revision: str | None = None
    status: str = "running"
    turns: int = 0
    evaluations: list[float] = field(default_factory=list)
    error: str | None = None


__all__ = [
    "AgentProfile",
    "CandidateRecord",
    "EvaluationResult",
    "RepositoryAgentRequest",
]
