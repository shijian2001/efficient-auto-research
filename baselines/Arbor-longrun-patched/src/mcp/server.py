"""The ``arbor mcp`` server — exposes :mod:`arbor.mcp.session_ops` over MCP.

A thin adapter: every MCP tool is a small wrapper that forwards to a deterministic
``session_ops`` function. There is **no LLM and no API key** here — the host agent
(Claude Code, Codex, …) supplies all reasoning and writes all code; this server
only manages Arbor's durable state and research guards.

The MCP SDK (``mcp`` package) is an *optional* dependency. Importing this module
never requires it — only :func:`build_server` and :func:`run` do — so the rest of
the package (and the unit tests) can introspect the tool surface without the SDK
installed. Install it with ``pip install arbor-agent[mcp]``.

Each tool takes an explicit ``run_name`` (the Arbor session) and an optional
``cwd`` (defaults to the server's working directory), keeping the server
stateless and safe to point at any project.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from . import session_ops as ops

# Tool names advertised to the host agent. Kept as data so tests can assert the
# surface without importing the (optional) MCP SDK.
TOOL_NAMES = (
    "tree_view",
    "tree_add_node",
    "tree_update_node",
    "tree_prune",
    "tree_set_meta",
    "eval_run",
    "worktree_create",
    "worktree_remove",
    "git_merge_branch",
    "generate_report",
    "open_dashboard",
    "scaffold_benchmark",
)

# Human-readable hint shown when the optional SDK is missing.
_MISSING_SDK_HINT = (
    "The MCP SDK is not installed. Install it with:\n"
    "    pip install arbor-agent[mcp]\n"
    "(or `uv pip install 'arbor-agent[mcp]'`)."
)


def _cwd(cwd: str | None) -> Path:
    """Resolve the project directory for a call (explicit arg or process cwd)."""
    return Path(cwd) if cwd else Path.cwd()


def build_server() -> Any:
    """Construct and return a configured ``FastMCP`` server.

    Raises:
        RuntimeError: if the optional ``mcp`` SDK is not installed.
    """
    try:
        import anyio
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised via run()
        raise RuntimeError(_MISSING_SDK_HINT) from exc

    server = FastMCP("arbor")

    async def _in_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking ``session_ops`` call on a worker thread.

        FastMCP invokes *synchronous* tool functions inline on its asyncio event
        loop, so a tool that spawns a child process (eval / git) would freeze the
        whole server — it could not read stdin, respond, or be cancelled — until
        the child exits. Offloading the blocking work keeps the server responsive
        and cancellable while a long evaluation or merge runs.
        """
        return await anyio.to_thread.run_sync(partial(func, *args, **kwargs))

    # ── Idea Tree ────────────────────────────────────────────────────────────

    @server.tool()
    def tree_view(run_name: str, fmt: str = "compact", cwd: str | None = None) -> str:
        """Render the Idea Tree (fmt: compact | constraints | markdown)."""
        return ops.tree_view(_cwd(cwd), run_name, fmt)

    @server.tool()
    def tree_add_node(
        run_name: str, parent_id: str, hypothesis: str,
        status: str = "pending", cwd: str | None = None,
    ) -> dict[str, Any]:
        """Add a child idea under parent_id; returns the assigned node id/depth."""
        return ops.tree_add_node(_cwd(cwd), run_name, parent_id, hypothesis, status=status)

    @server.tool()
    def tree_update_node(
        run_name: str, node_id: str,
        status: str | None = None, score: float | None = None,
        insight: str | None = None, result: str | None = None,
        code_ref: str | None = None, related_work: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Update whitelisted fields on a node (None values are ignored)."""
        return ops.tree_update_node(
            _cwd(cwd), run_name, node_id,
            status=status, score=score, insight=insight, result=result,
            code_ref=code_ref, related_work=related_work,
        )

    @server.tool()
    def tree_prune(run_name: str, node_id: str, reason: str = "", cwd: str | None = None) -> dict[str, Any]:
        """Prune a node and its subtree, recording an optional reason."""
        return ops.tree_prune(_cwd(cwd), run_name, node_id, reason)

    @server.tool()
    def tree_set_meta(
        run_name: str,
        baseline_score: float | None = None, trunk_score: float | None = None,
        test_baseline_score: float | None = None, test_trunk_score: float | None = None,
        eval_cmd: str | None = None, eval_cmd_test: str | None = None,
        eval_timeout: int | None = None, metric_direction: str | None = None,
        trunk_branch: str | None = None, dataset_info: str | None = None,
        submission_path: str | None = None, sample_submission_path: str | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Set session metadata (baseline/trunk scores, eval commands, …)."""
        return ops.tree_set_meta(
            _cwd(cwd), run_name,
            baseline_score=baseline_score, trunk_score=trunk_score,
            test_baseline_score=test_baseline_score, test_trunk_score=test_trunk_score,
            eval_cmd=eval_cmd, eval_cmd_test=eval_cmd_test, eval_timeout=eval_timeout,
            metric_direction=metric_direction, trunk_branch=trunk_branch,
            dataset_info=dataset_info, submission_path=submission_path,
            sample_submission_path=sample_submission_path,
        )

    # ── Benchmark scaffolding ──────────────────────────────────────────────────

    @server.tool()
    def scaffold_benchmark(
        name: str,
        metric_direction: str = "maximize",
        style: str = "light",
        split_kind: str = "seed_range",
        eval_entrypoint: str = "eval.py",
        edit: list[str] | None = None,
        git_init: bool = False,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        """Create the Arbor-ready reference folder in cwd.

        style: light (runnable eval + dev/test split + editable solution.py) |
        zoo (adds a natural-language README + PROVENANCE.md and runs the structural
        verifier). Writes measurement plumbing only — never the solution.
        Idempotent: existing files are reported as skipped. Takes no run_name:
        scaffolding precedes a session, so it operates on a directory.
        """
        return ops.scaffold_benchmark(
            _cwd(cwd), name=name, metric_direction=metric_direction, style=style,
            split_kind=split_kind, eval_entrypoint=eval_entrypoint, edit=edit,
            git_init=git_init,
        )

    # ── Evaluation ───────────────────────────────────────────────────────────

    @server.tool()
    async def eval_run(
        run_name: str, cmd: str, split: str = "dev", set_meta: str = "none",
        node_id: str | None = None, exec_cwd: str | None = None,
        timeout: int | None = None, cwd: str | None = None,
    ) -> dict[str, Any]:
        """Run an eval command, extract a score, log output, optionally record it.

        split: dev (B_dev) | test (B_test). set_meta: none | baseline | trunk |
        test_baseline | test_trunk.
        """
        # Offloaded: the eval command can run for minutes; it must not block the
        # server's event loop.
        return await _in_thread(
            ops.eval_run, _cwd(cwd), run_name, cmd, split=split, set_meta=set_meta,
            node_id=node_id, exec_cwd=exec_cwd, timeout=timeout,
        )

    # ── Worktrees ────────────────────────────────────────────────────────────

    @server.tool()
    async def worktree_create(
        run_name: str, node_id: str, branch_prefix: str = "exp",
        branch: str | None = None, trunk: str | None = None, cwd: str | None = None,
    ) -> dict[str, Any]:
        """Create an isolated git worktree+branch for an experiment node."""
        # Offloaded: spawns git subprocesses (must not block the event loop).
        return await _in_thread(
            ops.worktree_create, _cwd(cwd), run_name, node_id,
            branch_prefix=branch_prefix, branch=branch, trunk=trunk,
        )

    @server.tool()
    async def worktree_remove(worktree: str, cwd: str | None = None) -> dict[str, Any]:
        """Force-remove a previously created experiment worktree."""
        # Offloaded: spawns git subprocesses (must not block the event loop).
        return await _in_thread(ops.worktree_remove, _cwd(cwd), worktree)

    # ── Guarded merge ────────────────────────────────────────────────────────

    @server.tool()
    async def git_merge_branch(
        run_name: str, node_id: str, source_branch: str,
        target_branch: str | None = None, test_score: float | None = None,
        protected_paths: list[str] | None = None, required_outputs: list[str] | None = None,
        commit_message: str | None = None, timeout: int | None = None,
        dry_run: bool = False, cwd: str | None = None,
    ) -> dict[str, Any]:
        """Merge an experiment branch into trunk only if all guards pass.

        Guards: non-protected target, a B_test score (passed or re-evaluated),
        improvement over trunk/baseline, no protected-path changes, and presence
        of every required output. Use dry_run to check guards without merging.
        """
        # Offloaded: spawns git subprocesses and may re-run a B_test eval (must
        # not block the event loop).
        return await _in_thread(
            ops.git_merge_branch, _cwd(cwd), run_name, node_id, source_branch,
            target_branch=target_branch, test_score=test_score,
            protected_paths=protected_paths, required_outputs=required_outputs,
            commit_message=commit_message, timeout=timeout, dry_run=dry_run,
        )

    # ── Report ───────────────────────────────────────────────────────────────

    @server.tool()
    def generate_report(run_name: str, instruction: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        """Render REPORT.md for the session from its durable artifacts."""
        return ops.generate_session_report(_cwd(cwd), run_name, instruction=instruction)

    # ── Dashboard ────────────────────────────────────────────────────────────

    @server.tool()
    def open_dashboard(run_name: str, port: int = 8765, cwd: str | None = None) -> dict[str, Any]:
        """Open a read-only web monitor for the session; returns its URL.

        Surface the returned URL to the user so they can watch the Idea Tree grow
        in the browser as you work. Safe to call more than once.
        """
        return ops.open_dashboard(_cwd(cwd), run_name, port=port)

    return server


def run() -> None:
    """Entry point for ``arbor mcp`` — run the server over stdio.

    Stdio transport is what coding-agent harnesses launch (e.g.
    ``claude mcp add arbor -- arbor mcp``).
    """
    server = build_server()
    server.run()  # FastMCP defaults to the stdio transport.
