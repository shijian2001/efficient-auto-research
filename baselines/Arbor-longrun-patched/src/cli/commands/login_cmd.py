"""`arbor login` — subscription OAuth login (experimental).

Supports ChatGPT (OpenAI) and Claude (Anthropic) subscriptions: ``arbor login
openai`` / ``arbor login claude`` run a browser OAuth flow so subscribers can
drive Arbor with their subscription instead of a pay-per-token API key.

Heads-up: using a subscription token with third-party tooling may violate the
provider's terms and can get the account rate-limited or banned. This is
strictly opt-in.
"""

from __future__ import annotations

import typer

from ..._app import GLOBAL_CONFIG_FILE
from .config_cmd import write_user_llm_config

login_app = typer.Typer(
    name="login",
    help="Sign in with a model subscription (experimental).",
    no_args_is_help=True,
)


@login_app.command("openai")
def login_openai(
    no_browser: bool = typer.Option(
        False, "--no-browser",
        help="Print the auth URL instead of opening a browser.",
    ),
    set_default: bool = typer.Option(
        True, "--set-default/--no-set-default",
        help="Write provider=openai-oauth to the global config after login.",
    ),
) -> None:
    """Log in to ChatGPT and store the subscription token under ~/.arbor/oauth/."""
    from ...core.oauth import openai as oauth
    from ..style import console as _console

    _console.print(
        "[yellow]Experimental:[/] using a ChatGPT subscription token with "
        "third-party tools may violate OpenAI's terms and risks your account."
    )
    try:
        tokens = oauth.login(open_browser=not no_browser)
    except oauth.OAuthError as exc:
        _console.print(f"[red]login failed:[/] {exc}")
        raise typer.Exit(code=1)

    plan = tokens.plan_type or "unknown"
    who = tokens.account_id or "(account id unavailable)"
    _console.print(f"[green]✓[/] signed in to ChatGPT — plan=[bold]{plan}[/], account={who}")

    if set_default:
        from .._constants import DEFAULT_OPENAI_OAUTH_MODEL

        model = DEFAULT_OPENAI_OAUTH_MODEL
        write_user_llm_config({"provider": "openai-oauth", "model": model})
        _console.print(
            f"[green]✓[/] set provider=openai-oauth (model={model}) in {GLOBAL_CONFIG_FILE}"
        )
    _console.print("Run [bold]arbor[/] to start a session.")


@login_app.command("claude")
def login_claude(
    no_browser: bool = typer.Option(
        False, "--no-browser",
        help="Print the auth URL instead of opening a browser.",
    ),
    set_default: bool = typer.Option(
        True, "--set-default/--no-set-default",
        help="Write provider=anthropic-oauth to the global config after login.",
    ),
) -> None:
    """Log in to Claude and store the subscription token under ~/.arbor/oauth/."""
    from ...core.oauth import anthropic as oauth
    from ..style import console as _console

    _console.print(
        "[yellow]Experimental:[/] using a Claude subscription token with "
        "third-party tools may violate Anthropic's terms and risks your account."
    )
    try:
        tokens = oauth.login(open_browser=not no_browser)
    except oauth.OAuthError as exc:
        _console.print(f"[red]login failed:[/] {exc}")
        raise typer.Exit(code=1)

    who = tokens.account_email or "(account unavailable)"
    _console.print(f"[green]✓[/] signed in to Claude — account=[bold]{who}[/]")

    if set_default:
        from .._constants import DEFAULT_CLAUDE_OAUTH_MODEL

        model = DEFAULT_CLAUDE_OAUTH_MODEL
        write_user_llm_config({"provider": "anthropic-oauth", "model": model})
        _console.print(
            f"[green]✓[/] set provider=anthropic-oauth (model={model}) in {GLOBAL_CONFIG_FILE}"
        )
    _console.print("Run [bold]arbor[/] to start a session.")


@login_app.command("status")
def login_status() -> None:
    """Show the current subscription login state for each provider."""
    from ...core.oauth import anthropic as claude_oauth
    from ...core.oauth import openai as oauth
    from ..style import console as _console

    any_signed_in = False

    tokens = oauth.load_tokens()
    if tokens is not None:
        any_signed_in = True
        plan = tokens.plan_type or "unknown"
        state = "expired (will refresh)" if tokens.is_expired else "valid"
        _console.print(
            f"[green]signed in[/] to ChatGPT — plan=[bold]{plan}[/], "
            f"account={tokens.account_id or 'unknown'}, access token {state}"
        )

    ctokens = claude_oauth.load_tokens()
    if ctokens is not None:
        any_signed_in = True
        state = "expired (will refresh)" if ctokens.is_expired else "valid"
        _console.print(
            f"[green]signed in[/] to Claude — "
            f"account={ctokens.account_email or 'unknown'}, access token {state}"
        )

    if not any_signed_in:
        _console.print(
            "[yellow]not signed in[/] — run `arbor login openai` or `arbor login claude`"
        )
        raise typer.Exit(code=1)


@login_app.command("logout")
def login_logout(
    provider: str = typer.Argument(
        "all",
        help="Which token to remove: openai | claude | all (default).",
    ),
) -> None:
    """Delete the stored subscription token(s)."""
    from ...core.oauth import anthropic as claude_oauth
    from ...core.oauth import openai as oauth
    from ..style import console as _console

    target = provider.strip().lower()
    if target not in ("all", "openai", "chatgpt", "claude", "anthropic"):
        _console.print("[red]unknown provider[/] — use openai, claude, or all")
        raise typer.Exit(code=1)

    removed = False
    if target in ("all", "openai", "chatgpt"):
        if oauth.clear_tokens():
            _console.print("[green]✓[/] removed stored ChatGPT token")
            removed = True
    if target in ("all", "claude", "anthropic"):
        if claude_oauth.clear_tokens():
            _console.print("[green]✓[/] removed stored Claude token")
            removed = True
    if not removed:
        _console.print("[dim]nothing to remove (not signed in)[/]")
