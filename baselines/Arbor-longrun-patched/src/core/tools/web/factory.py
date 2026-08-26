"""Factories that turn a (duck-typed) SearchConfig into the right web tools.

Both the coordinator tool registry and the SearchAgent builder call these so
backend selection lives in ONE place. ``sc`` is read by attribute only (no
import of the coordinator config type), keeping the core layer decoupled.

Search tool selection:
- ``["alphaxiv"]`` only            → ``AlphaXivSearchTool`` (zero-config, tested path)
- ``["endpoint"]`` only            → legacy single-endpoint ``WebSearchTool``
- anything else / multiple         → multi-backend ``WebSearchTool(backends=…)``

Visit tool selection (``visit_backend``: auto|jina|requests|alphaxiv|endpoint):
- a browse endpoint configured (or ``endpoint``) → ``WebVisitTool``
- ``alphaxiv``, or auto with alphaXiv as the only backend → ``AlphaXivVisitTool``
- otherwise → keyless ``JinaVisitTool`` (raw-requests fallback), wrapped in a
  ``RoutingVisitTool`` when alphaXiv is also a backend so paper URLs still use
  the SDK for full text.
"""

from __future__ import annotations

from typing import Any

from ..base import Tool


def build_web_search_tool(sc: Any, *, cwd: str, workspace_dir: str | None = None) -> Tool | None:
    from .alphaxiv import AlphaXivSearchTool
    from .backends import build_search_backends, resolve_backend_names
    from .search import WebSearchTool

    names = resolve_backend_names(sc)
    if not names:
        return None
    if names == ["alphaxiv"]:
        return AlphaXivSearchTool(cwd=cwd, workspace_dir=workspace_dir)
    if names == ["endpoint"]:
        return WebSearchTool(
            cwd=cwd,
            endpoint_url=sc.web_search_endpoint,
            provider=getattr(sc, "web_search_provider", "google"),
            api_key=getattr(sc, "web_search_api_key", None),
            workspace_dir=workspace_dir,
        )
    return WebSearchTool(
        cwd=cwd, backends=build_search_backends(sc), workspace_dir=workspace_dir
    )


def build_web_visit_tool(sc: Any, *, cwd: str, workspace_dir: str | None = None) -> Tool | None:
    from .alphaxiv import AlphaXivVisitTool
    from .backends import resolve_backend_names
    from .keyless_visit import JinaVisitTool, RoutingVisitTool
    from .visit import WebVisitTool

    names = resolve_backend_names(sc)
    if not names:
        return None
    max_tok = getattr(sc, "visit_max_content_tokens", 2048)
    vb = (getattr(sc, "visit_backend", "auto") or "auto").lower()
    browse_ep = getattr(sc, "web_browse_endpoint", None)

    if (vb == "endpoint" or (vb == "auto" and browse_ep)) and browse_ep:
        return WebVisitTool(
            cwd=cwd,
            endpoint_url=browse_ep,
            max_content_tokens=max_tok,
            api_key=getattr(sc, "web_browse_api_key", None),
            workspace_dir=workspace_dir,
        )
    if vb == "alphaxiv" or (vb == "auto" and names == ["alphaxiv"]):
        return AlphaXivVisitTool(
            cwd=cwd, max_content_tokens=max_tok, workspace_dir=workspace_dir
        )

    # Keyless fetcher (auto/jina/requests). Route paper URLs to the alphaXiv SDK
    # when alphaXiv is one of the search backends.
    jina = JinaVisitTool(
        cwd=cwd,
        max_content_tokens=max_tok,
        jina_api_key=getattr(sc, "jina_api_key", None),
        use_jina=(vb != "requests"),
        workspace_dir=workspace_dir,
    )
    if "alphaxiv" in names:
        alpha = AlphaXivVisitTool(
            cwd=cwd, max_content_tokens=max_tok, workspace_dir=workspace_dir
        )
        return RoutingVisitTool(
            cwd=cwd, jina=jina, alphaxiv=alpha, workspace_dir=workspace_dir
        )
    return jina
