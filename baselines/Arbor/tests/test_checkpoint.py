"""Unit tests for the checkpoint IO that underpins --resume:
atomic write/read of the checkpoint and message history, schema-version
gating, and making an interrupted message tail safe to replay.
"""

from __future__ import annotations

import json

import pytest

from arbor.coordinator.checkpoint import (
    SCHEMA_VERSION,
    CacheAnchor,
    Checkpoint,
    GitState,
    InflightExecutor,
    UnsupportedCheckpointVersion,
    read_checkpoint,
    read_messages,
    seal_interrupted_tail,
    write_checkpoint,
    write_messages,
)


# ── Checkpoint round-trip ────────────────────────────────────────────

def _sample_checkpoint() -> Checkpoint:
    return Checkpoint(
        run_name="demo",
        cycle_num=4,
        phase="research",
        git=GitState(trunk_branch="trunk", active_branches=["exp/1"], worktrees=["/tmp/wt"]),
        inflight_executors=[InflightExecutor(node_id="1.2", branch="exp/1.2")],
        cache=CacheAnchor(stable_system_hash="abc123"),
        pending_user={"kind": "ask_back", "prompt": "Which dataset?", "node_id": "1", "options": []},
    )


def test_checkpoint_round_trip(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    cp = _sample_checkpoint()
    write_checkpoint(path, cp)

    loaded = read_checkpoint(path)
    assert loaded is not None
    assert loaded.run_name == "demo"
    assert loaded.cycle_num == 4
    assert loaded.git.trunk_branch == "trunk"
    assert loaded.inflight_executors[0].node_id == "1.2"
    assert loaded.cache.stable_system_hash == "abc123"
    assert loaded.pending_user["prompt"] == "Which dataset?"


def test_read_checkpoint_missing_returns_none(tmp_path) -> None:
    assert read_checkpoint(tmp_path / "nope.json") is None


def test_checkpoint_write_is_atomic_no_temp_left(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    write_checkpoint(path, _sample_checkpoint())
    # No leftover temp files from the atomic write.
    assert [p.name for p in tmp_path.iterdir()] == ["checkpoint.json"]


def test_read_checkpoint_corrupt_raises(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        read_checkpoint(path)


# ── Schema-version gating ────────────────────────────────────────────

def test_from_dict_rejects_newer_version() -> None:
    data = _sample_checkpoint().to_dict()
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(UnsupportedCheckpointVersion):
        Checkpoint.from_dict(data)


def test_from_dict_accepts_current_version() -> None:
    data = _sample_checkpoint().to_dict()
    assert Checkpoint.from_dict(data).run_name == "demo"


def test_from_dict_invalid_version_raises() -> None:
    data = _sample_checkpoint().to_dict()
    data["schema_version"] = "not-a-number"
    with pytest.raises(ValueError):
        Checkpoint.from_dict(data)


def test_from_dict_accepts_missing_version() -> None:
    # An unversioned dict is treated as compatible (backward compat).
    data = _sample_checkpoint().to_dict()
    del data["schema_version"]
    assert Checkpoint.from_dict(data).run_name == "demo"


# ── Message history round-trip ───────────────────────────────────────

def test_messages_round_trip(tmp_path) -> None:
    path = tmp_path / "messages.jsonl"
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    ]
    write_messages(path, msgs)
    assert read_messages(path) == msgs


def test_read_messages_missing_returns_empty(tmp_path) -> None:
    assert read_messages(tmp_path / "nope.jsonl") == []


def test_read_messages_skips_corrupt_line(tmp_path) -> None:
    path = tmp_path / "messages.jsonl"
    path.write_text(
        json.dumps({"role": "user", "content": "ok"}) + "\n"
        + "{ truncated half-written line\n"
        + json.dumps({"role": "assistant", "content": "fine"}) + "\n",
        encoding="utf-8",
    )
    out = read_messages(path)
    assert len(out) == 2  # the corrupt middle line is skipped, the rest survive
    assert out[0]["content"] == "ok"
    assert out[1]["content"] == "fine"


# ── seal_interrupted_tail ────────────────────────────────────────────

def test_seal_answers_dangling_tool_use() -> None:
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "RunExecutor", "input": {}},
        ]},
    ]
    sealed = seal_interrupted_tail(msgs)
    assert len(sealed) == 3
    last = sealed[-1]
    assert last["role"] == "user"
    result = last["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "t1"
    assert result["is_error"] is True


def test_seal_noop_when_tail_is_user() -> None:
    msgs = [{"role": "user", "content": "go"}]
    assert seal_interrupted_tail(msgs) is msgs


def test_seal_noop_when_assistant_has_no_tool_use() -> None:
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    assert seal_interrupted_tail(msgs) is msgs


def test_seal_answers_every_dangling_tool_use() -> None:
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "RunExecutor", "input": {}},
            {"type": "text", "text": "and also"},
            {"type": "tool_use", "id": "t2", "name": "SearchIdea", "input": {}},
        ]},
    ]
    results = seal_interrupted_tail(msgs)[-1]["content"]
    ids = [r["tool_use_id"] for r in results]
    assert ids == ["t1", "t2"]
    assert all(r["is_error"] for r in results)


def test_seal_empty() -> None:
    assert seal_interrupted_tail([]) == []
