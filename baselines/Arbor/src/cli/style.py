"""Shared terminal styling for the whole CLI.

Both the pre-launch intake REPL and the post-launch event stream render
through the singleton `console` here, so the visual transition between
phases is seamless.

Color and glyph choices mirror the semantic palette used by
``dashboard.py`` (HTML report) so the same status reads the same way in
both surfaces.
"""

from __future__ import annotations

from typing import Iterable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# Single Console for the entire process. Modules should import this
# rather than instantiating their own — that's what keeps intake and
# the live event stream visually continuous.
console = Console(highlight=False)


# ── Semantic palette ─────────────────────────────────────────────────

# Inline status glyphs. Single-char so they line up in the gutter.
GLYPH = {
    "session":   "▸",
    "cycle":     "◆",
    "phase":     "›",
    "proposed":  "◌",
    "selected":  "▶",
    "completed": "✓",
    "pruned":    "✗",
    "merged":    "↻",
    "executor":  "▸",
    "error":     "!",
    "converged": "★",
    "status":    "·",
    "arrow":     "›",
}

# Phase → display label and color.
PHASE_STYLE: dict[str, tuple[str, str]] = {
    "observe":  ("OBSERVE",  "cyan"),
    "ideate":   ("IDEATE",   "yellow"),
    "select":   ("SELECT",   "magenta"),
    "dispatch": ("DISPATCH", "blue"),
    "backprop": ("BACKPROP", "green"),
    "decide":   ("DECIDE",   "bold white"),
}


# ── Rendering primitives ─────────────────────────────────────────────


def render_panel(title: str, rows: Iterable[tuple[str, str]], *,
                 border_style: str = "cyan") -> None:
    """Render a labelled key/value panel — used for run kickoff banners.

    `rows` is an iterable of (label, value) pairs. Labels render dim,
    values bright. The panel itself has a subtle colored border.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column(style="white", overflow="fold")
    for label, value in rows:
        table.add_row(label, str(value) if value is not None else "—")

    panel = Panel(
        table,
        title=Text(title, style=f"bold {border_style}"),
        title_align="left",
        border_style=border_style,
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    console.print()


def render_status(message: str, *, style: str = "dim",
                  glyph: str = "·", indent: int = 2) -> None:
    """Print a status line — quiet, indented, glyph-prefixed.

    Used for orchestrator narration ("Starting coordinator...",
    "Pre-flight checks passed.", etc.) so it visually sits below
    section headers without competing with them.

    When the live dashboard is mounted, the message is appended to
    the rolling activity buffer instead of printed directly — that
    keeps the Live view from being interleaved with scrolling text.
    """
    try:
        from .run_dashboard import route_status
        if route_status(message, style=style, glyph=glyph):
            return
    except Exception:
        # Style helpers must not crash callers. Fall back to direct print.
        pass
    text = Text(" " * indent)
    text.append(f"{glyph} ", style=style)
    text.append(message, style=style)
    console.print(text)


def render_error_panel(title: str, body: str) -> None:
    """Bordered red panel for fatal errors only."""
    console.print()
    console.print(Panel(
        Text(body, style="red"),
        title=Text(title, style="bold red"),
        title_align="left",
        border_style="red",
        padding=(1, 2),
    ))
    console.print()


def render_logo(target: "Console | None" = None) -> None:
    """Print the ARBOR brand block — ASCII art + tagline, centered.

    Shown at the top of every run (interactive intake, resume, and headless
    ``--yes``) so the brand is always present, not just on the fresh-chat path.
    The art and tagline live here as the single source of truth so every
    surface stays in sync.

    ``target`` overrides the console (the intake REPL renders through its own);
    defaults to the shared module console.
    """
    from rich.align import Align

    from .._app import TAGLINE, TAGLINE_SUB

    con = target if target is not None else console
    # ASCII brand block: ANSI-shadow letters, cyan→magenta diagonal gradient.
    art = [
        " █████╗ ██████╗ ██████╗  ██████╗ ██████╗ ",
        "██╔══██╗██╔══██╗██╔══██╗██╔═══██╗██╔══██╗",
        "███████║██████╔╝██████╔╝██║   ██║██████╔╝",
        "██╔══██║██╔══██╗██╔══██╗██║   ██║██╔══██╗",
        "██║  ██║██║  ██║██████╔╝╚██████╔╝██║  ██║",
        "╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═╝",
    ]
    palette = ["#5fd7ff", "#5fafff", "#af87ff", "#d75fff", "#ff5fd7"]

    con.print()
    for row, line in enumerate(art):
        gradient = Text()
        for i, ch in enumerate(line):
            # shift hue along x and y for a subtle slant
            idx = ((i * len(palette) // max(len(line), 1)) + row) % len(palette)
            gradient.append(ch, style=palette[idx])
        con.print(Align.center(gradient))
    con.print(Align.center(Text(TAGLINE, style="bold white")))
    con.print(Align.center(Text(TAGLINE_SUB, style="dim white")))
    con.print()


# ── Score / number formatting ────────────────────────────────────────


def format_score(score: float | int | None) -> Text:
    """Render a numeric score with subtle color cues.

    Higher → greener, missing → dim 'n/a'. Caller decides where to
    place it; this returns a styled Text fragment.
    """
    if score is None or not isinstance(score, (int, float)):
        return Text("n/a", style="dim")
    if score >= 0.8:
        style = "bold green"
    elif score >= 0.5:
        style = "cyan"
    elif score >= 0.2:
        style = "yellow"
    else:
        style = "red"
    return Text(f"{score:.4f}", style=style)


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"
