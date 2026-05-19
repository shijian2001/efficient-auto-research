"""
Node embedding for kernel computation.

Only plan and code are used. Metric and error are intentionally excluded so
that nodes with the same strategy have high kernel similarity regardless of
outcome — enabling cross-outcome information sharing through the GP posterior.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("AutoResearch")

_model = None


def _get_model():
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("BAAI/bge-base-en-v1.5")
        logger.info("Embedding model loaded")
    return _model


def _get_dim() -> int:
    """Get embedding dimension of the model."""
    model = _get_model()
    if hasattr(model, "get_embedding_dimension"):
        return model.get_embedding_dimension()
    return model.get_sentence_embedding_dimension()


def embed_text(text: str) -> np.ndarray:
    """Embed a text string into a normalized vector."""
    if not text:
        return np.zeros(_get_dim())
    return _get_model().encode(text, normalize_embeddings=True)


def embed_attempt(
    plan: str,
    code: str,
    metric: float | None,
    error: str | None,
) -> np.ndarray:
    """
    Compute node embedding for kernel computation.

    Only plan and code determine kernel similarity — two nodes with the same
    strategy have high correlation regardless of outcome. This enables
    cross-outcome information sharing through the GP posterior.

    Metric and error are stored in node attributes and used by improved()
    for posterior updates, but do not affect kernel values.
    """
    dim = _get_dim()

    plan_emb = embed_text(plan) if plan else np.zeros(dim)
    code_emb = embed_text(code) if code else np.zeros(dim)

    return np.concatenate([plan_emb, code_emb])
