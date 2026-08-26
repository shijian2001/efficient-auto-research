"""CLI configuration resolution regressions."""

from arbor.cli.commands.run import _resolve_effective_options


def test_effective_options_support_documented_nested_llm_block():
    result = _resolve_effective_options(
        project_defaults={
            "llm": {
                "provider": "openai-chat",
                "model": "mock-model",
                "base_url": "http://127.0.0.1:8765/v1",
                "api_key": "dummy",
            }
        },
        user_llm={"provider": "anthropic", "model": "claude"},
        user_cli={},
        max_cycles=None,
        max_turns=None,
    )

    assert result["provider"] == "openai-chat"
    assert result["model"] == "mock-model"
    assert result["base_url"] == "http://127.0.0.1:8765/v1"
    assert result["api_key"] == "dummy"


def test_legacy_flat_llm_keys_override_nested_values():
    result = _resolve_effective_options(
        project_defaults={
            "provider": "openai-chat",
            "model": "flat-model",
            "llm": {"provider": "anthropic", "model": "nested-model"},
        },
        user_llm={},
        user_cli={},
        max_cycles=None,
        max_turns=None,
    )

    assert result["provider"] == "openai-chat"
    assert result["model"] == "flat-model"