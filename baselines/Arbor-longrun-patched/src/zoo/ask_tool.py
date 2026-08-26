"""A console-backed AskUser tool for the collection agents.

The discovery and bring-up agents (:mod:`arbor.zoo.agent_stages`) sometimes hit a
choice that is genuinely the *user's* to make — most often **which implementation to
treat as the baseline** when a repo ships both its own headline method and simpler
references (direct generation, naive RAG, an earlier system). Rather than guess, the
agent calls this tool and the question is put to the human at the terminal.

This is deliberately *not* the Coordinator's :class:`~arbor.coordinator.tools.AskUserTool`:
that one talks to a live UI through an event bus (``IdeaTree``/``await_user_decision``) for
the async research loop. The collection flow runs in a plain CLI (``asyncio.run`` from a
Typer command, the user sitting at the prompt), so a direct console round-trip is the right
fit. The console read is injectable (``ask=``) so the tool is testable without real stdin,
and it is only added to the toolset when stdin is a TTY — in non-interactive runs the agent
never has it and so never stalls.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..core.tools.base import Tool

# A console reader: (question, options) -> the user's answer, or None if they declined /
# no input is available. Injectable so tests don't touch real stdin.
AskFn = Callable[[str, list[str]], "str | None"]


def _console_ask(question: str, options: list[str]) -> str | None:
    """Default reader: print the question (and any options) and read one line of stdin."""
    import typer

    typer.secho("\n┃ the collection agent needs your input:", fg=typer.colors.MAGENTA, bold=True)
    typer.secho(f"┃ {question}", fg=typer.colors.MAGENTA)
    if options:
        for i, opt in enumerate(options, 1):
            typer.echo(f"   {i}. {opt}")
        typer.echo("   (type a number to pick one, or write your own answer)")
    try:
        raw = input("your answer> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if options and raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(options):
            return options[idx - 1]
    return raw


class ConsoleAskUserTool(Tool):
    """Ask the human at the terminal for a decision, then return their answer."""

    name = "AskUser"
    description = (
        "Ask the human operator a question and wait for their answer at the terminal.\n\n"
        "Use this ONLY for a choice that is genuinely the user's to make and that you "
        "cannot settle from the repo, the paper, or your tools. The most important case: "
        "when a repo ships both its own proposed method (the SOTA system) and simpler "
        "baselines (direct generation, naive RAG, an earlier system), ask which one to "
        "treat as the baseline — the baseline is the starting point Arbor will optimize, "
        "not necessarily the repo's headline method. Do NOT use this for routine progress "
        "updates or decisions you can make yourself.\n\n"
        "If no answer comes back, you are told to proceed on your best assumption — never "
        "block waiting on a reply, and never ask the same thing twice."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask (be specific and self-contained).",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional suggested choices. Omit for a free-form answer.",
            },
        },
        "required": ["question"],
    }
    # Not read-only: serializing this in the agent loop keeps two questions from being
    # put to the human at once. The collection agents run with auto_git off, so no commit.
    is_read_only = False

    def __init__(self, *, cwd: str, workspace_dir: str | None = None, ask: AskFn | None = None):
        super().__init__(cwd=cwd, workspace_dir=workspace_dir)
        self._ask: AskFn = ask or _console_ask

    async def execute(self, **kwargs: Any) -> str:
        question = (kwargs.get("question") or "").strip()
        if not question:
            return "Error: 'question' is required."
        options = [str(o) for o in (kwargs.get("options") or [])]
        # The reader blocks on stdin; run it off the event loop.
        answer = await asyncio.to_thread(self._ask, question, options)
        if not answer or not answer.strip():
            return (
                "No answer was provided. Proceed with your best assumption and state it "
                "explicitly in your final output — do not ask this again."
            )
        return f"User replied: {answer.strip()}"
