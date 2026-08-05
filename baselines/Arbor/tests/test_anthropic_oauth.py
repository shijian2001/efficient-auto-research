"""Tests for the experimental Claude subscription OAuth backend."""

from __future__ import annotations

import time

import pytest

from arbor.core import resolve_backend
from arbor.core.oauth import anthropic as oauth
from arbor.cli._constants import canonical_provider, default_model_for_provider


# ── Routing ──────────────────────────────────────────────────────────────────

def test_resolve_backend_recognizes_oauth():
    assert resolve_backend("anthropic-oauth", None, "claude-sonnet-4", None) == "anthropic-oauth"
    assert resolve_backend("claude-oauth", None, "claude-sonnet-4", None) == "anthropic-oauth"
    assert resolve_backend("claude-pro", None, "claude-sonnet-4", None) == "anthropic-oauth"


def test_canonical_and_default_model():
    assert canonical_provider("anthropic-oauth") == "anthropic-oauth"
    assert canonical_provider("claude-oauth") == "anthropic-oauth"
    from arbor.cli._constants import DEFAULT_CLAUDE_OAUTH_MODEL
    assert default_model_for_provider("anthropic-oauth") == DEFAULT_CLAUDE_OAUTH_MODEL


# ── Authorize URL / code parsing ─────────────────────────────────────────────

def test_authorize_url_has_pkce_and_state():
    url = oauth._authorize_url("chal", "st8")
    assert url.startswith(oauth.AUTHORIZE_URL)
    assert "code_challenge=chal" in url
    assert "code_challenge_method=S256" in url
    assert "state=st8" in url
    assert "code=true" in url


def test_split_code_with_state():
    code, state = oauth._split_code("abc#xyz", expected_state="fallback")
    assert code == "abc"
    assert state == "xyz"


def test_split_code_without_state_uses_expected():
    code, state = oauth._split_code("abc", expected_state="fallback")
    assert code == "abc"
    assert state == "fallback"


# ── Token persistence ────────────────────────────────────────────────────────

def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "oauth" / "anthropic.json")
    tokens = oauth.AnthropicTokens(
        access_token="at", refresh_token="rt", account_email="user@example.com",
        scope="user:inference", expires_at=time.time() + 3600,
    )
    oauth.save_tokens(tokens)
    loaded = oauth.load_tokens()
    assert loaded is not None
    assert loaded.access_token == "at"
    assert loaded.account_email == "user@example.com"
    assert not loaded.is_expired
    assert oauth.clear_tokens() is True
    assert oauth.load_tokens() is None


def test_load_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "nope.json")
    assert oauth.load_tokens() is None


# ── Token response parsing ───────────────────────────────────────────────────

def test_tokens_from_response_extracts_email():
    data = {
        "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        "scope": "user:inference",
        "account": {"email_address": "u@example.com", "uuid": "abc"},
    }
    tokens = oauth._tokens_from_response(data)
    assert tokens.access_token == "at"
    assert tokens.account_email == "u@example.com"
    assert tokens.scope == "user:inference"
    assert not tokens.is_expired


def test_tokens_from_response_requires_access_token():
    with pytest.raises(oauth.OAuthError):
        oauth._tokens_from_response({"refresh_token": "rt"})


# ── Refresh ──────────────────────────────────────────────────────────────────

def test_refresh_preserves_email_and_refresh_token(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "anthropic.json")
    monkeypatch.setattr(oauth, "_post_token", lambda body: {
        "access_token": "new-at", "expires_in": 3600,
        # refresh response omits refresh_token and account
    })
    old = oauth.AnthropicTokens(
        access_token="old", refresh_token="rt", account_email="keep@example.com",
        expires_at=0,
    )
    refreshed = oauth.refresh(old)
    assert refreshed.access_token == "new-at"
    assert refreshed.refresh_token == "rt"               # fell back to the old one
    assert refreshed.account_email == "keep@example.com"  # preserved across refresh


def test_get_valid_tokens_refreshes_when_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "anthropic.json")
    expired = oauth.AnthropicTokens(
        access_token="old", refresh_token="rt", expires_at=0,
    )
    oauth.save_tokens(expired)
    monkeypatch.setattr(oauth, "_post_token", lambda body: {
        "access_token": "fresh", "refresh_token": "rt2", "expires_in": 3600,
    })
    tokens = oauth.get_valid_tokens()
    assert tokens.access_token == "fresh"


def test_get_valid_tokens_raises_when_logged_out(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "TOKEN_PATH", tmp_path / "anthropic.json")
    with pytest.raises(oauth.OAuthError):
        oauth.get_valid_tokens()


# ── Provider wiring ──────────────────────────────────────────────────────────

def test_provider_builds_oauth_client(monkeypatch):
    from arbor.core.llm.claude_oauth import ClaudeOAuthProvider

    fake = oauth.AnthropicTokens(
        access_token="tok", refresh_token="rt", account_email="u@example.com",
        expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(oauth, "get_valid_tokens", lambda: fake)

    provider = ClaudeOAuthProvider(model="claude-sonnet-4-20250514")
    assert provider._access_token == "tok"
    # The OAuth beta header must be present on the client.
    headers = provider._client.default_headers
    assert headers.get("anthropic-beta") == oauth.OAUTH_BETA


def test_build_params_prepends_claude_code_identity(monkeypatch):
    from arbor.core.llm.claude_oauth import ClaudeOAuthProvider

    fake = oauth.AnthropicTokens(
        access_token="tok", refresh_token="rt", expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(oauth, "get_valid_tokens", lambda: fake)

    provider = ClaudeOAuthProvider(model="claude-sonnet-4-20250514")
    params = provider._build_params("real system prompt", [], None, 16384)
    system = params["system"]
    assert system[0]["text"] == oauth.CLAUDE_CODE_SYSTEM_PROMPT
    assert system[1]["text"] == "real system prompt"
