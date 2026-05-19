"""
Thompson Sampling on the search graph.

Selects which node to use as parent for the next attempt. The posterior
for each candidate is derived from the graph: the candidate's own children
AND its similar neighbors' children provide evidence about whether starting
from that node leads to improvement.
"""

from __future__ import annotations

import logging

import numpy as np

from agent.engine.graph import SearchGraph, Attempt

logger = logging.getLogger("AutoResearch")


def improved(child: Attempt, parent: Attempt) -> bool:
    """Did the child bring improvement over the parent?"""
    if child.metric is None:
        return False
    if parent.metric is None:
        return True
    return child.metric > parent.metric


def compute_posterior(node_id: str, graph: SearchGraph) -> tuple[int, int]:
    """
    Compute Beta posterior for "from this node, does the next step improve?"

    Evidence:
      1. This node's own children (direct experience).
      2. This node's KNN similar neighbors' children (borrowed experience).

    Returns (alpha, beta) parameters for Beta distribution.
    """
    alpha, beta = 1, 1
    node = graph.attempts[node_id]

    for child in graph.get_children(node_id):
        if improved(child, node):
            alpha += 1
        else:
            beta += 1

    for neighbor in graph.get_similar(node_id):
        for child in graph.get_children(neighbor.id):
            if improved(child, neighbor):
                alpha += 1
            else:
                beta += 1

    return alpha, beta


def select_parent(graph: SearchGraph) -> str | None:
    """
    Thompson Sampling: select which node to use as parent.

    Candidates: every existing node + None (start fresh).
    For each, sample from its Beta posterior and pick argmax.
    """
    if not graph.attempts:
        return None

    candidates: list[tuple[str | None, float]] = []

    for node_id in graph.attempts:
        alpha, beta = compute_posterior(node_id, graph)
        sample = float(np.random.beta(alpha, beta))
        candidates.append((node_id, sample))

    roots = graph.get_roots()
    root_success = sum(1 for r in roots if r.metric is not None)
    root_fail = len(roots) - root_success
    draft_sample = float(np.random.beta(1 + root_success, 1 + root_fail))
    candidates.append((None, draft_sample))

    best_id, best_sample = max(candidates, key=lambda x: x[1])

    if best_id:
        node = graph.attempts[best_id]
        logger.info(f"[TS] Selected node {best_id} (metric={node.metric}, sample={best_sample:.3f})")
    else:
        logger.info(f"[TS] Selected new draft (sample={best_sample:.3f})")

    return best_id
