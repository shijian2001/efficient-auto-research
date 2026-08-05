"""ChatGPT (OpenAI) subscription OAuth — login, token storage, refresh.

EXPERIMENTAL / UNSUPPORTED. Replicates the Codex CLI OAuth (PKCE) flow so that
ChatGPT Plus/Pro/Team subscribers can drive Arbor with their subscription
instead of a pay-per-token ``OPENAI_API_KEY``. The obtained access token is
sent to the ChatGPT backend (``chatgpt.com/backend-api/codex``) by
:class:`arbor.core.llm.openai_oauth.OpenAIOAuthProvider`.

Tokens live in ``~/.arbor/oauth/openai.json`` (chmod 600). Using a ChatGPT
subscription token with third-party tooling may violate OpenAI's terms and can
get the account rate-limited or banned; this path is strictly opt-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from ..._app import GLOBAL_CONFIG_DIR

# ── Public constants (verified against openai/codex) ─────────────────────────
ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_PORT = 1455
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
SCOPES = "openid profile email offline_access"
# The ChatGPT subscription backend speaks the Responses API at /codex/responses.
# The OpenAI SDK appends "/responses" to this base_url.
CHATGPT_RESPONSES_BASE_URL = "https://chatgpt.com/backend-api/codex"

TOKEN_PATH = GLOBAL_CONFIG_DIR / "oauth" / "openai.json"

# Refresh proactively this many seconds before the access token expires.
_REFRESH_SKEW = 300
# Default access-token lifetime when the issuer omits ``expires_in``.
_DEFAULT_EXPIRES_IN = 3600


class OAuthError(RuntimeError):
    """Raised for any failure in the login / refresh flow."""


@dataclass
class OpenAITokens:
    access_token: str
    refresh_token: str
    id_token: str = ""
    account_id: str = ""
    plan_type: str = ""
    expires_at: float = 0.0

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - _REFRESH_SKEW)


# ── Low-level encoding helpers ───────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_jwt_claims(jwt: str) -> dict[str, Any]:
    """Decode a JWT payload without signature verification.

    We only read non-sensitive routing claims (account id, plan type); the
    token's authority is enforced by the server on every request, so local
    verification adds nothing here.
    """
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _account_info(id_token: str) -> tuple[str, str]:
    """Extract ``(account_id, plan_type)`` from an id_token's auth claim."""
    claims = _decode_jwt_claims(id_token)
    auth = claims.get("https://api.openai.com/auth", {})
    if not isinstance(auth, dict):
        return "", ""
    account_id = str(auth.get("chatgpt_account_id") or "")
    plan_type = str(auth.get("chatgpt_plan_type") or "")
    return account_id, plan_type


def _pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ── Token persistence ────────────────────────────────────────────────────────

def load_tokens() -> OpenAITokens | None:
    """Return stored tokens, or ``None`` if the user has not logged in."""
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("access_token") or not data.get("refresh_token"):
        return None
    known = {f for f in OpenAITokens.__dataclass_fields__}
    return OpenAITokens(**{k: v for k, v in data.items() if k in known})


def save_tokens(tokens: OpenAITokens) -> None:
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

def _post_token(form: dict[str, str]) -> dict[str, Any]:
    url = f"{ISSUER}/oauth/token"
    try:
        resp = httpx.post(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:  # pragma: no cover - network failure
        raise OAuthError(f"token endpoint request failed: {exc}") from exc
    if resp.status_code != 200:
        raise OAuthError(
            f"token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def _tokens_from_response(data: dict[str, Any], *, fallback_refresh: str = "") -> OpenAITokens:
    access_token = data.get("access_token") or ""
    if not access_token:
        raise OAuthError("token response missing access_token")
    refresh_token = data.get("refresh_token") or fallback_refresh
    id_token = data.get("id_token") or ""
    account_id, plan_type = _account_info(id_token) if id_token else ("", "")
    expires_in = int(data.get("expires_in") or _DEFAULT_EXPIRES_IN)
    return OpenAITokens(
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=id_token,
        account_id=account_id,
        plan_type=plan_type,
        expires_at=time.time() + expires_in,
    )


def refresh(tokens: OpenAITokens) -> OpenAITokens:
    """Exchange the refresh token for a fresh access token and persist it."""
    if not tokens.refresh_token:
        raise OAuthError("no refresh token available; run `arbor login openai` again")
    data = _post_token({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": tokens.refresh_token,
        "scope": SCOPES,
    })
    refreshed = _tokens_from_response(data, fallback_refresh=tokens.refresh_token)
    # The refresh response often omits id_token; keep the previously known
    # account/plan so requests still carry the chatgpt-account-id header.
    if not refreshed.account_id:
        refreshed.account_id = tokens.account_id
        refreshed.plan_type = tokens.plan_type or refreshed.plan_type
        if not refreshed.id_token:
            refreshed.id_token = tokens.id_token
    save_tokens(refreshed)
    return refreshed


def get_valid_tokens() -> OpenAITokens:
    """Load tokens, refreshing if they are expired. Raises if not logged in."""
    tokens = load_tokens()
    if tokens is None:
        raise OAuthError(
            "not logged in to ChatGPT — run `arbor login openai`"
        )
    if tokens.is_expired:
        tokens = refresh(tokens)
    return tokens


# ── Browser login flow ───────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    """One-shot handler that captures the ``?code=&state=`` redirect."""

    result: dict[str, str] = {}
    event: threading.Event | None = None

    def do_GET(self) -> None:  # noqa: N802 (stdlib signature)
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/auth/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        type(self).result = {
            "code": (params.get("code") or [""])[0],
            "state": (params.get("state") or [""])[0],
            "error": (params.get("error") or [""])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body style='font-family:sans-serif;text-align:center;"
            b"margin-top:4rem'><h2>Arbor is now signed in to ChatGPT.</h2>"
            b"<p>You can close this tab and return to the terminal.</p>"
            b"</body></html>"
        )
        if type(self).event is not None:
            type(self).event.set()

    def log_message(self, *_args: Any) -> None:  # silence stdlib logging
        return


def _authorize_url(challenge: str, state: str) -> str:
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
    })
    return f"{ISSUER}/oauth/authorize?{query}"


def login(*, open_browser: bool = True, timeout: float = 300.0) -> OpenAITokens:
    """Run the interactive browser OAuth flow and persist the tokens.

    Spins up a localhost callback server on :data:`REDIRECT_PORT`, opens the
    authorization URL, waits for the redirect, exchanges the code (PKCE), and
    saves the resulting tokens. Returns the stored :class:`OpenAITokens`.
    """
    verifier, challenge = _pkce()
    state = _b64url(secrets.token_bytes(16))

    event = threading.Event()
    _CallbackHandler.result = {}
    _CallbackHandler.event = event
    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    except OSError as exc:
        raise OAuthError(
            f"cannot bind localhost:{REDIRECT_PORT} for the OAuth callback "
            f"({exc}). Close whatever is using that port and retry."
        ) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = _authorize_url(challenge, state)
        opened = webbrowser.open(url) if open_browser else False
        print("\nOpen this URL to authorize Arbor with your ChatGPT account:")
        print(f"\n  {url}\n")
        if not opened:
            print("(Could not auto-open a browser; paste the URL above.)\n")
        if not event.wait(timeout):
            raise OAuthError("timed out waiting for the OAuth callback")
    finally:
        server.shutdown()
        server.server_close()
        _CallbackHandler.event = None

    result = _CallbackHandler.result
    if result.get("error"):
        raise OAuthError(f"authorization denied: {result['error']}")
    if result.get("state") != state:
        raise OAuthError("OAuth state mismatch — aborting (possible CSRF)")
    code = result.get("code") or ""
    if not code:
        raise OAuthError("no authorization code returned")

    data = _post_token({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    tokens = _tokens_from_response(data)
    save_tokens(tokens)
    return tokens
