"""
Kernel Thompson Sampling for parent selection (GP Regression).

GP Regression model:
  1. Each node has a latent value f_i = expected metric achievable from that node.
  2. Prior: f ~ N(0, K), where K_ij = cosine_sim(embedding_i, embedding_j).
  3. Observations: the node's own metric (direct evidence of the value at that
     point in solution space) plus each child's metric (evidence of what
     expanding from it yields). Without the self-observation a fresh
     high-metric leaf has no observations at all, so TS keeps ignoring it.
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

# Stagnation-adaptive exploration temperature.
# When the global best has not improved for several steps, the search is stuck
# exploiting a local basin (self-observation pulls TS toward the current best,
# collapsing exploration variance — see chaii regression 0.588->0.541). We then
# scale the posterior *sampling* variance by a temperature T > 1 so Thompson
# Sampling reaches farther, more distinct parents and can break out. This is a
# pure search-layer knob: it does NOT touch LLM generation temperature.
#   T = 1 + STAGNATION_GAIN * max(0, stagnation - STAGNATION_TRIGGER), capped.
# stagnation=0 (best just improved) => T=1 => behaviour identical to before.
STAGNATION_TRIGGER = 3   # grace steps before heating starts
STAGNATION_GAIN = 0.5    # variance-temperature added per stagnant step
STAGNATION_T_MAX = 3.0   # cap (≈ the high-variance regime of the fair-run)


def stagnation_temperature(stagnation: int) -> float:
    """Map a stagnation count to the TS sampling-variance temperature T >= 1."""
    if stagnation <= STAGNATION_TRIGGER:
        return 1.0
    return min(STAGNATION_T_MAX, 1.0 + STAGNATION_GAIN * (stagnation - STAGNATION_TRIGGER))


def _collect_observations(
    graph: SearchGraph, node_ids: list[str], metric_sign: int = 1
) -> tuple[list[int], list[float]]:
    """
    Collect observations: each node's own metric plus its children's metrics.

    The self-observation is essential: a newly created high-metric node has no
    children yet, and without it the GP would have zero evidence at that point —
    the best node in the graph would look no more promising than an untried one.

    Observations are multiplied by metric_sign (+1 higher-is-better, -1
    lower-is-better) so that the downstream argmax over the posterior always
    means "most promising parent" regardless of the competition's direction.

    Returns:
      obs_indices: which node each observation belongs to
      obs_values: the observed (sign-oriented) metric values
    """
    obs_indices = []
    obs_values = []

    for i, nid in enumerate(node_ids):
        node = graph.attempts[nid]
        if node.metric is not None:
            obs_indices.append(i)
            obs_values.append(metric_sign * node.metric)
        for child in graph.get_children(nid):
            if child.metric is not None:
                obs_indices.append(i)
                obs_values.append(metric_sign * child.metric)

    return obs_indices, obs_values


def select_parent(graph: SearchGraph, stagnation: int = 0, metric_sign: int = 1) -> str | None:
    """
    Kernel Thompson Sampling via GP Regression.

    1. Get kernel matrix from graph.
    2. Collect continuous observations (children's metrics).
    3. Compute exact GP posterior (closed form).
    4. Sample jointly from posterior (variance scaled by stagnation temperature).
    5. Pick argmax.

    Args:
      stagnation: number of consecutive steps the global best has not improved.
        Drives the exploration temperature T (see stagnation_temperature). 0 =>
        T=1 => identical to the un-heated posterior sample.
      metric_sign: +1 if higher metric is better, -1 if lower is better.
        Observations are oriented by this sign so the final argmax targets the
        genuinely best node on lower-is-better tasks (log-loss/RMSE/MAE).
    """
    if not graph.attempts:
        return None

    node_ids = graph.node_ids
    n = len(node_ids)

    if n == 0:
        return None

    K = graph.kernel_matrix
    obs_indices, obs_values = _collect_observations(graph, node_ids, metric_sign)

    # Exploration temperature: >1 only when the best has stagnated (see module top).
    T = stagnation_temperature(stagnation)

    if not obs_values:
        # No observations — sample from prior (variance scaled by T)
        K_reg = K + 1e-6 * np.eye(n)
        f_sample = np.random.multivariate_normal(np.zeros(n), (T * T) * K_reg)
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

        # Joint sample from posterior (std scaled by T to widen exploration)
        eigvals, eigvecs = np.linalg.eigh(Sigma)
        eigvals = np.maximum(eigvals, 0)
        f_sample = mu + T * (eigvecs @ (np.sqrt(eigvals) * np.random.randn(n)))

    # Select argmax
    best_idx = int(np.argmax(f_sample))
    chosen_id = node_ids[best_idx]
    node = graph.attempts[chosen_id]
    logger.info(f"[KTS] Selected node {chosen_id} (metric={node.metric}, sampled_value={f_sample[best_idx]:.3f}, T={T:.2f}, stagnation={stagnation})")
    return chosen_id
