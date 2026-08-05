"""`arbor export` — export a run session to HTML or JSONL."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from ..._app import CONFIG_DIR_NAME


def export_command(
    session: Path = typer.Argument(
        ...,
        help=f"Path to session dir, or session name under <cwd>/{CONFIG_DIR_NAME}/sessions/",
    ),
    output: Optional[Path] = typer.Argument(
        None,
        help="Output path. Defaults to <session>/arbor-session-<name>.html. Use .jsonl for JSONL.",
    ),
    cwd: Path = typer.Option(Path("."), "--cwd", help="Resolve session names against this dir"),
    fmt: Optional[str] = typer.Option(
        None,
        "--format",
        "-f",
        help="Export format: html, jsonl, or trajectory. Inferred from output extension when omitted.",
    ),
) -> None:
    """Export a previous Arbor session for review or sharing."""

    from ...export import ExportError, export_session, resolve_session_dir

    try:
        session_dir = resolve_session_dir(session, cwd)
        if (fmt or "").lower() == "trajectory":
            from ...trajectory import write_trajectory
            path = write_trajectory(session_dir)
            typer.secho(f"Exported trajectory to: {path}", fg=typer.colors.GREEN)
            return
        result = export_session(session_dir, output, fmt=fmt)
    except ExportError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except OSError as exc:
        typer.secho(f"error: failed to write export: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    typer.secho(f"Exported {result.format.upper()} to: {result.path}", fg=typer.colors.GREEN)
