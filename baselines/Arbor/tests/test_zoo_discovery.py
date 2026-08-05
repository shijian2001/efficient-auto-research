"""Tests for the discovery agent stage (``arbor.zoo.agent_stages.discover``).

The agent run is injected, so the orchestration (query → agent → parse the chosen source)
is exercised without a live LLM or live web search.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from arbor.zoo import discover


def _fake_runner(reply: str):
    async def _run(*, cwd: Path, system_prompt: str, task: str, max_turns: int) -> str:
        return reply
    return _run


_CHOICE = (
    "I searched GitHub and arXiv. KernelBench fits best.\n\n"
    '```json\n'
    '{"name": "kernelbench", "source": {"kind": "git", '
    '"url": "https://github.com/ScalingIntelligence/KernelBench"}, '
    '"metric": "speedup, higher is better", "baseline": "torch reference in repo", '
    '"why": "ships an eval + baseline, real headroom"}\n'
    '```\n'
)


def test_discover_picks_a_source(tmp_path: Path) -> None:
    res = asyncio.run(discover("a GPU kernel optimization benchmark",
                               run_agent=_fake_runner(_CHOICE), work_dir=tmp_path / "w"))
    assert res.ok
    assert res.url == "https://github.com/ScalingIntelligence/KernelBench"
    assert res.name == "kernelbench"
    assert res.choice and res.choice["metric"].startswith("speedup")


def test_discover_no_json_is_not_ok(tmp_path: Path) -> None:
    res = asyncio.run(discover("something", run_agent=_fake_runner("I couldn't find anything useful."),
                               work_dir=tmp_path / "w"))
    assert not res.ok and res.url is None
    assert any("no JSON" in n for n in res.notes)


def test_discover_captures_baseline_plan(tmp_path: Path) -> None:
    reply = (
        '```json\n'
        '{"name": "gpqa", "source": {"kind": "hf", "url": "Idavidrein/gpqa"}, '
        '"metric": "accuracy, higher is better", "baseline": "naive RAG", '
        '"baseline_plan": {"source": "implement", "detail": "write a naive RAG baseline"}, '
        '"why": "user wants to climb GPQA"}\n```'
    )
    res = asyncio.run(discover("climb GPQA with a naive RAG baseline",
                               run_agent=_fake_runner(reply), work_dir=tmp_path / "w"))
    assert res.ok
    assert res.baseline_plan == {"source": "implement", "detail": "write a naive RAG baseline"}


def test_discover_baseline_plan_defaults_empty(tmp_path: Path) -> None:
    # The KernelBench reply has no baseline_plan → property is an empty dict, not None.
    res = asyncio.run(discover("a kernel benchmark",
                               run_agent=_fake_runner(_CHOICE), work_dir=tmp_path / "w"))
    assert res.baseline_plan == {}


def test_discover_null_source_is_not_ok(tmp_path: Path) -> None:
    reply = '```json\n{"name": null, "source": null, "why": "nothing suitable found"}\n```'
    res = asyncio.run(discover("obscure", run_agent=_fake_runner(reply), work_dir=tmp_path / "w"))
    assert not res.ok
    assert any("no source url" in n for n in res.notes)


def test_discover_surfaces_agent_error(tmp_path: Path) -> None:
    async def _boom(*, cwd, system_prompt, task, max_turns):
        raise RuntimeError("provider missing")

    res = asyncio.run(discover("x", run_agent=_boom, work_dir=tmp_path / "w"))
    assert not res.ok and any("agent run failed" in n for n in res.notes)
