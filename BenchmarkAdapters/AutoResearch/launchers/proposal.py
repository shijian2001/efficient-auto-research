"""Deferred model-adapter proposal contract for native search loops."""

from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import numpy as np


@dataclass(frozen=True)
class CandidateProposal:
    plan: str
    train_source: str
    embedding: np.ndarray


class ProposalProvider(Protocol):
    def __call__(self, request: Mapping[str, object]) -> CandidateProposal: ...


def command_proposal_provider(request: Mapping[str, object]) -> CandidateProposal:
    """Call a later model Adapter command; this layer contains no provider/API logic."""

    command = os.environ.get("AUTORESEARCH_PROPOSER_COMMAND")
    if not command:
        raise RuntimeError(
            "AUTORESEARCH_PROPOSER_COMMAND is required; model-provider wiring is a separate Adapter"
        )
    completed = subprocess.run(
        shlex.split(command),
        input=json.dumps(dict(request), sort_keys=True),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"proposal Adapter exited with code {completed.returncode}")
    payload = json.loads(completed.stdout)
    embedding = np.asarray(payload["embedding"], dtype=float)
    if embedding.ndim != 1 or embedding.size == 0 or not np.isfinite(embedding).all():
        raise RuntimeError("proposal Adapter returned an invalid embedding")
    return CandidateProposal(
        plan=str(payload["plan"]),
        train_source=str(payload["train_source"]),
        embedding=embedding,
    )


def load_factory(environment_name: str) -> Callable[..., object]:
    value = os.environ.get(environment_name)
    if not value or ":" not in value:
        raise RuntimeError(
            f"{environment_name}=module:callable is required; model-provider wiring is separate"
        )
    module_name, attribute = value.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise RuntimeError(f"configured model Adapter factory is not callable: {value}")
    return factory


__all__ = [
    "CandidateProposal",
    "ProposalProvider",
    "command_proposal_provider",
    "load_factory",
]
