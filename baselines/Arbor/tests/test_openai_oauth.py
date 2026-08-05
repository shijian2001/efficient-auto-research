"""Tests for the experimental ChatGPT subscription OAuth backend."""

from __future__ import annotations

import base64
import json
import time

import pytest

from arbor.core import resolve_backend
from arbor.core.oauth import openai as oauth
from arbor.cli._constants import canonical_provider, default_model_for_provider


def _fake_jwt(account_id: str = "acc-123", plan: str = "pro") -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({
        "email": "user@example.com",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan,
        },
    })
    return f"{header}.{payload}.sig"


# ── Routing ──────────────────────────────────────────────────────────────────

def test_resolve_backend_recognizes_oauth():
    assert resolve_backend("openai-oauth", None, "gpt-5", None) == "openai-oauth"
    assert resolve_backend("chatgpt", None, "gpt-5", None) == "openai-oauth"


def test_canonical_and_default_model():
    assert canonical_provider("openai-oauth") == "openai-oauth"
    assert canonical_provider("chatgpt") == "openai-oauth"
    assert default_model_for_provider("openai-oauth") == "gpt-5.5"


# ── JWT claim parsing ────────────────────────────────────────────────────────

def test_account_info_from_id_token():
    account_id, plan = oauth._account_info(_fake_jwt("acc-xyz", "team"))
    assert account_id == "acc-xyz"
    assert plan == "team"


def test_account_info_malformed_is_empty():
    assert oauth._account_info("not-a-jwt") == ("", "")


# ── Token persistence ────────────────────────────────────────────────────────

def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "oauth" / "openai.json")
    tokens = oauth.OpenAITokens(
        access_token="at", refresh_token="rt", account_id="acc", plan_type="pro",
        expires_at=time.time() + 3600,
    )
    oauth.save_tokens(tokens)
    loaded = oauth.load_tokens()
    assert loaded is not None
    assert loaded.access_token == "at"
    assert loaded.account_id == "acc"
    assert not loaded.is_expired
    assert oauth.clear_tokens() is True
    assert oauth.load_tokens() is None


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "nope.json")
    assert oauth.load_tokens() is None


# ── Refresh ──────────────────────────────────────────────────────────────────

def test_refresh_preserves_account_when_id_token_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "openai.json")
    monkeypatch.setattr(oauth, "_post_token", lambda form: {
        "access_token": "new-at", "expires_in": 3600,
        # no refresh_token, no id_token in the refresh response
    })
    old = oauth.OpenAITokens(
        access_token="old", refresh_token="rt", account_id="acc-keep",
        plan_type="pro", id_token=_fake_jwt("acc-keep", "pro"), expires_at=0,
    )
    refreshed = oauth.refresh(old)
    assert refreshed.access_token == "new-at"
    assert refreshed.refresh_token == "rt"        # fell back to the old one
    assert refreshed.account_id == "acc-keep"     # preserved across refresh
    assert refreshed.plan_type == "pro"


def test_get_valid_tokens_refreshes_when_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "openai.json")
    expired = oauth.OpenAITokens(
        access_token="old", refresh_token="rt", account_id="acc",
        plan_type="pro", expires_at=0,
    )
    oauth.save_tokens(expired)
    monkeypatch.setattr(oauth, "_post_token", lambda form: {
        "access_token": "fresh", "refresh_token": "rt2", "expires_in": 3600,
    })
    tokens = oauth.get_valid_tokens()
    assert tokens.access_token == "fresh"


def test_get_valid_tokens_raises_when_logged_out(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "openai.json")
    with pytest.raises(oauth.OAuthError):
        oauth.get_valid_tokens()


# ── Provider wiring ──────────────────────────────────────────────────────────

def test_provider_builds_chatgpt_client(monkeypatch):
    from arbor.core.llm.openai_oauth import OpenAIOAuthProvider

    fake = oauth.OpenAITokens(
        access_token="tok", refresh_token="rt", account_id="acc-1",
        plan_type="pro", expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(oauth, "get_valid_tokens", lambda: fake)

    provider = OpenAIOAuthProvider(model="gpt-5")
    assert provider.base_url == oauth.CHATGPT_RESPONSES_BASE_URL
    assert provider._account_id == "acc-1"
    assert provider._access_token == "tok"
    # The backend routing header must be present on the client.
    headers = provider._client.default_headers
    assert headers.get("chatgpt-account-id") == "acc-1"


def test_create_reassembles_streamed_items(monkeypatch):
    """The codex backend leaves completed.output empty and streams items via
    ``output_item.done``; create() must collect those into the response."""
    import asyncio
    from types import SimpleNamespace

    from arbor.core.llm.openai_oauth import OpenAIOAuthProvider

    fake = oauth.OpenAITokens(
        access_token="tok", refresh_token="rt", account_id="acc-1",
        plan_type="pro", expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(oauth, "get_valid_tokens", lambda: fake)
    provider = OpenAIOAuthProvider(model="gpt-5.5")

    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item={"type": "function_call", "call_id": "call_1",
                  "name": "get_weather", "arguments": '{"city": "Paris"}'},
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                output=[],  # codex leaves this empty on purpose
                usage=SimpleNamespace(input_tokens=12, output_tokens=7),
                model="gpt-5.5", status="completed",
                incomplete_details=None, output_text="",
            ),
        ),
    ]

    async def fake_create(**params):
        assert params["stream"] is True
        assert "max_output_tokens" not in params  # codex rejects it

        async def gen():
            for ev in events:
                yield ev

        return gen()

    provider._client.responses.create = fake_create  # type: ignore[assignment]

    result = asyncio.run(provider.create(
        system="s", messages=[{"role": "user", "content": "weather in Paris?"}],
        tools=[{"name": "get_weather", "description": "w",
                "input_schema": {"type": "object", "properties": {}}}],
    ))

    assert result.stop_reason == "tool_use"
    tool_calls = [b for b in result.content if getattr(b, "name", None) == "get_weather"]
    assert tool_calls and tool_calls[0].input == {"city": "Paris"}
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7

