from __future__ import annotations

import os
import stat
from pathlib import Path

from arbor.coordinator.tools.executor_run import _check_tamper, _guard_protected


class _Cfg:
    enforce_protected = True
    protected_paths: list[str] = []
    plugin = None


class _Bus:
    def __init__(self, sink):
        self._sink = sink

    def emit(self, name, payload):
        self._sink.append((name, payload))


class _Tree:
    def __init__(self):
        self.meta = {"protected_paths": ["data/**"]}
        self.events: list = []
        self.bus = _Bus(self.events)


def test_guard_then_clean_check_returns_no_changes(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "train.csv").write_text("a\n1\n", encoding="utf-8")
    cfg, tree = _Cfg(), _Tree()
    paths, manifest = _guard_protected(cfg, tree, tmp_path)
    assert paths == ["data/**"]
    changes = _check_tamper(cfg, tree, tmp_path, paths, manifest, "1", "branch-x")
    assert changes == []
    assert tree.events == []  # no tamper event on clean run


def test_tamper_is_detected_and_emitted(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "train.csv").write_text("a\n1\n", encoding="utf-8")
    cfg, tree = _Cfg(), _Tree()
    paths, manifest = _guard_protected(cfg, tree, tmp_path)
    # Simulate an executor that defeats the best-effort read-only attribute and
    # writes anyway; the manifest must still catch it.
    target = tmp_path / "data" / "train.csv"
    os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    target.write_text("a\n9999\n", encoding="utf-8")
    changes = _check_tamper(cfg, tree, tmp_path, paths, manifest, "1", "branch-x")
    assert [c.path for c in changes] == ["data/train.csv"]
    assert tree.events and tree.events[0][0] == "eval.protected_tamper"
    assert tree.events[0][1]["node_id"] == "1"


def test_disabled_enforcement_is_noop(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "train.csv").write_text("a\n1\n", encoding="utf-8")
    cfg, tree = _Cfg(), _Tree()
    cfg.enforce_protected = False
    paths, manifest = _guard_protected(cfg, tree, tmp_path)
    assert paths == [] and manifest == {}
