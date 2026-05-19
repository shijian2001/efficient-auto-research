"""
Search graph: Attempt nodes + derived_from edges.

The graph grows incrementally as the search progresses. Each execution step
produces a new Attempt node. The derived_from edges form a tree structure.
Cross-node correlation is handled by the kernel matrix in Thompson Sampling
(computed directly from node embeddings, not stored as explicit edges).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("AutoResearch")


@dataclass
class Attempt:
    """A single execution attempt — the only node type in the graph."""

    id: str
    plan: str
    code: str
    metric: float | None = None
    error: str | None = None
    parent_id: str | None = None
    embedding: np.ndarray | None = field(default=None, repr=False)


class SearchGraph:
    """
    Search history as a graph.

    Nodes: Attempt (one type, representing a single code execution).
    Edges: derived_from (parent-child relationship, forms a tree).

    Cross-node information sharing is handled by the kernel matrix in
    Kernel Thompson Sampling, which uses node embeddings to compute
    pairwise similarity without explicit edge storage.
    """

    def __init__(self) -> None:
        self.attempts: dict[str, Attempt] = {}
        self._children: dict[str, list[str]] = {}

    def add_attempt(self, attempt: Attempt) -> None:
        """Add a new attempt node and build derived_from edge."""
        self.attempts[attempt.id] = attempt
        self._children.setdefault(attempt.id, [])

        if attempt.parent_id and attempt.parent_id in self.attempts:
            self._children.setdefault(attempt.parent_id, []).append(attempt.id)

    def get_children(self, attempt_id: str) -> list[Attempt]:
        """Get direct children (via derived_from edge)."""
        return [self.attempts[i] for i in self._children.get(attempt_id, [])
                if i in self.attempts]

    def get_roots(self) -> list[Attempt]:
        """Get all root nodes (no parent)."""
        return [a for a in self.attempts.values() if a.parent_id is None]

    def most_similar(self, attempt_id: str, n: int = 3) -> list[Attempt]:
        """Find the n most similar nodes by embedding cosine similarity."""
        node = self.attempts.get(attempt_id)
        if node is None or node.embedding is None:
            return []

        sims: list[tuple[str, float]] = []
        for aid, a in self.attempts.items():
            if aid == attempt_id or a.embedding is None:
                continue
            sim = _cosine_sim(node.embedding, a.embedding)
            sims.append((aid, sim))

        sims.sort(key=lambda x: x[1], reverse=True)
        return [self.attempts[aid] for aid, _ in sims[:n]]


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
