"""Tests for the replay engine (``arbor replay``).

The dashboard is a pure projection of the event bus, so replay correctness is:
parsing ``events.jsonl`` faithfully, recovering identity/meta, and driving the
same ``RunState`` handlers the live dashboard wires up. We assert on the
resulting state rather than the terminal rendering, which is untestable here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbor.cli.replay import (
    _drive,
    _parse_events,
    demo_recording,
    load_recording,
    replay_recording,
    resolve_events_path,
)
from arbor.cli.run_dashboard import RunDashboard
from arbor.cli.run_state import RunState
from arbor.events.bus import EventBus


def _wire(state: RunState, bus: EventBus) -> None:
    """Subscribe the dashboard's RunState handlers without starting any threads.

    ``RunDashboard.__init__`` does no terminal work, and ``_wire_bus`` is the
    same registration the live path runs in ``__enter__`` — so this exercises the
    real wiring, just headless.
    """
    RunDashboard(state, bus, enable_input=False)._wire_bus()


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_skips_blank_malformed_and_gate_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"ts": 1.0, "type": "cycle.start", "data": {"cycle_num": 1}}),
            "   ",                                   # blank
            "{not json",                             # malformed
            json.dumps({"ts": 2.0, "type": "user.await", "data": {"node_id": "n1"}}),
            json.dumps({"ts": 2.5, "type": "user.input_received", "data": {}}),
            json.dumps(["not", "a", "dict"]),        # wrong shape
            json.dumps({"ts": 3.0, "type": "idea.proposed", "data": {"node_id": "n1"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    events = _parse_events(path)
    types = [t for _, t, _ in events]
    assert types == ["cycle.start", "idea.proposed"]   # gate + junk dropped


def test_parse_inherits_timestamp_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"ts": 5.0, "type": "cycle.start", "data": {}}),
            json.dumps({"type": "cycle.phase", "data": {"phase": "ideate"}}),  # no ts
        ]) + "\n",
        encoding="utf-8",
    )
    events = _parse_events(path)
    assert [ts for ts, _, _ in events] == [5.0, 5.0]


def test_resolve_events_path_accepts_dir_and_file(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")
    assert resolve_events_path(events) == events
    assert resolve_events_path(tmp_path) == events
    with pytest.raises(FileNotFoundError):
        resolve_events_path(tmp_path / "nope")


# ── identity / meta hydration ────────────────────────────────────────────────


def test_load_recording_recovers_identity_and_tree_meta(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text(
        "\n".join([
            json.dumps({"ts": 0.0, "type": "session.start",
                        "data": {"task": "go fast", "model": "claude-opus-4-8"}}),
            json.dumps({"ts": 0.4, "type": "cycle.start",
                        "data": {"cycle_num": 1, "total_cycles": 8}}),
        ]) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tree.json").write_text(
        json.dumps({"meta": {"metric_direction": "minimize", "baseline_score": 1.0}}),
        encoding="utf-8",
    )
    rec = load_recording(tmp_path)
    assert rec.model == "claude-opus-4-8"
    assert rec.task == "go fast"
    assert rec.total_cycles == 8
    assert rec.metric_direction == "minimize"
    assert rec.baseline_score == 1.0


def test_load_recording_warns_without_tree_json(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"ts": 0.0, "type": "cycle.start", "data": {}}) + "\n",
        encoding="utf-8",
    )
    rec = load_recording(tmp_path)
    assert rec.metric_direction == "maximize"          # default
    assert any("tree.json" in w for w in rec.meta_warnings)


def test_load_recording_rejects_empty(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_recording(tmp_path)


# ── driving the bus rebuilds dashboard state ─────────────────────────────────


def test_drive_rebuilds_state_from_events(tmp_path: Path) -> None:
    events = [
        (0.0, "idea.proposed", {"node_id": "a", "hypothesis": "h", "parent_id": "root"}),
        (1.0, "executor.start", {"node_id": "a", "branch": "arbor/a"}),
        (2.0, "idea.completed", {"node_id": "a", "score": 5.0, "status": "done"}),
        (3.0, "idea.merged", {"node_id": "a", "from_score": 1.0, "to_score": 5.0}),
        (4.0, "idea.proposed", {"node_id": "b", "hypothesis": "h2", "parent_id": "a"}),
        (5.0, "idea.pruned", {"node_id": "b", "reason": "worse"}),
    ]
    state = RunState()
    bus = EventBus()
    _wire(state, bus)
    _drive(bus, events, speed=1e9, max_gap_s=0.0)   # instant

    assert state.best_score == 5.0
    assert state.ideas_merged == 1
    assert state.ideas_pruned == 1
    assert state.ideas_proposed == 2
    assert state.ideas["a"].branch == "arbor/a"


# ── the bundled demo ─────────────────────────────────────────────────────────


def test_bundled_demo_loads_and_replays() -> None:
    rec = demo_recording()
    assert rec.run_name == "demo"
    assert rec.event_count > 50
    assert rec.baseline_score == 1.0
    assert rec.metric_direction == "maximize"

    state = RunState(metric_direction=rec.metric_direction)
    bus = EventBus()
    _wire(state, bus)
    _drive(bus, rec.events, speed=1e9, max_gap_s=0.0)

    # The demo trajectory ends on a 13.1× best with four merges and two prunes.
    assert state.best_score == pytest.approx(13.1)
    assert state.ideas_merged == 4
    assert state.ideas_pruned == 2


def test_replay_recording_runs_headless_without_tty() -> None:
    """End-to-end smoke: no TTY here, so the dashboard takes its Live fallback
    and the whole replay must complete without raising."""
    rec = demo_recording()
    reason = replay_recording(rec, speed=1e9, max_gap_s=0.0)
    assert reason == "ok"
