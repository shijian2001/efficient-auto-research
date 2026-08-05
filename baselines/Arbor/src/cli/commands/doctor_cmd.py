"""`arbor doctor` — diagnose install + runtime environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from ..._app import APP_NAME, GLOBAL_CONFIG_FILE, LEGACY_GLOBAL_CONFIG_FILE


def _ok(msg: str) -> None:
    typer.secho(f"  ✓ {msg}", fg=typer.colors.GREEN)


def _warn(msg: str, hint: str | None = None) -> None:
    typer.secho(f"  ! {msg}", fg=typer.colors.YELLOW)
    if hint:
        typer.secho(f"      → {hint}", fg=typer.colors.YELLOW, dim=True)


def _fail(msg: str, hint: str | None = None) -> None:
    typer.secho(f"  ✗ {msg}", fg=typer.colors.RED)
    if hint:
        typer.secho(f"      → {hint}", fg=typer.colors.RED, dim=True)


def doctor_command() -> None:
    """Diagnose the install — checks PATH, venv leakage, git, API keys."""
    typer.secho(f"\n{APP_NAME} doctor\n", fg=typer.colors.CYAN, bold=True)

    problems = 0

    # ── PATH ─────────────────────────────────────────────────────
    typer.secho("install", bold=True)
    # Two paths to report on:
    #   - `running` is THIS process's actual executable (where the
    #     command that the user invoked lives)
    #   - `path_first` is what their shell would pick when they type
    #     `arbor` from a fresh prompt. If these disagree, there's a
    #     shadowing arbor higher on PATH and the user is likely confused.
    running = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    path_first = shutil.which(APP_NAME)
    if not running and not path_first:
        _fail(f"`{APP_NAME}` not on PATH",
              "pipx install -e /path/to/arbor")
        problems += 1
    else:
        if running:
            _ok(f"running from {running}")
        if path_first and (not running or Path(path_first).resolve() != running):
            _warn(
                f"shell finds a different `{APP_NAME}` first: {path_first}",
                "you have two installs — uninstall the one you don't want, "
                "or reorder your PATH",
            )
            problems += 1
        active = str(running or path_first)
        # Detect "trapped in a venv" — the most common failure mode the
        # user hits when they did `pip install -e .` inside a project
        # venv and now only see the command after activating it.
        if "/.venv/" in active or "/venv/" in active:
            _warn(
                "looks like a project-local venv install",
                "this only works after `source .venv/bin/activate`. "
                "Run `pipx install -e <repo>` for a global install instead.",
            )
            problems += 1
        elif "/pipx/" in active or "/.local/pipx/" in active:
            _ok("pipx install — works from any directory")
        elif "/.local/" in active:
            _ok("user install — works from any directory")

    # python that's running us vs python on PATH
    py = sys.executable
    typer.echo(f"  · python: {py} ({'.'.join(map(str, sys.version_info[:3]))})")

    # ── package import ───────────────────────────────────────────
    try:
        import arbor  # noqa: F401
        _ok(f"arbor imports from {Path(arbor.__file__).parent}")
    except Exception as e:
        _fail(f"cannot import arbor: {e!r}")
        problems += 1

    # ── git ───────────────────────────────────────────────────────
    typer.echo()
    typer.secho("runtime", bold=True)
    if shutil.which("git"):
        try:
            v = subprocess.check_output(["git", "--version"], text=True).strip()
            _ok(v)
        except Exception:
            _warn("git found but failed to run")
    else:
        _fail("git not installed", "brew install git  /  apt install git")
        problems += 1

    # ── API key surface ──────────────────────────────────────────
    has_anth = bool(os.environ.get("ANTHROPIC_API_KEY") or
                    os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    has_oai = bool(os.environ.get("OPENAI_API_KEY"))
    has_cfg = GLOBAL_CONFIG_FILE.exists()
    has_legacy = LEGACY_GLOBAL_CONFIG_FILE.exists()
    if has_anth:
        _ok("ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN is set")
    if has_oai:
        _ok("OPENAI_API_KEY is set")
    if has_cfg:
        _ok(f"user config at {GLOBAL_CONFIG_FILE}")
    elif has_legacy:
        _warn(
            f"using legacy config at {LEGACY_GLOBAL_CONFIG_FILE}",
            f"works, but consider `cp {LEGACY_GLOBAL_CONFIG_FILE} {GLOBAL_CONFIG_FILE}` "
            f"so it lives under the new name",
        )
    if not (has_anth or has_oai or has_cfg or has_legacy):
        _warn(
            "no API key found (env or config)",
            f"set ANTHROPIC_API_KEY / OPENAI_API_KEY, or run `{APP_NAME} config init`",
        )

    # ── summary ──────────────────────────────────────────────────
    typer.echo()
    if problems == 0:
        typer.secho("all checks passed.", fg=typer.colors.GREEN, bold=True)
        raise typer.Exit(code=0)
    typer.secho(f"{problems} issue(s) — fix the items above.",
                fg=typer.colors.YELLOW, bold=True)
    raise typer.Exit(code=1)
