"""Easy-start presets — drop the "use it for real" barrier from *pay* to *free*.

Arbor needs an LLM, but it does not need a *paid* one to get started. Several
providers expose an OpenAI-compatible endpoint plus a free key (Gemini, Groq) or
run fully local with no key (Ollama). This module curates those into a short
menu so a first-time user can be running their own task in ~2 minutes instead of
hunting for a provider, an endpoint, and a model name.

The presets all map onto the ``openai-chat`` backend (an OpenAI-compatible
endpoint), so the only per-provider differences are the base URL, a sensible
default model, and where to grab a free key. Keep the pure mapping
(:func:`build_llm_from_preset`) free of I/O so it stays trivially testable; the
interactive chooser layers prompts on top.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EasyPreset:
    """One curated 'get running fast' option."""

    key: str            # short id used in flags / lookups
    label: str          # menu label
    blurb: str          # one-line what + why
    base_url: str
    default_model: str
    needs_key: bool
    signup_url: str | None = None
    provider: str = "openai-chat"
    # Dummy key written when needs_key is False, so the OpenAI-compatible client
    # has *something* to send to a local server that ignores it.
    placeholder_key: str = "local"


# Endpoints/models verified against provider docs (Gemini & Groq OpenAI-compat
# layers, Ollama's local OpenAI shim). Models are sensible free-tier defaults the
# user can override at the prompt; they're for *trying Arbor on your own task*,
# not for topping a benchmark — a serious run wants a stronger model.
EASY_PRESETS: tuple[EasyPreset, ...] = (
    EasyPreset(
        key="gemini",
        label="Google Gemini — free key, hosted",
        blurb="free tier at aistudio.google.com, no card needed",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-2.5-flash",
        needs_key=True,
        signup_url="https://aistudio.google.com/apikey",
    ),
    EasyPreset(
        key="groq",
        label="Groq — free key, very fast",
        blurb="free developer tier, OpenAI-compatible, low latency",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        needs_key=True,
        signup_url="https://console.groq.com/keys",
    ),
    EasyPreset(
        key="ollama",
        label="Ollama — local, no key, offline",
        blurb="runs on your machine; needs `ollama serve` + a pulled model",
        base_url="http://localhost:11434/v1",
        default_model="qwen2.5-coder",
        needs_key=False,
        signup_url="https://ollama.com/download",
        placeholder_key="ollama",
    ),
)


def preset_by_key(key: str) -> EasyPreset | None:
    key = (key or "").strip().lower()
    for preset in EASY_PRESETS:
        if preset.key == key:
            return preset
    return None


def build_llm_from_preset(
    preset: EasyPreset,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """Map a preset (+ optional overrides) onto a ``write_user_llm_config`` dict.

    Pure: no prompts, no disk. ``model`` falls back to the preset default; the key
    falls back to the preset's placeholder for keyless (local) providers so the
    OpenAI-compatible client always has a token to send.
    """
    llm: dict[str, str] = {
        "provider": preset.provider,
        "model": (model or "").strip() or preset.default_model,
        "base_url": preset.base_url,
    }
    key = (api_key or "").strip()
    if not key and not preset.needs_key:
        key = preset.placeholder_key
    if key:
        llm["api_key"] = key
    return llm
