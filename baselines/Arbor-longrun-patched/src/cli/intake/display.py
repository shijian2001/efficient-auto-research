"""Compact display for the intake REPL.

Installs itself as `core.agent.DISPLAY_HOOK` while a turn is in flight,
so the verbose default output (tool args, result previews, retry status)
gets routed here and rendered as a single refreshing line.

Three visible surfaces:
  - one transient line (Rich Live) showing "what the agent is doing now"
  - assistant text replies, printed as normal scrolling lines
  - errors / warnings as normal scrolling lines (not hidden)
"""

from __future__ import annotations

import os
import re
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.spinner import Spinner
from rich.text import Text

from ..._app import APP_NAME
from ...core import agent as agent_module
from ..style import console as _console


# Tool-specific status text. (verb, key) — verb is the action label,
# key picks the most informative argument to show alongside.
_TOOL_LABELS: dict[str, tuple[str, str | None]] = {
    "Read": ("Reading", "file_path"),
    "Glob": ("Searching for", "pattern"),
    "Grep": ("Grepping for", "pattern"),
    "Bash": ("Running", "command"),
    "LaunchExperiment": ("Drafting plan", "instruction"),
}

# Short verb shown while the LLM is reasoning *after* a given tool.
# Picked so the spinner reflects what the agent is actually doing now,
# not the model name or turn number (those are internal telemetry).
_POST_TOOL_VERB: dict[str, str] = {
    "Read":             "reviewing",
    "Glob":             "scanning results",
    "Grep":             "scanning matches",
    "Bash":             "reading output",
    "LaunchExperiment": "wrapping up",
}
_INITIAL_VERB = "getting oriented"

_CALLING_RE = re.compile(r"calling\s+(\S+?)(?:\.\.\.|$)")


class IntakeDisplay:
    """Rich-based intake renderer.

    Use as a context manager around `await agent.run(...)`. While active,
    DISPLAY_HOOK is set; on exit, it is restored to whatever it was before.
    """

    def __init__(self, *, console: Console | None = None) -> None:
        self._console = console or _console
        self._live: Live | None = None
        self._previous_hook: Any = None
        self._last_tool_name: str | None = None  # picks the spinner verb

    # ── lifecycle ──────────────────────────────────────────────────

    def __enter__(self) -> "IntakeDisplay":
        self._previous_hook = agent_module.DISPLAY_HOOK
        agent_module.DISPLAY_HOOK = self._on_event
        return self

    def __exit__(self, *exc) -> None:
        self._stop_live()
        agent_module.DISPLAY_HOOK = self._previous_hook

    # ── hook target ────────────────────────────────────────────────

    def _on_event(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "user":
            return  # we already echoed the user's input in the REPL prompt
        if kind == "assistant":
            self._render_assistant(data.get("message", ""))
            return
        if kind == "tool_call":
            name = data.get("name", "?")
            label = _format_tool_label(name, data.get("inputs") or {})
            self._last_tool_name = name
            self._set_status(label)
            return
        if kind == "tool_result":
            if data.get("is_error"):
                self._stop_live()
                snippet = (data.get("output") or "").splitlines()
                first = snippet[0] if snippet else ""
                self._console.print(f"[red]tool error ({data.get('name')}):[/red] {first}")
            return
        if kind == "status":
            self._on_status(data.get("message", ""))
            return

    # ── status interpretation ──────────────────────────────────────

    def _on_status(self, msg: str) -> None:
        """Translate orchestrator/agent status messages to spinner text.

        Anything we don't explicitly handle is silently dropped — the agent's
        per-turn telemetry (Turn N: calling MODEL...) is not for the user.
        """
        lower = msg.lower()

        # LLM is being called. Show what the agent is *thinking about*,
        # derived from the most recent tool, instead of a generic "thinking".
        if _CALLING_RE.search(msg):
            verb = _POST_TOOL_VERB.get(self._last_tool_name, _INITIAL_VERB) \
                if self._last_tool_name else _INITIAL_VERB
            self._set_status(verb)
            return

        if "max tokens" in lower or "recovery" in lower:
            self._set_status("recovering…")
            return
        if "max turns" in lower:
            self._stop_live()
            self._console.print("[yellow]reached max turns[/yellow]")
            return
        if "llm error after" in lower:
            self._stop_live()
            self._console.print(f"[red]{_short(msg, 220)}[/red]")
            return
        if "llm error" in lower or "retrying" in lower:
            self._set_status("retrying…")
            return

        # Unhandled status — drop. (Was scrolling as dim lines; turned out
        # to be mostly noise like "Turn N: …" variants the regex missed.)

    # ── rendering primitives ───────────────────────────────────────

    def _set_status(self, text: str) -> None:
        spinner = Spinner("dots", text=Text(text, style="dim"))
        if self._live is None:
            self._live = Live(spinner, console=self._console,
                              refresh_per_second=10, transient=True)
            self._live.start()
        else:
            self._live.update(spinner)

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _render_assistant(self, message: str) -> None:
        self._stop_live()
        if not message.strip():
            return
        # Header line: bold yellow product name + dim arrow.
        header = Text()
        header.append(APP_NAME, style="bold yellow")
        header.append(" ›", style="dim")
        self._console.print(header)
        # Body: render as Markdown so the agent can use **bold**, `code`,
        # ```fenced blocks```, lists, etc. Indented two columns for visual
        # separation from the user's prompt.
        body = Markdown(message, code_theme="monokai", inline_code_theme="monokai")
        self._console.print(Padding(body, (0, 0, 0, 2)))
        self._console.print()


# ── helpers ────────────────────────────────────────────────────────


def _format_tool_label(name: str, inputs: dict[str, Any]) -> str:
    """Produce a short status line for a tool call, e.g. 'Reading classify.py'."""
    verb, key = _TOOL_LABELS.get(name, (name, None))
    target = _pick_target(inputs, key)
    return f"{verb} {target}".strip() if target else verb


def _pick_target(inputs: dict[str, Any], preferred_key: str | None) -> str:
    """Pick the most informative input value and shorten it."""
    if preferred_key and inputs.get(preferred_key):
        return _shorten_value(inputs[preferred_key])
    for v in inputs.values():
        if v:
            return _shorten_value(v)
    return ""


def _shorten_value(v: Any) -> str:
    s = str(v).strip()
    # If it looks like a filesystem path, use the basename.
    if "/" in s and " " not in s and not s.startswith(("**", "*.", "?")):
        return os.path.basename(s.rstrip("/")) or s
    return _short(s, 60)


def _short(s: str, limit: int = 60) -> str:
    s = s.replace("\n", " ").strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s
