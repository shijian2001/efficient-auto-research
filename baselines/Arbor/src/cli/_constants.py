"""Shared CLI constants and small provider helpers.

Single source of truth for values the ``run`` / ``config`` commands and the
intake REPL all need, so they cannot drift apart — e.g. a provider added in one
command but forgotten in another, or two copies of a "default model" helper that
quietly disagree.
"""

from __future__ import annotations

# User-facing provider menu (setup wizard + `config init --provider`), in
# display order. Each is a single-axis value that maps 1:1 onto a backend, so
# the config file reads the same as the menu. `auto` resolves to one of the
# concrete three at setup time.
PROVIDER_CHOICES = ("auto", "openai-responses", "openai-chat", "openai-oauth", "anthropic", "anthropic-oauth")

# Concrete providers Arbor can store + serve after `auto` is resolved. `litellm`
# stays a valid backend for back-compat / advanced hand-edited configs, but is
# no longer advertised in the menu.
_BACKEND_PROVIDERS = {"anthropic", "anthropic-oauth", "openai-responses", "openai-chat", "openai-oauth", "litellm"}
VALID_OPENAI_APIS = {"chat", "responses"}

# Intake-agent LLM call budget — seeded into the agent config by ``run`` and
# applied directly by the REPL.
# Some reasoning models (e.g. Claude Opus 4.x) take longer than 20s on the first
# intake call, which made intake time out and report the model as unavailable.
# Use a 120s budget with one extra retry so the intake call can complete.
INTAKE_LLM_TIMEOUT = 120.0
INTAKE_LLM_PROVIDER_RETRIES = 0
INTAKE_LLM_RETRY_ATTEMPTS = 3
INTAKE_LLM_RETRY_BASE_DELAY = 1.0
INTAKE_LLM_RETRY_MAX_DELAY = 4.0

# Intake is a planning conversation (read the eval, propose a contract), not a
# deep-reasoning task — so it overrides the user's reasoning_effort (often
# "high") with a lighter setting to keep each turn snappy.
INTAKE_REASONING_EFFORT = "low"

DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_OPENAI_OAUTH_MODEL = "gpt-5.5"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-20250514"
# The subscription (OAuth) backend exposes only current snapshots; the dated
# API-key default above is retired there, so use a live one.
DEFAULT_CLAUDE_OAUTH_MODEL = "claude-sonnet-4-5-20250929"

# Read-only WebUI: the browser monitor binds here by default for interactive
# runs (no flag needed). If the port is taken we walk the next few ports so a
# second concurrent run doesn't collide.
DEFAULT_WEBUI_PORT = 8765
WEBUI_PORT_SCAN = 10


def canonical_provider(provider: str | None, openai_api: str | None = None) -> str:
    """Collapse any provider alias onto a single canonical, single-axis value.

    Returns one of ``auto`` | ``anthropic`` | ``openai-responses`` |
    ``openai-chat`` | ``litellm``. The legacy two-axis form (``openai`` plus
    ``openai_api: chat|responses``) folds into the matching ``openai-*`` value,
    so newly written configs only ever carry the single ``provider`` field.
    """
    p = (provider or "anthropic").strip().lower()
    api = (openai_api or "").strip().lower()
    if p == "auto":
        return "auto"
    if p in ("claude", "anthropic"):
        return "anthropic"
    if p in ("anthropic-oauth", "claude-oauth", "claude-pro", "anthropic_oauth"):
        return "anthropic-oauth"
    if p == "litellm":
        return "litellm"
    if p in ("openai-oauth", "chatgpt", "openai_oauth"):
        return "openai-oauth"
    if p in ("openai-chat", "chat", "openai_compat", "openai_chat"):
        return "openai-chat"
    if p in ("openai-responses", "responses", "openai_responses", "openai_response"):
        return "openai-responses"
    if p == "openai":  # legacy bare provider: respect the openai_api axis
        return "openai-chat" if api == "chat" else "openai-responses"
    return p  # unknown → passthrough; resolve_backend decides or errors later


def default_model_for_provider(provider: str | None) -> str | None:
    """Default model for ``provider``, or ``None`` to defer to the provider.

    ``anthropic``/``auto`` return ``None`` because Claude supplies its own
    default; the OpenAI family and litellm need an explicit model here. Callers
    that must persist a concrete string substitute :data:`DEFAULT_CLAUDE_MODEL`.
    """
    canon = canonical_provider(provider)
    if canon == "openai-oauth":
        return DEFAULT_OPENAI_OAUTH_MODEL
    if canon == "anthropic-oauth":
        return DEFAULT_CLAUDE_OAUTH_MODEL
    if canon.startswith("openai") or canon == "litellm":
        return DEFAULT_OPENAI_MODEL
    return None
