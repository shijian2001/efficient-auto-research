"""Unit tests for executor I/O — prompt construction and report parsing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools.executor_io import (
    _build_executor_prompt,
    _build_resume_context,
    _gather_ancestor_insights,
    _get_eval_info,
    _parse_executor_report,
    _substitute_eval_templates,
    _tail,
)


# ── _tail ────────────────────────────────────────────────────────────

def test_tail_passthrough_when_short() -> None:
    assert _tail("hello", 100) == "hello"


def test_tail_truncates_to_last_chars() -> None:
    out = _tail("abcdefghij", 4)
    assert out.endswith("ghij")
    assert "truncated" in out


# ── _substitute_eval_templates ───────────────────────────────────────

def test_substitute_eval_templates() -> None:
    cmd = "python eval.py --dir {cwd} --id {node_id}"
    assert _substitute_eval_templates(cmd, "/wt/path", "1.2") == \
        "python eval.py --dir /wt/path --id 1.2"


# ── _get_eval_info ───────────────────────────────────────────────────

def test_get_eval_info_empty_when_no_meta() -> None:
    tree = SimpleNamespace(meta={})
    assert _get_eval_info(tree) == ""


def test_get_eval_info_substitutes_when_context_given() -> None:
    tree = SimpleNamespace(meta={"eval_cmd": "run.py --dir {cwd} --id {node_id}"})
    out = _get_eval_info(tree, worktree_cwd="/wt", node_id="3")
    assert "run.py --dir /wt --id 3" in out
    assert "B_dev" in out


def test_get_eval_info_no_substitution_without_context() -> None:
    tree = SimpleNamespace(meta={"eval_cmd": "run.py --dir {cwd}"})
    out = _get_eval_info(tree)
    assert "{cwd}" in out  # left untouched when no worktree/node context


# ── _gather_ancestor_insights ────────────────────────────────────────

def _tree_with_insights() -> IdeaTree:
    t = IdeaTree(Node(id="ROOT", parent_id=None, depth=0))
    t.add_node(Node(id="1", parent_id="ROOT", depth=1, insight="root-level idea"))
    t.add_node(Node(id="1.1", parent_id="1", depth=2, insight="deeper idea"))
    t.add_node(Node(id="1.1.1", parent_id="1.1", depth=3))
    return t


def test_gather_ancestor_insights_orders_root_first() -> None:
    out = _gather_ancestor_insights(_tree_with_insights(), "1.1.1")
    assert "Insights from Prior Experiments" in out
    # Ancestors with insight, root-first; the node itself is excluded.
    assert out.index("1: root-level idea") < out.index("1.1: deeper idea")


def test_gather_ancestor_insights_empty_when_no_ancestor_insight() -> None:
    t = IdeaTree(Node(id="ROOT", parent_id=None, depth=0))
    t.add_node(Node(id="1", parent_id="ROOT", depth=1))  # no insight
    assert _gather_ancestor_insights(t, "1") == ""


# ── _build_executor_prompt ───────────────────────────────────────────

def test_build_executor_prompt_includes_sections() -> None:
    node = SimpleNamespace(id="2", hypothesis="make it faster")
    prompt = _build_executor_prompt(
        worktree_path="/wt",
        node=node,
        ancestor_insights="## Insights\n- prior",
        eval_info="## Evaluation Info\n- cmd",
        additional_context="extra notes",
    )
    assert "make it faster" in prompt
    assert "## Evaluation Info" in prompt
    assert "## Insights" in prompt
    assert "extra notes" in prompt
    assert "results/2-" in prompt  # result dir hint uses node id


def test_build_executor_prompt_omits_optional_sections() -> None:
    node = SimpleNamespace(id="2", hypothesis="h")
    prompt = _build_executor_prompt(
        worktree_path="/wt", node=node,
        ancestor_insights="", eval_info="", additional_context=None,
    )
    assert "Additional Context" not in prompt


# ── _build_resume_context ────────────────────────────────────────────

def test_build_resume_context_mentions_attempt_and_branch() -> None:
    cfg = SimpleNamespace(workspace_dir=None)
    node = SimpleNamespace(
        id="1", status="needs_retry", eval_status="failed_to_run",
        stop_reason="max_turns", code_ref="exp/n1-abc",
        result="ran but crashed", insight="ooming on large batch",
    )
    ctx = _build_resume_context(cfg, node, attempt=2)
    assert "attempt 2" in ctx
    assert "exp/n1-abc" in ctx          # committed-work branch path
    assert "ran but crashed" in ctx
    assert "ooming on large batch" in ctx


def test_build_resume_context_uncommitted_branch_message() -> None:
    cfg = SimpleNamespace(workspace_dir=None)
    node = SimpleNamespace(
        id="1", status="needs_retry", eval_status=None, stop_reason=None,
        code_ref=None, result="", insight="",
    )
    ctx = _build_resume_context(cfg, node, attempt=3)
    assert "starting from trunk" in ctx


# ── _parse_executor_report (with a fake provider) ────────────────────

class _FakeResp:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _FakeProvider:
    model = "fake-model"

    def __init__(self, text: str) -> None:
        self._text = text

    async def create(self, **_kw):
        return _FakeResp(self._text)


def test_parse_executor_report_extracts_json() -> None:
    provider = _FakeProvider('{"score": 42.5, "insight": "ok", "eval_status": "scored"}')
    out = asyncio.run(_parse_executor_report(provider, "report text", "hyp"))
    assert out["score"] == 42.5
    assert out["eval_status"] == "scored"


def test_parse_executor_report_strips_code_fences() -> None:
    provider = _FakeProvider('```json\n{"score": 1, "insight": "x"}\n```')
    out = asyncio.run(_parse_executor_report(provider, "report", "hyp"))
    assert out["score"] == 1


def test_parse_executor_report_falls_back_on_bad_json() -> None:
    provider = _FakeProvider("not json at all")
    out = asyncio.run(_parse_executor_report(provider, "report", "hyp"))
    assert out["score"] is None
    assert out["eval_status"] == "failed_to_run"
