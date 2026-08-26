"""Pre-launch git base-branch guard.

Every research run creates its trunk/experiment branches off the project's base
branch (``main``/``master``) and, when it finishes, leaves you checked out on the
working trunk (``coordinator/trunk``). The *next* run in that repo would then hit
the engine's "refusing to create a trunk from a non-base branch" guard and die
after the whole dashboard has spun up.

This module catches that situation up front and, in an interactive terminal,
offers to switch back to the base branch (the common case) / proceed anyway /
abort — so the user gets a one-keypress recovery instead of a raw error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal


def _git(cwd: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(cwd), stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def current_branch(cwd: Path) -> str | None:
    return _git(cwd, "branch", "--show-current") or None


def _branch_exists(cwd: Path, name: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}") is not None


def resolve_base_branch(cwd: Path, configured: str | None) -> str | None:
    """The repo's base branch: the configured one, else the first of main/master
    that exists. None if we can't tell (not a git repo / detached / neither)."""
    if configured:
        return configured
    for name in ("main", "master"):
        if _branch_exists(cwd, name):
            return name
    return None


def on_base_branch(cwd: Path, configured_base: str | None) -> tuple[bool, str | None, str | None]:
    """Return (is_on_base, current, base). ``is_on_base`` is True when we can't
    determine current/base (detached HEAD, no base) — the engine's own guard
    stays the backstop for those odd states."""
    cur = current_branch(cwd)
    base = resolve_base_branch(cwd, configured_base)
    if cur is None or base is None:
        return True, cur, base
    return cur == base, cur, base


def resolve_start_branch(
    cwd: Path,
    config: Any,
    *,
    allow_non_base: bool,
    interactive: bool,
    console: Any,
) -> Literal["proceed", "abort"]:
    """Ensure the run starts from the base branch, or the user knowingly opts out.

    Side effects: may ``git checkout`` the base branch, or set
    ``config.require_base_branch = False`` when the user proceeds on a non-base
    branch. Returns "proceed" or "abort".
    """
    on_base, cur, base = on_base_branch(cwd, config.base_branch)
    if on_base:
        return "proceed"

    # Explicit opt-out (flag) — honor it without prompting.
    if allow_non_base:
        config.require_base_branch = False
        return "proceed"

    # Non-interactive (piped / --yes / CI): can't ask. Fail clean with the
    # followable fix instead of crashing mid-run.
    if not interactive:
        from .style import render_error_panel
        render_error_panel(
            "not on the base branch",
            f"Currently on '{cur}', but runs start from the base branch '{base}'.\n"
            f"A previous run likely left you on '{cur}'. Either:\n"
            f"  • git checkout {base}\n"
            f"  • or re-run with --allow-non-base-branch to use this branch as-is.",
        )
        return "abort"

    # Interactive: offer the one-keypress recovery.
    import typer

    console.print()
    console.print(
        f"[yellow]You're on branch [bold]{cur}[/], not the base branch "
        f"[bold]{base}[/].[/]")
    console.print(f"[dim]A previous run usually leaves you on '{cur}'.[/]\n")
    console.print(f"  [bold]m[/]  checkout '{base}' and start fresh [dim](recommended)[/]")
    console.print(f"  [bold]p[/]  proceed on '{cur}' as-is")
    console.print("  [bold]a[/]  abort\n")

    while True:
        choice = typer.prompt(f"Checkout {base}, proceed, or abort? [m/p/a]",
                              default="m").strip().lower()
        if choice in ("m", "main", base):
            if _git(cwd, "checkout", base) is None:
                from .style import render_error_panel
                render_error_panel(
                    "checkout failed",
                    f"Could not checkout '{base}' (uncommitted changes on '{cur}'?). "
                    f"Resolve it manually, then re-run.",
                )
                return "abort"
            console.print(f"[green]✓[/] now on '{base}'")
            return "proceed"
        if choice in ("p", "proceed"):
            config.require_base_branch = False
            console.print(f"[dim]proceeding on '{cur}'[/]")
            return "proceed"
        if choice in ("a", "abort", "q"):
            return "abort"
        console.print("[yellow]  enter m, p, or a[/]")
