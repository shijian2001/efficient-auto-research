"""Unit tests for the helpers extracted from `_run_single_executor`:
the dispatch pre-flight guard and the result-summary formatter.
"""

from __future__ import annotations

from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools.executor_run import (
    _format_executor_summary,
    _validate_dispatch,
)


# ── _format_executor_summary (pure) ──────────────────────────────────

def _summary(**overrides) -> str:
    base = dict(
        node_id="1.2",
        hypothesis="speed up the loop",
        new_status="done",
        attempt=1,
        score=45.2,
        insight="it helped",
        code_ref="exp/n1-abc",
        agent_turns=7,
        propagation_result="propagated up",
        raw_report="ran fine",
        eval_status="scored",
        stop_reason=None,
    )
    base.update(overrides)
    return _format_executor_summary(**base)


def test_summary_includes_core_fields() -> None:
    out = _summary()
    assert "Executor Result for 1.2" in out
    assert "speed up the loop" in out
    assert "done (attempt 1)" in out
    assert "45.2%" in out
    assert "`exp/n1-abc`" in out
    assert "propagated up" in out
    assert "ran fine" in out


def test_summary_score_na_when_none() -> None:
    assert "**Score**: N/A" in _summary(score=None)


def test_summary_needs_retry_adds_hint() -> None:
    out = _summary(new_status="needs_retry", score=None,
                   eval_status="failed_to_run", stop_reason="max_turns")
    assert "needs_retry" in out
    assert "ResumeExecutor(node_id='1.2')" in out
    assert "stop_reason=max_turns" in out


def test_summary_done_has_no_retry_hint() -> None:
    assert "ResumeExecutor" not in _summary()


def test_summary_truncates_long_report() -> None:
    out = _summary(raw_report="x" * 9000)
    assert "middle truncated" in out
    assert "full report was 9000 chars" in out


# ── _validate_dispatch ───────────────────────────────────────────────

def _tree(max_depth=None, **meta) -> IdeaTree:
    t = IdeaTree(Node(id="ROOT", parent_id=None, depth=0), max_depth=max_depth)
    t.meta.update(meta)
    return t


def test_validate_ok_for_pending_node() -> None:
    t = _tree()
    t.add_node(Node(id="1", parent_id="ROOT", depth=1, status="pending"))
    node, attempt, err = _validate_dispatch(t, "1", resume=False)
    assert err is None
    assert node is not None and node.id == "1"
    assert attempt == 1


def test_validate_resume_increments_attempt() -> None:
    t = _tree()
    t.add_node(Node(id="1", parent_id="ROOT", depth=1, status="needs_retry", attempt=2))
    _node, attempt, err = _validate_dispatch(t, "1", resume=True)
    assert err is None
    assert attempt == 3


def test_validate_gold_medal_early_stop() -> None:
    t = _tree(achieved_medal="gold")
    node, _attempt, err = _validate_dispatch(t, "1", resume=False)
    assert node is None
    assert err is not None and "Gold medal" in err


def test_validate_missing_node() -> None:
    t = _tree()
    node, _attempt, err = _validate_dispatch(t, "nope", resume=False)
    assert node is None
    assert err is not None and "not found" in err


def test_validate_wrong_status() -> None:
    t = _tree()
    t.add_node(Node(id="1", parent_id="ROOT", depth=1, status="done"))
    _node, _attempt, err = _validate_dispatch(t, "1", resume=False)
    assert err is not None and "status=" in err


def test_validate_non_leaf_rejected_when_max_depth_set() -> None:
    t = _tree(max_depth=2)
    t.add_node(Node(id="1", parent_id="ROOT", depth=1, status="pending"))  # depth 1 < 2
    _node, _attempt, err = _validate_dispatch(t, "1", resume=False)
    assert err is not None and "max_depth" in err
