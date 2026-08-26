"""``arbor web`` — open a read-only browser monitor for a session.

Unlike the live dashboard (which only exists during ``arbor run``), this serves
a session *from disk*, so it works for the keyless, host-driven flow: while a
coding agent drives an Arbor run via the ``arbor mcp`` tools, the user (or the
agent, via the ``open_dashboard`` MCP tool) can watch the Idea Tree grow in the
browser. Read-only — the page only observes.
"""

from __future__ import annotations

import time
import webbrowser
from pathlib import Path

import typer

from ...export import ExportError, resolve_session_dir
from ...webui.launcher import start_session_webui


def web_command(
    session: str = typer.Argument(
        ..., help="Session name or path (under .arbor/sessions/), e.g. the run name."
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", help="Project directory holding .arbor/sessions/ (default: current directory)."
    ),
    port: int = typer.Option(8765, "--port", help="Preferred port (rolls forward if busy)."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the URL in a browser."),
) -> None:
    """Serve a read-only web monitor for an Arbor session and block until Ctrl-C."""
    base = Path(cwd) if cwd is not None else Path.cwd()
    try:
        session_dir = resolve_session_dir(Path(session), base)
    except ExportError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    server = start_session_webui(session_dir, run_name=session_dir.name, preferred=port)
    if server is None:
        typer.secho("error: could not bind a port for the WebUI.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"\nArbor monitor (read-only) → {server.url}", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"  watching {session_dir}", dim=True)
    typer.secho("  press Ctrl-C to stop.\n", dim=True)
    if open_browser:
        try:
            webbrowser.open(server.url)
        except Exception:  # pragma: no cover - headless / no browser available
            pass

    # The server runs on daemon threads; block here so the process stays alive.
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        typer.echo("\nstopping monitor…")
    finally:
        server.stop()
