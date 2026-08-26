"""Tests for the ``arbor idea-check`` CLI command.

The SearchAgent and provider are fully patched — no network, no credentials.
"""

from __future__ import annotations

import json


import arbor.cli.user_config as user_config
import arbor.coordinator.main as coordinator_main
import arbor.search_agent.agent as search_agent_agent
from arbor.cli.commands.idea_check_cmd import idea_check_command

_CANNED = json.dumps(
    {
        "summary": "Plenty of tree-search planning work exists.",
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


class _FakeAgent:
    def __init__(self, out: str):
        self._out = out

    async def run(self, _prompt: str) -> str:
        return self._out


def _patch_all(monkeypatch, agent_out: str = _CANNED):
    monkeypatch.setattr(user_config, "llm_defaults", lambda: {})
    monkeypatch.setattr(coordinator_main, "create_provider", lambda cfg: object())
    monkeypatch.setattr(
        search_agent_agent, "build_search_agent", lambda **kw: _FakeAgent(agent_out)
    )


def test_idea_check_renders_report_english(monkeypatch, capsys):
    _patch_all(monkeypatch)
    idea_check_command(
        hypothesis="Tree search over plans for code generation",
        focus=None,
        model=None,
        as_json=False,
        cwd=None,
    )
    out = capsys.readouterr().out
    assert "Tree of Thoughts" in out          # paper title preserved
    assert "PARTIAL OVERLAP" in out           # localized novelty badge (en)
    assert "Closest related work" in out      # english section label


def test_idea_check_renders_report_chinese(monkeypatch, capsys):
    # A Chinese idea => Chinese section labels + Chinese novelty badge.
    _patch_all(monkeypatch)
    idea_check_command(
        hypothesis="用树搜索在代码生成中规划方案",
        focus=None,
        model=None,
        as_json=False,
        cwd=None,
    )
    out = capsys.readouterr().out
    assert "想法新颖性审查" in out             # localized panel title
    assert "部分重叠" in out                    # localized novelty badge (zh)


def test_idea_check_json_mode(monkeypatch, capsys):
    _patch_all(monkeypatch)
    idea_check_command(
        hypothesis="Tree search over plans",
        focus=None,
        model=None,
        as_json=True,
        cwd=None,
    )
    out = capsys.readouterr().out
    # Raw JSON should round-trip (no report rendering, no status noise on stdout).
    assert json.loads(out.strip())["novelty_assessment"] == "partial-overlap"


def test_idea_check_unparseable_output_falls_back(monkeypatch, capsys):
    _patch_all(monkeypatch, agent_out="not json at all")
    idea_check_command(
        hypothesis="x",
        focus=None,
        model=None,
        as_json=False,
        cwd=None,
    )
    out = capsys.readouterr().out
    assert "not json at all" in out
