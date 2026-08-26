"""Tests for the pre-experiment auto-search wiring.

``dispatch_auto_search`` fires a background SearchAgent on a freshly-added node,
bypassing the post-experiment validation gate. Here we patch ``_run_one`` so no
real SearchAgent / network is involved.
"""

from __future__ import annotations

import asyncio
import json

import arbor.search_agent.agent as sa_agent
from arbor.coordinator.config import CoordinatorConfig
from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools import search_ctx
from arbor.coordinator.tools.search_ctx import (
    dispatch_auto_search,
    wait_for_pending_searches,
)
from arbor.coordinator.tools.tree_ops import TreeAddNodeTool


class _Provider:
    model = "m"


def _tree() -> IdeaTree:
    return IdeaTree(Node(id="ROOT", parent_id=None, depth=0))


def _cfg(*, auto: bool, backend: str = "alphaxiv") -> CoordinatorConfig:
    cfg = CoordinatorConfig(cwd=".")
    cfg.search.enabled = True
    cfg.search.builtin_backend = backend
    cfg.search.auto_search_on_add = auto
    return cfg


def _patch_run_one(monkeypatch, calls):
    async def fake_run_one(*, tree, config, provider, node_id, focus):
        calls.append((node_id, focus))
        await tree.async_update_node(node_id, related_work="[fake novelty verdict]")
        return f"{node_id}: done"

    monkeypatch.setattr(search_ctx, "_run_one", fake_run_one)


def test_dispatch_auto_search_runs_when_enabled(monkeypatch):
    calls: list = []
    _patch_run_one(monkeypatch, calls)
    tree = _tree()
    # Add a child node directly so we have something to annotate.
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="Some idea")
    tree.add_node(node)

    async def _go():
        dispatched = dispatch_auto_search(tree, _cfg(auto=True), _Provider(), "1")
        assert dispatched is True
        await wait_for_pending_searches()

    asyncio.run(_go())
    assert calls == [("1", None)]
    assert tree.get_node("1").related_work == "[fake novelty verdict]"


def test_dispatch_auto_search_noop_when_disabled(monkeypatch):
    calls: list = []
    _patch_run_one(monkeypatch, calls)
    tree = _tree()
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="Some idea")
    tree.add_node(node)

    async def _go():
        return dispatch_auto_search(tree, _cfg(auto=False), _Provider(), "1")

    assert asyncio.run(_go()) is False
    assert calls == []


def test_dispatch_auto_search_noop_without_backend(monkeypatch):
    calls: list = []
    _patch_run_one(monkeypatch, calls)
    tree = _tree()
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="Some idea")
    tree.add_node(node)

    async def _go():
        return dispatch_auto_search(tree, _cfg(auto=True, backend="none"), _Provider(), "1")

    assert asyncio.run(_go()) is False
    assert calls == []


def test_tree_add_node_triggers_auto_search(monkeypatch):
    calls: list = []
    _patch_run_one(monkeypatch, calls)
    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(auto=True), provider=_Provider())

    async def _go():
        msg = await tool.execute(parent_id="ROOT", hypothesis="A novel idea")
        await wait_for_pending_searches()
        return msg

    msg = asyncio.run(_go())
    assert "pre-experiment novelty check dispatched" in msg
    assert len(calls) == 1
    # The freshly-added node got the annotation.
    node_id = calls[0][0]
    assert tree.get_node(node_id).related_work == "[fake novelty verdict]"


def test_tree_add_node_no_auto_search_when_disabled(monkeypatch):
    calls: list = []
    _patch_run_one(monkeypatch, calls)
    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(auto=False), provider=_Provider())

    async def _go():
        return await tool.execute(parent_id="ROOT", hypothesis="A plain idea")

    msg = asyncio.run(_go())
    assert "pre-experiment novelty check" not in msg
    assert calls == []


# ── Integration: full pre-experiment chain, only the LLM agent mocked ─────────

_CANNED = json.dumps(
    {
        "summary": "Tree-of-thought planning is already well studied.",
        "related_papers": [
            {
                "title": "Tree of Thoughts",
                "url": "https://www.alphaxiv.org/abs/2305.10601",
                "one_line_relevance": "Closest planning-over-search method.",
            }
        ],
        "novelty_assessment": "partial-overlap",
        "overlap_risks": "Overlaps with ToT on the search structure.",
    }
)


class _FakeSearchAgent:
    """Stands in for the SearchAgent — returns a canned novelty JSON."""

    total_turns = 1
    total_input_tokens = 0
    total_output_tokens = 0

    async def run(self, _prompt: str) -> str:
        return _CANNED


def test_pre_experiment_chain_writes_rendered_related_work(monkeypatch):
    """End-to-end pre-experiment path with ONLY build_search_agent mocked:
    TreeAddNode -> dispatch_auto_search -> real _run_one -> JSON parse ->
    _render_markdown -> node.related_work. No network, no LLM, no validation gate.
    """
    # _run_one imports build_search_agent from this module at call time.
    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _FakeSearchAgent())

    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(auto=True), provider=_Provider())

    async def _go():
        msg = await tool.execute(parent_id="ROOT", hypothesis="Tree search over plans")
        await wait_for_pending_searches()
        return msg

    msg = asyncio.run(_go())
    assert "pre-experiment novelty check dispatched" in msg

    node = tree.get_node("1")
    assert node is not None
    rw = node.related_work
    # The verdict was parsed and rendered into the documented Markdown shape.
    assert "### Summary" in rw
    assert "Tree of Thoughts" in rw
    assert "https://www.alphaxiv.org/abs/2305.10601" in rw
    assert "partial-overlap" in rw
    assert "### Overlap Risks" in rw


def test_pre_experiment_chain_handles_unparseable_output(monkeypatch):
    """A non-JSON agent reply is stored as an [unparsed JSON] block, not a crash."""

    class _BadAgent(_FakeSearchAgent):
        async def run(self, _prompt: str) -> str:
            return "the model rambled and never emitted JSON"

    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _BadAgent())

    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(auto=True), provider=_Provider())

    async def _go():
        await tool.execute(parent_id="ROOT", hypothesis="Some idea")
        await wait_for_pending_searches()

    asyncio.run(_go())
    rw = tree.get_node("1").related_work
    assert "[unparsed JSON" in rw
    assert "rambled" in rw
