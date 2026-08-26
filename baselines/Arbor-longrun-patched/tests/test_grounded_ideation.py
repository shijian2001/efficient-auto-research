"""Tests for grounded ideation (roadmap 1.1 (a)+(b)).

Covers the ``search.grounded_ideation`` switch, the ``ResearchSearch`` tool
(registration + multi-intent dispatch), the ``Node.grounding`` field, the
``TreeAddNode(grounding=...)`` wiring, and the integrity separation between the
research lane and the post-experiment novelty-audit lane.

The research agent / network is never exercised: ``build_search_agent`` is
monkeypatched with a stub returning canned JSON (mirrors test_auto_search.py).
"""

from __future__ import annotations

import asyncio
import json

import arbor.search_agent.agent as sa_agent
from arbor.coordinator.config import CoordinatorConfig, SearchConfig
from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools import get_coordinator_tools
from arbor.coordinator.tools._agent_recover import (
    filter_sources_to_visited,
    recover_json,
    visited_urls,
)
from arbor.coordinator.tools.research_ctx import ResearchSearchTool
from arbor.coordinator.tools.tree_ops import TreeAddNodeTool


class _Provider:
    model = "m"


def _tree() -> IdeaTree:
    return IdeaTree(Node(id="ROOT", parent_id=None, depth=0))


def _cfg(*, grounded: bool, backend: str = "alphaxiv") -> CoordinatorConfig:
    cfg = CoordinatorConfig(cwd=".")
    cfg.search.enabled = True
    cfg.search.builtin_backend = backend
    cfg.search.grounded_ideation = grounded
    return cfg


_CANNED = json.dumps(
    {
        "summary": "Self-consistency decoding samples multiple chains and votes.",
        "details": (
            "The main approaches are majority vote [1] and weighted voting. "
            "An open angle is adaptive sample counts conditioned on agreement."
        ),
        "sources": [
            {
                "title": "Self-Consistency Improves CoT",
                "url": "https://www.alphaxiv.org/abs/2203.11171",
                "note": "Samples diverse reasoning paths and votes.",
            }
        ],
    }
)


class _FakeResearchAgent:
    """Stands in for the research agent — returns canned research JSON."""

    total_turns = 1
    total_input_tokens = 0
    total_output_tokens = 0

    async def run(self, _prompt: str) -> str:
        return _CANNED


# ── config default ───────────────────────────────────────────────────────────

def test_grounded_ideation_default_off():
    assert SearchConfig().grounded_ideation is False
    assert SearchConfig(builtin_backend="alphaxiv").grounded_ideation is False


# ── tool registration ────────────────────────────────────────────────────────

def _tool_names(cfg: CoordinatorConfig) -> list[str]:
    return sorted(t.name for t in get_coordinator_tools(_tree(), cfg, _Provider()))


def test_research_search_registered_when_on_with_backend():
    assert "ResearchSearch" in _tool_names(_cfg(grounded=True))


def test_research_search_not_registered_when_off():
    assert "ResearchSearch" not in _tool_names(_cfg(grounded=False))


def test_research_search_not_registered_without_backend():
    assert "ResearchSearch" not in _tool_names(_cfg(grounded=True, backend="none"))


def test_grounded_ideation_independent_of_mode():
    # ResearchSearch is its own lane: present regardless of executor/inline mode.
    cfg = _cfg(grounded=True)
    cfg.search.mode = "inline"
    assert "ResearchSearch" in _tool_names(cfg)


# ── Node.grounding field ─────────────────────────────────────────────────────

def test_node_grounding_round_trips():
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="idea", grounding="[1] paper")
    restored = Node.from_dict(node.to_dict())
    assert restored.grounding == "[1] paper"


def test_node_grounding_omitted_when_empty():
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="idea")
    assert "grounding" not in node.to_dict()


# ── TreeAddNode(grounding=...) ───────────────────────────────────────────────

def test_tree_add_node_stores_grounding():
    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(grounded=True), provider=_Provider())

    async def _go():
        return await tool.execute(
            parent_id="ROOT",
            hypothesis="Adaptive self-consistency",
            grounding="[1] https://www.alphaxiv.org/abs/2203.11171",
        )

    asyncio.run(_go())
    node = tree.get_node("1")
    assert node.grounding == "[1] https://www.alphaxiv.org/abs/2203.11171"


def test_tree_add_node_grounding_defaults_empty():
    tree = _tree()
    tool = TreeAddNodeTool(cwd=".", tree=tree, config=_cfg(grounded=True), provider=_Provider())
    asyncio.run(tool.execute(parent_id="ROOT", hypothesis="Plain idea"))
    assert tree.get_node("1").grounding == ""


# ── ResearchSearch dispatch ──────────────────────────────────────────────────

def test_research_search_returns_rendered_digest(monkeypatch):
    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _FakeResearchAgent())
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())

    digest = asyncio.run(tool.execute(query="self-consistency decoding", intent="survey"))

    assert "### Summary" in digest
    assert "### Findings" in digest
    assert "majority vote" in digest
    assert "### Sources" in digest
    assert "Self-Consistency Improves CoT" in digest
    assert "https://www.alphaxiv.org/abs/2203.11171" in digest


def test_research_search_requires_query():
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    assert "query is required" in asyncio.run(tool.execute(query="   "))


def test_research_search_rejects_bad_intent():
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    out = asyncio.run(tool.execute(query="x", intent="nonsense"))
    assert "intent must be one of" in out


def test_research_search_intent_optional(monkeypatch):
    """Omitting intent is allowed (the assistant infers it)."""
    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _FakeResearchAgent())
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    digest = asyncio.run(tool.execute(query="anything"))
    assert "### Summary" in digest


def test_research_search_no_backend_marker():
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True, backend="none"), provider=_Provider())
    assert "[research-failed:" in asyncio.run(tool.execute(query="anything"))


def test_research_search_unparseable_output(monkeypatch):
    class _BadAgent(_FakeResearchAgent):
        async def run(self, _prompt: str) -> str:
            return "the model rambled and never emitted JSON"

    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _BadAgent())
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    out = asyncio.run(tool.execute(query="x"))
    assert "JSON could not be parsed" in out
    assert "rambled" in out


def test_research_search_passes_research_system_prompt(monkeypatch):
    """The research lane must run the research-assistant prompt, not the novelty one."""
    captured: dict = {}

    def _fake_build(**kw):
        captured.update(kw)
        return _FakeResearchAgent()

    monkeypatch.setattr(sa_agent, "build_search_agent", _fake_build)
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    asyncio.run(tool.execute(query="x"))

    from arbor.search_agent.prompts import RESEARCH_AGENT_SYSTEM_PROMPT

    assert captured.get("system_prompt") == RESEARCH_AGENT_SYSTEM_PROMPT


def test_research_lane_uses_full_text_budget(monkeypatch):
    """The research lane bumps the visit token budget so method sections survive."""
    captured: dict = {}

    def _fake_build(**kw):
        captured.update(kw)
        return _FakeResearchAgent()

    monkeypatch.setattr(sa_agent, "build_search_agent", _fake_build)
    cfg = _cfg(grounded=True)
    cfg.search.visit_max_content_tokens = 2048
    cfg.search.research_visit_tokens = 6000
    tool = ResearchSearchTool(cwd=".", config=cfg, provider=_Provider())
    asyncio.run(tool.execute(query="x"))

    sc_passed = captured["search_config"]
    assert sc_passed.visit_max_content_tokens == 6000  # bumped for full-text reads


# ── integrity: separation of the two lanes ───────────────────────────────────

def test_research_does_not_touch_tree(monkeypatch):
    """ResearchSearch returns a digest to the caller; it never writes to a node."""
    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _FakeResearchAgent())
    tree = _tree()
    node = Node(id="1", parent_id="ROOT", depth=1, hypothesis="idea")
    tree.add_node(node)

    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    asyncio.run(tool.execute(query="self-consistency", intent="related_work"))

    assert tree.get_node("1").related_work == ""
    assert tree.get_node("1").grounding == ""


def test_grounding_and_related_work_are_independent_fields():
    """A node can carry a grounding citation and a separate novelty verdict."""
    node = Node(
        id="1",
        parent_id="ROOT",
        depth=1,
        hypothesis="idea",
        grounding="[1] grounding source",
        related_work="### Novelty\nnovel",
    )
    restored = Node.from_dict(node.to_dict())
    assert restored.grounding == "[1] grounding source"
    assert "novel" in restored.related_work


# ── agent-output recovery + visited-source filtering ─────────────────────────

class _AgentStub:
    """Minimal stand-in exposing the normalized transcript the Agent records."""

    def __init__(self, assistant_texts=None, tool_uses=None):
        self.assistant_texts = assistant_texts or []
        self.tool_uses = tool_uses or []


def test_recover_json_prefers_raw():
    agent = _AgentStub(assistant_texts=['{"a": 2}'])
    assert recover_json(agent, '{"a": 1}') == {"a": 1}


def test_recover_json_from_transcript_when_raw_unusable():
    # raw is a max_turns placeholder; the real JSON sits in an earlier turn.
    agent = _AgentStub(assistant_texts=[
        "Let me search first.",
        '{"summary": "done", "sources": []}',
        "Now let me keep going...",  # the nudge-bait final turn
    ])
    recovered = recover_json(agent, "Agent stopped after 12 turns without a final answer.")
    assert recovered == {"summary": "done", "sources": []}


def test_recover_json_none_when_no_json():
    agent = _AgentStub(assistant_texts=["no json here"])
    assert recover_json(agent, "still nothing") is None


def test_visited_urls_normalizes_versions():
    agent = _AgentStub(tool_uses=[
        {"name": "web_visit", "input": {"url": ["https://www.alphaxiv.org/abs/2203.11171v4"]}},
        {"name": "web_search", "input": {"query": ["x"]}},  # ignored
    ])
    v = visited_urls(agent)
    # version suffix stripped → matches the un-versioned citation
    assert filter_sources_to_visited(
        [{"url": "https://www.alphaxiv.org/abs/2203.11171"}], v
    ) == ([{"url": "https://www.alphaxiv.org/abs/2203.11171"}], 0)


def test_filter_drops_unvisited_source():
    visited = {"alphaxiv.org/abs/2203.11171"}
    sources = [
        {"url": "https://www.alphaxiv.org/abs/2203.11171"},  # visited → keep
        {"url": "https://www.alphaxiv.org/abs/2604.99999"},  # never visited → drop
    ]
    kept, dropped = filter_sources_to_visited(sources, visited)
    assert dropped == 1
    assert kept == [{"url": "https://www.alphaxiv.org/abs/2203.11171"}]


def test_filter_noop_when_nothing_visited():
    sources = [{"url": "https://a.com"}, {"url": "https://b.com"}]
    kept, dropped = filter_sources_to_visited(sources, set())
    assert dropped == 0 and kept == sources


def test_research_search_drops_fabricated_source(monkeypatch):
    """End-to-end: a cited-but-never-visited source is removed from the digest."""
    canned = json.dumps({
        "summary": "s",
        "details": "d",
        "sources": [
            {"title": "Real", "url": "https://www.alphaxiv.org/abs/2203.11171", "note": "n"},
            {"title": "Fake", "url": "https://www.alphaxiv.org/abs/2604.99999", "note": "n"},
        ],
    })

    class _Agent:
        assistant_texts = [canned]
        tool_uses = [{"name": "web_visit",
                      "input": {"url": ["https://www.alphaxiv.org/abs/2203.11171"]}}]

        async def run(self, _prompt):
            return canned

    monkeypatch.setattr(sa_agent, "build_search_agent", lambda **kw: _Agent())
    tool = ResearchSearchTool(cwd=".", config=_cfg(grounded=True), provider=_Provider())
    digest = asyncio.run(tool.execute(query="x", intent="related_work"))
    assert "Real" in digest
    assert "2604.99999" not in digest  # fabricated source dropped
    assert "dropped" in digest  # note surfaced

