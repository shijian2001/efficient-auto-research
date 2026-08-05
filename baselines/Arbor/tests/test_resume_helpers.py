"""Unit tests for the orchestrator's resume helpers."""

from __future__ import annotations

from arbor.coordinator.orchestrator import _resume_pending_user_note


def test_pending_note_empty_when_nothing_pending() -> None:
    assert _resume_pending_user_note(None) == ""
    assert _resume_pending_user_note({}) == ""


def test_pending_note_empty_without_prompt() -> None:
    # A payload with no human-facing prompt has nothing to surface.
    assert _resume_pending_user_note({"kind": "ask_back", "node_id": "1"}) == ""


def test_pending_note_includes_question_and_node() -> None:
    note = _resume_pending_user_note(
        {"kind": "ask_back", "prompt": "Which dataset split?", "node_id": "1.2"}
    )
    assert "Which dataset split?" in note
    assert "node 1.2" in note
    assert "AskUser" in note  # tells the agent how to re-ask


def test_pending_note_without_node_scope() -> None:
    note = _resume_pending_user_note({"prompt": "Proceed with plan B?", "node_id": ""})
    assert "Proceed with plan B?" in note
    assert "node" not in note.split(">")[0]  # no node scope in the lead-in


def test_pending_note_quotes_every_line() -> None:
    note = _resume_pending_user_note(
        {"prompt": "Which split?\nGive the full path.", "node_id": ""}
    )
    # Both lines stay inside the markdown blockquote.
    assert "> Which split?" in note
    assert "> Give the full path." in note
