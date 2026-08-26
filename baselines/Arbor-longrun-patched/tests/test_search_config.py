"""Tests for the search backend configuration + tool registration gates.

Covers ``SearchConfig.has_backend``, the ``build_search_agent`` guard, and the
coordinator tool-registration switch for the zero-config alphaXiv backend.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arbor.coordinator.config import CoordinatorConfig, SearchConfig
from arbor.coordinator.idea_tree import IdeaTree, Node
from arbor.coordinator.tools import get_coordinator_tools
from arbor.search_agent.agent import build_search_agent


def _tree() -> IdeaTree:
    return IdeaTree(Node(id="ROOT", parent_id=None, depth=0))


# ── has_backend ──────────────────────────────────────────────────────────────

def test_has_backend_alphaxiv_without_endpoint():
    assert SearchConfig(builtin_backend="alphaxiv").has_backend is True


def test_has_backend_http_endpoint():
    assert SearchConfig(web_search_endpoint="http://x/search").has_backend is True


def test_has_backend_none():
    assert SearchConfig().has_backend is False


# ── build_search_agent guard ─────────────────────────────────────────────────

def test_build_search_agent_requires_backend():
    with pytest.raises(ValueError):
        build_search_agent(
            provider=SimpleNamespace(model="m"),
            search_config=SearchConfig(),
            cwd=".",
        )


def test_build_search_agent_alphaxiv_backend():
    agent = build_search_agent(
        provider=SimpleNamespace(model="m"),
        search_config=SearchConfig(builtin_backend="alphaxiv"),
        cwd=".",
    )
    assert set(agent.tools.keys()) == {"web_search", "web_visit"}
    # The alphaXiv tools are the zero-config subclasses.
    from arbor.core.tools.web.alphaxiv import AlphaXivSearchTool, AlphaXivVisitTool

    assert isinstance(agent.tools["web_search"], AlphaXivSearchTool)
    assert isinstance(agent.tools["web_visit"], AlphaXivVisitTool)


# ── coordinator tool registration ────────────────────────────────────────────

def _tool_names(mode: str) -> list[str]:
    cfg = CoordinatorConfig(cwd=".")
    cfg.search.enabled = True
    cfg.search.builtin_backend = "alphaxiv"
    cfg.search.mode = mode
    tools = get_coordinator_tools(_tree(), cfg, SimpleNamespace(model="m"))
    return sorted(t.name for t in tools)


def test_executor_mode_registers_search_idea_context():
    names = _tool_names("executor")
    assert "SearchIdeaContext" in names
    assert "SearchIdeaContextParallel" in names
    assert "web_search" not in names  # raw tools NOT exposed to coordinator


def test_inline_mode_registers_raw_web_tools():
    names = _tool_names("inline")
    assert "web_search" in names
    assert "web_visit" in names


def test_no_backend_registers_no_search_tools():
    cfg = CoordinatorConfig(cwd=".")
    cfg.search.enabled = True
    cfg.search.builtin_backend = "none"  # and no endpoint
    names = sorted(t.name for t in get_coordinator_tools(_tree(), cfg, SimpleNamespace(model="m")))
    assert "SearchIdeaContext" not in names
    assert "web_search" not in names
