"""Unit tests for the extracted git-worktree lifecycle helpers.

Covers the pure naming helpers and an end-to-end create → finalize → remove
cycle against a real temporary git repository.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbor.coordinator.tools.worktree import (
    _compute_branch_name,
    _create_worktree,
    _finalize_worktree,
    _remove_worktree,
    _worktree_dir_name,
)


# ── _worktree_dir_name (pure) ────────────────────────────────────────

def test_worktree_dir_name_sanitizes_separators() -> None:
    assert _worktree_dir_name("exp/n1.2-foo") == "exp__n1_2-foo"


def test_worktree_dir_name_passthrough_when_short() -> None:
    assert _worktree_dir_name("simple-branch") == "simple-branch"


def test_worktree_dir_name_truncates_long_names_with_digest() -> None:
    long = "exp/" + "a" * 300
    out = _worktree_dir_name(long)
    # Bounded length, and disambiguated by a hash suffix so two different long
    # names can't collide after truncation.
    assert len(out) <= 180
    assert "__" in out
    assert out != _worktree_dir_name("exp/" + "a" * 299)


# ── _compute_branch_name (pure-ish) ──────────────────────────────────

def _cfg(prefix: str = "exp"):
    return SimpleNamespace(git_branch_prefix=prefix)


def test_compute_branch_name_uses_prefix_and_node() -> None:
    name = _compute_branch_name(_cfg("research"), "1.2", "speed up the loop")
    assert name.startswith("research/n")


def test_compute_branch_name_is_deterministic() -> None:
    a = _compute_branch_name(_cfg(), "1", "same hypothesis")
    b = _compute_branch_name(_cfg(), "1", "same hypothesis")
    assert a == b


def test_compute_branch_name_differs_by_hypothesis() -> None:
    a = _compute_branch_name(_cfg(), "1", "hypothesis A")
    b = _compute_branch_name(_cfg(), "1", "hypothesis B")
    assert a != b  # the trailing digest disambiguates


# ── Lifecycle against a real git repo ────────────────────────────────

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
)


def _init_repo(path: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True,
                       capture_output=True)
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("config", "commit.gpgsign", "false")
    (path / "README.md").write_text("hello\n")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")


@requires_git
def test_worktree_create_finalize_remove(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    branch = "exp/n1-test-deadbeef"

    async def scenario() -> None:
        wt_path, actual_branch = await _create_worktree(str(repo), branch)
        try:
            assert wt_path.exists()
            assert actual_branch == branch
            # The branch exists in the parent repo.
            branches = subprocess.run(
                ["git", "branch", "--list", branch], cwd=repo,
                capture_output=True, text=True,
            ).stdout
            assert branch in branches

            # A new file in the worktree is committed by _finalize_worktree.
            (wt_path / "result.txt").write_text("score=0.9\n")
            await _finalize_worktree(wt_path, "1")
            log = subprocess.run(
                ["git", "log", "--oneline", branch], cwd=repo,
                capture_output=True, text=True,
            ).stdout
            assert "finalize 1" in log
        finally:
            await _remove_worktree(str(repo), wt_path)
        assert not wt_path.exists()

    asyncio.run(scenario())


@requires_git
def test_worktree_create_collision_gets_suffixed(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    branch = "exp/n1-dup-cafebabe"

    async def scenario() -> None:
        wt1, b1 = await _create_worktree(str(repo), branch)
        wt2, b2 = await _create_worktree(str(repo), branch)
        try:
            # Second create hits an existing branch and falls back to a
            # timestamp-suffixed branch rather than failing.
            assert b1 == branch
            assert b2 != branch and b2.startswith(branch)
            assert wt1 != wt2
        finally:
            await _remove_worktree(str(repo), wt1)
            await _remove_worktree(str(repo), wt2)

    asyncio.run(scenario())
