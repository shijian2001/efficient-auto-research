"""Arbor MCP integration — deterministic, keyless tools for host coding agents.

This package exposes Arbor's *non-LLM* coordinator operations (Idea Tree state,
evaluation, git worktrees, guarded merges, report generation) over the Model
Context Protocol so a host agent (Claude Code, Codex, …) can drive a full Arbor
research workflow **using its own model** — no Arbor API key, no separate Arbor
runtime, no LLM calls inside Arbor.

Layout:

* :mod:`arbor.mcp.session_ops` — the deterministic operations, built on the real
  :class:`arbor.coordinator.idea_tree.IdeaTree` and
  :func:`arbor.report.generator.generate_report`. Importable and unit-tested on
  its own; it has **no** dependency on the MCP SDK.
* :mod:`arbor.mcp.server` — a thin MCP server (``FastMCP``) that maps each tool
  call onto a :mod:`session_ops` function. Requires the optional ``mcp`` extra
  (``pip install arbor-agent[mcp]``).

The split keeps the valuable logic testable without standing up an MCP client,
and keeps the SDK an optional dependency.
"""
