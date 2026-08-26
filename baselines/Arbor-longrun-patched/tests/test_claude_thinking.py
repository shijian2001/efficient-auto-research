"""Thinking-param shaping for ClaudeProvider.

Covers the split between legacy explicit-budget models and the newer
adaptive-thinking models (Opus 4.7/4.8) that require
``thinking.type=adaptive`` + ``output_config.effort``. Pure offline checks
against the request params the provider builds (no network).
"""

from __future__ import annotations

import pytest

from arbor.core.llm.claude import ClaudeProvider


def _provider(model: str, **kw) -> ClaudeProvider:
    kw.setdefault("api_key", "dummy")
    return ClaudeProvider(model=model, **kw)


def _thinking(model: str, *, max_tokens: int = 2048, **kw) -> dict:
    p = _provider(model, **kw)
    params = p._build_params("sys", [{"role": "user", "content": "hi"}], None, max_tokens)
    return {k: params.get(k) for k in ("thinking", "output_config")}


@pytest.mark.parametrize(
    "model",
    ["claude-opus-4-7", "claude-opus-4-8", "claude-opus-4-8-20260101"],
)
def test_adaptive_models_use_adaptive_thinking(model):
    out = _thinking(model, reasoning_effort="low")
    assert out["thinking"] == {"type": "adaptive"}
    assert out["output_config"] == {"effort": "low"}


@pytest.mark.parametrize(
    "effort,expected",
    [("low", "low"), ("medium", "medium"), ("high", "high"), ("bogus", "medium")],
)
def test_adaptive_effort_mapping(effort, expected):
    out = _thinking("claude-opus-4-8", reasoning_effort=effort)
    assert out["output_config"] == {"effort": expected}


def test_adaptive_no_effort_omits_thinking():
    out = _thinking("claude-opus-4-8", reasoning_effort=None)
    assert out["thinking"] is None
    assert out["output_config"] is None


@pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
def test_legacy_models_keep_budget_tokens(model):
    out = _thinking(model, reasoning_effort="low")
    assert out["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert out["output_config"] is None


def test_internal_message_metadata_is_not_sent_to_anthropic():
    provider = _provider("claude-sonnet-4-6")
    messages = provider._cache_messages([
        {
            "role": "user",
            "content": "continue",
            "_internal": "premature_stop_nudge",
        }
    ])

    assert "_internal" not in messages[0]
    assert messages[0]["role"] == "user"
