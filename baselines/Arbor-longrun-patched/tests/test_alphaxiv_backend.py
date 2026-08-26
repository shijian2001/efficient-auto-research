"""Unit tests for the zero-config alphaXiv search backend.

All alphaXiv SDK access is faked via ``_load_axv`` monkeypatching — these tests
never touch the network and do not require the optional ``alphaxiv-py`` package.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


from arbor.core.tools.web import alphaxiv as ax
from arbor.core.tools.web.alphaxiv import (
    AlphaXivSearchTool,
    AlphaXivVisitTool,
    _paper_id,
)


class _FakeError(Exception):
    pass


class _FakeClient:
    """Async-context-manager stand-in for ``alphaxiv.AlphaXivClient``."""

    def __init__(self, *, search=None, papers=None):
        self.search = search
        self.papers = papers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_fake(monkeypatch, *, search=None, papers=None):
    client = _FakeClient(search=search, papers=papers)
    monkeypatch.setattr(ax, "_load_axv", lambda: (lambda: client, _FakeError))
    return client


def _paper(pid="2401.01234", title="A Paper", abstract="We do things.", names=("Ann",), date="2024-01-01"):
    authors = [SimpleNamespace(display_name=n) for n in names]
    return SimpleNamespace(
        canonical_id=pid,
        universal_paper_id=pid,
        title=title,
        abstract=abstract,
        authors=authors,
        publication_date=date,
        topics=[],
        github_url=None,
    )


# ── id parsing ───────────────────────────────────────────────────────────────

def test_paper_id_parsing():
    assert _paper_id("https://www.alphaxiv.org/abs/2401.01234v2") == "2401.01234v2"
    assert _paper_id("https://www.alphaxiv.org/abs/1706.03762") == "1706.03762"
    assert _paper_id("https://example.com/foo") is None


# ── search ───────────────────────────────────────────────────────────────────

def test_search_maps_papers_to_candidate_list(monkeypatch):
    async def papers_rich(q):
        return [_paper(pid="2401.01234", title="Tree Search QA"),
                _paper(pid="2402.05555", title="Scratchpad Reasoning")]

    _install_fake(monkeypatch, search=SimpleNamespace(papers_rich=papers_rich))
    tool = AlphaXivSearchTool(cwd=".")
    out = asyncio.run(tool.execute(query=["multi-hop QA"]))

    assert "Search summary for 1 query" in out
    assert "https://www.alphaxiv.org/abs/2401.01234" in out
    assert "Tree Search QA" in out
    assert "Scratchpad Reasoning" in out


def test_search_no_results(monkeypatch):
    async def papers_rich(q):
        return []

    _install_fake(monkeypatch, search=SimpleNamespace(papers_rich=papers_rich))
    tool = AlphaXivSearchTool(cwd=".")
    out = asyncio.run(tool.execute(query=["nonexistent topic"]))
    assert "no results" in out.lower()


def test_search_handles_alphaxiv_error_per_query(monkeypatch):
    async def papers_rich(q):
        raise _FakeError("rate limited")

    _install_fake(monkeypatch, search=SimpleNamespace(papers_rich=papers_rich))
    tool = AlphaXivSearchTool(cwd=".")
    out = asyncio.run(tool.execute(query=["x"]))
    assert "failed" in out.lower()
    assert "rate limited" in out


# ── visit cascade ────────────────────────────────────────────────────────────

def _papers_ns(*, full_text=None, overview=None, get=None):
    async def _ft(pid):
        if full_text is None:
            raise _FakeError("no full text")
        return full_text

    async def _ov(pid):
        if overview is None:
            raise _FakeError("no overview")
        return overview

    async def _get(pid):
        if get is None:
            raise _FakeError("not found")
        return get

    return SimpleNamespace(full_text=_ft, overview=_ov, get=_get)


def test_visit_prefers_full_text(monkeypatch):
    papers = _papers_ns(full_text=SimpleNamespace(text="FULL TEXT BODY here."))
    _install_fake(monkeypatch, papers=papers)
    tool = AlphaXivVisitTool(cwd=".")
    out = asyncio.run(tool.execute(url="https://www.alphaxiv.org/abs/2401.01234", goal="g"))
    assert "FULL TEXT BODY" in out


def test_visit_falls_back_to_overview(monkeypatch):
    s = SimpleNamespace(
        summary="An overview summary.",
        original_problem=["P1"],
        solution=["S1"],
        key_insights=["I1"],
        results=["R1"],
    )
    overview = SimpleNamespace(title="T", summary=s)
    papers = _papers_ns(full_text=None, overview=overview)
    _install_fake(monkeypatch, papers=papers)
    tool = AlphaXivVisitTool(cwd=".")
    out = asyncio.run(tool.execute(url="https://www.alphaxiv.org/abs/2401.01234", goal="g"))
    assert "An overview summary." in out
    assert "P1" in out and "S1" in out


def test_visit_falls_back_to_abstract(monkeypatch):
    paper = SimpleNamespace(version=SimpleNamespace(abstract="ABSTRACT ONLY."))
    papers = _papers_ns(full_text=None, overview=None, get=paper)
    _install_fake(monkeypatch, papers=papers)
    tool = AlphaXivVisitTool(cwd=".")
    out = asyncio.run(tool.execute(url="https://www.alphaxiv.org/abs/2401.01234", goal="g"))
    assert "ABSTRACT ONLY." in out


def test_visit_non_alphaxiv_url(monkeypatch):
    # A non-alphaXiv URL never resolves to a paper id; the visit degrades to a
    # clean fail block (referencing the url) rather than raising.
    _install_fake(monkeypatch, papers=_papers_ns())
    tool = AlphaXivVisitTool(cwd=".")
    out = asyncio.run(tool.execute(url="https://example.com/foo", goal="g"))
    assert "https://example.com/foo" in out
    assert "could not be accessed" in out.lower()


# ── missing package ──────────────────────────────────────────────────────────

def test_missing_package_surfaces_actionable_message(monkeypatch):
    def _raise():
        raise RuntimeError(ax._MISSING_PKG_MSG)

    monkeypatch.setattr(ax, "_load_axv", _raise)
    tool = AlphaXivSearchTool(cwd=".")
    out = asyncio.run(tool.execute(query=["x"]))
    assert "search-failed" in out
    assert "alphaxiv-py" in out
    assert "3.12" in out
