"""Claude (Anthropic) subscription OAuth — login, token storage, refresh.

EXPERIMENTAL / UNSUPPORTED. Replicates the Claude Code OAuth (PKCE) flow so that
Claude Pro/Max subscribers can drive Arbor with their subscription instead of a
pay-per-token ``ANTHROPIC_API_KEY``. The obtained access token is sent to the
Anthropic Messages API as a ``Bearer`` token (with the ``anthropic-beta:
oauth-2025-04-20`` header) by
:class:`arbor.core.llm.claude_oauth.ClaudeOAuthProvider`.

Tokens live in ``~/.arbor/oauth/anthropic.json`` (chmod 600). Using a Claude
subscription token with third-party tooling may violate Anthropic's terms and
can get the account rate-limited or banned; this path is strictly opt-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from typing import Any, Callable

import httpx

from ..._app import GLOBAL_CONFIG_DIR

# ── Public constants (Claude Code public OAuth client) ───────────────────────
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"

# Beta header the subscription backend requires for OAuth (Bearer) requests.
OAUTH_BETA = "oauth-2025-04-20"
# The subscription backend only accepts requests that identify as Claude Code;
# this string must be the first system block on every Messages call.
CLAUDE_CODE_SYSTEM_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."

TOKEN_PATH = GLOBAL_CONFIG_DIR / "oauth" / "anthropic.json"

# Refresh proactively this many seconds before the access token expires.
_REFRESH_SKEW = 300
# Default access-token lifetime when the issuer omits ``expires_in``.
_DEFAULT_EXPIRES_IN = 3600


class OAuthError(RuntimeError):
    """Raised for any failure in the login / refresh flow."""


@dataclass
class AnthropicTokens:
    access_token: str
    refresh_token: str
    scope: str = ""
    account_email: str = ""
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - _REFRESH_SKEW)


# ── Low-level encoding helpers ───────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ── Token persistence ────────────────────────────────────────────────────────

def load_tokens() -> AnthropicTokens | None:
    """Return stored tokens, or ``None`` if the user has not logged in."""
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    known = {f for f in AnthropicTokens.__dataclass_fields__}
    return AnthropicTokens(**{k: v for k, v in data.items() if k in known})


def save_tokens(tokens: AnthropicTokens) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(asdict(tokens), indent=2), encoding="utf-8")
    try:
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass


def clear_tokens() -> bool:
    """Delete stored tokens. Returns True if a file was removed."""
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
        return True
    return False


# ── HTTP token exchange / refresh ────────────────────────────────────────────

def _post_token(body: dict[str, str]) -> dict[str, Any]:
    """POST a JSON body to the token endpoint (Anthropic expects JSON, not form)."""
    try:
        resp = httpx.post(
            TOKEN_URL,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network failure
        raise OAuthError(f"token endpoint request failed: {exc}") from exc
    if resp.status_code != 200:
        raise OAuthError(
            f"token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _tokens_from_response(
    data: dict[str, Any], *, fallback_refresh: str = "", fallback_email: str = ""
) -> AnthropicTokens:
    access_token = data.get("access_token") or ""
    if not access_token:
        raise OAuthError("token response missing access_token")
    refresh_token = data.get("refresh_token") or fallback_refresh
    account = data.get("account")
    email = ""
    if isinstance(account, dict):
        email = str(account.get("email_address") or account.get("email") or "")
    expires_in = int(data.get("expires_in") or _DEFAULT_EXPIRES_IN)
    return AnthropicTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        scope=str(data.get("scope") or ""),
        account_email=email or fallback_email,
        expires_at=time.time() + expires_in,
    )


def refresh(tokens: AnthropicTokens) -> AnthropicTokens:
    """Exchange the refresh token for a fresh access token and persist it."""
    if not tokens.refresh_token:
        raise OAuthError("no refresh token available; run `arbor login claude` again")
    data = _post_token({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": tokens.refresh_token,
    })
    refreshed = _tokens_from_response(
        data,
        fallback_refresh=tokens.refresh_token,
        fallback_email=tokens.account_email,
    )
    save_tokens(refreshed)
    return refreshed


def get_valid_tokens() -> AnthropicTokens:
    """Load tokens, refreshing if they are expired. Raises if not logged in."""
    tokens = load_tokens()
    if tokens is None:
        raise OAuthError("not logged in to Claude — run `arbor login claude`")
    if tokens.is_expired:
        tokens = refresh(tokens)
    return tokens


# ── Browser login flow (manual code paste) ───────────────────────────────────

def _authorize_url(challenge: str, state: str) -> str:
    query = urllib.parse.urlencode({
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def _split_code(raw: str, *, expected_state: str) -> tuple[str, str]:
    """Split the pasted ``code#state`` value into ``(code, state)``.

    Anthropic's callback page shows ``<code>#<state>``; some users paste only
    the code, in which case we assume the state we sent round-trips unchanged.
    """
    code, sep, state = raw.partition("#")
    return code.strip(), (state.strip() if sep else expected_state)


def login(
    *, open_browser: bool = True, code_input: Callable[[], str] | None = None
) -> AnthropicTokens:
    """Run the interactive browser OAuth flow and persist the tokens.

    Opens the Claude authorization URL, asks the user to paste the
    ``code#state`` value the callback page displays, exchanges it (PKCE), and
    saves the resulting tokens. Returns the stored :class:`AnthropicTokens`.
    """
    verifier, challenge = _pkce()
    state = _b64url(secrets.token_bytes(32))
    url = _authorize_url(challenge, state)

    opened = webbrowser.open(url) if open_browser else False
    print("\nOpen this URL to authorize Arbor with your Claude account:")
    print(f"\n  {url}\n")
    if not opened:
        print("(Could not auto-open a browser; paste the URL above.)\n")
    print("After approving, copy the code shown on the page and paste it here.")

    raw = (code_input() if code_input is not None else input("\nAuthorization code: ")).strip()
    if not raw:
        raise OAuthError("no authorization code provided")
    code, returned_state = _split_code(raw, expected_state=state)
    if returned_state != state:
        raise OAuthError("OAuth state mismatch — aborting (possible CSRF)")
    if not code:
        raise OAuthError("no authorization code provided")

    data = _post_token({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": returned_state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    tokens = _tokens_from_response(data)
    save_tokens(tokens)
    return tokens
