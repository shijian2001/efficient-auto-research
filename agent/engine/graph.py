"""
Search graph: Attempt nodes + derived_from edges (tree) + kernel-weighted similarity edges (graph).

The graph has two types of edges:
  - derived_from: parent-child relationship (sparse, tree structure).
  - similarity: kernel-weighted edges between all node pairs (dense, defines GP prior).

The kernel matrix K (where K_ij = cosine_sim(embedding_i, embedding_j)) is the
adjacency matrix of the similarity graph. It is maintained incrementally as nodes
are added, and used directly by Kernel Thompson Sampling for posterior computation.
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
    Search history as a kernel-weighted graph.

    Nodes: Attempt (one type, representing a single code execution).
    Edges:
      - derived_from: parent-child (sparse tree, used to define observations).
      - similarity: kernel-weighted (dense, K_ij = cosine_sim(emb_i, emb_j)).

    The kernel matrix is the graph's adjacency matrix — it defines the GP prior
    for Kernel Thompson Sampling. Nodes that are similar (high K_ij) share
    information through the GP posterior, enabling cross-branch learning.
    """

    def __init__(self) -> None:
        self.attempts: dict[str, Attempt] = {}
        self._children: dict[str, list[str]] = {}
        self._node_order: list[str] = []
        self._kernel_matrix: np.ndarray | None = None

    def add_attempt(self, attempt: Attempt) -> None:
        """Add a new attempt node. Updates derived_from edges and kernel matrix."""
        self.attempts[attempt.id] = attempt
        self._children.setdefault(attempt.id, [])
        self._node_order.append(attempt.id)

        if attempt.parent_id and attempt.parent_id in self.attempts:
            self._children.setdefault(attempt.parent_id, []).append(attempt.id)

        self._update_kernel_matrix(attempt)

    def get_children(self, attempt_id: str) -> list[Attempt]:
        """Get direct children (via derived_from edge)."""
        return [self.attempts[i] for i in self._children.get(attempt_id, [])
                if i in self.attempts]

    def get_roots(self) -> list[Attempt]:
        """Get all root nodes (no parent)."""
        return [a for a in self.attempts.values() if a.parent_id is None]

    @property
    def node_ids(self) -> list[str]:
        """Ordered list of node IDs (matches kernel matrix indexing)."""
        return self._node_order

    @property
    def kernel_matrix(self) -> np.ndarray:
        """
        The kernel (similarity) matrix — adjacency matrix of the similarity graph.

        K_ij = cosine_sim(embedding_i, embedding_j).
        This defines the GP prior: f ~ N(0, K).
        """
        if self._kernel_matrix is None:
            return np.array([[1.0]])
        return self._kernel_matrix

    def most_similar(self, attempt_id: str, n: int = 3) -> list[Attempt]:
        """Find the n most similar nodes by kernel value (for prompt context)."""
        if attempt_id not in self.attempts:
            return []
        idx = self._node_order.index(attempt_id)
        K = self.kernel_matrix
        sims = [(self._node_order[j], K[idx, j]) for j in range(len(self._node_order))
                if j != idx]
        sims.sort(key=lambda x: x[1], reverse=True)
        return [self.attempts[aid] for aid, _ in sims[:n]]

    def _update_kernel_matrix(self, new_attempt: Attempt) -> None:
        """Incrementally expand kernel matrix with new node."""
        n = len(self._node_order)

        if n == 1:
            self._kernel_matrix = np.array([[1.0]])
            return

        # Compute similarities between new node and all existing nodes
        new_row = np.zeros(n)
        new_row[-1] = 1.0  # self-similarity

        if new_attempt.embedding is not None:
            for i in range(n - 1):
                other = self.attempts[self._node_order[i]]
                if other.embedding is not None:
                    new_row[i] = _cosine_sim(new_attempt.embedding, other.embedding)

        # Expand matrix
        old_K = self._kernel_matrix
        new_K = np.zeros((n, n))
        new_K[:n-1, :n-1] = old_K
        new_K[n-1, :] = new_row
        new_K[:, n-1] = new_row
        self._kernel_matrix = new_K


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
