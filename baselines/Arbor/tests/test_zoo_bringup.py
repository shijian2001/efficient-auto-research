"""Tests for the bring-up agent stage (``arbor.zoo.agent_stages``).

The agent run is injected, so the orchestration (agent writes files → eval runs → score
parsed → verify) is exercised without a live LLM. A fake runner stands in for the agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from arbor.zoo import bringup, real_agent_runner

_PROVENANCE = (
    "# Provenance\n\n## Source\nx\n## Setup & environment\nx\n## Baseline\nx\n"
    "## Contamination assessment\nx\n## Caveats\nx\n"
)
_README = "# demo\n\nA demo benchmark.\n\n## The task\nx\n## Metric\nx\n"


def _fake_runner(files: dict[str, str]):
    """A stand-in agent: writes the files a real bring-up agent would, returns a transcript."""
    async def _run(*, cwd: Path, system_prompt: str, task: str, max_turns: int) -> str:
        for rel, content in files.items():
            p = cwd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return "bring-up done"
    return _run


def _good_files(score: str = "1.0") -> dict[str, str]:
    return {
        "eval.sh": f"#!/usr/bin/env bash\necho 'score: {score}'\n",
        "solution.py": "# baseline\n",
        "README.md": _README,
        "PROVENANCE.md": _PROVENANCE,
    }


def test_bringup_success(tmp_path: Path) -> None:
    pack = tmp_path / "p"
    pack.mkdir()
    res = asyncio.run(bringup(pack, run_agent=_fake_runner(_good_files("0.5"))))
    assert res.dev_score == 0.5
    assert res.ran
    assert res.ok
    assert not [r for r in res.verify if r.status == "fail"]
    assert res.transcript == "bring-up done"


def test_bringup_no_score_still_drafts(tmp_path: Path) -> None:
    # A non-running eval is a runnable draft, not a failure (we don't force-run): ok stays
    # True (artifacts verify), but ran is False and a note explains why.
    pack = tmp_path / "p"
    pack.mkdir()
    files = _good_files()
    files["eval.sh"] = "#!/usr/bin/env bash\necho 'nothing here'\n"
    res = asyncio.run(bringup(pack, run_agent=_fake_runner(files)))
    assert res.dev_score is None
    assert not res.ran
    assert res.ok
    assert any("runnable draft" in n for n in res.notes)


def test_bringup_failed_verify_is_incomplete(tmp_path: Path) -> None:
    pack = tmp_path / "p"
    pack.mkdir()
    files = _good_files()
    del files["PROVENANCE.md"]  # missing PROVENANCE → verify fails
    res = asyncio.run(bringup(pack, run_agent=_fake_runner(files)))
    assert not res.ok
    assert any(r.status == "fail" for r in res.verify)


def test_bringup_threads_instruction_and_plan(tmp_path: Path) -> None:
    # The user's request and the baseline plan must reach the agent's task text.
    pack = tmp_path / "p"
    pack.mkdir()
    seen: dict = {}

    async def _spy(*, cwd: Path, system_prompt: str, task: str, max_turns: int) -> str:
        seen["task"] = task
        for rel, content in _good_files().items():
            (cwd / rel).write_text(content)
        return "ok"

    asyncio.run(bringup(
        pack, run_agent=_spy,
        instruction="climb GPQA, design a self-consistency method",
        baseline_plan={"source": "implement", "detail": "self-consistency over 5 samples"},
    ))
    assert "self-consistency method" in seen["task"]
    assert "implement" in seen["task"] and "5 samples" in seen["task"]


def test_bringup_surfaces_agent_error(tmp_path: Path) -> None:
    pack = tmp_path / "p"
    pack.mkdir()

    async def _boom(*, cwd, system_prompt, task, max_turns):
        raise RuntimeError("provider exploded")

    res = asyncio.run(bringup(pack, run_agent=_boom))
    assert not res.ok
    assert any("agent run failed" in n for n in res.notes)


def test_real_agent_runner_is_callable() -> None:
    # Construction is cheap and import-light; running it needs a live provider.
    assert callable(real_agent_runner())
