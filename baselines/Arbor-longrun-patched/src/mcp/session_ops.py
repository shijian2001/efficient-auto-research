"""Deterministic, keyless Arbor session operations (the engine behind ``arbor mcp``).

Every function here is pure filesystem / git / subprocess work — **no LLM calls,
no API key**. The host coding agent supplies all reasoning and code edits; these
operations give it Arbor's durable state model and research discipline:

* **Idea Tree** state via the *real* :class:`arbor.coordinator.idea_tree.IdeaTree`
  (so the on-disk ``idea_tree.json`` is byte-compatible with the native runtime,
  the skill-suite fallback, and ``arbor export``).
* **Evaluation** that runs a command, extracts a score, logs the output, and can
  record it into tree metadata (B_dev / B_test discipline).
* **Worktrees** so each experiment is implemented on an isolated branch.
* **Guarded merges** that protect ``main``/``master`` and configured paths,
  re-verify on B_test, and require declared output files before merging.
* **Report** generation via the real :func:`generate_report`.

Session layout (matches the native runtime and the skill suite)::

    <cwd>/.arbor/sessions/<run_name>/
        .coordinator/idea_tree.json   # IdeaTree persistence (+ idea_tree.md)
        .coordinator/eval_logs/        # raw eval command output
        experiments/<node_id>/...      # per-experiment artifacts
        REPORT.md

Portability note: unlike the stdlib forward-test helper
(``skills/arbor-agent-tools/scripts/arbor_state.py``, which uses ``os.getuid()``),
this module is **cross-platform** — it derives temp-dir names from the username
so it also works on Windows, where ``os.getuid`` does not exist.
"""

from __future__ import annotations

import fnmatch
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ..coordinator.idea_tree import IdeaTree, Node
from ..report.generator import generate_report
from . import session_timing

# Branches we will never merge *into* — a hard safety rail for the trunk.
PROTECTED_BRANCHES = {"main", "master"}

# Score extraction, ported verbatim from the skill-suite forward-test helper so
# the two implementations agree on what counts as a "score" in eval output.
_SCORE_PATTERNS = [
    re.compile(r'"score"\s*:\s*([-+]?\d+(?:\.\d+)?)'),
    re.compile(r"\bval_bpb\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\bprimary_score\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\bscore\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\baccuracy\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\bacc\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\bf1\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"\bloss\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", re.I),
]


# ── Session path helpers ──────────────────────────────────────────────────────


def _safe_run_name(run_name: str) -> str:
    """Reduce a tool-supplied ``run_name`` to a single safe path component.

    The MCP tools take ``run_name`` from the host agent, so a value like
    ``../../etc`` or ``/abs/path`` must not let the session path escape
    ``<cwd>/.arbor/sessions/``. We map anything outside ``[A-Za-z0-9_.-]`` to
    ``-`` (this also neutralises ``/`` and ``\\``, so an absolute path can't reset
    the join) and strip leading dots so the result can never be ``.`` or ``..``.
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(run_name)).lstrip(".")
    return safe or "session"


def _stable_arbor_base(cwd: str | Path) -> Path:
    """Anchor the ``.arbor`` root at the *main* working tree, not a linked one.

    Host coding agents frequently drive a run from inside a linked git worktree
    (e.g. ``.claude/worktrees/<branch>``). Each worktree has its own working
    directory, so a session created under ``<worktree>/.arbor`` is invisible from
    the main checkout and appears to *vanish* the moment the agent switches
    branches/worktrees. To keep session state stable regardless of which worktree
    or branch is active, we resolve the main worktree root (the parent of the
    shared git common dir) and anchor ``.arbor`` there.

    Only linked worktrees are redirected: a normal single checkout (where the
    current toplevel already *is* the main worktree) and any non-git directory
    are returned unchanged, so existing behaviour is preserved.
    """
    base = Path(cwd).resolve()
    try:
        rc_top, top = git(base, "rev-parse", "--show-toplevel")
        if rc_top != 0 or not top:
            return base
        rc_common, common = git(base, "rev-parse", "--git-common-dir")
        if rc_common != 0 or not common:
            return base
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (base / common_path).resolve()
        main_root = common_path.parent.resolve()
        toplevel = Path(top).resolve()
        # Redirect only when we are inside a *linked* worktree (toplevel differs
        # from the main worktree root); otherwise keep the caller's directory. A
        # real main worktree root has a ``.git`` entry (dir or file). Inside a git
        # submodule, ``--git-common-dir`` points at the parent repo's
        # ``.git/modules/<name>`` whose parent is ``.git/modules`` — not a
        # worktree — so the ``.git`` check keeps us from anchoring there.
        if main_root != toplevel and (main_root / ".git").exists():
            return main_root
    except Exception:  # pragma: no cover - git absent / unexpected layout
        return base
    return base


def session_dir(cwd: str | Path, run_name: str) -> Path:
    """Return ``<base>/.arbor/sessions/<run_name>`` (the per-run session root).

    ``base`` is normally *cwd*, but is redirected to the main working tree when
    *cwd* is a linked git worktree (see :func:`_stable_arbor_base`) so the
    session — and the WebUI watching it — survive branch/worktree switches.
    """
    return _stable_arbor_base(cwd) / ".arbor" / "sessions" / _safe_run_name(run_name)


def coordinator_dir(cwd: str | Path, run_name: str) -> Path:
    """Return the coordinator state dir (``<session>/.coordinator``)."""
    return session_dir(cwd, run_name) / ".coordinator"


def _tree_paths(cwd: str | Path, run_name: str) -> tuple[Path, Path]:
    """Return ``(idea_tree.json, idea_tree.md)`` paths for the session."""
    coord = coordinator_dir(cwd, run_name)
    return coord / "idea_tree.json", coord / "idea_tree.md"


def _user_tag() -> str:
    """A filesystem-safe per-user tag for temp directories (cross-platform).

    Used instead of ``os.getuid()`` (POSIX-only) so worktree scratch dirs are
    namespaced per user on Windows as well.
    """
    try:
        name = getpass.getuser()
    except Exception:  # pragma: no cover - getuser can fail in odd sandboxes
        name = "arbor"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name) or "arbor"


# ── git / shell primitives ────────────────────────────────────────────────────


# Environment overlay for every git subprocess. ``GIT_TERMINAL_PROMPT=0`` makes
# git fail fast instead of blocking on an interactive credential/auth prompt —
# such a prompt would hang forever when launched from a stdio MCP server, which
# has no real terminal to answer it.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}


def git(cwd: str | Path, *argv: str) -> tuple[int, str]:
    """Run ``git`` in *cwd*; return ``(returncode, combined_output)`` (stripped).

    ``stdin`` is detached (``DEVNULL``) so the child never inherits — and blocks
    reading on — the server's stdio JSON-RPC channel, and ``GIT_TERMINAL_PROMPT``
    is disabled so git never stalls waiting for credentials. Both are required
    for the command to be safe to launch from inside an MCP stdio server.
    """
    proc = subprocess.run(
        ["git", *argv],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env={**os.environ, **_GIT_ENV},
    )
    return proc.returncode, proc.stdout.strip()


def _run_shell(cmd: str, cwd: str | Path, timeout: int) -> tuple[int, str, bool]:
    """Run *cmd* via the shell. Returns ``(returncode, output, timed_out)``.

    ``TERM=dumb`` keeps progress bars from spamming ANSI escapes into the log.
    On timeout the child is killed and whatever it printed so far is preserved.
    ``stdin`` is detached (``DEVNULL``) so a command that reads stdin gets EOF
    immediately rather than blocking forever on the MCP server's stdio channel.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        shell=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "TERM": "dumb"},
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode or 0, out, False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return -1, out + f"\n[timed out after {timeout}s]", True


def parse_score(text: str) -> float | None:
    """Best-effort numeric score extraction from arbitrary eval output.

    Strategy (first match wins):

    1. Any JSON object carrying ``score`` / ``primary_score`` / ``accuracy`` /
       ``acc`` (accuracy in ``[0, 1]`` is scaled to a percentage).
    2. A set of ``key: value`` / ``key=value`` regexes (score, f1, loss, …).
    3. A bare ``NN%`` percentage.
    """
    for match in re.finditer(r"\{[^{}]+\}", text):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        for key in ("score", "primary_score", "accuracy", "acc"):
            if isinstance(obj.get(key), (int, float)):
                value = float(obj[key])
                # Accuracy reported as a fraction is rescaled to a percentage so
                # all scores share one comparable unit.
                return value * 100.0 if key in {"accuracy", "acc"} and 0 <= value <= 1 else value
    for pattern in _SCORE_PATTERNS:
        hit = pattern.search(text)
        if hit:
            return float(hit.group(1))
    pct = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", text)
    if pct:
        return float(pct.group(1))
    return None


def _substitute(cmd: str, exec_cwd: Path, node_id: str) -> str:
    """Expand ``{cwd}`` / ``{node_id}`` placeholders in an eval command."""
    return cmd.replace("{cwd}", str(exec_cwd.resolve())).replace("{node_id}", node_id)


def _branch_name(prefix: str, node_id: str, hypothesis: str) -> str:
    """Deterministic, collision-resistant experiment branch name.

    ``<prefix>/n<node-id>-<hypothesis-slug>-<sha1[:8]>`` — the slug aids humans
    scanning ``git branch``; the hash disambiguates similar hypotheses.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", hypothesis.lower()).strip("-")[:32] or "idea"
    digest = hashlib.sha1(hypothesis.encode("utf-8")).hexdigest()[:8]
    safe_id = node_id.replace(".", "-")
    return f"{prefix}/n{safe_id}-{slug}-{digest}"


# ── Idea Tree operations (backed by the real IdeaTree) ────────────────────────


def load_or_init_tree(
    cwd: str | Path,
    run_name: str,
    *,
    task: str | None = None,
    max_depth: int | None = None,
) -> IdeaTree:
    """Load the session's Idea Tree, creating an empty one (ROOT) if absent.

    The returned tree has ``json_path``/``md_path`` wired to the session so any
    mutation persists automatically (``IdeaTree`` saves on every change).
    """
    json_path, md_path = _tree_paths(cwd, run_name)
    if json_path.exists():
        tree = IdeaTree.load_json(json_path)
        # ``load_json`` derives md_path from json_path; make it explicit/stable.
        tree.json_path = json_path
        tree.md_path = md_path
        return tree

    json_path.parent.mkdir(parents=True, exist_ok=True)
    root = Node(id="ROOT", parent_id=None, depth=0, hypothesis=task or "Research session")
    tree = IdeaTree(root=root, json_path=json_path, md_path=md_path, max_depth=max_depth)
    tree.save()
    return tree


def tree_view(cwd: str | Path, run_name: str, fmt: str = "compact") -> str:
    """Render the Idea Tree. ``fmt`` ∈ {compact, constraints, markdown}.

    * ``compact``     — terse overview for status checks.
    * ``constraints`` — the IDEATE pre-read (tree shape + prior insights).
    * ``markdown``    — full human-readable tree.
    """
    tree = load_or_init_tree(cwd, run_name)
    if fmt == "constraints":
        return tree.get_constraints_block()
    if fmt == "markdown":
        return tree.to_markdown()
    return tree.to_compact_summary()


def tree_add_node(
    cwd: str | Path,
    run_name: str,
    parent_id: str,
    hypothesis: str,
    *,
    status: str = "pending",
) -> dict[str, Any]:
    """Add a child idea under *parent_id*; returns the new node's id + depth.

    The id is auto-assigned by the real tree (ROOT→1,2,3; 1→1.1,1.2; …) and the
    write is persisted by ``IdeaTree.add_node``.
    """
    tree = load_or_init_tree(cwd, run_name)
    parent = tree.get_node(parent_id)
    if parent is None:
        raise ValueError(f"parent node {parent_id!r} not found")
    node_id = tree.next_child_id(parent_id)
    node = Node(
        id=node_id,
        parent_id=parent_id,
        depth=parent.depth + 1,
        hypothesis=hypothesis,
        status=status,  # type: ignore[arg-type]
    )
    tree.add_node(node)
    session_timing.record_event(
        coordinator_dir(cwd, run_name), node_id, "proposed", label=hypothesis,
    )
    return {"node_id": node_id, "depth": node.depth, "parent_id": parent_id}


def tree_update_node(cwd: str | Path, run_name: str, node_id: str, **fields: Any) -> dict[str, Any]:
    """Update whitelisted node fields (status/score/insight/result/code_ref/…).

    Only :data:`Node.MUTABLE_FIELDS` are accepted; ``None`` values are dropped so
    callers can pass a fixed kwarg set without clobbering existing data.
    """
    updates = {k: v for k, v in fields.items() if v is not None}
    tree = load_or_init_tree(cwd, run_name)
    if tree.get_node(node_id) is None:
        raise ValueError(f"node {node_id!r} not found")
    tree.update_node(node_id, **updates)
    new_status = updates.get("status")
    if new_status:
        node = tree.get_node(node_id)
        session_timing.record_event(
            coordinator_dir(cwd, run_name), node_id, str(new_status),
            label=(node.code_ref if node else None),
            score=updates.get("score", node.score if node else None),
        )
    return {"node_id": node_id, "updated": sorted(updates)}


def tree_prune(cwd: str | Path, run_name: str, node_id: str, reason: str = "") -> dict[str, Any]:
    """Prune a node and its subtree, recording an optional reason as insight."""
    tree = load_or_init_tree(cwd, run_name)
    if tree.get_node(node_id) is None:
        raise ValueError(f"node {node_id!r} not found")
    tree.prune_node(node_id, reason)
    session_timing.record_event(
        coordinator_dir(cwd, run_name), node_id, "pruned", label=reason or None,
    )
    return {"node_id": node_id, "status": "pruned", "reason": reason}


def tree_set_meta(cwd: str | Path, run_name: str, **meta: Any) -> dict[str, Any]:
    """Merge keys into tree metadata (baseline/trunk scores, eval cmds, …).

    ``None`` values are ignored so a caller can pass a uniform kwarg set and only
    set the fields it actually has.
    """
    updates = {k: v for k, v in meta.items() if v is not None}
    tree = load_or_init_tree(cwd, run_name)
    tree.meta.update(updates)
    tree.save()
    return {"meta_updated": sorted(updates)}


# ── Benchmark scaffolding ───────────────────────────────────────────────────────


def scaffold_benchmark(
    cwd: str | Path,
    *,
    name: str,
    metric_direction: str = "maximize",
    style: str = "light",
    split_kind: str = "seed_range",
    eval_entrypoint: str = "eval.py",
    edit: list[str] | None = None,
    git_init: bool = False,
) -> dict[str, Any]:
    """Create the Arbor reference folder under *cwd*; optionally git-init it.

    Pure filesystem + git work, no LLM. Returns created/skipped files, the zoo
    structural verify results (zoo style only), next-step hints, and whether a
    baseline commit was made.
    """
    from ..zoo import scaffold_benchmark as _scaffold

    target = Path(cwd)
    res = _scaffold(
        target, name=name, metric_direction=metric_direction, style=style,
        split_kind=split_kind, eval_entrypoint=eval_entrypoint, edit=edit,
    )
    committed = False
    if git_init and not (target / ".git").exists():
        git(target, "init")
        git(target, "add", "-A")
        # Commit with an ephemeral identity (-c) so the baseline commit succeeds on
        # machines/CI with no global git user configured, without mutating the
        # user's git config.
        rc, _ = git(
            target,
            "-c", "user.email=arbor@localhost", "-c", "user.name=Arbor",
            "commit", "-m", "baseline: scaffold Arbor benchmark structure",
        )
        committed = rc == 0
    return {
        "created": res.created,
        "skipped": res.skipped,
        "verify": [{"name": r.name, "status": r.status, "message": r.message}
                   for r in res.verify],
        "next_steps": res.next_steps,
        "git_committed": committed,
    }


# ── Evaluation ────────────────────────────────────────────────────────────────


def _apply_eval_meta(tree: IdeaTree, mode: str, score: float, cmd: str | None, split: str) -> None:
    """Record an eval score into tree metadata per *mode* (B_dev / B_test).

    Modes: ``baseline``/``trunk`` (B_dev) and ``test_baseline``/``test_trunk``
    (B_test). ``none`` records nothing. The first eval command seen for a split
    is remembered so later merges can re-verify on B_test automatically.
    """
    meta = tree.meta
    if mode == "baseline":
        meta["baseline_score"] = score
        meta.setdefault("trunk_score", None)
        if meta.get("trunk_score") is None:
            meta["trunk_score"] = score
        if cmd and not meta.get("eval_cmd"):
            meta["eval_cmd"] = cmd
    elif mode == "trunk":
        meta["trunk_score"] = score
        if cmd and not meta.get("eval_cmd"):
            meta["eval_cmd"] = cmd
    elif mode == "test_baseline":
        meta["test_baseline_score"] = score
        if meta.get("test_trunk_score") is None:
            meta["test_trunk_score"] = score
        if cmd and not meta.get("eval_cmd_test"):
            meta["eval_cmd_test"] = cmd
    elif mode == "test_trunk":
        meta["test_trunk_score"] = score
        if cmd and not meta.get("eval_cmd_test"):
            meta["eval_cmd_test"] = cmd
    elif mode != "none":
        raise ValueError(f"unknown set_meta mode: {mode}")
    # Remember the B_test command even when not recording a score, so merges can
    # re-run it for held-out verification.
    if split == "test" and cmd and not meta.get("eval_cmd_test"):
        meta["eval_cmd_test"] = cmd


def eval_run(
    cwd: str | Path,
    run_name: str,
    cmd: str,
    *,
    split: str = "dev",
    set_meta: str = "none",
    node_id: str | None = None,
    exec_cwd: str | Path | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run an evaluation command, extract a score, and log the raw output.

    Args:
        cmd: shell command; ``{cwd}`` and ``{node_id}`` are substituted.
        split: ``"dev"`` (B_dev, routine) or ``"test"`` (B_test, held-out).
        set_meta: where to record the score — see :func:`_apply_eval_meta`.
        node_id: experiment node (defaults to ``DEV``/``TEST`` by split).
        exec_cwd: directory to run in (defaults to *cwd*; e.g. a worktree).
        timeout: seconds (defaults to ``meta.eval_timeout`` or 2 h).

    Returns a dict with ``returncode``, ``timed_out``, ``score``, ``log_path``,
    the resolved ``command`` and ``exec_cwd``.
    """
    tree = load_or_init_tree(cwd, run_name)
    node_label = node_id or ("TEST" if split == "test" else "DEV")
    run_in = Path(exec_cwd or cwd).resolve()
    resolved = _substitute(cmd, run_in, node_label)
    eff_timeout = timeout or int(tree.meta.get("eval_timeout") or 7200)

    rc, output, timed_out = _run_shell(resolved, run_in, eff_timeout)

    # Persist the raw log so the host agent can inspect failures without
    # re-running, and so the score is auditable.
    log_dir = coordinator_dir(cwd, run_name) / "eval_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize the node label before using it as a filename component so a value
    # like "../x" cannot write the log outside eval_logs/.
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", node_label) or "node"
    log_path = log_dir / f"{split}_{safe_label}_{int(time.time())}.log"
    log_path.write_text(output, encoding="utf-8")

    score = parse_score(output)
    if score is not None and set_meta != "none":
        _apply_eval_meta(tree, set_meta, score, cmd, split)
        tree.save()

    return {
        "returncode": rc,
        "timed_out": timed_out,
        "score": score,
        "log_path": str(log_path),
        "command": resolved,
        "exec_cwd": str(run_in),
    }


# ── Worktrees ─────────────────────────────────────────────────────────────────


def _worktree_base(kind: str) -> Path:
    """Per-user scratch root for worktrees of a given *kind* (cross-platform)."""
    base = Path(tempfile.gettempdir()) / f"arbor-{kind}-{_user_tag()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def worktree_create(
    cwd: str | Path,
    run_name: str,
    node_id: str,
    *,
    branch_prefix: str = "exp",
    branch: str | None = None,
    trunk: str | None = None,
) -> dict[str, Any]:
    """Create an isolated git worktree + branch for an experiment node.

    Marks the node ``running`` and records its branch as ``code_ref``. The host
    agent then implements the idea inside the returned worktree path. A stale
    worktree at the same location is force-removed first so retries are clean.
    """
    tree = load_or_init_tree(cwd, run_name)
    node = tree.get_node(node_id)
    if node is None:
        raise ValueError(f"node {node_id!r} not found")
    branch = branch or _branch_name(branch_prefix, node_id, node.hypothesis)

    start = trunk or "HEAD"

    def _add(branch_name: str) -> tuple[int, str, Path]:
        wt = _worktree_base("worktrees") / branch_name.replace("/", "__").replace(".", "_")
        if wt.exists():
            # `git worktree remove` only handles registered worktrees; rmtree also
            # clears an orphaned directory so the subsequent `worktree add` (which
            # refuses a non-empty path) succeeds on a retry.
            git(cwd, "worktree", "remove", "--force", str(wt))
            shutil.rmtree(wt, ignore_errors=True)
        rc, out = git(cwd, "worktree", "add", "-b", branch_name, str(wt), start)
        return rc, out, wt

    rc, out, wt = _add(branch)
    if rc != 0:
        # The branch likely already exists from a prior iteration (worktree_remove
        # preserves branches for later merging). Mirror the native runtime and
        # retry under a unique timestamp suffix instead of failing the retry.
        branch = f"{branch}-{time.strftime('%m%d-%H%M%S', time.gmtime())}"
        rc, out, wt = _add(branch)
        if rc != 0:
            raise RuntimeError(f"git worktree add failed: {out}")

    tree.update_node(node_id, status="running", code_ref=branch)
    session_timing.record_event(
        coordinator_dir(cwd, run_name), node_id, "running", label=branch,
    )
    return {"worktree": str(wt), "branch": branch, "node_id": node_id}


def worktree_remove(cwd: str | Path, worktree: str | Path) -> dict[str, Any]:
    """Force-remove a previously created experiment worktree.

    The path comes from a tool call, so we refuse to ``rmtree`` anything that is
    not strictly inside our per-user worktree scratch root — a purpose-specific
    tool should never expose unbounded deletion, even though the host agent is
    otherwise trusted.
    """
    wt = Path(worktree).resolve()
    base = _worktree_base("worktrees").resolve()
    if base not in wt.parents:
        raise ValueError(f"refusing to remove a worktree outside {base}: {wt}")
    rc, out = git(cwd, "worktree", "remove", "--force", str(wt))
    # Clear any leftover directory git did not remove (e.g. an orphaned path).
    shutil.rmtree(wt, ignore_errors=True)
    return {"removed": str(wt), "returncode": rc, "output": out}


# ── Guarded merge ─────────────────────────────────────────────────────────────


def git_merge_branch(
    cwd: str | Path,
    run_name: str,
    node_id: str,
    source_branch: str,
    *,
    target_branch: str | None = None,
    test_score: float | None = None,
    protected_paths: list[str] | None = None,
    required_outputs: list[str] | None = None,
    commit_message: str | None = None,
    timeout: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge an experiment branch into trunk **only if** every guard passes.

    Guards (in order):

    1. A target branch is known (arg or ``meta.trunk_branch``) and is **not** a
       protected branch (``main``/``master``).
    2. A B_test score exists — passed in, or obtained by re-running
       ``meta.eval_cmd_test`` in a detached worktree of *source_branch*.
    3. The B_test score is an improvement over the trunk/baseline test score
       (respecting ``meta.metric_direction``).
    4. No changed file matches a *protected_paths* glob.
    5. Every *required_outputs* path exists on the source branch.

    On success it performs a ``--no-ff`` merge, restores the original branch,
    updates ``meta.test_trunk_score`` and marks the node ``merged``. ``dry_run``
    runs all guards but skips the actual merge.

    Raises ``ValueError`` (guard violations) or ``RuntimeError`` (git failures).
    """
    tree = load_or_init_tree(cwd, run_name)
    target = target_branch or tree.meta.get("trunk_branch")
    if not target:
        raise ValueError("no target branch (pass target_branch or set meta.trunk_branch)")
    if target in PROTECTED_BRANCHES:
        raise ValueError(f"refusing to merge into protected branch: {target}")

    # ── Guard 2: obtain a held-out (B_test) score ────────────────────────────
    eval_cmd_test = tree.meta.get("eval_cmd_test")
    if test_score is None and eval_cmd_test:
        wt = _worktree_base("merge-eval") / source_branch.replace("/", "__").replace(".", "_")
        if wt.exists():
            git(cwd, "worktree", "remove", "--force", str(wt))
            shutil.rmtree(wt, ignore_errors=True)
        rc, out = git(cwd, "worktree", "add", "--detach", str(wt), source_branch)
        if rc != 0:
            raise RuntimeError(f"could not create B_test eval worktree: {out}")
        try:
            eff_timeout = timeout or int(tree.meta.get("eval_timeout") or 7200)
            rc, output, _ = _run_shell(_substitute(eval_cmd_test, wt, node_id), wt, eff_timeout)
            test_score = parse_score(output)
            if rc != 0 or test_score is None:
                raise ValueError("B_test evaluation failed or score missing:\n" + output[-2000:])
        finally:
            git(cwd, "worktree", "remove", "--force", str(wt))
            shutil.rmtree(wt, ignore_errors=True)

    if test_score is None:
        raise ValueError("no test score available; set meta.eval_cmd_test or pass test_score")

    # ── Guard 3: must beat the current trunk/baseline on B_test ──────────────
    ref_score = tree.meta.get("test_trunk_score")
    if ref_score is None:
        ref_score = tree.meta.get("test_baseline_score")
    if isinstance(ref_score, (int, float)) and not tree.is_improvement(float(test_score), float(ref_score)):
        raise ValueError(f"merge rejected: test_score={test_score} is not better than {ref_score}")

    # ── Guard 4: protected paths must be untouched ───────────────────────────
    if protected_paths:
        rc, diff = git(cwd, "diff", "--name-only", f"{target}...{source_branch}")
        # Fail closed: if we cannot compute the changed files, we cannot prove
        # the protected paths are untouched, so refuse rather than merge blindly.
        if rc != 0:
            raise RuntimeError(
                f"could not diff {target}...{source_branch} for the protected-path check:\n{diff}"
            )
        files = [ln.strip() for ln in diff.splitlines() if ln.strip()]
        for pattern in protected_paths:
            for path in files:
                if fnmatch.fnmatch(path, pattern):
                    raise ValueError(f"merge rejected: protected path {path} matches {pattern}")

    # ── Guard 5: required outputs must exist on the source branch ────────────
    for output_path in required_outputs or []:
        rc, _ = git(cwd, "show", f"{source_branch}:{output_path}")
        if rc != 0:
            raise ValueError(f"merge rejected: required output missing on branch: {output_path}")

    if dry_run:
        return {"ok": True, "dry_run": True, "test_score": test_score, "target": target}

    # ── Perform the merge, then return to the original branch ────────────────
    _, current = git(cwd, "branch", "--show-current")
    rc, out = git(cwd, "checkout", target)
    if rc != 0:
        raise RuntimeError(f"could not checkout target {target}: {out}")
    message = commit_message or f"coordinator: merge {node_id} from {source_branch}"
    rc, out = git(cwd, "merge", "--no-ff", "-m", message, source_branch)
    if rc != 0:
        git(cwd, "merge", "--abort")
        if current:
            git(cwd, "checkout", current)
        raise RuntimeError(f"merge failed:\n{out}")
    _, merge_hash = git(cwd, "rev-parse", "--short", "HEAD")
    if current and current != target:
        git(cwd, "checkout", current)

    tree.meta["test_trunk_score"] = float(test_score)
    tree.update_node(node_id, status="merged", code_ref=source_branch)
    session_timing.record_event(
        coordinator_dir(cwd, run_name), node_id, "merged",
        label=source_branch, score=test_score,
    )
    return {
        "merged": source_branch,
        "target": target,
        "test_score": test_score,
        "merge_hash": merge_hash,
    }


# ── Report ────────────────────────────────────────────────────────────────────


def generate_session_report(cwd: str | Path, run_name: str, *, instruction: str | None = None) -> dict[str, Any]:
    """Render ``REPORT.md`` for the session via the real report generator."""
    out = generate_report(session_dir(cwd, run_name), instruction=instruction)
    return {"report_path": str(out)}


# ── Dashboard ─────────────────────────────────────────────────────────────────

# Keep references to dashboards started in-process (e.g. from the long-lived MCP
# server) so repeated calls return the existing URL instead of leaking servers.
_DASHBOARDS: dict[str, Any] = {}


def open_dashboard(cwd: str | Path, run_name: str, *, port: int = 8765) -> dict[str, Any]:
    """Start (or reuse) a read-only web monitor for the session; return its URL.

    The monitor is file-backed: it polls the session directory, so it reflects
    whatever the host agent writes via the tree tools. Safe to call repeatedly —
    a single dashboard per session is kept alive for the life of the process.
    The WebUI import is local so the (stdlib-only) HTTP server is loaded lazily
    and never affects the keyless import guarantee.
    """
    sdir = session_dir(cwd, run_name)
    key = str(sdir)
    existing = _DASHBOARDS.get(key)
    if existing is not None:
        return {"url": existing.url, "session_dir": key, "reused": True}

    from ..webui.launcher import start_session_webui

    server = start_session_webui(sdir, run_name=run_name, preferred=port)
    if server is None:
        raise RuntimeError("could not bind a port for the web monitor")
    _DASHBOARDS[key] = server
    return {"url": server.url, "session_dir": key, "reused": False}
