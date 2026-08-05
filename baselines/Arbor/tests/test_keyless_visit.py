"""Tests for the keyless visit tools (Jina reader + raw-requests fallback)."""

from __future__ import annotations

import asyncio

import arbor.core.tools.web.keyless_visit as K
from arbor.core.tools.web.keyless_visit import (
    JinaVisitTool,
    RoutingVisitTool,
    _html_to_text,
)


class _Resp:
    def __init__(self, text, status=200, ctype="text/html"):
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": ctype}


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><head><style>x{}</style></head><body><p>Hello <b>world</b></p>" \
           "<script>evil()</script></body></html>"
    out = _html_to_text(html)
    assert "Hello" in out and "world" in out
    assert "evil" not in out
    assert "<" not in out


def test_jina_visit_uses_reader(monkeypatch):
    calls = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        return _Resp("# Clean markdown\n\nbody", ctype="text/plain")

    monkeypatch.setattr(K.requests, "get", fake_get)
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000)
    out = asyncio.run(tool.execute(url="https://example.com/x", goal="g"))
    assert "r.jina.ai" in calls["url"]
    assert "Clean markdown" in out


def test_jina_visit_falls_back_to_requests(monkeypatch):
    seq = []

    def fake_get(url, headers=None, timeout=None):
        seq.append(url)
        if url.startswith("https://r.jina.ai/"):
            return _Resp("", status=500)  # jina fails
        return _Resp("<p>Direct page body</p>")  # raw fetch succeeds

    monkeypatch.setattr(K.requests, "get", fake_get)
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000)
    out = asyncio.run(tool.execute(url="https://example.com/x", goal="g"))
    assert any(u.startswith("https://r.jina.ai/") for u in seq)  # tried jina first
    assert "Direct page body" in out


def test_jina_visit_requests_only_skips_jina(monkeypatch):
    seq = []

    def fake_get(url, headers=None, timeout=None):
        seq.append(url)
        return _Resp("<p>raw</p>")

    monkeypatch.setattr(K.requests, "get", fake_get)
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000, use_jina=False)
    asyncio.run(tool.execute(url="https://example.com/x", goal="g"))
    assert all(not u.startswith("https://r.jina.ai/") for u in seq)


def test_jina_visit_both_fail(monkeypatch):
    monkeypatch.setattr(K.requests, "get", lambda *a, **k: _Resp("", status=500))
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000)
    out = asyncio.run(tool.execute(url="https://example.com/x", goal="g"))
    # WebVisitTool._format_block turns a "[visit] ..." failure into the
    # standard "could not be accessed" block.
    assert "could not be accessed" in out


# ── RoutingVisitTool ─────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, label):
        self.label = label
        self.seen = None

    async def execute(self, *, url, goal):
        self.seen = url
        return f"[{self.label}] {url}"


def test_routing_splits_alphaxiv_and_general():
    alpha = _Recorder("alpha")
    jina = _Recorder("jina")
    tool = RoutingVisitTool(cwd=".", jina=jina, alphaxiv=alpha)
    out = asyncio.run(tool.execute(
        url=["https://www.alphaxiv.org/abs/2203.11171", "https://example.com/p"],
        goal="g",
    ))
    assert alpha.seen == ["https://www.alphaxiv.org/abs/2203.11171"]
    assert jina.seen == ["https://example.com/p"]
    assert "[alpha]" in out and "[jina]" in out


def test_routing_without_alphaxiv_sends_all_to_jina():
    jina = _Recorder("jina")
    tool = RoutingVisitTool(cwd=".", jina=jina, alphaxiv=None)
    asyncio.run(tool.execute(
        url=["https://www.alphaxiv.org/abs/2203.11171", "https://example.com/p"],
        goal="g",
    ))
    assert jina.seen == [
        "https://www.alphaxiv.org/abs/2203.11171",
        "https://example.com/p",
    ]


# ── PDF handling (roadmap 1.1d) ──────────────────────────────────────────────

class _PdfResp:
    def __init__(self, content, ctype="application/pdf", status=200):
        self.content = content
        self.text = ""
        self.status_code = status
        self.headers = {"Content-Type": ctype}


def test_requests_routes_pdf_to_parser(monkeypatch):
    monkeypatch.setattr(K, "_pdf_to_text", lambda data: "PARSED PDF TEXT")
    monkeypatch.setattr(K.requests, "get", lambda *a, **k: _PdfResp(b"%PDF-1.4 ..."))
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000, use_jina=False)
    out = asyncio.run(tool.execute(url="https://arxiv.org/pdf/2203.11171", goal="g"))
    assert "PARSED PDF TEXT" in out


def test_requests_pdf_by_url_suffix(monkeypatch):
    # content-type is generic but the URL ends with .pdf → still parsed as PDF
    monkeypatch.setattr(K, "_pdf_to_text", lambda data: "FROM SUFFIX")
    monkeypatch.setattr(K.requests, "get",
                        lambda *a, **k: _PdfResp(b"%PDF", ctype="application/octet-stream"))
    tool = JinaVisitTool(cwd=".", max_content_tokens=1000, use_jina=False)
    out = asyncio.run(tool.execute(url="https://host/paper.pdf", goal="g"))
    assert "FROM SUFFIX" in out


def test_pdf_to_text_graceful_on_garbage():
    # Not a real PDF → returns "" instead of raising.
    assert K._pdf_to_text(b"this is not a pdf") == ""


def test_pdf_to_text_roundtrip():
    # Build a real one-page PDF with pypdf and read it back.
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    # A blank page extracts to empty text but must not raise.
    assert isinstance(K._pdf_to_text(buf.getvalue()), str)
