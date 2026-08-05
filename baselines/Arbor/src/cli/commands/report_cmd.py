"""`arbor report` — regenerate REPORT.md from a finished session."""

from __future__ import annotations

from pathlib import Path

import typer

from ..._app import CONFIG_DIR_NAME


def report_command(
    session: Path = typer.Argument(
        ...,
        help=f"Path to session dir, or session name under <cwd>/{CONFIG_DIR_NAME}/sessions/",
    ),
    cwd: Path = typer.Option(Path("."), "--cwd", help="Resolve session names against this dir"),
) -> None:
    """Regenerate REPORT.md for a previous session."""
    from ...report import generate_report

    session_dir = _resolve_session_dir(session, cwd.resolve())
    if session_dir is None:
        typer.secho(f"error: session not found: {session}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    path = generate_report(session_dir)
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)


def _resolve_session_dir(session: Path, cwd: Path) -> Path | None:
    candidate = Path(session)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    if candidate.exists():
        return candidate.resolve()
    sessions_root = cwd / CONFIG_DIR_NAME / "sessions"
    by_name = sessions_root / str(session)
    if by_name.exists():
        return by_name.resolve()
    return None
