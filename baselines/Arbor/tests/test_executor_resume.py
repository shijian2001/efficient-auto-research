"""Unit tests for executor resume/retry status correctness.

The repo ships no test harness, so this file is self-contained: run it directly
with any Python 3.10+ that can import the ``arbor`` package
(``python tests/test_executor_resume.py``), or collect it with pytest. It maps
the ``arbor`` package onto ``src/`` itself so no install step is required.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if "arbor" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "arbor", _ROOT / "src" / "__init__.py",
        submodule_search_locations=[str(_ROOT / "src")],
    )
    _arbor = importlib.util.module_from_spec(_spec)
    sys.modules["arbor"] = _arbor
    _spec.loader.exec_module(_arbor)

from arbor.coordinator.idea_tree import Node  # noqa: E402
from arbor.coordinator.tools.executor_run import (  # noqa: E402
    _CYCLE_STATUSES,
    _classify_executor_outcome,
)


def test_needs_retry_consumes_a_cycle() -> None:
    # A timed-out/errored attempt spent compute, so it must spend budget —
    # otherwise a perpetually-failing node never hits max_cycles.
    assert "needs_retry" in _CYCLE_STATUSES


def test_classifier_table() -> None:
    cases = [
        # A real metric is "done" even on a late stop or a contradictory eval_status.
        (dict(score=45.2, eval_status="scored", stop_reason="finished", raw_report="ok"), "done"),
        (dict(score=0.0, eval_status="scored", stop_reason="max_turns", raw_report="ok"), "done"),
        (dict(score=12.0, eval_status="failed_to_run", stop_reason="finished", raw_report="ok"), "done"),
        # No score + turn cap / timeout / error → needs_retry.
        (dict(score=None, eval_status="failed_to_run", stop_reason="max_turns", raw_report="x"), "needs_retry"),
        (dict(score=None, eval_status="scored", stop_reason=None, raw_report="[Timed out after 1s]"), "needs_retry"),
        (dict(score=None, eval_status="scored", stop_reason=None, raw_report="[Error: boom]"), "needs_retry"),
        # Intentional skip on solid work is an acceptable terminal "done".
        (dict(score=None, eval_status="skipped", stop_reason="finished", raw_report="impl done, eval skipped"), "done"),
        # ...but a skip that also ran out of turns is conservative needs_retry
        # (max_turns is checked before skipped — the work may be incomplete).
        (dict(score=None, eval_status="skipped", stop_reason="max_turns", raw_report="skipped, then ran out"), "needs_retry"),
        # Unparseable / eval crashed with a normal finish → needs_retry.
        (dict(score=None, eval_status="failed_to_run", stop_reason="finished", raw_report="garbage"), "needs_retry"),
        # A bool is not a numeric score.
        (dict(score=True, eval_status="scored", stop_reason="finished", raw_report="ok"), "needs_retry"),
    ]
    for kw, expected in cases:
        got = _classify_executor_outcome(**kw)
        assert got == expected, f"{kw} -> {got!r}, expected {expected!r}"


def test_node_roundtrip_new_fields() -> None:
    n = Node(
        id="1", parent_id="ROOT", status="needs_retry", score=None,
        eval_status="failed_to_run", stop_reason="max_turns", attempt=2, code_ref="br",
    )
    d = n.to_dict()
    assert d["status"] == "needs_retry"
    assert d["eval_status"] == "failed_to_run"
    assert d["stop_reason"] == "max_turns"
    assert d["attempt"] == 2

    n2 = Node.from_dict(d)
    assert (n2.status, n2.eval_status, n2.stop_reason, n2.attempt) == (
        "needs_retry", "failed_to_run", "max_turns", 2,
    )


def test_node_backcompat_defaults() -> None:
    # Old tree JSON lacks the new keys — they default, and to_dict omits them
    # so existing readers and diffs stay clean.
    old = {"id": "2", "parent_id": "ROOT", "status": "done", "score": 5.0}
    o = Node.from_dict(old)
    assert o.attempt == 1
    assert o.eval_status is None
    assert o.stop_reason is None
    d = o.to_dict()
    assert "attempt" not in d
    assert "eval_status" not in d
    assert "stop_reason" not in d


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
