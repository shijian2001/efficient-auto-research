"""
Search graph: Attempt nodes + derived_from edges (tree) + similar edges (graph).

The graph grows incrementally as the search progresses. Each execution step
produces a new Attempt node. Similarity edges are built via KNN on node
embeddings, turning the search tree into a graph that enables cross-branch
information sharing.
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
    Edges:
      - derived_from: parent-child relationship (forms a tree).
      - similar: KNN on embeddings (cross-branch bridges, turns tree into graph).

    The similar edges enable Thompson Sampling to borrow experience from
    structurally related nodes when estimating expected reward.
    """

    def __init__(self, k: int = 5) -> None:
        self.k = k
        self.attempts: dict[str, Attempt] = {}
        self._children: dict[str, list[str]] = {}
        self._similar: dict[str, list[str]] = {}

    def add_attempt(self, attempt: Attempt) -> None:
        """Add a new attempt node and build its edges."""
        self.attempts[attempt.id] = attempt
        self._children.setdefault(attempt.id, [])
        self._similar.setdefault(attempt.id, [])

        if attempt.parent_id and attempt.parent_id in self.attempts:
            self._children.setdefault(attempt.parent_id, []).append(attempt.id)

        if attempt.embedding is not None:
            self._build_similar_edges(attempt)

    def get_children(self, attempt_id: str) -> list[Attempt]:
        """Get direct children (via derived_from edge)."""
        return [self.attempts[i] for i in self._children.get(attempt_id, [])
                if i in self.attempts]

    def get_similar(self, attempt_id: str) -> list[Attempt]:
        """Get KNN similar neighbors."""
        return [self.attempts[i] for i in self._similar.get(attempt_id, [])
                if i in self.attempts]

    def get_similar_with_score(self, attempt_id: str) -> list[tuple[Attempt, float]]:
        """Get KNN similar neighbors with their cosine similarity scores."""
        result = []
        node = self.attempts.get(attempt_id)
        if node is None or node.embedding is None:
            return result
        for aid in self._similar.get(attempt_id, []):
            neighbor = self.attempts.get(aid)
            if neighbor is None or neighbor.embedding is None:
                continue
            sim = _cosine_sim(node.embedding, neighbor.embedding)
            result.append((neighbor, sim))
        return result

    def get_roots(self) -> list[Attempt]:
        """Get all root nodes (no parent)."""
        return [a for a in self.attempts.values() if a.parent_id is None]

    def _build_similar_edges(self, new_attempt: Attempt) -> None:
        """Connect new node to its K nearest neighbors by cosine similarity."""
        if len(self.attempts) <= 1:
            return

        sims: list[tuple[str, float]] = []
        for aid, a in self.attempts.items():
            if aid == new_attempt.id or a.embedding is None:
                continue
            sim = _cosine_sim(new_attempt.embedding, a.embedding)
            sims.append((aid, sim))

        sims.sort(key=lambda x: x[1], reverse=True)

        for aid, _ in sims[:self.k]:
            self._similar[new_attempt.id].append(aid)
            self._similar.setdefault(aid, []).append(new_attempt.id)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
