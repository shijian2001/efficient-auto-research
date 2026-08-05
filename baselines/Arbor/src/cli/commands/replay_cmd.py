"""`arbor replay` — replay a recorded session through the live dashboard.

Zero-setup way to *see Arbor work*: the live agent needs an LLM key, but
replaying a recorded run needs nothing. ``arbor replay --demo`` plays a bundled
sample; ``arbor replay <session>`` plays any real run's ``events.jsonl``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from ..._app import CONFIG_DIR_NAME


def replay_command(
    source: Optional[Path] = typer.Argument(
        None,
        help=(
            f"Session dir, session name under <cwd>/{CONFIG_DIR_NAME}/sessions/, "
            "or a path to events.jsonl. Omit with --demo."
        ),
    ),
    demo: bool = typer.Option(
        False, "--demo", help="Replay the bundled sample recording (no API key needed)."
    ),
    html: bool = typer.Option(
        False, "--html",
        help="Write a shareable, self-contained interactive tree-replay page instead "
             "of playing in the terminal.",
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o", help="Output path for --html (default: <session>/arbor-tree.html).",
    ),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the generated HTML in your browser.",
    ),
    speed: float = typer.Option(
        None, "--speed", "-s", help="Timeline compression (higher = faster). Default 12×.",
    ),
    max_gap: float = typer.Option(
        None, "--max-gap", help="Cap any single idle gap, in seconds. Default 2.0.",
    ),
    cwd: Path = typer.Option(Path("."), "--cwd", help="Resolve session names against this dir"),
) -> None:
    """Replay a previous Arbor session — watch the idea tree grow with zero setup."""

    from ..replay import (
        DEFAULT_MAX_GAP_S,
        DEFAULT_SPEED,
        Recording,
        demo_recording,
        load_recording,
        print_recording_banner,
        replay_recording,
    )

    if demo and source is not None:
        typer.secho(
            "error: pass either a session or --demo, not both", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    if not demo and source is None:
        typer.secho(
            "error: give a session path/name, or use --demo for the bundled sample",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    rec: Recording
    try:
        if demo:
            rec = demo_recording()
        else:
            assert source is not None
            rec = load_recording(_resolve_source(source, cwd))
    except FileNotFoundError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    print_recording_banner(rec, is_demo=demo)
    if html:
        _write_html(rec, out, open_browser=open_browser)
        return

    exit_reason = replay_recording(
        rec,
        speed=DEFAULT_SPEED if speed is None else speed,
        max_gap_s=DEFAULT_MAX_GAP_S if max_gap is None else max_gap,
    )
    if exit_reason == "interrupted":
        raise typer.Exit(code=130)


def _write_html(rec, out: Optional[Path], *, open_browser: bool) -> None:
    """Render the shareable tree-replay page and (optionally) open it."""
    from ..tree_export import default_html_path, write_tree_html

    target = out if out is not None else default_html_path(rec)
    try:
        path = write_tree_html(rec, target)
    except OSError as exc:
        typer.secho(f"error: failed to write HTML: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"Wrote interactive tree replay to: {path}", fg=typer.colors.GREEN)
    if open_browser:
        import webbrowser
        if not webbrowser.open(path.resolve().as_uri()):
            typer.secho("(could not open a browser — open the file above manually)",
                        fg=typer.colors.YELLOW)


def _resolve_source(source: Path, cwd: Path) -> Path:
    """Let a bare session *name* resolve under ``<cwd>/<CONFIG_DIR_NAME>/sessions``.

    A path that already exists (file or dir) is used as-is; otherwise we try the
    conventional sessions directory so ``arbor replay my-run`` works the same way
    ``arbor export my-run`` does.
    """
    source = Path(source).expanduser()
    if source.exists():
        return source
    candidate = Path(cwd).expanduser() / CONFIG_DIR_NAME / "sessions" / str(source)
    if candidate.exists():
        return candidate
    return source  # let the loader raise a precise FileNotFoundError
