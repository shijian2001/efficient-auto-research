"""Tests for the Arbor MCP server adapter (`arbor.mcp.server`).

The server build requires the optional MCP SDK; these tests skip cleanly if it
is not installed. They assert the advertised tool surface and — crucially — that
constructing the server pulls in no LLM/provider code and needs no API key.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from arbor.mcp import server as srv

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tool_names_surface_is_stable() -> None:
    # The documented contract host agents rely on.
    assert set(srv.TOOL_NAMES) == {
        "tree_view", "tree_add_node", "tree_update_node", "tree_prune",
        "tree_set_meta", "eval_run", "worktree_create", "worktree_remove",
        "git_merge_branch", "generate_report", "open_dashboard",
        "scaffold_benchmark",
    }


def test_build_server_registers_every_tool() -> None:
    pytest.importorskip("mcp", reason="MCP SDK (the [mcp] extra) is not installed")
    server = srv.build_server()
    # FastMCP exposes registered tools asynchronously; resolve in a loop.
    import asyncio

    tools = asyncio.run(server.list_tools())
    registered = {t.name for t in tools}
    assert registered == set(srv.TOOL_NAMES)


def test_server_build_requires_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server builds successfully with no provider API keys set."""
    pytest.importorskip("mcp", reason="MCP SDK (the [mcp] extra) is not installed")
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    server = srv.build_server()  # must not raise / must not need credentials
    assert server is not None


def test_server_import_does_not_load_llm_modules() -> None:
    """Building the server must load no LLM/agent code (it makes no model calls).

    Run in a *fresh* subprocess so the check is hermetic — other tests in the
    suite import the LLM stack, which would otherwise pollute ``sys.modules``.
    """
    pytest.importorskip("mcp", reason="MCP SDK (the [mcp] extra) is not installed")
    script = textwrap.dedent(
        f"""
        import importlib.util, sys
        from pathlib import Path
        # Bootstrap the `arbor` package from src/ (mirrors tests/conftest.py).
        root = Path(r{str(_REPO_ROOT)!r})
        spec = importlib.util.spec_from_file_location(
            "arbor", root / "src" / "__init__.py",
            submodule_search_locations=[str(root / "src")],
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["arbor"] = mod
        spec.loader.exec_module(mod)

        from arbor.mcp import server as srv
        srv.build_server()

        forbidden = ("arbor.core.agent", "arbor.core.llm", "arbor.core.llm.claude")
        leaked = [m for m in forbidden if m in sys.modules]
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        print("CLEAN")
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"keyless server loaded LLM modules:\n{proc.stdout}\n{proc.stderr}"
    assert "CLEAN" in proc.stdout
