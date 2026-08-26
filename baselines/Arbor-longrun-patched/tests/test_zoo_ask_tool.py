"""Tests for the console-backed AskUser tool used by the collection agents.

No LLM, no real stdin — the console reader is injected so the tool's contract (what it
returns to the agent for a given human answer) is exercised deterministically.
"""

from __future__ import annotations

import asyncio

from arbor.zoo import ConsoleAskUserTool


def _run(tool: ConsoleAskUserTool, **kwargs) -> str:
    return asyncio.run(tool.execute(**kwargs))


def test_returns_user_answer() -> None:
    tool = ConsoleAskUserTool(cwd=".", ask=lambda q, opts: "run_naive_rag.py")
    assert _run(tool, question="which baseline?") == "User replied: run_naive_rag.py"


def test_passes_question_and_options_to_reader() -> None:
    seen: dict = {}

    def ask(question: str, options: list[str]) -> str:
        seen["q"], seen["opts"] = question, options
        return options[0]

    tool = ConsoleAskUserTool(cwd=".", ask=ask)
    out = _run(tool, question="pick the baseline", options=["a.py", "b.py"])
    assert seen == {"q": "pick the baseline", "opts": ["a.py", "b.py"]}
    assert out == "User replied: a.py"


def test_no_answer_tells_agent_to_proceed() -> None:
    # User declined / EOF / non-interactive: the reader returns None.
    tool = ConsoleAskUserTool(cwd=".", ask=lambda q, opts: None)
    out = _run(tool, question="which baseline?")
    assert "best assumption" in out and "do not ask this again" in out


def test_blank_answer_treated_as_no_answer() -> None:
    tool = ConsoleAskUserTool(cwd=".", ask=lambda q, opts: "   ")
    assert "best assumption" in _run(tool, question="which baseline?")


def test_missing_question_errors() -> None:
    tool = ConsoleAskUserTool(cwd=".", ask=lambda q, opts: "x")
    assert _run(tool, question="  ").startswith("Error:")


def test_answer_is_stripped() -> None:
    tool = ConsoleAskUserTool(cwd=".", ask=lambda q, opts: "  run_direct_gen.py \n")
    assert _run(tool, question="which?") == "User replied: run_direct_gen.py"


def test_tool_schema_shape() -> None:
    tool = ConsoleAskUserTool(cwd=".")
    schema = tool.to_api_schema()
    assert schema["name"] == "AskUser"
    assert schema["input_schema"]["required"] == ["question"]
    assert tool.is_read_only is False
