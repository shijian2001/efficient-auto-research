from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from arbor.coordinator.tools.integrity import (
    apply_readonly,
    build_protected_manifest,
    clear_readonly,
    verify_protected_manifest,
)


def _make_tree(root: Path) -> None:
    (root / "data").mkdir(parents=True)
    (root / "data" / "train.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "data" / "test.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "model.py").write_text("print('hi')\n", encoding="utf-8")


def test_manifest_covers_only_protected_globs(tmp_path: Path):
    _make_tree(tmp_path)
    manifest = build_protected_manifest(tmp_path, ["data/**"])
    assert set(manifest) == {"data/train.csv", "data/test.csv"}
    assert all(len(h) == 64 for h in manifest.values())


def test_manifest_is_deterministic(tmp_path: Path):
    _make_tree(tmp_path)
    assert build_protected_manifest(tmp_path, ["data/**"]) == build_protected_manifest(
        tmp_path, ["data/**"]
    )


def test_verify_detects_modify_add_remove(tmp_path: Path):
    _make_tree(tmp_path)
    manifest = build_protected_manifest(tmp_path, ["data/**"])
    # modify
    (tmp_path / "data" / "train.csv").write_text("a,b\n9,9\n", encoding="utf-8")
    # add
    (tmp_path / "data" / "leak.csv").write_text("x\n", encoding="utf-8")
    # remove
    (tmp_path / "data" / "test.csv").unlink()
    changes = verify_protected_manifest(tmp_path, ["data/**"], manifest)
    by_path = {c.path: c.kind for c in changes}
    assert by_path == {
        "data/train.csv": "modified",
        "data/leak.csv": "added",
        "data/test.csv": "removed",
    }


def test_verify_clean_returns_empty(tmp_path: Path):
    _make_tree(tmp_path)
    manifest = build_protected_manifest(tmp_path, ["data/**"])
    assert verify_protected_manifest(tmp_path, ["data/**"], manifest) == []


def test_readonly_roundtrip_never_raises_and_restores(tmp_path: Path):
    _make_tree(tmp_path)
    apply_readonly(tmp_path, ["data/**"])  # must not raise
    clear_readonly(tmp_path, ["data/**"])  # must not raise
    # after clearing, the file is writable again
    (tmp_path / "data" / "train.csv").write_text("a,b\n5,5\n", encoding="utf-8")


def test_apply_readonly_on_missing_path_is_noop(tmp_path: Path):
    apply_readonly(tmp_path, ["does/not/exist/**"])  # must not raise
    clear_readonly(tmp_path, ["does/not/exist/**"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bit not meaningful on Windows")
def test_readonly_preserves_executable_bit(tmp_path: Path):
    # A protected executable (e.g. evaluation/grade.sh) must keep its +x bit so
    # git does not see a spurious mode change that would block the merge.
    (tmp_path / "evaluation").mkdir()
    script = tmp_path / "evaluation" / "grade.sh"
    script.write_text("#!/bin/sh\necho score: 1.0\n", encoding="utf-8")
    script.chmod(0o755)

    apply_readonly(tmp_path, ["evaluation/**"])
    mode = stat.S_IMODE(script.stat().st_mode)
    assert mode & stat.S_IXUSR  # exec bit preserved
    assert not (mode & stat.S_IWUSR)  # write removed

    clear_readonly(tmp_path, ["evaluation/**"])
    mode = stat.S_IMODE(script.stat().st_mode)
    assert mode & stat.S_IXUSR  # still executable
    assert mode & stat.S_IWUSR  # writable again
