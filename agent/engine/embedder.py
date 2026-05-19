"""
Node embedding: concat(plan_emb, code_emb, metric_vec, error_emb).

Uses a single text embedding model for all text parts. Missing parts are
zero-filled so that all nodes live in the same vector space regardless of
whether they have metric, error, or both.
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
    Compute node embedding as concatenation of independently embedded parts.

    - plan: strategy description
    - code: full implementation (model handles length internally)
    - metric: scalar value, expanded to a small vector
    - error: error message text

    Missing parts are zero-filled.
    """
    dim = _get_dim()

    plan_emb = embed_text(plan) if plan else np.zeros(dim)
    code_emb = embed_text(code) if code else np.zeros(dim)
    error_emb = embed_text(error) if error else np.zeros(dim)

    metric_vec = np.zeros(16)
    if metric is not None:
        metric_vec[0] = metric

    return np.concatenate([plan_emb, code_emb, metric_vec, error_emb])
