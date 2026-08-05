"""Live event renderer for the coordinator run.

Subscribes to the EventBus and prints a polished, color-coded stream
through the shared `cli.style.console` so it matches the intake REPL
visually. One line per event, glyph-prefixed, with a dim wall-clock
timestamp in the gutter.

For long runs we deliberately stay scroll-based (no Live panel): events
fire over hours, scrollback is the user's history, and a busy live
layout would fight terminal multiplexers and CI logs.
"""

from __future__ import annotations

from datetime import datetime

from rich.text import Text

from ...cli.style import (
    GLYPH,
    PHASE_STYLE,
    console,
    format_duration,
    format_score,
    render_panel,
    render_status,
)
from ..bus import Event, EventBus
from .. import types as ev


# ── public api ─────────────────────────────────────────────────────


def attach(bus: EventBus) -> None:
    """Wire up handlers for the most useful events."""
    bus.on(ev.SESSION_START,       _on_session_start)
    bus.on(ev.SESSION_END,         _on_session_end)
    bus.on(ev.CYCLE_START,         _on_cycle_start)
    bus.on(ev.CYCLE_END,           _on_cycle_end)
    bus.on(ev.PHASE_CHANGE,        _on_phase_change)
    bus.on(ev.IDEA_PROPOSED,       _on_idea_proposed)
    bus.on(ev.IDEA_COMPLETED,      _on_idea_completed)
    bus.on(ev.IDEA_PRUNED,         _on_idea_pruned)
    bus.on(ev.IDEA_MERGED,         _on_idea_merged)
    bus.on(ev.EXECUTOR_START,      _on_executor_start)
    bus.on(ev.EXECUTOR_END,        _on_executor_end)
    bus.on(ev.LLM_ERROR,           _on_llm_error)
    bus.on(ev.CONVERGENCE_REACHED, _on_convergence_reached)


# ── primitives ─────────────────────────────────────────────────────


def _ts_text() -> Text:
    """Dim wall-clock timestamp printed in the gutter of every line."""
    return Text(datetime.now().strftime("%H:%M:%S"), style="bright_black")


def _line(glyph: str, glyph_style: str, body: Text, *, indent: int = 0) -> None:
    """Render a single event line: `HH:MM:SS  ▸  body...`.

    The two-space gutter after the timestamp keeps glyphs vertically
    aligned regardless of indent depth (used for nesting cycle/phase/
    idea levels without losing the timeline column). We force a single
    visible line with ellipsis overflow so wraparound never breaks the
    gutter on narrow terminals.
    """
    line = _ts_text()
    line.append("  ")
    if indent:
        line.append(" " * indent)
    line.append(f"{glyph} ", style=glyph_style)
    line.append_text(body)
    console.print(line, overflow="ellipsis", no_wrap=True)


def _truncate(s: str, limit: int = 80) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ── handlers ───────────────────────────────────────────────────────


def _on_session_start(e: Event) -> None:
    """Quiet line; the kickoff Panel is already printed by `cli/commands/run.py`
    before handing off to the orchestrator. Repeating it here would double up."""
    d = e.data
    body = Text()
    body.append("session started", style="bold cyan")
    model = d.get("model")
    if model:
        body.append("  ")
        body.append(f"model={model}", style="dim")
    _line(GLYPH["session"], "bold cyan", body)


def _on_session_end(e: Event) -> None:
    d = e.data
    reason = d.get("exit_reason", "ok")
    reason_style = "bold green" if reason == "ok" else "bold yellow"

    rows: list[tuple[str, str]] = [
        ("exit",     reason),
        ("duration", format_duration(d.get("duration"))),
        ("turns",    str(d.get("turns", "—"))),
        ("tokens",   f"{d.get('input_tokens', 0):,} in / {d.get('output_tokens', 0):,} out"),
    ]
    render_panel(
        "session complete",
        rows,
        border_style="green" if reason == "ok" else "yellow",
    )
    # also drop a single colored marker line into the timeline so the
    # scrollback shows a clean end-of-stream punctuation
    _line(GLYPH["session"], reason_style, Text(f"session ended ({reason})", style=reason_style))


def _on_cycle_start(e: Event) -> None:
    d = e.data
    n = d.get("cycle_num")
    total = d.get("total_cycles", "?")
    body = Text()
    body.append(f"cycle {n}", style="bold cyan")
    body.append(f" / {total}", style="dim")
    console.print()  # blank line separates cycles visually
    _line(GLYPH["cycle"], "bold cyan", body)


def _on_cycle_end(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"cycle {d.get('cycle_num')} done", style="cyan")
    body.append("  ")
    body.append(format_duration(d.get("duration")), style="dim")
    _line(GLYPH["cycle"], "cyan", body)


def _on_phase_change(e: Event) -> None:
    phase = e.data.get("phase", "?")
    label, color = PHASE_STYLE.get(phase, (phase.upper(), "white"))
    body = Text(label, style=f"bold {color}")
    _line(GLYPH["phase"], color, body, indent=2)


def _on_idea_proposed(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"idea {d.get('node_id')} ", style="yellow")
    body.append("proposed", style="dim")
    body.append("  ")
    body.append(_truncate(d.get("hypothesis") or ""), style="white")
    _line(GLYPH["proposed"], "yellow", body, indent=2)


def _on_idea_completed(e: Event) -> None:
    d = e.data
    status = (d.get("status") or "done").lower()
    glyph_style = {
        "merged": "bold green",
        "done":   "bold cyan",
        "needs_retry": "bold yellow",
        "failed": "bold red",
        "pruned": "bright_black",
    }.get(status, "cyan")
    body = Text()
    body.append(f"idea {d.get('node_id')} ", style="white")
    body.append(status, style=glyph_style)
    body.append("  score=", style="dim")
    body.append_text(format_score(d.get("score")))
    _line(GLYPH["completed"], glyph_style, body, indent=2)


def _on_idea_pruned(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"idea {d.get('node_id')} ", style="bright_black")
    body.append("pruned", style="bright_black")
    reason = (d.get("reason") or "").strip()
    if reason:
        body.append("  ")
        body.append(_truncate(reason, 60), style="dim")
    _line(GLYPH["pruned"], "bright_black", body, indent=2)


def _on_idea_merged(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"idea {d.get('node_id')} merged ", style="bold green")
    f, t = d.get("from_score"), d.get("to_score")
    body.append_text(format_score(f))
    body.append(" → ", style="dim")
    body.append_text(format_score(t))
    branch = d.get("branch")
    if branch:
        body.append("  on ", style="dim")
        body.append(str(branch), style="cyan")
    _line(GLYPH["merged"], "bold green", body, indent=2)


def _on_executor_start(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"executor {d.get('node_id')} ", style="magenta")
    body.append("start", style="dim")
    branch = d.get("branch")
    if branch:
        body.append("  branch=", style="dim")
        body.append(str(branch), style="cyan")
    _line(GLYPH["executor"], "magenta", body, indent=4)


def _on_executor_end(e: Event) -> None:
    d = e.data
    body = Text()
    body.append(f"executor {d.get('node_id')} ", style="magenta")
    body.append("done", style="dim")
    body.append("  score=", style="dim")
    body.append_text(format_score(d.get("score")))
    body.append("  ")
    body.append(format_duration(d.get("duration")), style="dim")
    _line(GLYPH["executor"], "magenta", body, indent=4)


def _on_llm_error(e: Event) -> None:
    d = e.data
    body = Text()
    body.append("LLM error", style="bold red")
    provider = d.get("provider")
    if provider:
        body.append(f" ({provider})", style="dim")
    body.append("  ")
    body.append(_truncate(str(d.get("error") or ""), 100), style="red")
    _line(GLYPH["error"], "bold red", body)


def _on_convergence_reached(e: Event) -> None:
    d = e.data
    body = Text()
    body.append("converged", style="bold green")
    reason = d.get("reason")
    if reason:
        body.append("  ")
        body.append(str(reason), style="dim")
    final = d.get("final_score")
    if final is not None:
        body.append("  final=", style="dim")
        body.append_text(format_score(final))
    _line(GLYPH["converged"], "bold green", body)


# Re-export so external callers (e.g. orchestrator narration) can route
# through the same surface without importing the style module directly.
status = render_status
