"""Tests for the collection spine (cache / acquire / scaffold / collect / CLI).

Deterministic only — no LLM, no network. Git acquisition is exercised against a local
throwaway repo; the cache is redirected via ARBOR_BENCHMARK_CACHE.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from arbor.cli.app import app
from arbor.zoo import (
    GitRepoAcquirer,
    Sources,
    cache_root,
    collect,
    select_acquirer,
)
from arbor.zoo.cache import Manifest, SourceRecord, load_manifest, record_source, sha256_file


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_repo(root: Path) -> Path:
    """A throwaway git repo with one commit (the 'benchmark' to clone)."""
    repo = root / "src_repo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# upstream benchmark\n")
    (repo / "baseline.py").write_text("def solve(x): return x\n")
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "baseline", cwd=repo)
    return repo


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "cache"
    monkeypatch.setenv("ARBOR_BENCHMARK_CACHE", str(d))
    return d


# ── cache ─────────────────────────────────────────────────────────────────────

def test_cache_root_env_override(cache: Path) -> None:
    assert cache_root() == cache.resolve()


def test_manifest_roundtrip(cache: Path) -> None:
    d = cache / "demo"
    record_source(d, "demo", SourceRecord(kind="git", locator="u", commit="abc", license="MIT"))
    m = load_manifest(d)
    assert isinstance(m, Manifest) and m.name == "demo"
    assert m.sources[0].locator == "u" and m.sources[0].commit == "abc"


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_bytes(b"hello")
    assert sha256_file(f) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


# ── acquire ───────────────────────────────────────────────────────────────────

def test_select_acquirer_git_vs_hf() -> None:
    assert select_acquirer("https://github.com/o/r").kind == "git"
    assert select_acquirer("git@github.com:o/r.git").kind == "git"
    assert select_acquirer("hf:openai/gsm8k").kind == "hf"
    assert select_acquirer("datasets/openai/gsm8k").kind == "hf"
    assert select_acquirer("not a spec at all !!!") is None


def test_git_acquirer_clones_and_records(cache: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    acq = GitRepoAcquirer()
    assert acq.matches(str(repo))  # a local git dir matches
    acquired = acq.acquire(Sources(kind="git", locator=str(repo)), "demo")
    assert (acquired.materials_dir / "baseline.py").exists()
    assert acquired.manifest.sources[0].kind == "git"
    assert acquired.manifest.sources[0].commit  # HEAD recorded


def test_git_acquirer_idempotent(cache: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    acq = GitRepoAcquirer()
    acq.acquire(Sources(kind="git", locator=str(repo)), "demo")
    # second call reuses the existing clone (no crash)
    again = acq.acquire(Sources(kind="git", locator=str(repo)), "demo")
    assert (again.materials_dir / "baseline.py").exists()


# ── scaffold ──────────────────────────────────────────────────────────────────
# (the scaffolder itself is covered by tests/test_zoo_scaffold.py; collect's use of
# it is exercised by test_collect_spine below)


# ── collect (end-to-end spine) ─────────────────────────────────────────────────

def test_collect_spine(cache: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    zoo = tmp_path / "zoo"
    result = collect(str(repo), name="demo", dest_root=zoo)
    assert result.ok
    assert result.acquired is not None and (result.acquired.materials_dir / "baseline.py").exists()
    assert result.draft_pack_dir == zoo / "demo" and (zoo / "demo" / "README.md").exists()
    assert [r for r in result.verify_results if r.status == "fail"] == []
    assert result.pending  # human/agent steps remain


def test_collect_unknown_spec(tmp_path: Path) -> None:
    result = collect("?!? not a spec", name="x", dest_root=tmp_path)
    assert not result.ok and any("no acquirer" in n for n in result.notes)


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_add(cache: Path, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    zoo = tmp_path / "zoo"
    result = CliRunner().invoke(
        app, ["benchmark", "add", str(repo), "--name", "demo", "--dest", str(zoo)]
    )
    assert result.exit_code == 0, result.output
    assert (zoo / "demo" / "README.md").exists()
    assert "still to do" in result.output


def test_cli_add_url_without_name_errors(cache: Path, tmp_path: Path) -> None:
    # A URL/path spec skips discovery; without --name (and none to infer) it errors.
    repo = _make_repo(tmp_path)
    result = CliRunner().invoke(app, ["benchmark", "add", str(repo)])
    assert result.exit_code == 2


def test_cli_add_query_path_brings_up(cache: Path, tmp_path: Path, monkeypatch) -> None:
    # A natural-language request: discovery + bring-up are faked (no LLM/network), but the
    # spine in between (acquire + scaffold) runs for real, and the original request +
    # baseline plan must be threaded into bring-up.
    from arbor.cli.commands import benchmark_cmd
    from arbor.zoo import BringupResult, DiscoveryResult

    repo = _make_repo(tmp_path)
    zoo = tmp_path / "zoo"
    captured: dict = {}

    async def fake_discover(query, *, run_agent, work_dir, max_turns):
        captured["query"] = query
        return DiscoveryResult(
            choice={"name": "demo", "source": {"kind": "git", "url": str(repo)},
                    "metric": "accuracy, higher better", "baseline": "naive rag",
                    "baseline_plan": {"source": "implement", "detail": "naive rag baseline"},
                    "why": "fits"},
            ok=True,
        )

    async def fake_bringup(pack_dir, *, run_agent, materials_dir=None, instruction="",
                           baseline_plan=None, max_turns=40, eval_timeout=600):
        captured["instruction"] = instruction
        captured["plan"] = baseline_plan
        return BringupResult(ok=True, ran=False, notes=["runnable draft ready"])

    monkeypatch.setattr(benchmark_cmd, "discover", fake_discover)
    monkeypatch.setattr(benchmark_cmd, "bringup", fake_bringup)

    result = CliRunner().invoke(
        app, ["benchmark", "add", "get me the WebThinker GPQA benchmark", "--yes",
              "--dest", str(zoo)]
    )
    assert result.exit_code == 0, result.output
    assert (zoo / "demo" / "README.md").exists()
    assert captured["query"] == "get me the WebThinker GPQA benchmark"
    # the user's original words reach bring-up (so a baseline can be implemented to them)
    assert captured["instruction"] == "get me the WebThinker GPQA benchmark"
    assert captured["plan"] == {"source": "implement", "detail": "naive rag baseline"}
    assert "runnable draft ready" in result.output
