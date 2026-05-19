"""
Kernel Thompson Sampling for parent selection (GP Regression).

GP Regression model:
  1. Each node has a latent value f_i = expected metric of its children.
  2. Prior: f ~ N(0, K), where K_ij = cosine_sim(embedding_i, embedding_j).
  3. Observation: y_ij = child_j's metric (continuous, observed when node i is selected as parent).
  4. Posterior: exact closed-form (Gaussian prior + Gaussian likelihood = Gaussian posterior).
  5. Thompson Sampling: joint sample from posterior → argmax.

References:
  - Rasmussen & Williams, 2006 (GP Regression, Ch.2)
  - Chowdhury & Gopalan, 2017 (Kernelized TS)
"""

from __future__ import annotations

import logging

import numpy as np

from agent.engine.graph import SearchGraph, Attempt

logger = logging.getLogger("AutoResearch")

# Observation noise variance (standard GP regression parameter)
NOISE_VARIANCE = 0.01


def _collect_observations(graph: SearchGraph, node_ids: list[str]) -> tuple[list[int], list[float]]:
    """
    Collect observations: for each node, gather its children's metrics.

    Returns:
      obs_indices: which node each observation belongs to
      obs_values: the observed metric values
    """
    obs_indices = []
    obs_values = []

    for i, nid in enumerate(node_ids):
        for child in graph.get_children(nid):
            if child.metric is not None:
                obs_indices.append(i)
                obs_values.append(child.metric)

    return obs_indices, obs_values


def select_parent(graph: SearchGraph) -> str | None:
    """
    Kernel Thompson Sampling via GP Regression.

    1. Get kernel matrix from graph.
    2. Collect continuous observations (children's metrics).
    3. Compute exact GP posterior (closed form).
    4. Sample jointly from posterior.
    5. Pick argmax.
    """
    if not graph.attempts:
        return None

    node_ids = graph.node_ids
    n = len(node_ids)

    if n == 0:
        return None

    K = graph.kernel_matrix
    obs_indices, obs_values = _collect_observations(graph, node_ids)

    if not obs_values:
        # No observations — sample from prior
        K_reg = K + 1e-6 * np.eye(n)
        f_sample = np.random.multivariate_normal(np.zeros(n), K_reg)
    else:
        # GP Regression exact posterior
        # K_obs: kernel values between observation points
        # k_star: kernel values between all nodes and observation points
        m = len(obs_values)
        y = np.array(obs_values)

        # Build observation kernel matrix (m x m)
        K_obs = np.zeros((m, m))
        for i in range(m):
            for j in range(m):
                K_obs[i, j] = K[obs_indices[i], obs_indices[j]]
        K_obs += NOISE_VARIANCE * np.eye(m)

        # Build cross-kernel matrix (n x m)
        K_cross = np.zeros((n, m))
        for j in range(m):
            K_cross[:, j] = K[:, obs_indices[j]]

        # Posterior mean and covariance (exact closed form)
        # mu = K_cross @ K_obs^{-1} @ y
        # Sigma = K - K_cross @ K_obs^{-1} @ K_cross^T
        L = np.linalg.cholesky(K_obs)
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
        mu = K_cross @ alpha

        v = np.linalg.solve(L, K_cross.T)
        Sigma = K - v.T @ v
        Sigma += 1e-6 * np.eye(n)  # numerical stability

        # Joint sample from posterior
        eigvals, eigvecs = np.linalg.eigh(Sigma)
        eigvals = np.maximum(eigvals, 0)
        f_sample = mu + eigvecs @ (np.sqrt(eigvals) * np.random.randn(n))

    # Select argmax
    best_idx = int(np.argmax(f_sample))
    chosen_id = node_ids[best_idx]
    node = graph.attempts[chosen_id]
    logger.info(f"[KTS] Selected node {chosen_id} (metric={node.metric}, sampled_value={f_sample[best_idx]:.3f})")
    return chosen_id
