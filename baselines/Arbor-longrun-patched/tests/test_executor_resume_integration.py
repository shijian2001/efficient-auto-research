"""Integration tests for the executor resume/retry lifecycle.

These exercise ``_run_single_executor`` end-to-end with the heavy collaborators
(the Agent, git worktree helpers, the report parser, insight propagation) mocked
out, so the status-classification, attempt-counter, resume-branch, and turn-bump
wiring is verified for real — not just the pure helper functions.

Self-contained: run directly (``python tests/test_executor_resume_integration.py``)
or via pytest. Maps the ``arbor`` package onto ``src/`` so no install is needed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if "arbor" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "arbor", _ROOT / "src" / "__init__.py",
        submodule_search_locations=[str(_ROOT / "src")],
    )
    _arbor = importlib.util.module_from_spec(_spec)
    sys.modules["arbor"] = _arbor
    _spec.loader.exec_module(_arbor)

import arbor.core.agent as agent_mod  # noqa: E402
import arbor.core.tools as tools_mod  # noqa: E402
import arbor.core.tools.executor_tool as exectool_mod  # noqa: E402
import arbor.executor.prompts as prompts_mod  # noqa: E402
import arbor.coordinator.tools.executor_run as er  # noqa: E402
from arbor.coordinator.config import CoordinatorConfig  # noqa: E402
from arbor.coordinator.idea_tree import IdeaTree, Node  # noqa: E402


# ── Fakes ───────────────────────────────────────────────────────────────────

class _FakeGitManager:
    def __init__(self):
        self._initialized = False
        self.branch_name = None
        self.cwd = None


class FakeAgent:
    """Stands in for core.agent.Agent. Scripted via class attributes set per test."""

    next_report = "Score: 45.2%\nLooks good."
    next_turns = 5
    next_stop_reason = "finished"
    raise_timeout = False

    def __init__(self, *, provider, tools, system_prompt, config):
        self.config = config
        self.tools = dict(tools) if tools else {}
        self.git_manager = _FakeGitManager()
        self.total_turns = 0
        self.stop_reason = None
        self.total_input_tokens = 10
        self.total_output_tokens = 20

    async def run(self, prompt):
        if type(self).raise_timeout:
            # Simulate the asyncio.wait_for timeout the dispatcher wraps run() in.
            raise asyncio.TimeoutError()
        self.total_turns = type(self).next_turns
        self.stop_reason = type(self).next_stop_reason
        return type(self).next_report


class FakeExecutorTool:
    name = "Executor"

    def __init__(self, *, cwd, parent_agent, workspace_dir):
        pass


# ── Harness ─────────────────────────────────────────────────────────────────

def _make_tree(node_status="pending", *, code_ref=None, attempt=1):
    root = Node(id="ROOT", parent_id=None, depth=0, status="done")
    tree = IdeaTree(root=root, json_path=None, md_path=None, max_depth=None)
    child = Node(
        id="1", parent_id="ROOT", depth=0, status=node_status,
        hypothesis="use dropout", code_ref=code_ref, attempt=attempt,
        result="prior partial result" if code_ref else "",
    )
    tree._nodes["1"] = child
    root.children_ids.append("1")
    return tree


class _Recorder:
    """Captures the kwargs the mocked collaborators were called with."""

    def __init__(self):
        self.create_kwargs = None
        self.injected_context = None
        self.executor_max_turns = None


def _patches(rec, parsed):
    """Context managers mocking every heavy collaborator of _run_single_executor."""

    async def fake_create_worktree(cwd, branch_name, start_point=None):
        rec.create_kwargs = {"branch_name": branch_name, "start_point": start_point}
        return Path(tempfile.gettempdir()) / "fake-wt", branch_name

    async def fake_noop(*a, **k):
        return None

    async def fake_parse(*a, **k):
        return dict(parsed)

    async def fake_propagate(*a, **k):
        return ""

    def fake_build_prompt(*, worktree_path, node, ancestor_insights, eval_info, additional_context):
        rec.injected_context = additional_context
        return "PROMPT"

    class _CapturingAgent(FakeAgent):
        def __init__(self, *, provider, tools, system_prompt, config):
            super().__init__(provider=provider, tools=tools, system_prompt=system_prompt, config=config)
            rec.executor_max_turns = config.max_turns

    return [
        patch.object(agent_mod, "Agent", _CapturingAgent),
        patch.object(tools_mod, "get_all_tools", lambda **k: []),
        patch.object(exectool_mod, "ExecutorTool", FakeExecutorTool),
        patch.object(prompts_mod, "build_system_prompt", lambda cfg, plugin=None: "sys"),
        patch.object(er, "_create_worktree", fake_create_worktree),
        patch.object(er, "_finalize_worktree", fake_noop),
        patch.object(er, "_remove_worktree", fake_noop),
        patch.object(er, "_run_after_executor_hook", fake_noop),
        patch.object(er, "_parse_executor_report", fake_parse),
        patch.object(er, "propagate_insights", fake_propagate),
        patch.object(er, "_build_executor_prompt", fake_build_prompt),
        patch.object(er, "_gather_ancestor_insights", lambda *a, **k: ""),
        patch.object(er, "_get_eval_info", lambda *a, **k: ""),
    ]


async def _run(tree, *, parsed, resume=False, extra_turns=0, executor_timeout=None):
    rec = _Recorder()
    cfg = CoordinatorConfig(cwd=".")
    cfg.workspace_dir = None  # skip artifact writes + git diff
    if executor_timeout is not None:
        cfg.executor_timeout = executor_timeout
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patches(rec, parsed):
            stack.enter_context(p)
        out = await er._run_single_executor(
            tree=tree, config=cfg, provider=object(), node_id="1",
            resume=resume, extra_turns=extra_turns,
        )
    return out, rec


# ── Tests: classification through the real lifecycle ─────────────────────────

def test_success_marks_done():
    tree = _make_tree("pending")
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Score: 45.2%"
    FakeAgent.next_stop_reason = "finished"
    _, _ = asyncio.run(_run(tree, parsed={"score": 45.2, "eval_status": "scored", "insight": "i", "result": "r"}))
    n = tree.get_node("1")
    assert n.status == "done", n.status
    assert n.score == 45.2
    assert n.eval_status == "scored"
    assert n.attempt == 1


def test_timeout_marks_needs_retry():
    tree = _make_tree("pending")
    FakeAgent.raise_timeout = True
    try:
        _, _ = asyncio.run(_run(
            tree,
            parsed={"score": None, "eval_status": "failed_to_run", "insight": "", "result": ""},
        ))
    finally:
        FakeAgent.raise_timeout = False
    n = tree.get_node("1")
    assert n.status == "needs_retry", n.status
    assert n.score is None


def test_max_turns_marks_needs_retry():
    tree = _make_tree("pending")
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Agent stopped after 50 turns without a final answer."
    FakeAgent.next_stop_reason = "max_turns"
    _, _ = asyncio.run(_run(tree, parsed={"score": None, "eval_status": "failed_to_run", "insight": "", "result": ""}))
    n = tree.get_node("1")
    assert n.status == "needs_retry", n.status


def test_needs_retry_consumes_cycle_through_lifecycle():
    # Two nodes: dispatch one that ends needs_retry, then confirm the cycle counter
    # advanced (so a perpetually-failing node still spends max_cycles budget).
    tree = _make_tree("pending")
    before = er._completed_cycles(tree)
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Agent stopped after 50 turns without a final answer."
    FakeAgent.next_stop_reason = "max_turns"
    asyncio.run(_run(tree, parsed={"score": None, "eval_status": "failed_to_run", "insight": "", "result": ""}))
    after = er._completed_cycles(tree)
    assert after == before + 1, (before, after)


# ── Tests: resume path ───────────────────────────────────────────────────────

def test_resume_branches_from_code_ref_and_bumps_turns():
    tree = _make_tree("needs_retry", code_ref="coordinator/n1-x-abc123", attempt=1)
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Score: 51.0%"
    FakeAgent.next_stop_reason = "finished"
    _, rec = asyncio.run(_run(
        tree,
        parsed={"score": 51.0, "eval_status": "scored", "insight": "i", "result": "r"},
        resume=True, extra_turns=7,
    ))
    # Worktree branched from the preserved branch, not trunk:
    assert rec.create_kwargs["start_point"] == "coordinator/n1-x-abc123"
    # Resume branch carries the attempt suffix:
    assert rec.create_kwargs["branch_name"].endswith("-a2")
    # Turn budget was raised by extra_turns (50 default + 7):
    assert rec.executor_max_turns == 57, rec.executor_max_turns
    # Prior context was injected into the prompt:
    assert rec.injected_context is not None
    assert "Resuming a prior attempt" in rec.injected_context
    assert "coordinator/n1-x-abc123" in rec.injected_context
    # Attempt counter advanced and the node is now done:
    n = tree.get_node("1")
    assert n.attempt == 2
    assert n.status == "done"


def test_resume_still_failing_increments_attempt():
    tree = _make_tree("needs_retry", code_ref="br", attempt=2)
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Agent stopped after 50 turns without a final answer."
    FakeAgent.next_stop_reason = "max_turns"
    asyncio.run(_run(tree, parsed={"score": None, "eval_status": "failed_to_run", "insight": "", "result": ""},
                     resume=True, extra_turns=5))
    n = tree.get_node("1")
    assert n.status == "needs_retry"
    assert n.attempt == 3


# ── Tests: dispatch guards ───────────────────────────────────────────────────

def test_dispatch_guard_rejects_done_node():
    tree = _make_tree("done")
    out = asyncio.run(er._run_single_executor(
        tree=tree, config=CoordinatorConfig(cwd="."), provider=object(), node_id="1",
    ))
    assert "status='done'" in out and "Only 'pending' or 'needs_retry'" in out


def test_dispatch_guard_allows_needs_retry():
    # Reaches the worktree stage (guard passed) — mock create to short-circuit.
    tree = _make_tree("needs_retry", code_ref="br")
    FakeAgent.raise_timeout = False
    FakeAgent.next_report = "Score: 1.0%"
    FakeAgent.next_stop_reason = "finished"
    out, rec = asyncio.run(_run(tree, parsed={"score": 1.0, "eval_status": "scored", "insight": "", "result": ""}))
    assert rec.create_kwargs is not None  # got past the guard
    assert tree.get_node("1").status == "done"


# ── Tests: ResumeExecutorTool guards ─────────────────────────────────────────

def _resume_tool(tree):
    return er.ResumeExecutorTool(
        cwd=".", tree=tree, config=CoordinatorConfig(cwd="."), provider=object(),
    )


def test_resume_tool_rejects_non_needs_retry():
    tree = _make_tree("done")
    out = asyncio.run(_resume_tool(tree).execute(node_id="1"))
    assert "only applies to 'needs_retry'" in out


def test_resume_tool_rejects_missing_code_ref():
    tree = _make_tree("needs_retry", code_ref=None)
    out = asyncio.run(_resume_tool(tree).execute(node_id="1"))
    assert "no preserved branch" in out


def test_resume_tool_enforces_max_retries():
    # max_retries=3 ⇒ allow while attempt<=3; attempt=4 is refused.
    tree = _make_tree("needs_retry", code_ref="br", attempt=4)
    out = asyncio.run(_resume_tool(tree).execute(node_id="1"))
    assert "allowed retries" in out


def test_resume_tool_allows_within_retry_budget():
    tree = _make_tree("needs_retry", code_ref="br", attempt=3)
    tool = _resume_tool(tree)
    rec = _Recorder()
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _patches(rec, {"score": 9.0, "eval_status": "scored", "insight": "", "result": ""}):
            stack.enter_context(p)
        out = asyncio.run(tool.execute(node_id="1", extra_turns=3))
    assert "allowed retries" not in out
    assert rec.create_kwargs is not None  # actually dispatched


# ── Tests: artifacts + resume-context builder ────────────────────────────────

def test_save_artifacts_includes_new_fields():
    with tempfile.TemporaryDirectory() as ws:
        cfg = CoordinatorConfig(cwd=".")
        cfg.workspace_dir = ws
        asyncio.run(er._save_experiment_artifacts(
            config=cfg, node_id="1", hypothesis="h", raw_report="report body",
            parsed={"score": None, "insight": "i", "result": "r"},
            actual_branch="br", agent_turns=12,
            status="needs_retry", eval_status="failed_to_run",
            stop_reason="max_turns", attempt=2,
        ))
        metrics = json.loads((Path(ws) / "experiments" / "1" / "metrics.json").read_text())
        assert metrics["status"] == "needs_retry"
        assert metrics["eval_status"] == "failed_to_run"
        assert metrics["stop_reason"] == "max_turns"
        assert metrics["attempt"] == 2
        report = (Path(ws) / "experiments" / "1" / "report.md").read_text()
        assert "**Status**: needs_retry" in report
        assert "**Attempt**: 2" in report


def test_build_resume_context_reads_prior_artifacts():
    with tempfile.TemporaryDirectory() as ws:
        exp = Path(ws) / "experiments" / "1"
        exp.mkdir(parents=True)
        (exp / "report.md").write_text("PRIOR REPORT CONTENT")
        (exp / "diff.patch").write_text("PRIOR DIFF CONTENT")
        cfg = CoordinatorConfig(cwd=".")
        cfg.workspace_dir = ws
        node = Node(id="1", parent_id="ROOT", status="needs_retry",
                    code_ref="br", eval_status="failed_to_run", stop_reason="max_turns",
                    result="res", insight="ins")
        ctx = er._build_resume_context(cfg, node, attempt=2)
        assert "Resuming a prior attempt (attempt 2)" in ctx
        assert "br" in ctx
        assert "PRIOR REPORT CONTENT" in ctx
        assert "PRIOR DIFF CONTENT" in ctx


def test_build_resume_context_handles_missing_code_ref():
    cfg = CoordinatorConfig(cwd=".")
    cfg.workspace_dir = None
    node = Node(id="1", parent_id="ROOT", status="needs_retry", code_ref=None)
    ctx = er._build_resume_context(cfg, node, attempt=2)
    assert "`None`" not in ctx
    assert "starting from trunk" in ctx


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v) and not inspect.iscoroutinefunction(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
