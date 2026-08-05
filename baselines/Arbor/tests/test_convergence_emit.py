"""Unit tests for convergence reaction + the CONVERGENCE_REACHED emission."""

from __future__ import annotations

from types import SimpleNamespace

from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools.executor_run import _react_convergence
from arbor.events.types import CONVERGENCE_REACHED


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, etype: str, data: dict) -> None:
        self.events.append((etype, data))


class _FakeDetector:
    def __init__(self) -> None:
        self.stop_written = False

    def format_intervention(self, signal) -> str:
        return f"INTERVENTION[{signal.level}]"

    def write_stop_signal(self, workspace_dir) -> None:
        self.stop_written = True


def _signal(level: str, reason: str = "plateau detected"):
    return SimpleNamespace(level=level, reason=reason)


def _tree_with_bus(*, done_score: float | None = None, trunk_score=None) -> IdeaTree:
    t = IdeaTree(Node(id="ROOT", parent_id=None, depth=0))
    if trunk_score is not None:
        t.meta["trunk_score"] = trunk_score
    if done_score is not None:
        t.add_node(Node(id="1", parent_id="ROOT", depth=1, status="done", score=done_score))
    # Attach the recording bus AFTER setup so add_node's IDEA_PROPOSED isn't
    # captured — we only want to observe what _react_convergence emits.
    t.bus = _RecordingBus()
    return t


_cfg = SimpleNamespace(workspace_dir="/tmp/ws")


def test_no_signal_is_noop() -> None:
    tree = _tree_with_bus()
    det = _FakeDetector()
    out = _react_convergence(det, tree, _cfg, "RESULT", None)
    assert out == "RESULT"
    assert tree.bus.events == []
    assert det.stop_written is False


def test_warn_signal_appends_but_does_not_emit() -> None:
    tree = _tree_with_bus()
    det = _FakeDetector()
    out = _react_convergence(det, tree, _cfg, "RESULT", _signal("warn"))
    assert "INTERVENTION[warn]" in out
    assert tree.bus.events == []          # warn is not "reached convergence"
    assert det.stop_written is False


def test_stop_signal_emits_convergence_reached() -> None:
    tree = _tree_with_bus(done_score=42.0)
    det = _FakeDetector()
    out = _react_convergence(det, tree, _cfg, "RESULT", _signal("stop", reason="velocity flat"))
    assert "INTERVENTION[stop]" in out
    assert det.stop_written is True       # hard stop writes the stop signal
    assert len(tree.bus.events) == 1
    etype, data = tree.bus.events[0]
    assert etype == CONVERGENCE_REACHED
    assert data["reason"] == "velocity flat"
    assert data["final_score"] == 42.0    # best done node's score


def test_stop_final_score_falls_back_to_trunk() -> None:
    tree = _tree_with_bus(trunk_score=7.5)   # no done nodes
    out = _react_convergence(_FakeDetector(), tree, _cfg, "R", _signal("stop"))
    assert "INTERVENTION[stop]" in out
    assert tree.bus.events[0][1]["final_score"] == 7.5
