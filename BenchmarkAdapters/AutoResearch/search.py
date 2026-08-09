"""Search-side contract shared by native Autoresearch launcher bridges."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .broker import CandidateDevBroker


@dataclass(frozen=True)
class SearchContext:
    agent: str
    native_backend: str
    outer_seed: int
    outer_deadline_monotonic: float
    candidate_training_seconds: int
    program_path: Path
    baseline_train_path: Path
    output_dir: Path
    broker: CandidateDevBroker


@dataclass(frozen=True)
class SearchOutcome:
    native_component: str
    declared_revision_id: str | None
    completed: bool
    timed_out: bool = False
    failure_reason: str | None = None
    metadata: Mapping[str, object] | None = None


class SearchRunner(Protocol):
    def __call__(self, context: SearchContext) -> SearchOutcome: ...


__all__ = ["SearchContext", "SearchOutcome", "SearchRunner"]
