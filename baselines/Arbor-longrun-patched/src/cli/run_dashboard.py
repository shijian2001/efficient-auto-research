"""Live dashboard for the coordinator run.

A single ``rich.live.Live`` view that owns the terminal while the
experiment is in flight. It has three regions, all rendered from
``RunState``:

  ┌─────────────────────────────────────────────┐
  │  arbor · <run_name>                         │
  │  cycle 3/5   ideas 12  ✓4 ✗5 ◌3   elapsed   │
  │  best 0.7421         phase DISPATCH         │
  └─────────────────────────────────────────────┘
  recent activity
    15:42  ▸ executor n5 running …
    15:38  ↻ idea n3 merged 0.68 → 0.74
    …
  type a message to the agent  (Ctrl-C to abort) >

The activity region is a fixed-height rolling window — newest events
push old ones out the top, so the dashboard never scrolls the
terminal.

User input runs in a daemon thread that reads ``sys.stdin`` line by
line. Each line is queued on the state and surfaced inline; the agent
loop's inter-turn hook pulls messages off and injects them as fresh
user turns at the next opportunity.
"""

from __future__ import annotations

import datetime
import os
import shlex
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..core import agent as agent_module
from ..events import types as ev
from ..events.bus import EventBus
from . import run_state as rs_module
from .run_state import RunState, _short
from .chart import render_progress_chart
from .style import PHASE_STYLE, console, format_duration

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application, run_in_terminal
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        BufferControl, Float, FloatContainer, FormattedTextControl, HSplit, Window,
    )
    from prompt_toolkit.layout import Layout as PTLayout
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style
    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    _PROMPT_TOOLKIT_AVAILABLE = False
    PromptSession = Any  # type: ignore[misc,assignment]
    Application = Any  # type: ignore[misc,assignment]
    run_in_terminal = None  # type: ignore[assignment]
    Buffer = Any  # type: ignore[misc,assignment]
    Completer = object  # type: ignore[assignment,misc]
    Completion = Any  # type: ignore[misc,assignment]
    Condition = Any  # type: ignore[misc,assignment]
    ANSI = Any  # type: ignore[misc,assignment]
    InMemoryHistory = Any  # type: ignore[misc,assignment]
    KeyBindings = Any  # type: ignore[misc,assignment]
    Style = Any  # type: ignore[misc,assignment]



# POSIX-only raw stdin reading. On Windows we just skip interactive
# input — the dashboard still renders, the user just can't type.
try:
    import select
    import termios
    import tty
    _RAW_TTY_AVAILABLE = True
except ImportError:
    _RAW_TTY_AVAILABLE = False


# How often the Live view repaints. The screen only changes when state
# mutates, so 8 fps is plenty smooth and cheap.
REFRESH_HZ = 8

# Rows the prompt_toolkit input region occupies below the dashboard (the prompt
# line + the hint toolbar). The Rich dashboard is rendered to the remaining
# height so the two never overlap.
INPUT_REGION_HEIGHT = 2


# ── Layout height budget ─────────────────────────────────────────────────────
# Section heights (panel height incl. borders, i.e. the value passed to
# Layout(size=...)). Optional panels are only added while they fit, so the
# total layout height never exceeds the terminal — otherwise rich.Live (inline,
# screen=False) scrolls on every repaint and leaves duplicate frames in the
# scrollback. ``ideas`` is flex (the remainder) and always present.
_HEADER_H = 6
_FOOTER_H = 3
_IDEAS_MIN_H = 5            # comfortable floor below ambient panels
_GATE_H = 7
_CHART_MAX_H, _CHART_MIN_H = 12, 7
_REASON_MAX_H, _REASON_MIN_H = 8, 6
# Transient slash-command output panel (just above the input).
_COMMAND_MAX_H, _COMMAND_MIN_H = 14, 3
# How long a command's output stays pinned above the input before auto-hiding.
_COMMAND_TTL_S = 30.0
# The conversation panel must show even in short terminals — when it can't fit,
# the user's question and the "thinking…" signal both vanish and the run looks
# unresponsive. So keep the floor small (compact 2-3 turns) and let it grow.
_REPLY_MAX_H, _REPLY_MIN_H = 24, 6


def _plan_section_sizes(
    avail: int,
    *,
    has_gate: bool,
    has_reply: bool,
    has_chart: bool,
    has_reasoning: bool,
    reply_pref: int = _REPLY_MIN_H,
    header_h: int = _HEADER_H,
    reply_expanded: bool = False,
    footer_h: int = _FOOTER_H,
    command_pref: int = 0,
) -> dict[str, int]:
    """Choose which optional panels to show and how tall, so that
    ``header_h + footer_h + sum(result.values()) <= avail`` always holds
    (``ideas`` takes the non-negative remainder).

    Priority order is gate > reply > chart > reasoning. gate/reply are modal
    and only need a single row of ``ideas`` to coexist; chart/reasoning are
    ambient and keep a comfortable ``_IDEAS_MIN_H`` floor so they never starve
    the main idea-tree surface. A panel is absent from the result when it is
    not requested or does not fit.

    ``footer_h`` is the height reserved for the input footer: ``_FOOTER_H`` on
    the legacy Live path, ``0`` under the pt Application (where the input lives
    in real pt windows outside the captured layout).

    When ``reply_expanded`` is set, the conversation panel is the focus: ambient
    panels are dropped and the reply claims everything down to a one-row ideas
    sliver (ignoring the normal ``_REPLY_MAX_H`` cap), so a long answer is
    readable inline.
    """
    avail = max(header_h + footer_h + 1, avail)
    used = header_h + footer_h
    out: dict[str, int] = {}

    if reply_expanded and has_reply:
        has_chart = has_reasoning = False

    if has_gate and used + _GATE_H + 1 <= avail:        # +1: keep ideas alive
        out["gate"] = _GATE_H
        used += _GATE_H
    if command_pref and not reply_expanded:
        # Transient command output sits just above the input; give it priority
        # so a /cost or /status result is never starved, but keep ideas alive.
        size = min(_COMMAND_MAX_H, max(_COMMAND_MIN_H, command_pref))
        if used + size + 1 <= avail:
            out["command"] = size
            used += size
    if has_reply:
        if reply_expanded:
            size = max(_REPLY_MIN_H, avail - used - 1)   # claim screen, 1-row ideas sliver
        else:
            size = min(avail - used - 1, max(_REPLY_MIN_H, min(_REPLY_MAX_H, reply_pref)))
        if size >= _REPLY_MIN_H:
            out["reply"] = size
            used += size
    if has_chart:
        room = avail - used - _IDEAS_MIN_H
        if room >= _CHART_MIN_H:
            out["chart"] = min(_CHART_MAX_H, room)
            used += out["chart"]
    if has_reasoning:
        room = avail - used - _IDEAS_MIN_H
        if room >= _REASON_MIN_H:
            out["reasoning"] = min(_REASON_MAX_H, room)
            used += out["reasoning"]
    return out


# Per-status glyph + glyph color + status-text style for the ideas
# table. Aligned with dashboard.py / cli.style so terminal and HTML
# report stay consistent.
_STATUS_DECOR: dict[str, tuple[str, str, str]] = {
    # status        glyph  glyph_style    status_style
    "proposed":   ("◌",   "yellow",       "yellow"),
    "running":    ("▸",   "magenta",      "magenta"),
    "done":       ("✓",   "bold green",   "green"),
    "merged":     ("↻",   "bold green",   "bold green"),
    "needs_retry":("⟳",   "bold yellow",  "yellow"),
    "pruned":     ("✗",   "bright_black", "bright_black"),
    "failed":     ("!",   "bold red",     "red"),
}


_DASHBOARD_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show dashboard commands"),
    ("/ask", "ask the read-only companion: /ask <question>"),
    ("/steer", "inject a message into the research agent: /steer <message>"),
    ("/mode", "set default input target: /mode ask|research"),
    ("/status", "print run status"),
    ("/skill", "ask the agent to load skill(s): /skill <name...>"),
    ("/tree", "print current idea tree snapshot"),
    ("/evidence", "show score/baseline evidence"),
    ("/reply", "expand/collapse the full companion answer (or press Tab)"),
    ("/chart", "toggle the live progress chart"),
    ("/branches", "show explored branch refs"),
    ("/cost", "print token usage"),
    ("/pause", "ask the agent to pause after the current step"),
    ("/resume", "resume after /pause"),
    ("/report", "show session/report artifact paths"),
    ("/export", "export session to HTML, or .jsonl when path ends with .jsonl"),
    ("/abort", "abort the run"),
    ("/quit", "abort the run"),
]


# The short list surfaced in the live completion menu + footer hint. Every
# command in _DASHBOARD_COMMANDS still works when typed in full; this is just
# the curated set that pops up so the menu stays uncluttered. /help lists them all.
_MENU_COMMANDS: list[tuple[str, str]] = [
    ("/ask", "ask the read-only companion: /ask <question>"),
    ("/steer", "inject a message into the research agent: /steer <message>"),
    ("/help", "show all dashboard commands"),
    ("/status", "print run status"),
]


# ── public api ─────────────────────────────────────────────────────


class RunDashboard:
    """Context manager that owns the terminal during a run.

    Wraps three concerns:
      - Rich Live painting (header / activity / footer)
      - DISPLAY_HOOK redirect (tool calls + tool errors only)
      - EventBus subscription (cycle / idea / executor / etc.)
      - stdin reader thread (user typing → state queue)

    All four feed into ``self.state``. Repaint happens in a small
    timer thread driven by the ``state._dirty`` event.
    """

    def __init__(self, state: RunState, bus: EventBus, *, enable_input: bool = True,
                 companion: Any | None = None, input_mode: str = "app") -> None:
        self.state = state
        self.bus = bus
        self._enable_input = enable_input
        self._input_mode = input_mode if input_mode in {"app", "prompt", "raw", "line"} else "app"
        if self._input_mode == "prompt":
            self._input_mode = "app"          # legacy alias for the pt Application path
        if self._input_mode == "app" and not _PROMPT_TOOLKIT_AVAILABLE:
            self._input_mode = "line"
        # Read-only Q&A companion (#11). When present, plain (non-slash) input
        # is routed to it instead of being injected into the research agent.
        self._companion = companion
        self._live: Live | None = None
        # prompt_toolkit Application (the default "app" path). It owns the screen
        # so CJK input composes inline and there is no flicker. Runs in its own
        # daemon thread; None on the legacy Rich-Live fallback.
        self._app: Application | None = None
        self._app_thread: threading.Thread | None = None
        self._app_buffer: Buffer | None = None
        self._app_failed = False
        # Capture console sized to the dashboard region (terminal minus the input
        # rows). The _render* sizing reads use this, not the global console.
        self._region_console: Console | None = None
        self._previous_hook: Any = None
        self._stdin_stop = threading.Event()
        self._stdin_thread: threading.Thread | None = None
        self._paint_stop = threading.Event()
        self._paint_thread: threading.Thread | None = None
        # Live-typed buffer rendered in the footer (legacy raw/line path only;
        # the pt Application owns its own Buffer). Owned by the stdin thread.
        self._input_buffer: str = ""
        # Caret position within ``_input_buffer`` (0 = before first char,
        # len = after last). Arrow keys / Home / End / Ctrl-A/E move it.
        self._cursor_pos: int = 0
        self._saved_termios: Any = None
        self._input_active = False
        # Default is Claude-Code-style side chat: plain text asks a read-only
        # companion. Use /steer once or /mode research to affect the main run.
        self._input_target = "ask"
        # Sequence number of the last companion answer mirrored to the
        # scrollback above the Live region. The fixed-height reply panel crops
        # long answers, so we echo each completed answer upward once for a
        # durable, scrollable record. Owned by the paint loop.
        self._mirrored_companion_seq = 0

    def _rc(self) -> Console:
        """The console whose size drives layout decisions: the dashboard-region
        capture console under the pt Application, else the global console."""
        return self._region_console or console

    # ── lifecycle ──────────────────────────────────────────────

    def __enter__(self) -> "RunDashboard":
        rs_module.set_current(self.state)
        self._wire_bus()
        self._previous_hook = agent_module.DISPLAY_HOOK
        agent_module.DISPLAY_HOOK = self._on_agent_event

        started = False
        if self._app_path_viable():
            started = self._start_app()
        if not started:
            self._start_live_fallback()
        self._start_paint_loop()
        self.state.mark_dirty()
        return self

    def _app_path_viable(self) -> bool:
        """The pt Application owns a real terminal. It needs both stdin and
        stdout to be a TTY; piped/CI/Windows fall back to the Rich-Live path."""
        return (
            self._input_mode == "app"
            and _PROMPT_TOOLKIT_AVAILABLE
            and sys.stdin is not None and sys.stdin.isatty()
            and sys.stdout is not None and sys.stdout.isatty()
        )

    def _start_app(self) -> bool:
        """Build + launch the prompt_toolkit Application in a daemon thread.
        Returns True once it is running; False (with a clean terminal) if it
        failed to come up, so the caller can fall back to the Rich-Live path."""
        try:
            self._app = self._build_dashboard_app()
        except Exception as exc:
            _dump_crash("dashboard.build_app", exc)
            self._app = None
            return False
        self._app_thread = threading.Thread(target=self._run_app, daemon=True,
                                            name="arbor-dashboard-app")
        self._app_thread.start()
        # Wait briefly for the app loop to take the terminal (or die trying).
        for _ in range(100):
            if self._app_failed:
                self._app = None
                return False
            if getattr(self._app, "is_running", False):
                self._input_active = self._enable_input
                return True
            time.sleep(0.01)
        # Never came up — treat as failure but leave the (daemon) thread.
        if not getattr(self._app, "is_running", False):
            self._app = None
            return False
        self._input_active = self._enable_input
        return True

    def _run_app(self) -> None:
        try:
            # handle_sigint=False: the main thread runs the orchestrator's
            # asyncio loop and owns SIGINT; our Ctrl-C key binding re-raises it.
            self._app.run(handle_sigint=False)
        except Exception as exc:
            _dump_crash("dashboard.app", exc)
            self._app_failed = True

    def _start_live_fallback(self) -> None:
        """Legacy path: a Rich Live surface + threaded raw/line stdin reader.
        Used when the pt Application can't own the terminal (no TTY / Windows /
        app failed to start) or when input_mode is explicitly raw/line."""
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=REFRESH_HZ,
            transient=False,        # leave a clean final frame on exit
            screen=False,           # don't take alt-screen; preserves scrollback
            vertical_overflow="crop",  # never scroll-and-duplicate if a panel overshoots
        )
        self._live.start()
        self._start_stdin_reader()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._stdin_stop.set()
        self._paint_stop.set()
        # Restore the TTY *before* anything else so the user sees a
        # working shell prompt even if the rest of teardown explodes.
        # (No-op on the pt path — prompt_toolkit restores it on app.exit.)
        self._restore_termios()
        if self._paint_thread is not None:
            self._paint_thread.join(timeout=0.5)
        # Stop the pt Application: ask its loop to exit, then join the thread.
        # prompt_toolkit restores the terminal (raw mode, cursor) on exit, and
        # erase_when_done=False leaves the final frame in the scrollback.
        app = self._app
        if app is not None:
            try:
                if getattr(app, "is_running", False) and app.loop is not None:
                    app.loop.call_soon_threadsafe(app.exit)
            except Exception:
                pass
            if self._app_thread is not None:
                self._app_thread.join(timeout=1.5)
            self._app = None
        if self._live is not None:
            try:
                self._live.update(self._render())
            except Exception:
                pass
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

        agent_module.DISPLAY_HOOK = self._previous_hook
        rs_module.set_current(None)

        # If we're tearing down because of an uncaught exception, dump
        # it where the user can find it AND echo a single line to stderr
        # so they don't think the program died silently. We dump
        # KeyboardInterrupt too — "I pressed Ctrl-C by accident" is one
        # of the more common "why did it exit?" puzzles, and the input
        # trace appended below pinpoints it.
        if exc_val is not None:
            where = ("dashboard.run (KeyboardInterrupt)"
                     if isinstance(exc_val, KeyboardInterrupt)
                     else "dashboard.run")
            path = _dump_crash(where, exc_val)
            try:
                if isinstance(exc_val, KeyboardInterrupt):
                    msg = "\narbor: interrupted (Ctrl-C)"
                else:
                    msg = f"\narbor: crashed — {type(exc_val).__name__}: {exc_val}"
                if path:
                    msg += f"\n       see {path} (includes recent keystroke trace)"
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:
                pass

    # ── paint loop ─────────────────────────────────────────────

    def _start_paint_loop(self) -> None:
        def run() -> None:
            while not self._paint_stop.is_set():
                # Wait up to 0.4s for a dirty bit. Even with no events we still
                # repaint periodically so elapsed counters + the thinking spinner
                # tick forward, and a terminal resize is picked up within a tick.
                self.state._dirty.wait(timeout=0.4)
                self.state._dirty.clear()
                # Echo any freshly-completed companion answers to the scrollback
                # before repainting, so long replies survive the panel's crop.
                try:
                    self._mirror_new_companion_turns()
                except Exception:
                    pass
                app = self._app
                if app is not None:
                    try:
                        app.invalidate()
                    except Exception:
                        pass
                elif self._live is not None:
                    try:
                        self._live.update(self._render())
                    except Exception:
                        pass

        self._paint_thread = threading.Thread(target=run, daemon=True,
                                              name="arbor-dashboard-paint")
        self._paint_thread.start()

    # ── prompt_toolkit Application (default path) ──────────────

    def _build_dashboard_app(self, *, app_input=None, app_output=None) -> "Application":
        """The single-renderer dashboard: a Rich-rendered content window pinned
        above a real prompt_toolkit input line. prompt_toolkit owns the cursor,
        so CJK input methods compose inline at the caret and nothing flickers.

        ``app_input``/``app_output`` are injectable for tests (DummyInput/
        DummyOutput); in production they default to the real terminal."""
        buf = Buffer(
            completer=_DashboardSlashCompleter(self),
            complete_while_typing=True,
            multiline=False,
            read_only=Condition(lambda: not self._enable_input),
        )
        self._app_buffer = buf

        dashboard_window = Window(
            content=FormattedTextControl(self._dashboard_ansi, focusable=False),
            wrap_lines=False,
            always_hide_cursor=True,
        )
        prompt_window = Window(
            BufferControl(buffer=buf, focusable=True),
            height=1,
            get_line_prefix=lambda lineno, wrap_count: self._pt_line_prefix(),
        )
        toolbar = Window(
            FormattedTextControl(lambda: ANSI(self._markup_to_ansi(self._input_hint(buf.text)))),
            height=1,
        )
        root = FloatContainer(
            content=HSplit([dashboard_window, prompt_window, toolbar]),
            floats=[Float(content=CompletionsMenu(max_height=8, scroll_offset=1),
                          xcursor=True, ycursor=True)],
        )
        style = Style.from_dict({
            "completion-menu.completion": "bg:#1f2937 #d1d5db",
            "completion-menu.completion.current": "bg:#0891b2 #ffffff",
            "completion-menu.meta.completion": "bg:#111827 #9ca3af",
            "completion-menu.meta.completion.current": "bg:#0e7490 #ffffff",
        })
        return Application(
            layout=PTLayout(root, focused_element=prompt_window),
            key_bindings=self._build_key_bindings(buf),
            style=style,
            full_screen=False,
            mouse_support=False,
            erase_when_done=False,        # keep the final frame in the scrollback
            input=app_input,
            output=app_output,
        )

    def _markup_to_ansi(self, markup: str) -> str:
        """Render a Rich-markup string to an ANSI string for a pt control.

        The footer hint helpers (`_input_hint`) emit Rich markup like
        ``[dim]…[/]``; pt's ``ANSI`` wants real escape codes, so we round-trip
        through a small capture console."""
        try:
            cap_console = Console(force_terminal=True, color_system="truecolor",
                                  width=max(20, self._rc().size.width), highlight=False)
            with cap_console.capture() as cap:
                cap_console.print(Text.from_markup(markup), end="", soft_wrap=True)
            return cap.get()
        except Exception:
            return markup

    def _pt_line_prefix(self) -> "ANSI":
        """The prompt label shown before the pt input buffer (ask/research/gate),
        or a read-only notice when input is disabled."""
        if not self._enable_input:
            return ANSI("\033[2mlive input off — Ctrl-C to abort\033[0m ")
        s = self.state
        if s.pending_gate is not None:
            label, color = "gate", "\033[1;33m"
        elif self._input_target == "research":
            label, color = "research", "\033[1;33m"
        else:
            label, color = "ask", "\033[1;35m"
        return ANSI(f"{color}{label}\033[0m \033[2m›\033[0m ")

    def _build_key_bindings(self, buf: "Buffer") -> "KeyBindings":
        """Key map for the pt Application. Default emacs editing (insert,
        Backspace, Ctrl-A/E/U/W, arrows, Home/End — including CJK) comes free
        from the BufferControl; we add submit / expand / scroll / interrupt."""
        kb = KeyBindings()
        expanded = Condition(lambda: self.state.reply_expanded)

        @kb.add("enter")
        def _(event) -> None:
            text = buf.text
            buf.reset()
            try:
                self._submit_line(text)
            except Exception as exc:
                _dump_crash("stdin.pt_submit", exc)
                self.state.last_error_text = f"input handler: {exc!r}"
                self.state.last_error_at = time.monotonic()
                self.state.mark_dirty()

        @kb.add("tab", filter=Condition(lambda: buf.text.startswith("/")))
        def _(event) -> None:
            event.current_buffer.complete_next()

        @kb.add("tab")
        def _(event) -> None:
            self._toggle_reply_expanded()

        @kb.add("escape", filter=expanded, eager=True)
        def _(event) -> None:
            self._set_reply_expanded(False)

        @kb.add("pageup", filter=expanded)
        def _(event) -> None:
            self._scroll_reply(-max(1, self._rc().size.height - 8))

        @kb.add("pagedown", filter=expanded)
        def _(event) -> None:
            self._scroll_reply(max(1, self._rc().size.height - 8))

        @kb.add("up", filter=expanded)
        def _(event) -> None:
            self._scroll_reply(-1)

        @kb.add("down", filter=expanded)
        def _(event) -> None:
            self._scroll_reply(1)

        @kb.add("c-c")
        def _(event) -> None:
            import signal
            os.kill(os.getpid(), signal.SIGINT)

        @kb.add("c-d")
        def _(event) -> None:
            # No-op: stray Ctrl-D (CJK IME / paste paths) must not abort. Use ^C.
            pass

        return kb

    def _print_above(self, render) -> None:
        """Run ``render`` (which prints to the global console) above the pinned
        pt region via run_in_terminal, scheduled onto the app's loop so it is
        safe from any thread. Falls back to a direct call on the legacy path."""
        app = self._app
        if app is None or not getattr(app, "is_running", False) or run_in_terminal is None:
            try:
                render()
            except Exception:
                pass
            return

        def _schedule() -> None:
            try:
                import asyncio
                asyncio.ensure_future(run_in_terminal(render))
            except Exception:
                try:
                    render()
                except Exception:
                    pass

        try:
            app.loop.call_soon_threadsafe(_schedule)
        except Exception:
            try:
                render()
            except Exception:
                pass

    # ── stdin reader ───────────────────────────────────────────

    def _start_stdin_reader(self) -> None:
        # Opt-out: caller asked for a read-only dashboard. This disables prompt,
        # line, and raw input while keeping the live status surface running.
        if not self._enable_input:
            return
        # Only read if we actually have a tty AND a POSIX termios.
        # Piped / CI / Windows fall back to "no input" mode.
        if not _RAW_TTY_AVAILABLE:
            return
        if not (sys.stdin and sys.stdin.isatty()):
            return

        if self._input_mode == "prompt":
            self._start_prompt_reader()
            return

        if self._input_mode == "line":
            self._start_line_stdin_reader()
            return

        # Put stdin into cbreak mode so we get one keystroke at a
        # time and don't echo. Without this, characters the user
        # types stay invisibly buffered until they hit Enter — which
        # is the bug we're fixing.
        fd = sys.stdin.fileno()
        try:
            self._saved_termios = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:
            self._saved_termios = None
            return

        def run() -> None:
            while not self._stdin_stop.is_set():
                # Poll so we wake up on shutdown rather than blocking
                # forever inside read().
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                except Exception:
                    return
                if not ready:
                    continue
                ch = _read_codepoint(fd)
                if ch is None:
                    return
                if not ch:
                    continue        # invalid byte, recover on next read
                try:
                    self._handle_key(ch)
                except Exception as exc:
                    # A bug in key handling must NOT take down the run.
                    # Drop a crash note where the user can find it, clear
                    # the input buffer so the bad state can't repeat, and
                    # keep reading.
                    _dump_crash("stdin._handle_key", exc)
                    self._input_buffer = ""
                    self._cursor_pos = 0
                    self.state.last_error_text = f"input handler: {exc!r}"
                    self.state.last_error_at = time.monotonic()
                    self.state.mark_dirty()

        self._stdin_thread = threading.Thread(target=run, daemon=True,
                                              name="arbor-dashboard-stdin")
        self._stdin_thread.start()
        self._input_active = True
        self.state.mark_dirty()

    def _start_prompt_reader(self) -> None:
        """Prompt-toolkit input loop.

        This is the default interactive path. It gives the terminal a real input
        prompt and cursor, so IME candidate windows anchor correctly, while the
        Rich Live dashboard remains a status surface above it.
        """
        def run() -> None:
            session = _build_dashboard_prompt_session(self)
            while not self._stdin_stop.is_set():
                try:
                    with _patch_live_prompt_stdout():
                        line = session.prompt(self._prompt_message())
                except EOFError:
                    return
                except KeyboardInterrupt:
                    import signal
                    os.kill(os.getpid(), signal.SIGINT)
                    return
                if self._stdin_stop.is_set():
                    return
                try:
                    self._submit_line(line)
                except Exception as exc:
                    _dump_crash("stdin.prompt_submit", exc)
                    self.state.last_error_text = f"input handler: {exc!r}"
                    self.state.last_error_at = time.monotonic()
                    self.state.mark_dirty()

        self._stdin_thread = threading.Thread(target=run, daemon=True,
                                              name="arbor-dashboard-prompt")
        self._stdin_thread.start()
        self._input_active = True
        self.state.mark_dirty()

    def _prompt_message(self) -> ANSI:
        if self.state.pending_gate is not None:
            label = "gate"
            color = "\033[1;33m"
        elif self._input_target == "research":
            label = "research"
            color = "\033[1;33m"
        else:
            label = "ask"
            color = "\033[1;35m"
        return ANSI(f"{color}{label}\033[0m \033[2m›\033[0m ")

    def _start_line_stdin_reader(self) -> None:
        """Canonical-mode fallback for terminals where cbreak is unsafe.

        The footer cannot show a live caret in this mode, but commands and
        questions still work after Enter, and CJK IME composition stays in the
        terminal's normal line discipline.
        """
        def run() -> None:
            while not self._stdin_stop.is_set():
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                except Exception:
                    return
                if not ready:
                    continue
                try:
                    line = sys.stdin.readline()
                except Exception:
                    return
                if line == "":
                    return
                try:
                    self._submit_line(line.rstrip("\r\n"))
                except Exception as exc:
                    _dump_crash("stdin._submit_line", exc)
                    self.state.last_error_text = f"input handler: {exc!r}"
                    self.state.last_error_at = time.monotonic()
                    self.state.mark_dirty()

        self._stdin_thread = threading.Thread(target=run, daemon=True,
                                              name="arbor-dashboard-stdin-line")
        self._stdin_thread.start()
        self._input_active = True
        self.state.mark_dirty()

    def _submit_line(self, line: str) -> None:
        line = line.strip()
        self._input_buffer = ""
        self._cursor_pos = 0
        gate = self.state.pending_gate
        if gate is not None:
            self._handle_gate_input(line, gate)
            return
        if line:
            self.state.on_status_narration(
                f"received input: {_short(line, 80)}",
                style="cyan",
                glyph="›",
            )
            if line.startswith("/"):
                self._handle_slash_command(line)
            else:
                self._route_plain_input(line)
        else:
            self.state.mark_dirty()

    def _route_plain_input(self, text: str) -> None:
        if self._input_target == "research":
            self._steer_research(text, source="input")
            return
        self._ask_companion(text)

    def _ask_companion(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        if self._companion is not None:
            self._companion.ask(question)
            self.state.on_status_narration(
                "sent to read-only companion",
                style="magenta",
                glyph="›",
            )
            return
        self._print_command_output(
            "ask",
            ["[yellow]read-only companion is unavailable; use /steer to affect the research agent[/]"],
        )

    def _steer_research(self, payload: str, *, source: str = "steer") -> None:
        payload = payload.strip()
        if not payload:
            self._print_command_output(
                "steer", ["[yellow]usage: /steer <message to the research agent>[/]"])
            return
        self.state.push_user_message(payload)
        label = (
            "research mode input queued"
            if source == "input"
            else "steered the research agent"
        )
        self.state.on_status_narration(
            f"{label} — takes effect at the next agent turn",
            style="yellow",
            glyph="!",
        )
        self._print_command_output(
            "research",
            [f"[yellow]queued for research agent[/]  [dim]{escape(_short(payload, 100))}[/]"],
        )

    # ── expanded reply reader ──────────────────────────────────

    def _has_reply_answer(self) -> bool:
        """True if there's a completed companion answer that can be expanded."""
        s = self.state
        gate_active = s.pending_gate is not None and (s.gate_discussion_busy or s.gate_discussion_turns)
        turns = s.gate_discussion_turns if gate_active else s.companion_turns
        try:
            return any(role == "companion" for role, _ in turns)
        except RuntimeError:
            return False

    def _set_reply_expanded(self, value: bool) -> None:
        if value and not self._has_reply_answer():
            return                       # nothing to expand yet
        self.state.reply_expanded = value
        self.state.reply_scroll = 0      # always (re)start at the top
        self.state.mark_dirty()

    def _toggle_reply_expanded(self) -> None:
        self._set_reply_expanded(not self.state.reply_expanded)

    def _scroll_reply(self, delta: int) -> None:
        """Move the expanded reader window by ``delta`` lines (clamped in the
        renderer against the wrapped line count)."""
        if not self.state.reply_expanded:
            return
        self.state.reply_scroll = max(0, self.state.reply_scroll + delta)
        self.state.mark_dirty()

    def _handle_key(self, ch: str) -> None:
        """Process one keystroke.

        - Enter   → commit the buffer as a user message (if non-empty)
        - ^C / ^D → re-raise so the orchestrator's KeyboardInterrupt
                    path runs (we restore termios in __exit__)
        - ^U      → wipe the buffer
        - ^W      → delete the last whitespace-separated word
        - ^A / Home / Esc[H → caret to start
        - ^E / End  / Esc[F → caret to end
        - ←  / →             → move caret one char
        - Backspace          → delete char before caret
        - Delete (Esc[3~)    → delete char under caret
        - printable          → insert at caret
        - everything else (fn-keys, other escape seqs) → ignore
        """
        buf = self._input_buffer
        pos = self._cursor_pos

        # Cheap rolling trace of every key. Costs nothing; the deque
        # holds 64 entries max. Dumped only on crash / interrupt.
        if ch in ("\r", "\n"):
            _trace_input("enter", buf)
        elif ch.isprintable():
            _trace_input(f"insert {ch!r}", buf)
        elif ch == "\x1b":
            _trace_input("esc-start", buf)
        else:
            _trace_input(f"ctrl {ord(ch):#04x}", buf)

        if ch in ("\r", "\n"):
            self._submit_line(buf)
            return

        if ch == "\t":              # Tab: toggle the expanded reply reader
            self._toggle_reply_expanded()
            return

        if ch == "\x03":            # Ctrl-C
            # Restore TTY first so the traceback prints cleanly, then
            # raise into the main thread via os.kill on our own PID.
            # We log the keystroke so a user who exited "by accident"
            # can see what triggered it.
            _trace_input("ctrl-c", buf)
            self._restore_termios()
            import signal
            os.kill(os.getpid(), signal.SIGINT)
            return
        if ch == "\x04":            # Ctrl-D
            # Was previously treated as abort. That was a footgun —
            # CJK IMEs and some paste paths can deliver stray \x04 and
            # nuke the session. Now it's a no-op (we just clear the
            # buffer to give a visible signal). Use Ctrl-C to abort.
            _trace_input("ctrl-d (ignored)", buf)
            self._input_buffer = ""
            self._cursor_pos = 0
            self.state.last_error_text = "Ctrl-D ignored — use Ctrl-C to abort"
            self.state.last_error_at = time.monotonic()
            self.state.mark_dirty()
            return
        if ch == "\x15":            # Ctrl-U: clear line
            self._input_buffer = ""
            self._cursor_pos = 0
            self.state.mark_dirty()
            return
        if ch == "\x17":            # Ctrl-W: delete word left
            left = buf[:pos].rstrip()
            sp = left.rfind(" ")
            new_left = left[: sp + 1] if sp >= 0 else ""
            self._input_buffer = new_left + buf[pos:]
            self._cursor_pos = len(new_left)
            self.state.mark_dirty()
            return
        if ch == "\x01":            # Ctrl-A: home
            self._cursor_pos = 0
            self.state.mark_dirty()
            return
        if ch == "\x05":            # Ctrl-E: end
            self._cursor_pos = len(buf)
            self.state.mark_dirty()
            return
        if ch == "\x02":            # Ctrl-B: left
            self._cursor_pos = max(0, pos - 1)
            self.state.mark_dirty()
            return
        if ch == "\x06":            # Ctrl-F: right
            self._cursor_pos = min(len(buf), pos + 1)
            self.state.mark_dirty()
            return
        if ch in ("\x7f", "\x08"):  # Backspace / DEL
            if pos > 0:
                self._input_buffer = buf[: pos - 1] + buf[pos:]
                self._cursor_pos = pos - 1
                self.state.mark_dirty()
            return
        if ch == "\x1b":            # Escape — read the rest of the sequence
            seq = self._drain_escape_sequence()
            self._apply_escape_sequence(seq)
            return
        if ch.isprintable():
            self._input_buffer = buf[:pos] + ch + buf[pos:]
            self._cursor_pos = pos + len(ch)
            self.state.mark_dirty()

    def _handle_slash_command(self, line: str) -> None:
        cmd = line.lower().split()[0]
        if cmd in ("/steer", "/research", "/agent", "/main"):
            # The explicit "influence the agent" path: inject into the research
            # agent's context at its next turn. Distinct from plain questions,
            # which go to the read-only companion.
            payload = line[len(cmd):].strip()
            self._steer_research(payload)
            return
        if cmd in ("/ask", "/chat", "/question"):
            question = line[len(cmd):].strip()
            if not question:
                self._print_command_output(
                    "ask", ["[yellow]usage: /ask <question for read-only companion>[/]"])
                return
            self._ask_companion(question)
            return
        if cmd == "/mode":
            mode = line[len("/mode"):].strip().lower()
            aliases = {
                "ask": "ask",
                "chat": "ask",
                "companion": "ask",
                "readonly": "ask",
                "read-only": "ask",
                "research": "research",
                "agent": "research",
                "main": "research",
                "steer": "research",
            }
            if mode not in aliases:
                self._print_command_output(
                    "mode",
                    [
                        f"[dim]current[/] [bold]{self._input_target}[/]",
                        "[cyan]/mode ask[/] [dim]plain text asks the read-only companion[/]",
                        "[cyan]/mode research[/] [dim]plain text affects the research agent[/]",
                    ],
                )
                return
            self._input_target = aliases[mode]
            if self._input_target == "ask":
                msg = "plain input now asks the read-only companion"
                style = "magenta"
            else:
                msg = "plain input now affects the research agent"
                style = "yellow"
            self.state.on_status_narration(msg, style=style, glyph="›")
            self._print_command_output("mode", [f"[green]{escape(msg)}[/]"])
            return
        if cmd == "/help":
            rows = [f"[cyan]{name:<8}[/] [dim]{desc}[/]" for name, desc in _DASHBOARD_COMMANDS]
            rows.extend([
                "",
                "[dim]plain text follows the current mode shown in the input title[/]",
                "[dim]aliases: /research /agent /main = /steer, /chat /question = /ask[/]",
            ])
            self._print_command_output("dashboard commands", rows)
            return
        if cmd == "/skill":
            payload = line[len("/skill"):].strip()
            if not payload:
                self._print_command_output(
                    "skill", ["[yellow]usage: /skill <skill_name> [skill_name...][/]"],
                )
                return
            names = [part for part in payload.split() if part]
            quoted = ", ".join(f"`{name}`" for name in names)
            self.state.push_control_message(
                "SYSTEM CONTROL: The user requested /skill. At the next useful "
                f"opportunity, call LoadSkill for {quoted} and apply the loaded "
                "playbook(s). If LoadSkill is unavailable, tell the user briefly."
            )
            self.state.on_status_narration(
                "skill request queued — takes effect at the next agent turn",
                style="cyan",
                glyph="›",
            )
            self._print_command_output(
                "skill",
                [f"[cyan]queued[/] [dim]{escape(', '.join(names))}[/]"],
            )
            return
        if cmd == "/status":
            s = self.state
            used = s.branch_budget_used
            total = s.total_cycles or "?"
            rows = [
                f"[dim]run[/] {escape(s.run_name or '—')}",
                f"[dim]branches[/] {used}/{total} used, {s.ideas_running} running, {s.ideas_merged} merged",
                f"[dim]best[/] {s.best_score:.4f}" if s.best_score is not None else "[dim]best[/] —",
                f"[dim]elapsed[/] {format_duration(s.elapsed_seconds)}",
                f"[dim]cwd[/] {escape(s.cwd or '—')}",
            ]
            if s.paused:
                rows.append("[yellow]pause requested[/] — waiting for the agent's next turn")
            self._print_command_output("status", rows)
            return
        if cmd == "/cost":
            s = self.state
            total = s.tokens_in + s.tokens_out
            rows = [
                f"[dim]total tokens[/] {_fmt_tokens(total)}",
                f"[dim]input[/] {_fmt_tokens(s.tokens_in)}",
                f"[dim]output[/] {_fmt_tokens(s.tokens_out)}",
                f"[dim]rate[/] {int(total / s.elapsed_seconds) if s.elapsed_seconds > 1 else 0}/s",
            ]
            self._print_command_output("cost", rows)
            return
        if cmd == "/tree":
            self._print_command_output("idea tree", self._tree_snapshot_lines())
            return
        if cmd == "/evidence":
            self._print_command_output("evidence", self._evidence_lines())
            return
        if cmd in ("/reply", "/expand", "/full"):
            if not self._has_reply_answer():
                self._print_command_output(
                    "reply", ["[yellow]no companion answer to expand yet — ask a question first[/]"])
                return
            self._toggle_reply_expanded()
            status = "expanded — PgUp/PgDn ↑/↓ to scroll, Esc/Tab to collapse" \
                if self.state.reply_expanded else "collapsed"
            self._print_command_output("reply", [f"[dim]full answer[/] {status}"])
            return
        if cmd == "/chart":
            self.state.show_chart = not self.state.show_chart
            status = "shown" if self.state.show_chart else "hidden"
            self._print_command_output(
                "chart", [f"[dim]progress chart[/] {status}"]
            )
            return
        if cmd == "/branches":
            self._print_command_output("branches", self._branch_lines())
            return
        if cmd == "/report":
            s = self.state
            session = s.session_dir or "—"
            rows = [
                f"[dim]session[/] {escape(session)}",
                f"[dim]report[/] {escape(str(Path(session) / 'REPORT.md')) if session != '—' else 'available after run'}",
                f"[dim]events[/] {escape(str(Path(session) / 'events.jsonl')) if session != '—' else '—'}",
                f"[dim]conversation[/] {escape(s.conversation_path or '—')}",
            ]
            self._print_command_output("artifacts", rows)
            return
        if cmd == "/export":
            payload = line[len("/export"):].strip()
            if not self.state.session_dir:
                self._print_command_output("export", ["[yellow]no session directory is available yet[/]"])
                return
            try:
                parts = shlex.split(payload) if payload else []
            except ValueError as exc:
                self._print_command_output(
                    "export",
                    ["[yellow]usage: /export [path.html|path.jsonl][/]", f"[red]{escape(str(exc))}[/]"],
                )
                return
            if len(parts) > 1:
                self._print_command_output("export", ["[yellow]usage: /export [path.html|path.jsonl][/]"])
                return
            try:
                from ..export import export_session
                output = Path(parts[0]).expanduser() if parts else None
                result = export_session(Path(self.state.session_dir), output)
            except Exception as exc:
                self._print_command_output("export", [f"[red]failed:[/] {escape(str(exc))}"])
                return
            self._print_command_output(
                "export",
                [
                    f"[green]{escape(result.format.upper())} written[/]",
                    f"[dim]{escape(str(result.path))}[/]",
                ],
            )
            return
        if cmd == "/pause":
            self.state.paused = True
            self.state.push_control_message(
                "SYSTEM CONTROL: The user requested /pause. Finish the current tool call, "
                "then stop launching new executors or merges until the user sends /resume. "
                "If the user asked a question, answer it before continuing."
            )
            self.state.on_status_narration(
                "pause requested — takes effect at the next agent turn",
                style="yellow",
                glyph="!",
            )
            self._print_command_output(
                "pause",
                ["[yellow]pause requested[/] — current tool calls cannot be interrupted"],
            )
            return
        if cmd == "/resume":
            self.state.paused = False
            self.state.push_control_message(
                "SYSTEM CONTROL: The user resumed the run with /resume. Continue the research plan."
            )
            self.state.on_status_narration("resumed", style="green", glyph="✓")
            self._print_command_output("resume", ["[green]run resumed[/]"])
            return
        if cmd in ("/abort", "/quit"):
            self._restore_termios()
            import signal
            os.kill(os.getpid(), signal.SIGINT)
            return
        self._print_command_output(
            "unknown command",
            [f"[yellow]{escape(cmd)}[/] [dim](try /help)[/]"],
        )

    def _handle_gate_input(self, line: str, gate: dict[str, Any]) -> None:
        """Route input while the coordinator is paused on AWAIT_USER.

        Plain text opens/continues an isolated discussion branch. The gate
        companion submits the final answer once the user's intent is clear.
        """
        line = line.strip()
        if not line:
            self._print_gate_help()
            self.state.mark_dirty()
            return

        if line.startswith("/"):
            cmd = line.lower().split()[0]
            value = _gate_command_value(line)
            if value is not None:
                self.bus.emit(ev.USER_INPUT_RECEIVED, {
                    "node_id": gate.get("node_id", ""),
                    "value": value,
                })
                self._print_command_output(
                    "gate submitted",
                    [f"[green]{escape(_short(value, 120))}[/]"],
                )
                return
            if cmd in ("/gate", "/gate-help"):
                self._print_gate_help()
                return
            dashboard_commands = {
                "/help", "/status", "/cost", "/tree", "/evidence", "/reply", "/chart",
                "/branches", "/report", "/export", "/pause", "/resume", "/skill", "/abort", "/quit",
            }
            if cmd in dashboard_commands:
                self._handle_slash_command(line)
                return

        if self._companion is not None and hasattr(self._companion, "ask_gate"):
            self._companion.ask_gate(gate, line)
            return

        self.state.gate_discussion_ask(line)
        self.state.gate_discussion_reply(
            "Gate discussion is unavailable in this terminal. Run with live dashboard input "
            "enabled, or wait for the gate timeout so the coordinator can continue."
        )

    def _print_gate_help(self) -> None:
        self._print_command_output("gate discussion", [
            "[dim]plain text[/] discuss this paused decision with an isolated companion",
            "[cyan]/approve[/] [dim]approve the gate immediately[/]",
            "[cyan]/skip[/] [dim]skip/reject the gate immediately[/]",
            "[cyan]/edit <text>[/] [dim]submit revised feedback immediately[/]",
            "[dim]when your intent is clear, the companion submits the final feedback automatically[/]",
            "[dim]/status /tree /cost[/] still work while the gate is open",
        ])

    def _print_command_output(self, title: str, rows: list[str]) -> None:
        # Render inline, just above the input box. Under the full-screen pt app,
        # printing to the scrollback (the old behaviour) scrolls straight off the
        # top of the pinned dashboard, so the result looks lost.
        self.state.set_command_output(title, rows)

    def _snapshot_ledger(self) -> tuple[list[str], dict]:
        """Cheap copy of the idea ledger (order + records).

        ``list(...)`` / ``dict(...)`` take copies so a concurrent mutation from
        the event thread can't corrupt a walk. If the copy races and raises
        ``RuntimeError`` mid-iteration, retry once under the state lock.
        """
        s = self.state
        try:
            return list(s.idea_order), dict(s.ideas)
        except RuntimeError:
            with s._lock:
                return list(s.idea_order), dict(s.ideas)

    def _tree_snapshot_lines(self) -> list[str]:
        order, ledger = self._snapshot_ledger()
        if not order:
            return ["[dim]no ideas yet[/]"]
        rows: list[str] = []
        for node_id in order[-20:]:
            rec = ledger.get(node_id)
            if rec is None:
                continue
            glyph, _, status_style = _STATUS_DECOR.get(rec.status, ("◌", "dim", "dim"))
            split = getattr(rec, "score_split", "dev")
            score = f" {rec.score:.4f} ({split})" if rec.score is not None else ""
            rows.append(
                f"{escape(glyph)} [yellow]{escape(node_id):<4}[/] "
                f"[{status_style}]{escape(rec.status):<8}[/]{escape(score):>9}  "
                f"{escape(_short(rec.hypothesis or '(no description)', 90))}"
            )
        return rows

    def _evidence_lines(self) -> list[str]:
        s = self.state
        arrow = "lower is better" if s.metric_direction == "minimize" else "higher is better"
        rows = [f"[dim]metric[/] {escape(s.metric_direction)} [bright_black]({arrow})[/]"]
        rows.append(
            f"[dim]baseline[/] {s.baseline_score:.4f}"
            if s.baseline_score is not None else "[dim]baseline[/] —"
        )
        rows.append(
            f"[dim]trunk·dev[/] {s.trunk_score:.4f}"
            if s.trunk_score is not None else "[dim]trunk·dev[/] —"
        )
        test_trunk = getattr(s, "test_trunk_score", None)
        if test_trunk is not None:
            rows.append(f"[dim]trunk·test[/] {test_trunk:.4f}")
        rows.append(
            f"[dim]best branch[/] {s.best_score:.4f}"
            if s.best_score is not None else "[dim]best branch[/] —"
        )
        if s.baseline_score is not None and s.best_score is not None:
            delta = s.best_score - s.baseline_score
            if s.metric_direction == "minimize":
                delta = -delta
            rows.append(f"[dim]improvement[/] {delta:+.4f}")
        rows.append(f"[dim]events[/] {escape(str(Path(s.session_dir or '.') / 'events.jsonl'))}")
        return rows

    def _branch_lines(self) -> list[str]:
        order, ledger = self._snapshot_ledger()
        rows: list[str] = []
        for node_id in order[-20:]:
            rec = ledger.get(node_id)
            if rec is None or not rec.branch:
                continue
            rows.append(
                f"[yellow]{escape(node_id):<4}[/] [{_STATUS_DECOR.get(rec.status, ('', 'dim', 'dim'))[2]}]"
                f"{escape(rec.status):<8}[/] [cyan]{escape(rec.branch)}[/]"
            )
        return rows or ["[dim]no branch refs yet[/]"]

    def _drain_escape_sequence(self) -> str:
        """After reading an ESC byte, pull the rest of the CSI sequence.

        Most terminal control codes we care about look like
        ``ESC [ <params> <final>`` where final is an ASCII letter (or ~
        for the extended forms used by Home/End/Delete/PgUp/PgDn).
        We read greedily until we either see a final byte or the input
        goes quiet — whichever comes first.
        """
        seq = ""
        try:
            for _ in range(8):       # cap to avoid pathological loops
                r, _, _ = select.select([sys.stdin], [], [], 0.02)
                if not r:
                    break
                b = os.read(sys.stdin.fileno(), 1)
                if not b:
                    break
                c = b.decode("latin-1", errors="ignore")
                seq += c
                # Final byte for CSI sequences: 0x40..0x7e
                if seq and ("A" <= c <= "Z" or "a" <= c <= "z" or c == "~"):
                    break
        except Exception:
            pass
        return seq

    def _apply_escape_sequence(self, seq: str) -> None:
        """Map common CSI / SS3 sequences to caret operations (or, while the
        reply reader is expanded, to scrolling it)."""
        expanded = self.state.reply_expanded
        if not seq:                  # bare Esc
            if expanded:
                self._set_reply_expanded(False)
            return
        buf = self._input_buffer
        pos = self._cursor_pos
        # While the expanded reader is open, vertical-motion keys scroll it
        # instead of doing nothing. Horizontal keys still move the caret so the
        # input line stays editable.
        if expanded:
            page = max(1, self._rc().size.height - 8)
            if seq in ("[A", "OA"):              # up
                self._scroll_reply(-1); return
            if seq in ("[B", "OB"):              # down
                self._scroll_reply(1); return
            if seq == "[5~":                     # PgUp
                self._scroll_reply(-page); return
            if seq == "[6~":                     # PgDn
                self._scroll_reply(page); return
            if seq in ("[H", "OH", "[1~", "[7~"):  # Home → top
                self.state.reply_scroll = 0
                self.state.mark_dirty(); return
            if seq in ("[F", "OF", "[4~", "[8~"):  # End → bottom (clamped in render)
                self.state.reply_scroll = 10 ** 9
                self.state.mark_dirty(); return
        # CSI form: "[X"  /  SS3 form: "OX"
        if seq in ("[D", "OD"):     # left arrow
            self._cursor_pos = max(0, pos - 1)
            self.state.mark_dirty(); return
        if seq in ("[C", "OC"):     # right arrow
            self._cursor_pos = min(len(buf), pos + 1)
            self.state.mark_dirty(); return
        if seq in ("[H", "OH", "[1~", "[7~"):  # home
            self._cursor_pos = 0
            self.state.mark_dirty(); return
        if seq in ("[F", "OF", "[4~", "[8~"):  # end
            self._cursor_pos = len(buf)
            self.state.mark_dirty(); return
        if seq == "[3~":             # Delete (forward)
            if pos < len(buf):
                self._input_buffer = buf[:pos] + buf[pos + 1:]
                self.state.mark_dirty()
            return
        # arrow up/down — ignore for now (no history)
        # everything else: swallow silently

    def _restore_termios(self) -> None:
        if self._saved_termios is not None and _RAW_TTY_AVAILABLE:
            try:
                termios.tcsetattr(sys.stdin.fileno(),
                                  termios.TCSADRAIN,
                                  self._saved_termios)
            except Exception:
                pass
            self._saved_termios = None

    # ── DISPLAY_HOOK target ────────────────────────────────────

    def _on_agent_event(self, kind: str, data: dict[str, Any]) -> None:
        # Raw user prompts are massive system-prompt blobs — never useful.
        if kind == "user":
            return
        # Assistant text: capture, let the state decide whether to surface
        # it (only after a user note, so verbose internal reasoning stays
        # hidden by default).
        if kind == "assistant":
            message = data.get("message", "")
            # Push the FULL reply to terminal scrollback (above the Live
            # region) before the panel sees a truncated view. The reply
            # panel only fits a few lines — sending the long form upward
            # gives the user a permanent record to scroll back to.
            #
            # Only mirror while we're actively awaiting a reply to a user
            # question. The coordinator emits assistant text constantly as
            # part of its research loop; gating on ``awaiting_reply``
            # keeps that internal narration out of scrollback.
            if (
                message
                and message.strip()
                and self.state.awaiting_reply
            ):
                self._print_full_reply(message.strip())
            self.state.on_assistant_text(message)
            return
        if kind == "tool_call":
            self.state.on_tool_call(data.get("name", "?"),
                                    data.get("inputs") or {})
            return
        if kind == "tool_result":
            if data.get("is_error"):
                self.state.on_tool_error(data.get("name", "?"),
                                         data.get("output") or "")
            return
        if kind == "status":
            self._on_status(data.get("message", ""))

    def _on_status(self, msg: str) -> None:
        """Promote only the status messages that signal real changes the
        user should notice. Everything else is per-turn telemetry."""
        low = msg.lower()
        if "calling" in low and "..." in low:
            return                     # per-turn "calling MODEL..."
        if "max tokens" in low or "max turns" in low:
            self.state.on_status_narration(msg, style="yellow", glyph="!")
            return
        if "llm error" in low or "retrying" in low:
            self.state.on_status_narration(msg, style="red", glyph="!")
            return
        if "compaction" in low or "summarization" in low:
            self.state.on_status_narration(msg, style="cyan", glyph="·")
            return
        # Anything else: leave silent. The orchestrator's narration is
        # already routed through render_status which re-enters this
        # path via style.render_status (see style.py).

    # ── bus wiring ─────────────────────────────────────────────

    def _wire_bus(self) -> None:
        s = self.state
        b = self.bus
        b.on(ev.SESSION_START,       s.on_session_start)
        b.on(ev.SESSION_END,         s.on_session_end)
        b.on(ev.CYCLE_START,         s.on_cycle_start)
        b.on(ev.CYCLE_END,           s.on_cycle_end)
        b.on(ev.PHASE_CHANGE,        s.on_phase_change)
        b.on(ev.IDEA_PROPOSED,       s.on_idea_proposed)
        b.on(ev.IDEA_COMPLETED,      s.on_idea_completed)
        b.on(ev.IDEA_PRUNED,         s.on_idea_pruned)
        b.on(ev.IDEA_MERGED,         s.on_idea_merged)
        b.on(ev.EXECUTOR_START,      s.on_executor_start)
        b.on(ev.EXECUTOR_END,        s.on_executor_end)
        b.on(ev.LLM_CALL,            s.on_llm_call)
        b.on(ev.LLM_ERROR,           s.on_llm_error)
        b.on(ev.CONVERGENCE_REACHED, s.on_convergence)
        # Live reasoning panel (#6): streamed thinking + per-agent tool activity.
        b.on(ev.THINKING_DELTA,      s.on_thinking_delta)
        b.on(ev.TOOL_START,          s.on_tool_start)
        b.on(ev.TOOL_END,            s.on_tool_end)
        b.on(ev.HEARTBEAT,           s.on_heartbeat)
        # HITL review gate (#2) / ask-back (#10): show the prompt, collect a reply.
        b.on(ev.AWAIT_USER,          s.open_gate)
        b.on(ev.USER_INPUT_RECEIVED, s.close_gate)

    # ── rendering ──────────────────────────────────────────────

    def _dashboard_ansi(self) -> "ANSI":
        """Render the dashboard to an ANSI string for the pt Application's
        content window. Sized to the dashboard region (terminal minus the input
        rows); the capture console is rebuilt each frame so resize is honored."""
        try:
            size = self._app.output.get_size()  # type: ignore[union-attr]
            rows, cols = size.rows, size.columns
        except Exception:
            size = console.size
            cols, rows = size.width, size.height
        width = max(20, cols)
        height = max(1, rows - INPUT_REGION_HEIGHT)
        self._region_console = Console(
            force_terminal=True, color_system="truecolor",
            width=width, height=height, highlight=False,
        )
        with self._region_console.capture() as cap:
            self._region_console.print(self._render(include_footer=False), end="")
        return ANSI(cap.get())

    def _render(self, include_footer: bool = True) -> Layout:
        s = self.state
        avail = self._rc().size.height
        header_h = _HEADER_H + (1 if s.webui_url else 0)
        # Decide which optional panels fit and how tall, so the layout never
        # overflows the terminal (overflow makes Live scroll + duplicate frames).
        # The footer is only part of the budget on the legacy Live path; under
        # the pt Application the input lives in real pt windows below us.
        footer_h = _FOOTER_H if include_footer else 0
        cmd = self._active_command_output()
        command_pref = (len(cmd[1]) + 3) if cmd else 0   # title + rows + 2 borders
        plan = _plan_section_sizes(
            avail,
            has_gate=s.pending_gate is not None,
            has_reply=self._reply_visible(),
            has_chart=s.show_chart and self._has_chart_data(),
            has_reasoning=bool(s.thinking_feed or s.agent_activity),
            reply_pref=avail // 3,
            header_h=header_h,
            reply_expanded=s.reply_expanded,
            footer_h=footer_h,
            command_pref=command_pref,
        )

        layout = Layout()
        # Visual order is fixed; the budget only decides inclusion + height.
        sections = [Layout(self._render_header(), name="header", size=header_h)]
        if "chart" in plan:
            sections.append(Layout(self._render_chart(plan["chart"]),
                                   name="chart", size=plan["chart"]))
        sections.append(Layout(self._render_ideas(), name="ideas"))
        if "reasoning" in plan:
            sections.append(Layout(self._render_reasoning(),
                                   name="reasoning", size=plan["reasoning"]))
        if "gate" in plan:
            sections.append(Layout(self._render_gate(), name="gate", size=plan["gate"]))
        if "reply" in plan:
            sections.append(Layout(self._render_reply(plan["reply"]),
                                   name="reply", size=plan["reply"]))
        if "command" in plan and cmd:
            sections.append(Layout(self._render_command(cmd, plan["command"]),
                                   name="command", size=plan["command"]))
        if include_footer:
            sections.append(Layout(self._render_footer(), name="footer", size=_FOOTER_H))
        layout.split_column(*sections)
        return layout

    def _active_command_output(self) -> tuple[str, list[str]] | None:
        """The last slash-command output, if still within its display window."""
        s = self.state
        if s.command_output is None:
            return None
        if time.monotonic() - s.command_output_at > _COMMAND_TTL_S:
            return None
        return s.command_output

    def _render_command(self, cmd: tuple[str, list[str]], panel_size: int) -> Panel:
        """Transient panel for slash-command output, pinned just above the input
        so a /cost or /status result is visible where you typed it."""
        title_text, rows = cmd
        body = Table.grid(padding=(0, 0))
        body.add_column(overflow="fold")
        room = max(1, panel_size - 2)        # borders
        shown = rows[:room]
        for row in shown:
            body.add_row(Text.from_markup(f"  {row}"))
        if len(rows) > room:
            body.add_row(Text.from_markup(
                f"  [bright_black]… {len(rows) - room} more line(s)[/]"))
        title = Text(f"{title_text}", style="bold cyan")
        title.append("  · command output", style="dim")
        return Panel(body, title=title, title_align="left",
                     border_style="cyan", padding=(0, 1))

    def _render_header(self) -> Panel:
        """Tempo + focus + recent error. The explored-ideas table below
        carries the per-idea detail, so the header stays compact and
        purely about the run as a whole."""
        s = self.state

        # Row 1: progress numbers — branch budget / counts / best / elapsed.
        used = s.branch_budget_used
        total = s.total_cycles or "?"
        branch_budget = f"[bold cyan]branches {used}[/] [dim]/ {total}[/]"
        if s.ideas_running:
            branch_budget += f" [magenta]+{s.ideas_running} running[/]"
        if s.paused:
            branch_budget += " [yellow]paused[/]"
        ideas = (
            f"[bold]ideas {s.ideas_proposed}[/]  "
            f"[green]✓{s.ideas_done}[/] "
            f"[bright_black]✗{s.ideas_pruned}[/] "
            f"[yellow]⟳{s.ideas_needs_retry}[/] "
            f"[magenta]▸{s.ideas_running}[/]"
        )
        direction_glyph = "↓" if s.metric_direction == "minimize" else "↑"
        if s.best_score is not None:
            spark = _sparkline(s.best_score_history)
            best = f"[dim]best{direction_glyph}[/] [bold]{s.best_score:.4f}[/]"
            if spark:
                best += f"  [bright_green]{spark}[/]"
        else:
            best = f"[dim]best{direction_glyph} —[/]"
        elapsed = f"[dim]elapsed[/] {format_duration(s.elapsed_seconds)}"
        row1 = Table.grid(padding=(0, 3))
        for _ in range(4):
            row1.add_column()
        row1.add_row(branch_budget, ideas, best, elapsed)

        # Row 2: phase pipeline — the whole cycle with the current stage lit, so
        # you can see where in the loop the coordinator is at a glance.
        _PIPELINE = ("observe", "ideate", "select", "dispatch", "backprop", "decide")
        stages = []
        for p in _PIPELINE:
            label, color = PHASE_STYLE.get(p, (p.upper(), "white"))
            if p == s.phase:
                stages.append(f"[bold {color}]{label}[/]")
            else:
                stages.append(f"[bright_black]{label.lower()}[/]")
        pipeline_text = "[bright_black] › [/]".join(stages)

        # Row 3: what the coordinator is doing *right now* — the focal line, kept
        # on its own row (not buried beside the phase) so it's the obvious
        # "what's happening" anchor. now_action carries its own Rich markup
        # (set by orchestrator narration), so it is interpolated, not escaped.
        if s.now_action:
            since = time.monotonic() - s.now_action_started_at
            since_s = format_duration(since) if since > 1 else ""
            now_text = (
                f"[bold cyan]now[/] {s.now_action}"
                + (f"  [dim]({since_s})[/]" if since_s else "")
            )
        else:
            now_text = "[bold cyan]now[/] [dim]waiting…[/]"

        # Row 3: token burn (cheap budget signal) + error flash.
        tokens_text = ""
        if s.tokens_in or s.tokens_out:
            total = s.tokens_in + s.tokens_out
            rate = total / s.elapsed_seconds if s.elapsed_seconds > 1 else 0
            tokens_text = (
                f"[dim]tokens[/] [cyan]{_fmt_tokens(total)}[/] "
                f"[bright_black]({_fmt_tokens(s.tokens_in)} in / "
                f"{_fmt_tokens(s.tokens_out)} out · {int(rate)}/s)[/]"
            )
        error_text = ""
        if s.last_error_text and (time.monotonic() - s.last_error_at) < 30:
            status_style = s.last_error_style or "dim"
            status_glyph = s.last_error_glyph or "·"
            error_text = (
                f"  [{status_style}]{escape(status_glyph)}[/] "
                f"[{status_style}]{escape(_short(s.last_error_text, 80))}[/]"
            )

        body = Table.grid(padding=(0, 0))
        body.add_column(overflow="ellipsis", no_wrap=True)
        body.add_row(row1)
        body.add_row(Text.from_markup(pipeline_text))
        if s.webui_url:
            body.add_row(Text.from_markup(f"[dim]webui[/] [cyan underline]{escape(s.webui_url)}[/]"))
        body.add_row(Text.from_markup(now_text))
        meta_bits: list[str] = []
        if s.baseline_score is not None:
            meta_bits.append(f"[dim]baseline[/] {s.baseline_score:.4f}")
        if s.trunk_score is not None:
            meta_bits.append(f"[dim]trunk[/] {s.trunk_score:.4f}")
        metric_meta = "    ".join(meta_bits)
        body.add_row(Text.from_markup((tokens_text + "    " + metric_meta).strip() + error_text))

        title = Text()
        title.append("arbor", style="bold yellow")
        if self.state.run_name:
            title.append(f"  ·  {self.state.run_name}", style="dim")
        if self.state.model:
            title.append(f"  ·  {self.state.model}", style="dim")
        return Panel(body, title=title, title_align="left",
                     border_style="cyan", padding=(0, 2))

    def _chart_points(self) -> list[tuple[float, float, str]]:
        """Collect (elapsed_seconds, score, status) for every scored idea.

        Copies the ledger defensively — the event thread mutates it
        concurrently — mirroring webui.snapshot.state_to_dict.
        """
        s = self.state
        try:
            ideas = list(s.ideas.values())
        except RuntimeError:            # event thread added an idea mid-iteration
            with s._lock:
                ideas = list(s.ideas.values())
        pts: list[tuple[float, float, str]] = []
        for rec in ideas:
            if rec.score is None:
                continue
            ts = rec.finished_at if rec.finished_at is not None else rec.proposed_at
            elapsed = max(0.0, ts - s.started_at)
            pts.append((elapsed, float(rec.score), rec.status))
        return pts

    def _has_chart_data(self) -> bool:
        s = self.state
        if s.baseline_score is not None:
            return True
        return any(rec.score is not None for rec in s.ideas.values())

    def _render_chart(self, panel_size: int = _CHART_MAX_H) -> Panel:
        """Live score-over-elapsed-time chart (scatter + baseline + frontier).

        ``panel_size`` is the height budgeted for the panel; the renderer gets
        ``panel_size - 3`` rows (panel border + padding take ~3)."""
        s = self.state
        # Inner width: panel padding (1 each side) + borders (1 each side).
        width = max(20, self._rc().size.width - 6)
        rows = render_progress_chart(
            self._chart_points(),
            baseline=s.baseline_score,
            metric_direction=s.metric_direction,
            width=width,
            height=max(5, panel_size - 3),
            now_elapsed=s.elapsed_seconds,
        )
        body = Table.grid()
        body.add_column(no_wrap=True, overflow="crop")
        for row in rows:
            body.add_row(row)
        title = Text("progress", style="bold cyan")
        if s.best_score is not None:
            glyph = "↓" if s.metric_direction == "minimize" else "↑"
            title.append(f"  ·  best{glyph} {s.best_score:.4f}", style="dim")
        return Panel(body, title=title, title_align="left",
                     border_style="cyan", padding=(0, 1))

    def _render_ideas(self) -> Padding:
        """Hierarchical idea tree — the main information surface.

        We build a children map from each IdeaRecord's parent_id and
        recurse breadth-first from the synthetic root. Layout per node:

            ✓ n3  chunked attention                  merged    0.7421
            │
            ├─ ↻ n5  + learned routing  ← now        running   2m 14s
            │   └─ ✗ n8  + dropout                   pruned    regression
            └─ ◌ n6  hierarchical summarization      queued

        Pruned subtrees collapse to a single ``(N pruned)`` line once
        they get deep enough — we keep the visual budget for the path
        that's still being explored.
        """
        s = self.state
        # The idea tree is now a bordered panel (matching the reasoning panel)
        # so it reads as a distinct "what we're exploring" surface. Glyph legend
        # rides the bottom border; the title says what the panel is.
        panel_title = Text("idea tree", style="bold cyan")
        panel_title.append("  · hypotheses being explored", style="dim")
        legend = Text.from_markup(
            "[bright_black]◌ queued  ▸ running  ✓ kept  ✗ pruned  ↻ merged[/]")

        def _panel(body) -> Panel:
            return Panel(body, title=panel_title, title_align="left",
                         subtitle=legend, subtitle_align="left",
                         border_style="cyan", padding=(0, 1))

        # Snapshot the ledger so a concurrent event-thread mutation can't
        # corrupt our walk; on an unrecoverable race, skip this frame and let
        # the next paint catch up.
        try:
            order, ledger = self._snapshot_ledger()
        except Exception:
            return _panel(Text("tree unavailable this frame…", style="dim"))

        # ── Build the children map. Nodes whose parent we never saw
        # (e.g. the literal arbor cycle root, or out-of-order events) get
        # attached as top-level so they still render. We also drop any
        # self-referential edge defensively so a cycle can't recurse
        # forever (RecursionError would just spam the crash log).
        children: dict[str | None, list[str]] = {}
        for node_id in order:
            rec = ledger.get(node_id)
            if rec is None:
                continue
            parent = rec.parent_id
            if not isinstance(parent, str) or parent == node_id or parent not in ledger:
                parent = None
            children.setdefault(parent, []).append(node_id)

        if not order:
            body = Text(
                "no ideas yet — agent is still observing the project…",
                style="dim",
            )
            return _panel(body)

        # ── Width budget for the hypothesis column. We reserve room
        # for the indent guides (variable, capped at ~16 chars), glyph,
        # id, status and metric columns; everything else goes to text.
        now = time.monotonic()
        terminal_width = self._rc().size.width
        fixed = 2 + 4 + 1 + 2 + 8 + 2 + 10 + 7 + 4

        rows: list[Text] = []
        seen: set[str] = set()       # cycle guard for the recursive walk

        def _walk(node_id: str, prefix: str, is_last: bool) -> None:
            if node_id in seen:
                return                # parent_id cycle — bail silently
            seen.add(node_id)
            rec = ledger.get(node_id)
            if rec is None:
                return
            connector = ("└─ " if is_last else "├─ ") if prefix or rec.parent_id else ""
            indent = prefix + connector
            hyp_max = max(20, terminal_width - fixed - len(indent))
            rows.append(_format_idea_row(
                rec, node_id, s.current_idea_node, now, hyp_max,
                metric_direction=s.metric_direction,
                best_score=s.best_score,
                indent=indent,
            ))
            kids = children.get(node_id, [])
            if kids:
                child_prefix = prefix + ("    " if is_last else "│   ")
                for i, child in enumerate(kids):
                    _walk(child, child_prefix, i == len(kids) - 1)

        roots = children.get(None, [])
        for i, root in enumerate(roots):
            _walk(root, "", i == len(roots) - 1)

        # Cap rendered rows to roughly the terminal height to keep the
        # Live view stable on huge trees. Newest activity stays at the
        # bottom; older nodes (top of tree) get truncated.
        max_rows = max(6, self._rc().size.height - 14)
        if len(rows) > max_rows:
            dropped = len(rows) - max_rows
            head = Text(f"  … {dropped} earlier node(s) hidden — see REPORT.md",
                        style="bright_black")
            rows = [head] + rows[-max_rows:]

        grid = Table.grid()
        grid.add_column()
        for r in rows:
            grid.add_row(r)
        return _panel(grid)

    def _reply_visible(self) -> bool:
        """The conversation panel shows when there's any companion Q&A to
        display (or one is in flight). Keeps the chrome away when idle."""
        s = self.state
        return bool(
            s.companion_busy or s.companion_turns
            or (s.pending_gate is not None and (s.gate_discussion_busy or s.gate_discussion_turns))
        )

    def _print_full_reply(self, message: str) -> None:
        """Write the full assistant reply to the terminal scrollback.

        The conversation panel is fixed-size and truncates long replies. We
        mirror the full text above the live region (via ``_print_above``) so the
        user can scroll up and read everything verbatim.
        """
        def _render() -> None:
            target = self._live.console if self._live is not None else console
            from rich.markdown import Markdown
            header = Text()
            header.append("agent reply", style="bold cyan")
            header.append("  ·  full", style="dim")
            target.print(header)
            # Markdown renders code fences / lists / bold properly.
            body = Markdown(message, code_theme="monokai",
                            inline_code_theme="monokai")
            target.print(Padding(body, (0, 0, 0, 2)))
            if self.state.conversation_path:
                target.print(
                    f"[dim]saved in {escape(self.state.conversation_path)}[/dim]"
                )
            target.print()
        self._print_above(_render)

    def _mirror_new_companion_turns(self) -> None:
        """Echo each completed companion answer to the scrollback once.

        The reply panel is fixed-height and crops long answers; mirroring the
        full Q&A above the live region gives the user a permanent, scrollable
        record. Called from the paint loop, so it runs on every state change.
        Robust against ``companion_turns`` being a bounded deque: we key off the
        monotonic ``companion_reply_seq`` rather than the visible turn count.
        """
        s = self.state
        seq = s.companion_reply_seq
        if seq <= self._mirrored_companion_seq:
            return
        # Pull the most recent (question, answer) pair off the tail. At reply
        # time the tail is [..., ("you", q), ("companion", a)].
        try:
            turns = list(s.companion_turns)
        except RuntimeError:
            return
        question = ""
        answer = ""
        for role, text in reversed(turns):
            if role == "companion" and not answer:
                answer = text
            elif role == "you" and answer and not question:
                question = text
                break

        def _render() -> None:
            target = self._live.console if self._live is not None else console
            from rich.markdown import Markdown
            if question:
                target.print(Text.from_markup(
                    f"[magenta]you[/]  {escape(_short(question, 200))}"))
            header = Text()
            header.append("companion", style="bold blue")
            header.append("  ·  answer", style="dim")
            target.print(header)
            if answer:
                target.print(Padding(
                    Markdown(answer, code_theme="monokai",
                             inline_code_theme="monokai"),
                    (0, 0, 0, 2)))
            target.print()
        self._print_above(_render)
        self._mirrored_companion_seq = seq

    def _render_reply(self, panel_size: int | None = None) -> Panel:
        """The conversation panel — a read-only Q&A companion (#11).

        Plain typed input is answered here by a *separate* read-only agent that
        never touches the research run. To influence the agent, use /steer.
        The research agent's own activity lives in the reasoning panel.

        Two modes: a compact peek (last exchange trimmed, with a pointer to the
        full answer), and an expanded scrollable reader (Tab / /reply) that
        claims the screen so a long answer is readable inline.
        """
        s = self.state
        gate_active = s.pending_gate is not None and (s.gate_discussion_busy or s.gate_discussion_turns)
        turns = list(s.gate_discussion_turns if gate_active else s.companion_turns)
        busy = s.gate_discussion_busy if gate_active else s.companion_busy

        latest_answer = next((t for r, t in reversed(turns) if r == "companion"), "")
        if s.reply_expanded and latest_answer:
            return self._render_reply_expanded(latest_answer, panel_size, gate_active, busy)

        body_lines: list[Text] = []
        if not turns and not busy:
            body_lines.append(Text.from_markup(
                "[dim]ask a question about the run — answered by a read-only "
                "companion. use [/][bold]/steer[/][dim] to influence the agent.[/]"
            ))
        else:
            for role, text in turns[-6:]:
                if role == "you":
                    body_lines.append(Text.from_markup(
                        f"[magenta]you[/]  {escape(_short(text, 200))}"))
                else:
                    # Peek: show the first few lines, then point at the full
                    # answer (mirrored to the scrollback above + Tab to expand).
                    max_lines = min(10, max(4, self._rc().size.height // 5))
                    reply, truncated = _trim_lines(text, max_lines=max_lines, max_chars=4000)
                    total = len(text.strip().splitlines())
                    body_lines.append(Text.from_markup(
                        f"[bold blue]companion[/]  {escape(reply)}"))
                    if truncated:
                        more = max(0, total - max_lines)
                        more_txt = f" ({more} more lines)" if more else ""
                        body_lines.append(Text.from_markup(
                            f"[bright_black]↑ full reply in the scrollback above{escape(more_txt)} · "
                            f"press Tab to expand here[/]"))
            if busy:
                label = "gate companion" if gate_active else "companion"
                since = s.gate_discussion_busy_since if gate_active else s.companion_busy_since
                elapsed = (
                    f"  [bright_black]{format_duration(time.monotonic() - since)}[/]"
                    if since else ""
                )
                body_lines.append(Text.from_markup(
                    f"[magenta]{_spinner()}[/] [dim]{label} thinking…[/]{elapsed}"))
        grid = Table.grid(padding=(0, 0))
        grid.add_column(overflow="fold")
        for ln in body_lines:
            grid.add_row(ln)
        if gate_active:
            title = Text("gate discussion · isolated", style="bold magenta")
            title.append("  · coordinator resumes automatically", style="dim")
        else:
            title = Text("ask · read-only companion", style="bold magenta")
        if latest_answer:
            title.append("  · Tab to expand", style="dim")
        if s.user_locale and s.user_locale != "und":
            title.append(f"  · {s.user_locale}", style="dim")
        return Panel(grid, title=title, title_align="left",
                     border_style="magenta", padding=(0, 1))

    def _render_reply_expanded(self, answer: str, panel_size: int | None,
                               gate_active: bool, busy: bool = False) -> Panel:
        """Scrollable full-answer reader. Wraps the latest answer to the panel
        width and shows a window of it controlled by ``state.reply_scroll``."""
        s = self.state
        width = max(20, self._rc().size.width - 6)
        size = panel_size or self._rc().size.height
        body_h = max(1, size - 3)            # borders (2) + status row (1)

        wrapped = Text(answer.strip(), no_wrap=False).wrap(self._rc(), width)
        total = len(wrapped)
        max_scroll = max(0, total - body_h)
        scroll = max(0, min(s.reply_scroll, max_scroll))
        if scroll != s.reply_scroll:
            s.reply_scroll = scroll          # clamp persistently so keys agree
        window = wrapped[scroll:scroll + body_h]

        grid = Table.grid(padding=(0, 0))
        grid.add_column(overflow="fold")
        first = scroll + 1
        last = min(total, scroll + body_h)
        more_below = max_scroll - scroll
        pos = f"[bright_black]lines {first}–{last}/{total}[/]"
        nav = "[dim]PgUp/PgDn ↑/↓ scroll · Esc collapse[/]"
        if more_below > 0:
            nav = f"[bold blue]▼ {more_below} more lines[/]  " + nav
        if busy:
            # A new question is being answered while we're reading the previous
            # answer — keep the liveness signal visible here too, not just in the
            # collapsed peek, so a follow-up never looks unanswered.
            label = "gate companion" if gate_active else "companion"
            since = s.gate_discussion_busy_since if gate_active else s.companion_busy_since
            elapsed = format_duration(time.monotonic() - since) if since else ""
            nav = (f"[magenta]{_spinner()} {label} thinking… {elapsed}[/]   " + nav)
        grid.add_row(Text.from_markup(f"{pos}  {nav}"))
        for ln in window:
            grid.add_row(ln)

        title = Text("ask · expanded" if not gate_active else "gate discussion · expanded",
                     style="bold magenta")
        return Panel(grid, title=title, title_align="left",
                     border_style="magenta", padding=(0, 1))

    def _render_reasoning(self) -> Panel:
        """Live reasoning panel (#6): per-agent tool activity + recent thinking,
        attributed by agent label so parallel executors stay distinguishable.

        Fed by the bus (THINKING_DELTA / TOOL_START / TOOL_END / HEARTBEAT),
        independent of the now-line that the legacy DISPLAY_HOOK still drives."""
        s = self.state
        now = time.monotonic()
        grid = Table.grid(padding=(0, 0))
        grid.add_column(overflow="fold")

        # Zone A — current tool per agent, running entries first so a live
        # executor never scrolls off behind finished ones.
        try:
            items = list(s.agent_activity.items())
        except RuntimeError:        # event thread added an agent mid-iteration
            items = []
        running = [(a, v) for a, v in items if v.get("ok") is None]
        finished = [(a, v) for a, v in items if v.get("ok") is not None]
        n_fin = max(0, 5 - len(running))
        shown = running + (finished[-n_fin:] if n_fin else [])
        for agent, act in shown:
            ok = act.get("ok")
            glyph, gstyle = ("▸", "magenta") if ok is None else \
                            ("✓", "green") if ok else ("✗", "red")
            line = Text()
            line.append(f"{glyph} ", style=gstyle)
            line.append(f"{agent} ", style="cyan")
            line.append(str(act.get("tool") or "?"), style="bold")
            if act.get("preview"):
                line.append(f"  {act['preview']}", style="dim")
            if ok is None and act.get("started_at"):
                line.append(f"  ({format_duration(now - act['started_at'])})",
                            style="bright_black")
            elif act.get("duration") is not None:
                line.append(f"  ({format_duration(act['duration'])})",
                            style="bright_black")
            grid.add_row(line)

        if shown and s.thinking_feed:
            grid.add_row(Text(""))

        # Zone B — recent streamed reasoning, attributed and dimmed.
        for agent, text in list(s.thinking_feed)[-5:]:
            line = Text()
            line.append(f"{agent}  ", style="bright_black")
            line.append(text, style="dim")
            grid.add_row(line)

        if not shown and not s.thinking_feed:
            grid.add_row(Text.from_markup("[dim]no reasoning yet[/]"))

        title = Text("reasoning", style="bold blue")
        title.append("  · what the agents are doing now", style="dim")
        if s.last_activity_at:
            idle = now - s.last_activity_at
            if idle < 3:
                title.append("  · working", style="green")
            else:
                title.append(f"  · idle {format_duration(idle)}", style="dim")
        return Panel(grid, title=title, title_align="left",
                     border_style="blue", padding=(0, 1))

    def _render_gate(self) -> Panel:
        """The HITL review gate (#2) / ask-back (#10): the engine is blocked
        waiting for the user. Shows the prompt + options; the input box answers it."""
        g = self.state.pending_gate or {}
        kind = str(g.get("kind") or "review")
        grid = Table.grid(padding=(0, 0))
        grid.add_column(overflow="fold")
        grid.add_row(Text.from_markup(
            f"[bold yellow]⏸ awaiting your decision[/]  "
            f"[dim]{escape(kind)}"
            + (f" · node {escape(str(g.get('node_id')))}" if g.get("node_id") else "")
            + "[/]"))
        grid.add_row(Text.from_markup(
            f"[white]{escape(_short(str(g.get('prompt') or ''), 300))}[/]"))
        opts = g.get("options") or []
        if opts:
            grid.add_row(Text.from_markup(
                "[dim]options:[/] " + "   ".join(f"[cyan]{escape(str(o))}[/]" for o in opts)))
        grid.add_row(Text.from_markup(
            "[dim]type naturally to discuss. the companion submits final feedback when ready.[/]"))
        return Panel(grid, title=Text("review gate", style="bold yellow"),
                     title_align="left", border_style="yellow", padding=(0, 1))

    def _render_footer(self) -> Panel:
        grid = Table.grid(padding=(0, 0))
        grid.add_column()

        prompt = Text()
        prompt.append("› ", style="bold cyan")
        title = self._input_title()
        if not self._enable_input:
            prompt.append(
                "live input off — Ctrl-C to abort",
                style="dim",
            )
            grid.add_row(prompt)
            return Panel(grid, title=title, title_align="left",
                         border_style="bright_black", padding=(0, 1))
        if not self._input_active:
            prompt.append(
                "input unavailable in this terminal — run in a TTY for live commands",
                style="dim",
            )
            grid.add_row(prompt)
            return Panel(grid, title=title, title_align="left",
                         border_style="bright_black", padding=(0, 1))
        if self._input_mode == "prompt":
            prompt.append(self._prompt_toolkit_footer(), style="dim")
            grid.add_row(prompt)
            return Panel(grid, title=title, title_align="left",
                         subtitle=Text.from_markup(self._input_hint()),
                         subtitle_align="left",
                         border_style="bright_black", padding=(0, 1))
        if self._input_mode == "line":
            if self.state.pending_gate is not None:
                prompt.append(
                    self._line_mode_prompt(gate=True),
                    style="dim",
                )
            else:
                prompt.append(
                    self._line_mode_prompt(gate=False),
                    style="dim",
                )
            grid.add_row(prompt)
            return Panel(grid, title=title, title_align="left",
                         subtitle=Text.from_markup(self._input_hint()),
                         subtitle_align="left",
                         border_style="bright_black", padding=(0, 1))
        buf = self._input_buffer
        pos = max(0, min(self._cursor_pos, len(buf)))
        if buf:
            # Render: text-before-caret + inverted-caret-char + text-after
            # The inverted char is the visible caret. If the caret is at
            # end-of-buffer we use a solid block as a stand-in.
            prompt.append(buf[:pos], style="white")
            if pos < len(buf):
                prompt.append(buf[pos], style="black on bright_cyan")
                prompt.append(buf[pos + 1:], style="white")
            else:
                prompt.append("█", style="bold cyan")
        else:
            if self.state.pending_gate is not None:
                prompt.append(
                    "discuss this gate naturally — coordinator resumes when feedback is ready",
                    style="dim",
                )
                prompt.append("█", style="dim")
                grid.add_row(prompt)
                return Panel(grid, title="input · gate", title_align="left",
                             subtitle=Text.from_markup("[dim]/approve  /skip  /edit <text>  /status  /tree[/]"),
                             subtitle_align="left",
                             border_style="bright_black", padding=(0, 1))
            prompt.append(
                self._empty_input_prompt(),
                style="dim",
            )
            prompt.append("█", style="dim")
        grid.add_row(prompt)

        return Panel(grid, title=title, title_align="left",
                     subtitle=Text.from_markup(self._input_hint(buf)),
                     subtitle_align="left",
                     border_style="bright_black", padding=(0, 1))

    def _empty_input_prompt(self) -> str:
        if self._input_target == "research":
            return "research mode — plain text affects the agent; /ask for side questions"
        return "ask mode — plain text asks companion; /steer to affect research; / for commands"

    def _prompt_toolkit_footer(self) -> str:
        status = self._input_status_hint()
        if status:
            if self.state.pending_gate is not None and self.state.gate_discussion_busy:
                return "gate companion is preparing a reply; prompt below stays available"
            if self.state.companion_busy:
                return "companion is preparing a reply; prompt below stays available"
            if self.state.awaiting_reply:
                return "research message queued; the agent will see it at the next turn"
        if self.state.pending_gate is not None:
            return "use the gate prompt below: /approve, /skip, /edit <text>, or ask naturally"
        if self._input_target == "research":
            return "use the research prompt below; plain text affects the main agent"
        return "use the ask prompt below; plain text goes to the read-only companion"

    def _input_hint(self, buf: str = "") -> str:
        status = self._input_status_hint()
        if buf.startswith("/"):
            prefix = buf.split()[0].lower()
            # Suggest the curated short list on a bare "/", but recognise EVERY
            # real command (incl. hidden ones like /cost) so a valid command is
            # never mislabelled "unknown" just because it isn't in the menu.
            menu = [(n, d) for n, d in _MENU_COMMANDS if prefix == "/" or n.startswith(prefix)]
            full = [(n, d) for n, d in _DASHBOARD_COMMANDS if n.startswith(prefix)]
            shown = menu or full
            if shown:
                base = "   ".join(f"[cyan]{n}[/] [dim]{d}[/]" for n, d in shown[:4])
            else:
                base = "[yellow]unknown command[/] [dim]Enter to run, /help for commands[/]"
            return f"{status}   {base}" if status else base
        if self._input_target == "research":
            base = "[dim]/mode ask switches back · /status /tree /cost /help[/]"
        else:
            base = "[dim]/mode research to make plain text affect the agent · /status /tree /cost /help[/]"
        return f"{status}   {base}" if status else base

    def _input_title(self) -> str:
        s = self.state
        return _compose_input_title(
            pending_gate=s.pending_gate,
            gate_discussion_busy=s.gate_discussion_busy,
            companion_busy=s.companion_busy,
            awaiting_reply=s.awaiting_reply,
            input_target=self._input_target,
        )

    def _input_status_hint(self) -> str:
        s = self.state
        return _compose_input_status_hint(
            pending_gate=s.pending_gate,
            gate_discussion_busy=s.gate_discussion_busy,
            companion_busy=s.companion_busy,
            awaiting_reply=s.awaiting_reply,
        )

    def _line_mode_prompt(self, *, gate: bool) -> str:
        return _compose_line_mode_prompt(
            status=self._input_status_hint(),
            gate=gate,
            input_target=self._input_target,
        )


# ── helpers ────────────────────────────────────────────────────────


def _compose_input_status_hint(
    *,
    pending_gate: object,
    gate_discussion_busy: bool,
    companion_busy: bool,
    awaiting_reply: bool,
) -> str:
    """Transient status line shown above the input (pure)."""
    if pending_gate is not None and gate_discussion_busy:
        return "[magenta]gate companion is preparing a reply...[/]"
    if companion_busy:
        return "[magenta]companion is preparing a reply...[/]"
    if awaiting_reply:
        return "[yellow]research agent will see your message at the next turn[/]"
    return ""


def _compose_input_title(
    *,
    pending_gate: object,
    gate_discussion_busy: bool,
    companion_busy: bool,
    awaiting_reply: bool,
    input_target: str,
) -> str:
    """Title for the input panel reflecting the current input mode (pure)."""
    if pending_gate is not None:
        if gate_discussion_busy:
            return "input · gate · companion thinking"
        return "input · gate"
    if companion_busy:
        return "input · ask · companion thinking"
    if awaiting_reply:
        return "input · research · queued"
    if input_target == "research":
        return "input · research"
    return "input · ask"


def _compose_line_mode_prompt(*, status: str, gate: bool, input_target: str) -> str:
    """Plain-text line-mode prompt (pure). ``status`` is the status hint, if any."""
    if status:
        # Markup is stripped here because this prompt is appended as styled
        # plain text; the richer version is still shown in the subtitle.
        if "companion" in status:
            return "waiting for companion reply — you can still type another line and press Enter"
        return "research message queued — you can keep typing or use /mode ask"
    if gate:
        return "line mode — type /approve, /skip, /edit <text>, or a question; Enter to send"
    if input_target == "research":
        return "line mode research — plain text affects the agent; Enter to send"
    return "line mode ask — plain text asks companion; Enter to send"


class _DashboardSlashCompleter(Completer):
    """Slash command completions for the live dashboard prompt."""

    def __init__(self, dashboard: RunDashboard) -> None:
        self._dashboard = dashboard

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if text.startswith("/mode "):
            yield from self._word_completions(
                text,
                "/mode ",
                [("ask", "plain text asks the read-only companion"),
                 ("research", "plain text affects the main research agent")],
            )
            return
        # Only the curated short list pops up; every other command still works
        # when typed in full (and /help lists them all).
        commands = list(_MENU_COMMANDS)
        if self._dashboard.state.pending_gate is not None:
            commands = [
                ("/approve", "approve the paused gate"),
                ("/skip", "skip/reject the paused gate"),
                ("/edit", "submit revised gate feedback: /edit <text>"),
                ("/gate", "show gate help"),
                *commands,
            ]
        width = max((len(name) for name, _ in commands), default=0)
        for name, desc in commands:
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=f"  {name:<{width}}  ",
                    display_meta=desc,
                )

    def _word_completions(
        self,
        text: str,
        prefix_text: str,
        candidates: Iterable[tuple[str, str]],
    ):
        fragment = text[len(prefix_text):]
        start_position = -len(fragment) if fragment else 0
        for word, desc in candidates:
            if word.startswith(fragment):
                yield Completion(
                    word,
                    start_position=start_position,
                    display=f"  {word:<8}  ",
                    display_meta=desc,
                )


def _build_dashboard_prompt_session(dashboard: RunDashboard):
    return PromptSession(
        completer=_DashboardSlashCompleter(dashboard),
        complete_while_typing=True,
        history=InMemoryHistory(),
        style=Style.from_dict({
            "completion-menu.completion": "bg:#1f2937 #d1d5db",
            "completion-menu.completion.current": "bg:#0891b2 #ffffff",
            "completion-menu.meta.completion": "bg:#111827 #9ca3af",
            "completion-menu.meta.completion.current": "bg:#0e7490 #ffffff",
        }),
    )


def _patch_live_prompt_stdout():
    # Rich Live repaints with ANSI cursor/color control codes. prompt_toolkit
    # escapes those by default, which turns the dashboard into visible "?[36m" /
    # "?[2K?[1A" garbage while a prompt is active. Keep stdout raw so Live can
    # repaint.
    return patch_stdout(raw=True)


def _read_codepoint(fd: int) -> str | None:
    """Read one full UTF-8 code point from a cbreak-mode tty.

    A character may be 1–4 bytes; reading one byte and decoding (the
    previous approach) corrupted everything beyond ASCII. The lead
    byte's high bits encode the total length, so we read the first
    byte, then the right number of continuation bytes, then decode the
    full sequence.

    IMEs (Chinese / Japanese / etc.) typically deliver an entire
    composed character as a single burst once the user confirms it
    from the candidate window, so the continuation bytes are already
    queued in the kernel by the time we ask for them.
    """
    try:
        first = os.read(fd, 1)
    except Exception:
        return None
    if not first:
        return None
    b0 = first[0]
    if b0 < 0x80:
        n_more = 0
    elif (b0 >> 5) == 0b110:
        n_more = 1
    elif (b0 >> 4) == 0b1110:
        n_more = 2
    elif (b0 >> 3) == 0b11110:
        n_more = 3
    else:
        # Invalid lead byte (stray continuation, or > 4-byte form).
        # Drop it and let the next iteration recover.
        return ""
    rest = b""
    while len(rest) < n_more:
        try:
            chunk = os.read(fd, n_more - len(rest))
        except Exception:
            return None
        if not chunk:
            return None
        rest += chunk
    try:
        return (first + rest).decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _gate_command_value(line: str) -> str | None:
    """Translate explicit gate slash commands into coordinator replies."""
    text = line.strip()
    if not text.startswith("/"):
        return None
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower()
    rest = rest.strip()
    if cmd in {"/approve", "/accept", "/yes", "/y"}:
        return "approve"
    if cmd in {"/skip", "/reject", "/no", "/n"}:
        return "skip"
    if cmd == "/edit":
        return f"edit {rest}" if rest else None
    if cmd in {"/answer", "/submit"}:
        return rest or None
    return None


# ── crash dump ─────────────────────────────────────────────────────
#
# The dashboard owns the terminal, so an uncaught traceback either
# (a) gets eaten by the Live view's redraw and the process exits with
# nothing on screen, or (b) leaves the TTY in cbreak mode after the
# crash so the user can't even use their shell. Both feel like
# "the program flashed and died" with no explanation.
#
# We write the traceback to ``$ARBOR_CRASH_LOG`` (or
# ``~/.arbor/crashes/<timestamp>.log`` by default) so we can find out
# WHY it died after the fact. Used by both the stdin thread and the
# RunDashboard context manager.

def _dump_crash(where: str, exc: BaseException) -> str | None:
    """Write a crash record to disk and return its path.

    Best-effort — if the filesystem hates us, we swallow and move on.
    The user can set ``ARBOR_CRASH_LOG`` to a fixed path; otherwise we
    rotate via timestamps so multiple crashes don't overwrite each other.
    """
    import traceback

    try:
        env_path = os.environ.get("ARBOR_CRASH_LOG")
        if env_path:
            path = os.path.expanduser(env_path)
        else:
            base = os.path.expanduser("~/.arbor/crashes")
            os.makedirs(base, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(base, f"{stamp}.log")
        body = (
            f"# arbor crash\n"
            f"# where: {where}\n"
            f"# time:  {datetime.datetime.now().isoformat()}\n"
            f"# pid:   {os.getpid()}\n\n"
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write(body)
            f.write("".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ))
            # Recent input keystrokes — invaluable when the crash was
            # really an "accidental Ctrl-C / Ctrl-D" footgun.
            tail = _input_trace_tail()
            if tail:
                f.write("\n# recent keystrokes:\n")
                for line in tail:
                    f.write(f"  {line}\n")
        return path
    except Exception:
        return None


# ── input keystroke trace ──────────────────────────────────────────
#
# An in-memory ring buffer of the last few keystrokes (and which path
# in ``_handle_key`` they took). When the process exits unexpectedly
# we dump the tail to the crash log so we can diagnose "I just typed
# a few chars and it died". Off by default — no debug spam, just a
# rolling buffer that costs nothing.

_INPUT_TRACE: deque[str] = deque(maxlen=64)


def _trace_input(action: str, buf: str) -> None:
    _INPUT_TRACE.append(
        f"{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}  "
        f"{action:24s}  buf={buf!r}"
    )


def _input_trace_tail() -> list[str]:
    return list(_INPUT_TRACE)


# Eight unicode block-glyphs in ascending height. Mapping any value
# into its index gives us a one-line ASCII-art trend without dragging
# in a charting library.
_SPARK_BARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """Render a list of floats as a unicode sparkline.

    We scale to the local min/max rather than [0, 1] so that small
    changes are still visible — the user cares about *trend*, not
    absolute scale (the bold number next to it already shows that).
    """
    if not values:
        return ""
    if len(values) == 1:
        return _SPARK_BARS[-1]
    vmin = min(values)
    vmax = max(values)
    if vmax - vmin < 1e-9:
        # All equal: render a flat mid-line.
        return _SPARK_BARS[len(_SPARK_BARS) // 2] * len(values)
    span = vmax - vmin
    last = len(_SPARK_BARS) - 1
    return "".join(
        _SPARK_BARS[int((v - vmin) / span * last)] for v in values
    )


# Braille spinner frames. The paint loop repaints at least once a second (its
# _dirty.wait timeout), so a time-driven frame index gives a visible "alive"
# tick next to the elapsed counter even while no events arrive.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _spinner() -> str:
    """Pick a spinner frame from the wall clock (no per-call state needed)."""
    return _SPINNER_FRAMES[int(time.monotonic() * 8) % len(_SPINNER_FRAMES)]


def _fmt_tokens(n: int) -> str:
    """Compact token count: 1234 → 1.2k, 1234567 → 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def _trim_lines(s: str, *, max_lines: int, max_chars: int) -> tuple[str, bool]:
    """Cap a multi-line string to a fixed visual size for the reply
    panel. Whichever bound bites first wins; we add ``…`` so the user
    knows there's more."""
    s = s.strip()
    truncated = False
    if len(s) > max_chars:
        s = s[: max_chars - 1].rstrip() + "…"
        truncated = True
    lines = s.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
        truncated = True
    return "\n".join(lines), truncated


def _format_idea_metric(
    rec,
    now: float,
    *,
    metric_direction: str = "maximize",
    best_score: float | None = None,
) -> Text:
    """Right-hand metric cell: score for completed ideas, running
    duration for in-flight ones, reason for pruned, blank otherwise."""
    assert isinstance(rec, rs_module.IdeaRecord)
    if rec.status == "running" and rec.started_at is not None:
        return Text(format_duration(now - rec.started_at), style="magenta")
    if rec.status in ("done", "merged") and rec.score is not None:
        if best_score is not None and abs(rec.score - best_score) < 1e-12:
            style = "bold green"
        elif metric_direction == "minimize":
            style = "cyan"
        elif rec.score >= 0.8:
            style = "bold green"
        elif rec.score >= 0.5:
            style = "cyan"
        elif rec.score >= 0.2:
            style = "yellow"
        else:
            style = "red"
        return Text(f"{rec.score:.4f}", style=style)
    if rec.status == "pruned":
        # Hard-truncate so the metric cell stays exactly 10 chars and
        # the row never wraps onto a second line.
        return Text(_short(rec.pruned_reason or "—", 10), style="bright_black")
    if rec.status == "failed":
        return Text("failed", style="red")
    if rec.status == "needs_retry":
        return Text("retry", style="yellow")
    return Text("")


def _format_idea_row(rec, node_id: str, current_id: str | None,
                     now: float, hyp_max: int,
                     metric_direction: str = "maximize",
                     best_score: float | None = None,
                     indent: str = "") -> Text:
    """Single line for the ideas tree.

    Layout (mono-width assumptions; we pad with spaces ourselves so
    Rich never re-flows the columns):

        <indent>✓ n3   chunked attention …                  merged    0.7421
        <indent>▸ n6   hierarchical summarization …         running     2m 14s  ← now
        ────────── ── ──── ─────────────                     ────────  ────────  ──────
        tree-guide g  id    hypothesis                       status    metric   focus

    ``indent`` carries the box-drawing prefix (``│   ├─ ``) so the
    caller controls the tree shape without this function needing to
    know about siblings.
    """
    assert isinstance(rec, rs_module.IdeaRecord)
    glyph, glyph_style, status_style = _STATUS_DECOR.get(
        rec.status, ("◌", "dim", "dim"))
    is_current = (node_id == current_id)
    hyp_style = "bold white" if is_current else "white"

    line = Text()
    if indent:
        line.append(indent, style="bright_black")
    line.append(f"{glyph} ", style=glyph_style)
    line.append(f"{node_id:<4}", style="yellow")
    line.append(" ")
    hyp = _short(rec.hypothesis or "(no description)", hyp_max)
    line.append(hyp.ljust(hyp_max), style=hyp_style)
    line.append("  ")
    line.append(f"{rec.status:<8}", style=status_style)
    line.append("  ")
    metric = _format_idea_metric(
        rec,
        now,
        metric_direction=metric_direction,
        best_score=best_score,
    )
    metric_plain = metric.plain
    pad = max(0, 10 - len(metric_plain))
    line.append(" " * pad)
    line.append_text(metric)
    if is_current:
        line.append("  ")
        line.append("← now", style="bold cyan")
    return line


# ── style.render_status redirect target ───────────────────────────
#
# style.render_status calls back here whenever a CURRENT RunState is
# set, so orchestrator narration ("Pre-flight checks passed.", etc.)
# lands in the activity pane instead of scrolling above the Live view.

def route_status(msg: str, style: str, glyph: str) -> bool:
    state = rs_module.CURRENT
    if state is None:
        return False
    state.on_status_narration(msg, style=style, glyph=glyph)
    return True
