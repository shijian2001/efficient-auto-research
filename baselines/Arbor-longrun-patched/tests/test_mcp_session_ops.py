"""Tests for the deterministic Arbor MCP session operations.

These exercise the real ``arbor.mcp.session_ops`` engine against a throwaway git
repo. They never touch an LLM or require the MCP SDK. Worktree-dependent paths
are guarded by :func:`_git_worktree_works` so a host whose ``git worktree`` is
broken (some sandboxed Windows setups) skips rather than false-fails; they still
run on Linux CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from arbor.mcp import session_ops as ops


# ── fixtures / helpers ────────────────────────────────────────────────────────


def _init_repo(root: Path) -> Path:
    """Create a git repo on a non-protected ``trunk`` branch with one commit."""
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ops.git(repo, "init", "-q")
    ops.git(repo, "config", "user.email", "test@example.com")
    ops.git(repo, "config", "user.name", "Test")
    ops.git(repo, "checkout", "-q", "-b", "trunk")
    (repo / "model.py").write_text("v = 1\n", encoding="utf-8")
    ops.git(repo, "add", "-A")
    ops.git(repo, "commit", "-qm", "init")
    return repo


def _make_branch(repo: Path, branch: str, *, content: str, add_output: str | None = None) -> None:
    """Create *branch* off trunk with a change (and optional new output file)."""
    ops.git(repo, "checkout", "-q", "-b", branch, "trunk")
    (repo / "model.py").write_text(content, encoding="utf-8")
    if add_output:
        (repo / add_output).write_text("done\n", encoding="utf-8")
    ops.git(repo, "add", "-A")
    ops.git(repo, "commit", "-qm", f"work on {branch}")
    ops.git(repo, "checkout", "-q", "trunk")


def _git_worktree_works(tmp_path: Path) -> bool:
    """Probe whether ``git worktree add`` actually works in this environment."""
    repo = _init_repo(tmp_path / "probe")
    try:
        res = ops.worktree_create(repo, "probe_run", node_id="DEV", branch="probe/wt")
        ops.worktree_remove(repo, res["worktree"])
        return True
    except Exception:
        return False


# ── Idea Tree ops ─────────────────────────────────────────────────────────────


def test_init_and_add_nodes_assigns_hierarchical_ids(tmp_path: Path) -> None:
    cwd = tmp_path

    first = ops.tree_add_node(cwd, "r", "ROOT", "first idea")
    second = ops.tree_add_node(cwd, "r", "ROOT", "second idea")
    child = ops.tree_add_node(cwd, "r", first["node_id"], "refinement")

    assert first["node_id"] == "1" and first["depth"] == 1
    assert second["node_id"] == "2"
    assert child["node_id"] == "1.1" and child["depth"] == 2
    # Persisted to the canonical session location.
    assert (cwd / ".arbor" / "sessions" / "r" / ".coordinator" / "idea_tree.json").is_file()


def test_update_node_drops_none_and_persists_score(tmp_path: Path) -> None:
    ops.tree_add_node(tmp_path, "r", "ROOT", "idea")
    out = ops.tree_update_node(tmp_path, "r", "1", status="done", score=0.42, insight=None)

    assert out["updated"] == ["score", "status"]  # insight=None was dropped
    tree = ops.load_or_init_tree(tmp_path, "r")
    node = tree.get_node("1")
    assert node is not None and node.status == "done" and node.score == 0.42


def test_mutations_write_activity_sidecar(tmp_path: Path) -> None:
    """Tree mutations record an activity sidecar (run clock + per-node timing +
    event log) so the keyless WebUI has timing to render."""
    from arbor.mcp import session_timing

    ops.tree_add_node(tmp_path, "r", "ROOT", "an idea")
    ops.tree_update_node(tmp_path, "r", "1", status="done", score=0.9)

    sidecar = session_timing.load(ops.coordinator_dir(tmp_path, "r"))
    assert isinstance(sidecar.get("session_started_at"), (int, float))
    assert sidecar["nodes"]["1"].get("created_at") is not None
    assert sidecar["nodes"]["1"].get("finished_at") is not None
    kinds = [e["kind"] for e in sidecar["events"]]
    assert kinds == ["proposed", "done"]


def test_stable_base_unchanged_outside_linked_worktree(tmp_path: Path) -> None:
    """A non-git directory (and a normal checkout) is never redirected, so the
    canonical ``<cwd>/.arbor/sessions`` layout is preserved."""
    assert ops._stable_arbor_base(tmp_path) == tmp_path.resolve()


def test_stable_base_redirects_from_linked_worktree(tmp_path: Path) -> None:
    """Inside a linked git worktree, ``.arbor`` anchors at the MAIN worktree root
    so a file-backed session doesn't vanish when the agent switches worktrees."""
    if not _git_worktree_works(tmp_path / "_probe"):
        pytest.skip("git worktree not functional in this environment")
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    res = ops.worktree_create(repo, "r", "1", branch="exp/wt")
    wt = Path(res["worktree"])
    assert wt.resolve() != repo.resolve()                 # genuinely a linked worktree
    assert ops._stable_arbor_base(wt) == repo.resolve()   # redirected to the main root
    # …and the main checkout itself is still left unchanged.
    assert ops._stable_arbor_base(repo) == repo.resolve()


def test_prune_marks_subtree(tmp_path: Path) -> None:
    ops.tree_add_node(tmp_path, "r", "ROOT", "idea")
    ops.tree_add_node(tmp_path, "r", "1", "child")
    ops.tree_prune(tmp_path, "r", "1", reason="dead end")

    tree = ops.load_or_init_tree(tmp_path, "r")
    assert tree.get_node("1").status == "pruned"
    assert tree.get_node("1.1").status == "pruned"  # subtree pruned too


def test_set_meta_merges_and_ignores_none(tmp_path: Path) -> None:
    ops.tree_set_meta(tmp_path, "r", baseline_score=1.5, metric_direction="minimize", eval_cmd=None)
    tree = ops.load_or_init_tree(tmp_path, "r")
    assert tree.meta["baseline_score"] == 1.5
    assert tree.meta["metric_direction"] == "minimize"


def test_update_unknown_node_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ops.tree_update_node(tmp_path, "r", "999", status="done")


# ── score parsing / eval ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"score": 0.83}', 0.83),
        ('{"accuracy": 0.9}', 90.0),    # JSON fraction accuracy rescaled to percent
        ("primary_score = 12.5", 12.5),
        ("accuracy = 0.88", 0.88),      # regex branch: no rescale (faithful port)
        ("final result 73%", 73.0),
        ("no numbers here", None),
    ],
)
def test_parse_score(text: str, expected: float | None) -> None:
    assert ops.parse_score(text) == expected


def test_eval_run_detaches_stdin_and_does_not_hang(tmp_path: Path) -> None:
    # Reproduces the MCP-server stall: a command that reads stdin must not block.
    # With stdin detached (DEVNULL) the read returns EOF immediately instead of
    # waiting forever on the server's stdio channel. The short timeout bounds a
    # regression so it fails fast rather than hanging the suite.
    py = sys.executable.replace("\\", "/")
    cmd = f'"{py}" -c "import sys; sys.stdin.read(); print(\'score=1.0\')"'
    out = ops.eval_run(tmp_path, "r", cmd, split="dev", timeout=30)
    assert out["timed_out"] is False
    assert out["returncode"] == 0
    assert out["score"] == 1.0


def test_eval_run_records_score_and_writes_log(tmp_path: Path) -> None:
    out = ops.eval_run(tmp_path, "r", "echo score=0.85", split="dev", set_meta="baseline")

    assert out["returncode"] == 0 and out["timed_out"] is False
    assert out["score"] == 0.85
    assert Path(out["log_path"]).is_file()
    tree = ops.load_or_init_tree(tmp_path, "r")
    assert tree.meta["baseline_score"] == 0.85
    assert tree.meta["trunk_score"] == 0.85  # baseline seeds trunk
    assert tree.meta["eval_cmd"] == "echo score=0.85"


# ── guarded merge (worktree-free guards) ──────────────────────────────────────


def test_merge_rejects_protected_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    with pytest.raises(ValueError, match="protected branch"):
        ops.git_merge_branch(repo, "r", "1", "exp/x", target_branch="main", test_score=1.0)


def test_merge_rejects_non_improvement(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    ops.tree_set_meta(repo, "r", test_trunk_score=0.9, metric_direction="maximize")
    with pytest.raises(ValueError, match="not better"):
        ops.git_merge_branch(repo, "r", "1", "exp/x", target_branch="trunk", test_score=0.5)


def test_merge_rejects_protected_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_branch(repo, "exp/x", content="v = 2\n")
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    with pytest.raises(ValueError, match="protected path"):
        ops.git_merge_branch(
            repo, "r", "1", "exp/x", target_branch="trunk",
            test_score=1.0, protected_paths=["model.py"],
        )


def test_merge_protected_path_check_fails_closed_on_bad_diff(tmp_path: Path) -> None:
    # If the changed-file diff can't be computed (e.g. an unknown source branch),
    # the protected-path guard must refuse the merge rather than silently skip it.
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    with pytest.raises(RuntimeError, match="protected-path check"):
        ops.git_merge_branch(
            repo, "r", "1", "exp/does-not-exist", target_branch="trunk",
            test_score=1.0, protected_paths=["model.py"],
        )


def test_merge_rejects_missing_required_output(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_branch(repo, "exp/x", content="v = 2\n")
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    with pytest.raises(ValueError, match="required output missing"):
        ops.git_merge_branch(
            repo, "r", "1", "exp/x", target_branch="trunk",
            test_score=1.0, required_outputs=["submission.csv"],
        )


def test_merge_success_updates_tree_and_meta(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_branch(repo, "exp/win", content="v = 2\n", add_output="submission.csv")
    ops.tree_add_node(repo, "r", "ROOT", "winning idea")
    ops.tree_set_meta(repo, "r", test_baseline_score=0.5, metric_direction="maximize")

    out = ops.git_merge_branch(
        repo, "r", "1", "exp/win", target_branch="trunk",
        test_score=0.9, required_outputs=["submission.csv"],
    )

    assert out["merged"] == "exp/win" and out["test_score"] == 0.9
    tree = ops.load_or_init_tree(repo, "r")
    assert tree.get_node("1").status == "merged"
    assert tree.meta["test_trunk_score"] == 0.9
    # The change actually landed on trunk.
    assert (repo / "submission.csv").exists()


def test_merge_dry_run_does_not_merge(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_branch(repo, "exp/x", content="v = 2\n")
    ops.tree_add_node(repo, "r", "ROOT", "idea")
    out = ops.git_merge_branch(
        repo, "r", "1", "exp/x", target_branch="trunk", test_score=0.9, dry_run=True,
    )
    assert out["dry_run"] is True
    # trunk is untouched.
    rc, log = ops.git(repo, "log", "--oneline")
    assert "exp/x" not in log


# ── report ────────────────────────────────────────────────────────────────────


def test_generate_session_report_writes_file(tmp_path: Path) -> None:
    ops.tree_add_node(tmp_path, "r", "ROOT", "idea")
    ops.tree_update_node(tmp_path, "r", "1", status="done", score=0.7)
    out = ops.generate_session_report(tmp_path, "r")
    assert Path(out["report_path"]).name == "REPORT.md"
    assert Path(out["report_path"]).is_file()


# ── worktree (environment-guarded) ────────────────────────────────────────────


def test_worktree_create_and_remove(tmp_path: Path) -> None:
    if not _git_worktree_works(tmp_path / "_probe"):
        pytest.skip("git worktree not functional in this environment")
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")

    res = ops.worktree_create(repo, "r", "1", branch="exp/wt")
    assert Path(res["worktree"]).is_dir()
    tree = ops.load_or_init_tree(repo, "r")
    assert tree.get_node("1").status == "running"
    assert tree.get_node("1").code_ref == "exp/wt"

    rm = ops.worktree_remove(repo, res["worktree"])
    assert rm["returncode"] == 0


def test_worktree_create_recovers_when_branch_already_exists(tmp_path: Path) -> None:
    # worktree_remove preserves the branch (for later merging), so iterating on
    # the same node must not fail on the deterministic branch name — it should
    # recover under a suffixed branch rather than raising.
    if not _git_worktree_works(tmp_path / "_probe"):
        pytest.skip("git worktree not functional in this environment")
    repo = _init_repo(tmp_path)
    ops.tree_add_node(repo, "r", "ROOT", "idea")

    first = ops.worktree_create(repo, "r", "1", branch="exp/dup")
    ops.worktree_remove(repo, first["worktree"])  # branch 'exp/dup' is preserved

    second = ops.worktree_create(repo, "r", "1", branch="exp/dup")
    assert second["branch"] != "exp/dup"  # recovered under a unique suffix
    assert Path(second["worktree"]).is_dir()
    ops.worktree_remove(repo, second["worktree"])


# ── path-boundary hardening (security review follow-up) ───────────────────────


def test_session_dir_rejects_path_traversal(tmp_path: Path) -> None:
    """A crafted run_name must never escape ``<cwd>/.arbor/sessions/``."""
    sessions = (tmp_path / ".arbor" / "sessions").resolve()
    for bad in ("../../etc", "/etc/passwd", "..", ".", "a/../../b", "....//"):
        resolved = ops.session_dir(tmp_path, bad).resolve()
        assert sessions in resolved.parents, f"{bad!r} escaped to {resolved}"
    # A normal name is preserved verbatim.
    assert ops.session_dir(tmp_path, "my-run_1.2").name == "my-run_1.2"


def test_worktree_remove_refuses_paths_outside_scratch(tmp_path: Path) -> None:
    """worktree_remove must not rmtree a path outside the worktree scratch root."""
    victim = tmp_path / "precious"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        ops.worktree_remove(tmp_path, victim)

    assert victim.is_dir() and (victim / "keep.txt").exists()  # untouched


# ── scaffold_benchmark ────────────────────────────────────────────────────────


def test_scaffold_benchmark_light(tmp_path):
    out = ops.scaffold_benchmark(tmp_path, name="demo", style="light")
    assert "eval.py" in out["created"]
    assert out["verify"] == []
    assert out["git_committed"] is False
    assert isinstance(out["next_steps"], list)


def test_scaffold_benchmark_zoo_with_git_init(tmp_path):
    out = ops.scaffold_benchmark(tmp_path, name="demo", style="zoo", git_init=True)
    assert out["git_committed"] is True
    assert (tmp_path / ".git").exists()
    assert all(r["status"] != "fail" for r in out["verify"])
