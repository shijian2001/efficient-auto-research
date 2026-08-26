"""Setup-time backend auto-detection for ``provider: auto``.

When the user picks ``auto`` we resolve it to a *concrete* backend **once**, at
``arbor setup`` / ``arbor config init`` time, and freeze the result in the config
file. The runtime stays pure and fast (``resolve_backend`` never touches the
network); the only network probe happens here, during setup.

Resolution rules:

* ``claude*`` → ``anthropic`` (native Messages API: signed thinking blocks +
  prompt caching), against the official endpoint or a custom ``base_url``.
* Anything else → **probe** ``{base_url}/responses``. If the endpoint serves the
  OpenAI Responses API we pick ``openai-responses`` so the reasoning chain is
  preserved across ReAct turns; otherwise we fall back to ``openai-chat`` (chat
  completions), which every OpenAI-compatible endpoint supports.

The probe is best-effort and never raises — an inconclusive result (network
error, bad key, …) falls back to ``openai-chat``, which the user can always
override by setting ``provider`` explicitly.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# How long to wait for the one-shot Responses probe before giving up and
# falling back to chat completions. Setup is interactive, so keep it snappy.
_PROBE_TIMEOUT = 10.0


def probe_responses_api(
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    timeout: float = _PROBE_TIMEOUT,
) -> bool:
    """Best-effort: ``True`` iff ``{base_url}/responses`` actually answers this
    model with a usable response.

    Never raises. Only a clean success counts as "supported" — this is
    deliberately conservative. A false positive (picking the Responses API when
    it won't work) breaks every run, which is exactly the failure we're trying
    to prevent; a false negative merely costs the reasoning-chain upgrade and
    falls back to chat completions, which still works. Note that a route can
    *exist* yet reject the model (e.g. a proxy that answers ``/responses`` with
    400 "this model does not support the responses endpoint"), so "the route is
    there" is not enough — we require an actual 2xx.
    """
    try:
        from openai import OpenAI
    except Exception:  # pragma: no cover - openai always installed in practice
        return False

    try:
        client = OpenAI(
            api_key=api_key or "dummy",
            base_url=base_url or None,
            max_retries=0,
            timeout=timeout,
        )
        # Minimal request: no reasoning, tiny output. We only care whether the
        # /responses route returns a usable response for this model.
        resp = client.responses.create(model=model, input="ping", max_output_tokens=16)
        # A genuine Responses API returns an object with an id; anything else
        # (a chat shim echoing JSON, an empty body, …) is not the real thing.
        return getattr(resp, "id", None) is not None
    except Exception as e:
        # 404 (no route), 400 (route exists but model unsupported / bad request),
        # auth, connection, timeout — all inconclusive or negative. Fall back to
        # the universal chat path.
        log.debug("responses probe failed for %s @ %s: %s", model, base_url, e)
        return False


def resolve_auto_provider(
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    timeout: float = _PROBE_TIMEOUT,
) -> tuple[str, str]:
    """Resolve ``provider: auto`` to a concrete backend at setup time.

    Returns ``(provider, reason)`` where ``provider`` is one of ``anthropic`` |
    ``openai-responses`` | ``openai-chat`` (the user-facing menu values minus
    ``auto``) and ``reason`` is a short human-readable note for the setup output.
    """
    bare = (model or "").rsplit("/", 1)[-1].lower()

    if bare.startswith(("claude", "anthropic")):
        return "anthropic", "Claude model → native Anthropic Messages API"

    if probe_responses_api(model=model, base_url=base_url, api_key=api_key, timeout=timeout):
        return (
            "openai-responses",
            "endpoint serves the Responses API → openai-responses (reasoning chain preserved)",
        )
    return "openai-chat", "no Responses API on this endpoint → openai-chat (chat completions)"
