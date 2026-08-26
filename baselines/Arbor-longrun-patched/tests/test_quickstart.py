"""Tests for the easy-start presets (``arbor quickstart`` / setup rung 2).

The interactive chooser is I/O; the value is in the pure mapping from a preset
(+ overrides) to a ``write_user_llm_config`` dict, so that's what we lock down.
"""

from __future__ import annotations

from arbor.cli.quickstart import (
    EASY_PRESETS,
    build_llm_from_preset,
    preset_by_key,
)


def test_presets_are_well_formed() -> None:
    keys = [p.key for p in EASY_PRESETS]
    assert keys == ["gemini", "groq", "ollama"]          # stable menu order
    assert len(set(keys)) == len(keys)                   # unique
    for p in EASY_PRESETS:
        assert p.provider == "openai-chat"               # all OpenAI-compatible
        assert p.base_url.startswith("http")
        assert p.default_model
        # Hosted presets need a key; the local one ships a placeholder instead.
        assert p.needs_key == (p.key != "ollama")


def test_preset_by_key_is_case_insensitive() -> None:
    assert preset_by_key("GEMINI").key == "gemini"
    assert preset_by_key(" groq ").key == "groq"
    assert preset_by_key("nope") is None


def test_build_hosted_preset_with_key_and_default_model() -> None:
    gemini = preset_by_key("gemini")
    llm = build_llm_from_preset(gemini, api_key="secret-key")
    assert llm == {
        "provider": "openai-chat",
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": "secret-key",
    }


def test_build_hosted_preset_without_key_omits_it() -> None:
    """A blank key must not be written — the runtime then resolves it from env."""
    groq = preset_by_key("groq")
    llm = build_llm_from_preset(groq, api_key="   ")
    assert "api_key" not in llm
    assert llm["base_url"] == "https://api.groq.com/openai/v1"


def test_build_local_preset_injects_placeholder_key() -> None:
    ollama = preset_by_key("ollama")
    llm = build_llm_from_preset(ollama)
    assert llm["api_key"] == "ollama"          # keyless local server still needs a token
    assert llm["base_url"] == "http://localhost:11434/v1"


def test_model_override_wins() -> None:
    gemini = preset_by_key("gemini")
    llm = build_llm_from_preset(gemini, api_key="k", model="gemini-2.5-pro")
    assert llm["model"] == "gemini-2.5-pro"
    # Whitespace-only override falls back to the preset default.
    assert build_llm_from_preset(gemini, api_key="k", model="  ")["model"] == "gemini-2.5-flash"
