"""Grep tool — search file contents using ripgrep with fallback.
Description ported from Claude Code's GrepTool."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from .base import Tool


class GrepTool(Tool):
    name = "Grep"
    description = (
        "A powerful search tool built on ripgrep.\n"
        "\n"
        "  Usage:\n"
        "  - ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` "
        "as a Bash command. The Grep tool has been optimized for correct "
        "output formatting.\n"
        "  - Supports full regex syntax (e.g., \"log.*Error\", "
        "\"function\\\\s+\\\\w+\")\n"
        "  - Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") "
        "or type parameter (e.g., \"js\", \"py\", \"rust\")\n"
        "  - Output modes: \"content\" shows matching lines, "
        "\"files_with_matches\" shows only file paths (default), "
        "\"count\" shows match counts\n"
        "  - Use Executor tool for open-ended searches requiring multiple "
        "rounds\n"
        "  - Pattern syntax: Uses ripgrep (not grep) — literal braces need "
        "escaping (use `interface\\\\{\\\\}` to find `interface{}` in Go "
        "code)\n"
        "  - Multiline matching: By default patterns match within single "
        "lines only. For cross-line patterns, use `multiline: true`"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Default: working directory.",
            },
            "glob": {
                "type": "string",
                "description": (
                    'Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}") '
                    "— maps to rg --glob"
                ),
            },
            "type": {
                "type": "string",
                "description": (
                    "File type to search (e.g. 'py', 'js', 'rust'). "
                    "More efficient than glob for standard file types."
                ),
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": (
                    'Output mode. "content" shows matching lines with context, '
                    '"files_with_matches" shows file paths only (default), '
                    '"count" shows match counts per file.'
                ),
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "Case insensitive search (rg -i). Default: false.",
            },
            "context_lines": {
                "type": "integer",
                "description": (
                    "Lines of context before and after each match (for content "
                    "mode). Maps to rg -C. Default: 0."
                ),
            },
            "max_results": {
                "type": "integer",
                "description": (
                    "Maximum number of output lines/entries. Default: 250. "
                    "Pass 0 for unlimited (use sparingly)."
                ),
            },
            "multiline": {
                "type": "boolean",
                "description": (
                    "Enable multiline mode where . matches newlines and "
                    "patterns can span lines (rg -U). Default: false."
                ),
            },
        },
        "required": ["pattern"],
    }
    is_read_only = True
    max_result_chars = 50_000

    async def execute(self, **kwargs: Any) -> str:
        pattern: str = kwargs["pattern"]
        path: str = kwargs.get("path", self.cwd)
        glob_filter = kwargs.get("glob")
        type_filter = kwargs.get("type")
        output_mode: str = kwargs.get("output_mode", "files_with_matches")
        case_insensitive: bool = kwargs.get("case_insensitive", False)
        context_lines: int = kwargs.get("context_lines", 0)
        max_results: int = kwargs.get("max_results", 250)
        multiline: bool = kwargs.get("multiline", False)

        if not os.path.isabs(path):
            path = os.path.join(self.cwd, path)

        path, blocked = self.authorize_path(path)
        if blocked:
            return f"BLOCKED: {blocked}"

        # Check for ripgrep, fallback to grep
        rg_path = shutil.which("rg")
        if not rg_path:
            return await self._fallback_grep(pattern, path, case_insensitive, max_results)

        cmd = [rg_path, "--no-heading", "--color=never"]

        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")

        # Options
        if case_insensitive:
            cmd.append("-i")
        if context_lines > 0 and output_mode == "content":
            cmd.extend(["-C", str(context_lines)])
        if output_mode == "content":
            cmd.append("-n")  # Line numbers
        if multiline:
            cmd.extend(["-U", "--multiline-dotall"])

        # File filtering
        if glob_filter:
            cmd.extend(["--glob", glob_filter])
        if type_filter:
            cmd.extend(["--type", type_filter])

        # Exclude VCS and build directories
        for d in [".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv"]:
            cmd.extend(["--glob", f"!{d}"])

        cmd.extend(["--", pattern, path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return "[Search timed out after 30s]"
        except Exception as e:
            return f"[Error running ripgrep: {e}]"

        output = stdout.decode("utf-8", errors="replace")

        if proc.returncode == 1:
            return "No matches found."
        if proc.returncode not in (0, 1):
            err = stderr.decode("utf-8", errors="replace")
            return f"Error: {err.strip()}"

        # Truncate to max_results lines
        lines = output.split("\n")
        if max_results > 0 and len(lines) > max_results:
            lines = lines[:max_results]
            output = "\n".join(lines) + f"\n\n[Showing first {max_results} of more results]"
        else:
            output = "\n".join(lines)

        return self._truncate(output.strip())

    async def _fallback_grep(
        self, pattern: str, path: str, case_insensitive: bool, max_results: int,
    ) -> str:
        """Fallback to system grep when ripgrep is not installed."""
        cmd = ["grep", "-rn", "--color=never"]
        if case_insensitive:
            cmd.append("-i")
        for d in [".git", "node_modules", "__pycache__", ".venv"]:
            cmd.extend(["--exclude-dir", d])
        cmd.extend(["--", pattern, path])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return "[Search timed out after 30s]"
        except Exception as e:
            return f"[Error running grep: {e}]"

        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode == 1:
            return "No matches found."
        if proc.returncode not in (0, 1):
            return f"Error: {stderr.decode('utf-8', errors='replace').strip()}"

        lines = output.split("\n")
        if max_results > 0 and len(lines) > max_results:
            lines = lines[:max_results]
            output = "\n".join(lines) + (
                f"\n\n[Showing first {max_results} results. "
                f"Install ripgrep for better performance: https://github.com/BurntSushi/ripgrep]"
            )
        else:
            output = "\n".join(lines)

        return self._truncate(output.strip())
