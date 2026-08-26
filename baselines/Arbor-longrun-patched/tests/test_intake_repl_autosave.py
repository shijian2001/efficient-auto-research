"""Integration tests for intake conversation autosave + --continue.

Drives ``run_intake`` with the LLM call and terminal input stubbed, so we can
assert the REPL persists the conversation each turn and that ``--continue``
reloads the newest unfinished one and keeps appending to the same record.
"""

from __future__ import annotations

import asyncio

from arbor.cli.intake import repl
from arbor.cli.intake.conversation_store import find_conversations, load_messages


class _FakeProvider:
    model = "fake-model"
    base_url = None

    async def create(self, **_kw):  # pragma: no cover - Agent.run is stubbed
        raise AssertionError("provider.create should not be called; Agent.run is stubbed")

    def count_tokens(self, text: str) -> int:
        return len(text.split())


async def _fake_agent_run(self, user_message: str) -> str:
    """Append a user + assistant turn, like the real loop would, minus the LLM."""
    self.messages.append({"role": "user", "content": user_message})
    self.messages.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
    return "ok"


def _scripted_reader(inputs):
    queue = list(inputs)

    async def _reader(_session):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return _reader


class _NoopDisplay:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, cwd, inputs, *, continue_latest=False):
    monkeypatch.setattr(repl.Agent, "run", _fake_agent_run)
    monkeypatch.setattr(repl, "_read_user_line", _scripted_reader(inputs))
    monkeypatch.setattr(repl, "_build_session", lambda *a, **k: None)
    monkeypatch.setattr(repl, "_print_welcome", lambda *a, **k: None)
    monkeypatch.setattr(repl, "IntakeDisplay", _NoopDisplay)
    return asyncio.run(
        repl.run_intake(
            provider=_FakeProvider(),
            starting_cwd=cwd,
            seed_message=None,
            continue_latest=continue_latest,
        )
    )


def test_intake_autosaves_conversation_each_turn(tmp_path, monkeypatch):
    outcome = _run(monkeypatch, tmp_path, ["hello arbor", "/quit"])
    assert outcome is None  # /quit aborts without launching

    convs = find_conversations(tmp_path)
    assert len(convs) == 1
    rec = convs[0]
    assert rec.launched is False
    assert rec.title.startswith("hello arbor")
    user_msgs = [m.get("content") for m in load_messages(rec) if m.get("role") == "user"]
    assert "hello arbor" in user_msgs


def test_continue_reloads_and_appends_to_same_conversation(tmp_path, monkeypatch):
    _run(monkeypatch, tmp_path, ["first message", "/quit"])
    _run(monkeypatch, tmp_path, ["second message", "/quit"], continue_latest=True)

    convs = find_conversations(tmp_path)
    assert len(convs) == 1  # continued the existing record, did not fork a new one
    user_msgs = [m.get("content") for m in load_messages(convs[0]) if m.get("role") == "user"]
    assert "first message" in user_msgs
    assert "second message" in user_msgs


def test_continue_with_no_history_starts_fresh(tmp_path, monkeypatch):
    # --continue with nothing to continue must not crash; it starts a fresh chat.
    outcome = _run(monkeypatch, tmp_path, ["only message", "/quit"], continue_latest=True)
    assert outcome is None
    convs = find_conversations(tmp_path)
    assert len(convs) == 1


def test_visible_text_extracts_prose_and_skips_tool_plumbing():
    assert repl._visible_text("hi there") == "hi there"
    assert repl._visible_text([{"type": "text", "text": "a"}, {"type": "tool_use", "name": "Bash"}]) == "a"
    assert repl._visible_text([{"type": "tool_result", "content": "internal"}]) == ""
    assert repl._visible_text(None) == ""


def _capture_history(monkeypatch, messages):
    import io

    from rich.console import Console

    buf = io.StringIO()
    monkeypatch.setattr(repl, "_console", Console(file=buf, width=200, soft_wrap=True))
    repl._print_resumed_history(messages)
    return buf.getvalue()


def test_print_resumed_history_replays_user_and_assistant(monkeypatch):
    out = _capture_history(
        monkeypatch,
        [
            {"role": "user", "content": "summarize the paper"},
            {"role": "assistant", "content": [{"type": "text", "text": "Here is the summary"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "internal"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        ],
    )
    assert "summarize the paper" in out
    assert "Here is the summary" in out
    assert "previous conversation" in out
    assert "internal" not in out  # tool_result is plumbing, not shown


def test_print_resumed_history_empty_is_silent(monkeypatch):
    assert _capture_history(monkeypatch, []) == ""
