"""Unit tests for the intake conversation store.

The store persists the *pre-launch intake conversation* (the chat you have with
``arbor`` before a research run is launched) so it can be auto-continued
(``arbor --continue``) or picked from ``/resume``. It reuses the same atomic
JSONL message IO as run checkpoints.
"""

from __future__ import annotations

import json

from arbor.cli.intake.conversation_store import (
    ConversationRecord,
    conversations_root,
    find_conversations,
    latest_unfinished,
    load_messages,
    new_conversation,
    save_conversation,
)


def _msgs(*user_texts: str) -> list[dict]:
    out: list[dict] = []
    for t in user_texts:
        out.append({"role": "user", "content": t})
        out.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]})
    return out


def test_new_conversation_is_not_written_until_saved(tmp_path):
    rec = new_conversation(tmp_path)
    assert isinstance(rec, ConversationRecord)
    assert rec.conv_id.startswith("conv_")
    assert rec.cwd == tmp_path
    # Nothing on disk yet.
    assert not rec.dir.exists()
    assert find_conversations(tmp_path) == []


def test_save_then_load_round_trips_messages(tmp_path):
    rec = new_conversation(tmp_path)
    messages = _msgs("first task", "second task")
    save_conversation(rec, messages)

    assert rec.messages_path.is_file()
    assert rec.meta_path.is_file()
    assert load_messages(rec) == messages


def test_save_redacts_tool_result_payloads(tmp_path):
    rec = new_conversation(tmp_path)
    messages = [
        {"role": "user", "content": "read the file"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "sensitive file body"}
            ],
        },
    ]

    save_conversation(rec, messages)

    persisted = load_messages(rec)
    assert "sensitive file body" not in rec.messages_path.read_text(encoding="utf-8")
    assert persisted[-1]["content"][0]["content"] == (
        "[tool result omitted from persisted intake history; "
        "ask the user to re-authorize the path before re-reading]"
    )


def test_save_redacts_context_summaries_that_may_contain_tool_data(tmp_path):
    rec = new_conversation(tmp_path)
    save_conversation(
        rec,
        [
            {"role": "user", "content": "read"},
            {
                "role": "user",
                "_internal": "context_summary",
                "content": "summary accidentally contains sensitive file body",
            },
        ],
    )

    raw = rec.messages_path.read_text(encoding="utf-8")
    assert "sensitive file body" not in raw
    assert load_messages(rec)[-1]["_internal"] == "context_summary"


def test_meta_records_title_turns_and_launched(tmp_path):
    rec = new_conversation(tmp_path)
    save_conversation(rec, _msgs("optimize the dev score please"), launched=False)

    meta = json.loads(rec.meta_path.read_text(encoding="utf-8"))
    assert meta["title"].startswith("optimize the dev score")
    assert meta["turns"] == 1            # one user message
    assert meta["launched"] is False
    assert meta["conv_id"] == rec.conv_id


def test_find_conversations_newest_first(tmp_path):
    a = new_conversation(tmp_path)
    save_conversation(a, _msgs("alpha"))
    b = new_conversation(tmp_path)
    save_conversation(b, _msgs("beta"))
    # Force a strictly newer update on `a`.
    save_conversation(a, _msgs("alpha", "alpha again"))

    found = find_conversations(tmp_path)
    assert [r.conv_id for r in found][0] == a.conv_id
    assert {r.conv_id for r in found} == {a.conv_id, b.conv_id}


def test_latest_unfinished_skips_launched_and_empty(tmp_path):
    # Launched conversation — excluded.
    launched = new_conversation(tmp_path)
    save_conversation(launched, _msgs("this one launched"), launched=True)

    # Unfinished with content — the expected pick.
    unfinished = new_conversation(tmp_path)
    save_conversation(unfinished, _msgs("still planning"), launched=False)

    pick = latest_unfinished(tmp_path)
    assert pick is not None
    assert pick.conv_id == unfinished.conv_id


def test_latest_unfinished_none_when_all_launched(tmp_path):
    rec = new_conversation(tmp_path)
    save_conversation(rec, _msgs("done"), launched=True)
    assert latest_unfinished(tmp_path) is None


def test_latest_unfinished_none_on_empty_dir(tmp_path):
    assert latest_unfinished(tmp_path) is None
    assert conversations_root(tmp_path) == tmp_path / ".arbor" / "conversations"


def test_save_is_atomic_and_meta_is_valid_json(tmp_path):
    rec = new_conversation(tmp_path)
    save_conversation(rec, _msgs("hello"))
    # No leftover temp files in the conversation dir.
    leftovers = [p.name for p in rec.dir.iterdir() if p.name.startswith(".")]
    assert leftovers == []
    json.loads(rec.meta_path.read_text(encoding="utf-8"))  # must parse


def test_corrupt_meta_is_skipped_not_fatal(tmp_path):
    good = new_conversation(tmp_path)
    save_conversation(good, _msgs("good one"))

    bad_dir = conversations_root(tmp_path) / "conv_broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "meta.json").write_text("{ not json", encoding="utf-8")

    found = find_conversations(tmp_path)
    assert {r.conv_id for r in found} == {good.conv_id}  # broken one skipped


def test_new_conversation_ids_are_unique(tmp_path):
    ids = set()
    for _ in range(5):
        rec = new_conversation(tmp_path)
        save_conversation(rec, _msgs("x"))
        ids.add(rec.conv_id)
    assert len(ids) == 5


def test_find_conversations_rejects_metadata_id_mismatch(tmp_path):
    rec = new_conversation(tmp_path)
    save_conversation(rec, _msgs("safe"))
    meta = json.loads(rec.meta_path.read_text(encoding="utf-8"))
    meta["conv_id"] = "../../outside"
    rec.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert find_conversations(tmp_path) == []


def test_find_conversations_rejects_symlinked_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text(
        json.dumps({"conv_id": "conv_20260711_000000"}),
        encoding="utf-8",
    )
    root = conversations_root(tmp_path)
    root.mkdir(parents=True)
    link = root / "conv_20260711_000000"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - platform permission edge
        import pytest
        pytest.skip(f"symlink unavailable: {exc}")

    assert find_conversations(tmp_path) == []
