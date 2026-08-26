"""FileRead tool — read file contents with line numbers, offset/limit, and
media file support. Description ported from Claude Code's FileReadTool."""

from __future__ import annotations

import mimetypes
import os
from typing import Any

from .base import Tool


class FileReadTool(Tool):
    name = "Read"
    description = (
        "Reads a file from the local filesystem, subject to the active path "
        "scope and safety policy. If access is blocked, ask the user to approve "
        "the exact path instead of searching elsewhere.\n"
        "\n"
        "Usage:\n"
        "- The file_path parameter must be an absolute path, not a relative path\n"
        "- By default, it reads up to 2000 lines starting from the beginning of "
        "the file\n"
        "- You can optionally specify a line offset and limit (especially handy "
        "for long files), but it's recommended to read the whole file by not "
        "providing these parameters\n"
        "- Results are returned using cat -n format, with line numbers starting "
        "at 1\n"
        "- This tool can read PDF files (.pdf). For large PDFs, provide the "
        "pages parameter to read specific page ranges (e.g., pages: \"1-5\")\n"
        "- This tool can only read files, not directories. To find files in a "
        "directory, use the Glob tool.\n"
        "- If you read a file that exists but has empty contents you will "
        "receive a warning."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (0-indexed). Default: 0.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Default: 2000.",
            },
        },
        "required": ["file_path"],
    }
    is_read_only = True
    max_result_chars = 200_000

    async def execute(self, **kwargs: Any) -> str:
        file_path: str = kwargs["file_path"]
        offset: int = kwargs.get("offset", 0)
        limit: int = kwargs.get("limit", 2000)

        # Resolve path relative to cwd
        if not os.path.isabs(file_path):
            file_path = os.path.join(self.cwd, file_path)

        file_path, blocked = self.authorize_path(file_path)
        if blocked:
            return f"BLOCKED: {blocked}"

        if not os.path.exists(file_path):
            return f"Error: File not found: {file_path}"

        if os.path.isdir(file_path):
            return (
                f"Error: {file_path} is a directory, not a file. "
                f"Use Bash with 'ls' to list directories."
            )

        # Check for binary files
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type and not mime_type.startswith("text/") and mime_type not in (
            "application/json", "application/xml", "application/javascript",
            "application/x-python-code", "application/toml", "application/yaml",
            "application/x-sh", "application/x-shellscript",
        ):
            # Check for known structured formats we can handle
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".pdf":
                return self._read_pdf(file_path)
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"):
                return f"[Image file: {file_path} ({mime_type}). Use Bash to process images if needed.]"
            if ext == ".ipynb":
                return self._read_notebook(file_path)
            # Other binary
            file_size = os.path.getsize(file_path)
            return f"[Binary file: {file_path} ({mime_type}, {file_size} bytes). Cannot display as text.]"

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except PermissionError:
            return f"Error: Permission denied: {file_path}"
        except Exception as e:
            return f"Error reading file: {e}"

        if not all_lines:
            return f"Warning: File {file_path} exists but is empty."

        total_lines = len(all_lines)
        selected = all_lines[offset: offset + limit]

        # Format with line numbers (1-indexed display)
        numbered = []
        for i, line in enumerate(selected, start=offset + 1):
            numbered.append(f"{i}\t{line.rstrip()}")

        result = "\n".join(numbered)

        if offset + limit < total_lines:
            result += (
                f"\n\n[Showing lines {offset + 1}-{offset + len(selected)} "
                f"of {total_lines} total. Use offset/limit to read more.]"
            )

        return self._truncate(result)

    def _read_pdf(self, file_path: str) -> str:
        """Attempt to read PDF using available libraries."""
        try:
            import subprocess
            # Try pdftotext first (poppler-utils)
            result = subprocess.run(
                ["pdftotext", "-layout", file_path, "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return self._truncate(f"[PDF: {file_path}]\n\n{result.stdout}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return (
            f"[PDF file: {file_path}. "
            f"Install pdftotext (poppler-utils) to read PDFs, "
            f"or use Bash: `pdftotext {file_path} -`]"
        )

    def _read_notebook(self, file_path: str) -> str:
        """Read a Jupyter notebook as formatted text."""
        import json
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                nb = json.load(f)
        except Exception as e:
            return f"Error reading notebook: {e}"

        cells = nb.get("cells", [])
        parts = [f"[Jupyter Notebook: {file_path}, {len(cells)} cells]\n"]

        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")
            source = "".join(cell.get("source", []))
            parts.append(f"--- Cell {i + 1} ({cell_type}) ---")
            parts.append(source)

            # Include outputs for code cells
            outputs = cell.get("outputs", [])
            for out in outputs:
                if "text" in out:
                    parts.append("[Output]\n" + "".join(out["text"]))
                elif "data" in out:
                    if "text/plain" in out["data"]:
                        parts.append("[Output]\n" + "".join(out["data"]["text/plain"]))

            parts.append("")

        return self._truncate("\n".join(parts))
