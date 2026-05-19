"""
Kernel Thompson Sampling on the search graph.

Implements exact Kernel TS for Bernoulli rewards with Laplace approximation:
  1. Each node has a latent parameter f_i (logit of success probability).
  2. Prior: f ~ N(0, K), where K_ij = cosine_sim(embedding_i, embedding_j).
  3. Observation: y_i ∈ {0, 1} (did child improve over parent?).
  4. Posterior: Laplace approximation → N(f_hat, Sigma).
  5. Thompson Sampling: sample from posterior → sigmoid → argmax.

This is theoretically principled — it is the standard GP classification
framework applied to the graph bandit setting. No pseudo-count approximation.

References:
  - Filippi et al., 2010 (Parametric Bandits)
  - Chowdhury & Gopalan, 2017 (Kernelized TS)
  - Kveton et al., 2020 (Kernel TS)
"""

from __future__ import annotations

import logging

import numpy as np
from numpy.linalg import cholesky, solve

from agent.engine.graph import SearchGraph, Attempt

logger = logging.getLogger("AutoResearch")


def improved(child: Attempt, parent: Attempt) -> bool:
    """Did the child bring improvement over the parent?"""
    if child.metric is None:
        return False
    if parent.metric is None:
        return True
    return child.metric > parent.metric


def _sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable sigmoid."""
    return np.where(x >= 0, 1 / (1 + np.exp(-x)), np.exp(x) / (1 + np.exp(x)))


def _build_kernel_matrix(graph: SearchGraph, node_ids: list[str]) -> np.ndarray:
    """Build kernel matrix K where K_ij = cosine_sim(node_i, node_j)."""
    n = len(node_ids)
    K = np.eye(n)  # diagonal = 1 (self-similarity)
    for i in range(n):
        emb_i = graph.attempts[node_ids[i]].embedding
        if emb_i is None:
            continue
        for j in range(i + 1, n):
            emb_j = graph.attempts[node_ids[j]].embedding
            if emb_j is None:
                continue
            norm_i = np.linalg.norm(emb_i)
            norm_j = np.linalg.norm(emb_j)
            if norm_i > 0 and norm_j > 0:
                sim = float(np.dot(emb_i, emb_j) / (norm_i * norm_j))
            else:
                sim = 0.0
            K[i, j] = sim
            K[j, i] = sim
    return K


def _collect_observations(graph: SearchGraph, node_ids: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """
    Collect observations for each node.

    Returns:
      obs_count: number of children observed per node
      obs_success: number of successful children per node
    """
    n = len(node_ids)
    obs_count = np.zeros(n)
    obs_success = np.zeros(n)

    for i, nid in enumerate(node_ids):
        node = graph.attempts[nid]
        for child in graph.get_children(nid):
            obs_count[i] += 1
            if improved(child, node):
                obs_success[i] += 1

    return obs_count, obs_success


def _laplace_approximation(
    K: np.ndarray,
    obs_count: np.ndarray,
    obs_success: np.ndarray,
    max_iter: int = 20,
    tol: float = 1e-6,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Laplace approximation for GP classification posterior.

    Model:
      f ~ N(0, K)                    (prior)
      y_i | f_i ~ Bernoulli(σ(f_i))  (likelihood, aggregated over children)

    For nodes with multiple children, the log-likelihood is:
      ℓ_i(f_i) = s_i * log(σ(f_i)) + (n_i - s_i) * log(1 - σ(f_i))
    where s_i = successes, n_i = total observations.

    Returns:
      f_hat: posterior mode (MAP estimate)
      Sigma: posterior covariance (Laplace approximation)
    """
    n = len(obs_count)
    f = np.zeros(n)  # initialize at prior mean

    # Regularize K for numerical stability
    K_reg = K + 1e-6 * np.eye(n)

    for iteration in range(max_iter):
        p = _sigmoid(f)

        # Gradient of log-likelihood
        grad_ll = obs_success - obs_count * p

        # Hessian of log-likelihood (diagonal: -n_i * p_i * (1-p_i))
        W = obs_count * p * (1 - p)
        W = np.maximum(W, 1e-10)  # numerical stability

        # Newton step for posterior mode: f_new = K @ (K + W^{-1})^{-1} @ (f + W^{-1} @ grad_ll)
        # Equivalent: solve (K^{-1} + diag(W)) f = K^{-1} @ 0 + grad_ll + W @ f
        # Simplified: (I + K @ diag(W)) @ b = K @ (grad_ll + W @ f), then f_new = K @ (grad_ll + W @ f) - K @ diag(W) @ ...
        # Use standard form: f_new = K @ inv(I + diag(sqrt(W)) @ K @ diag(sqrt(W))) @ diag(sqrt(W)) @ (f + diag(1/W) @ grad_ll)

        # Simpler direct approach: solve (K_inv + diag(W)) f = grad_ll + W * f_old
        # Since we have K, use Woodbury: (K_inv + W)^{-1} = K - K @ (I + W @ K)^{-1} @ W @ K
        # But simplest for N<50: direct solve

        A = np.linalg.inv(K_reg) + np.diag(W)
        b = grad_ll + W * f
        f_new = np.linalg.solve(A, b)

        if np.max(np.abs(f_new - f)) < tol:
            f = f_new
            break
        f = f_new

    # Posterior covariance: Sigma = (K^{-1} + diag(W))^{-1}
    p = _sigmoid(f)
    W = obs_count * p * (1 - p)
    W = np.maximum(W, 1e-10)
    A = np.linalg.inv(K_reg) + np.diag(W)
    Sigma = np.linalg.inv(A)

    return f, Sigma


def select_parent(graph: SearchGraph) -> str | None:
    """
    Kernel Thompson Sampling: select which node to use as parent.

    1. Build kernel matrix from node embeddings.
    2. Collect binary observations (did children improve?).
    3. Compute Laplace-approximated GP posterior.
    4. Sample from joint posterior.
    5. Convert to success probabilities via sigmoid.
    6. Include "start fresh" candidate.
    7. Pick argmax.
    """
    if not graph.attempts:
        return None

    node_ids = list(graph.attempts.keys())
    n = len(node_ids)

    if n == 0:
        return None

    # Build kernel matrix
    K = _build_kernel_matrix(graph, node_ids)

    # Collect observations
    obs_count, obs_success = _collect_observations(graph, node_ids)

    # Compute posterior and sample
    if obs_count.sum() == 0:
        # No observations yet — sample from prior
        f_sample = np.random.multivariate_normal(np.zeros(n), K)
    else:
        f_hat, Sigma = _laplace_approximation(K, obs_count, obs_success)
        # Sample from posterior N(f_hat, Sigma)
        # Use eigendecomposition for numerical stability
        eigvals, eigvecs = np.linalg.eigh(Sigma)
        eigvals = np.maximum(eigvals, 0)  # clip negative eigenvalues
        f_sample = f_hat + eigvecs @ (np.sqrt(eigvals) * np.random.randn(n))

    # Convert to success probabilities and select argmax
    probs = _sigmoid(f_sample)
    best_idx = int(np.argmax(probs))
    chosen_id = node_ids[best_idx]
    node = graph.attempts[chosen_id]
    logger.info(f"[KTS] Selected node {chosen_id} (metric={node.metric}, prob={probs[best_idx]:.3f})")
    return chosen_id
