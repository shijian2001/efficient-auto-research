"""Bash tool — execute shell commands with timeout, background execution, and
dangerous command detection. Ported from Claude Code's BashTool."""

# pylint: disable=broad-exception-caught

from __future__ import annotations

import asyncio
import copy
import os
import re
import signal
import uuid
from typing import Any

from .base import Tool

# ---------------------------------------------------------------------------
# Dangerous command patterns
# ---------------------------------------------------------------------------

_BACKGROUND_CHECK = re.compile(r"echo\s+\$BACKGROUND_([a-f0-9]+)")
_DEFAULT_TIMEOUT_SECONDS = 600
_LONG_COMMAND_TIMEOUT_SECONDS = 1_800
_TERMINATE_GRACE_SECONDS = 5

_LONG_RUNNING_HINTS = (
    "run_eval.py",
    "python run_eval",
    "python3 run_eval",
    "uv run python run_eval",
    "bash eval.sh",
    "./eval.sh",
    "python train",
    "python3 train",
    "uv run python train",
    "train.py",
    "pytest",
    "make test",
    "npm test",
    "cargo test",
    "evaluate",
)


def _looks_long_running(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    return any(hint in normalized for hint in _LONG_RUNNING_HINTS)


async def terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: int = _TERMINATE_GRACE_SECONDS,
) -> None:
    """Terminate a timed-out subprocess, escalating only if it ignores SIGTERM.

    Kills the whole process group (the child is a session leader via
    ``start_new_session=True``), so grandchildren don't orphan. Shared with
    :mod:`run_training`.
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            proc.terminate()
        except ProcessLookupError:
            await proc.wait()  # already gone — just reap so returncode is set
            return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except ProcessLookupError:
                await proc.wait()
                return
        await proc.wait()


_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgit\s+checkout\s+--\s"), "Discard changes"),
    (re.compile(r"\bgit\s+branch\s+-[a-zA-Z]*D"), "Force delete branch"),
    (re.compile(r"\b(DROP|TRUNCATE|DELETE\s+FROM)\b", re.IGNORECASE), "Destructive SQL"),
    (re.compile(r"\bkill\s+-9"), "Force kill process"),
    (re.compile(r">\s*/dev/sd[a-z]"), "Write to block device"),
    (re.compile(r"\bmkfs\b"), "Format filesystem"),
    (re.compile(r"\bdd\s+.*of=/"), "Raw disk write"),
]

# Blocked patterns — these commands are REFUSED outright (not just warned).
_BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|.*-[a-zA-Z]*f[a-zA-Z]*)"), "Recursive/force delete is blocked"),
    (re.compile(r"\bgit\s+push\s+.*--force"), "Force push is blocked"),
    (re.compile(r"\bgit\s+reset\s+--hard"), "Hard reset is blocked"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*f"), "Force clean is blocked"),
    (re.compile(r"\bgit\s+checkout\s+(main|master)\s*($|[;&|])"), "Checking out main/master is blocked to protect the main branch"),
    (re.compile(r"\bgit\s+switch\s+(main|master)\s*($|[;&|])"), "Switching to main/master is blocked to protect the main branch"),
    (re.compile(r"\bgit\s+branch\s+(-[a-zA-Z]*[fD][a-zA-Z]*\s+|.*--force\s+)(main|master)\b"), "Force-moving/deleting main/master branch is blocked"),
    (re.compile(r"\bgit\s+push\s+\S+\s+\S*:?\s*\b(main|master)\b"), "Pushing to main/master is blocked to protect the main branch"),
    (re.compile(r"\bgit\s+merge\b"), "Direct git merge via Bash is blocked — use the GitMergeBranch tool instead"),
    (re.compile(r"(?:^|[/\\])private[/\\]data(?:[/\\]|$)", re.IGNORECASE), "Access to private test data is blocked"),
    (re.compile(r"(?:^|[/\\])prepared[/\\]private(?:[/\\]|$)", re.IGNORECASE), "Access to private test data is blocked"),
]


def _check_blocked(command: str) -> str | None:
    """Return an error string if the command matches a blocked pattern."""
    for pattern, label in _BLOCKED_PATTERNS:
        if pattern.search(command):
            return label
    return None


def _check_dangerous(command: str) -> str | None:
    """Return a warning string if the command matches a dangerous pattern."""
    for pattern, label in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return label
    return None


class BashTool(Tool):
    name = "Bash"
    description = (
        "Executes a given bash command and returns its output.\n"
        "\n"
        "The working directory persists between commands, but shell state does not.\n"
        "\n"
        "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, "
        "`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or "
        "after you have verified that a dedicated tool cannot accomplish your task. "
        "Instead, use the appropriate dedicated tool as this will provide a much "
        "better experience:\n"
        "\n"
        " - File search: Use Glob (NOT find or ls)\n"
        " - Content search: Use Grep (NOT grep or rg)\n"
        " - Read files: Use Read (NOT cat/head/tail)\n"
        " - Edit files: Use Edit (NOT sed/awk)\n"
        " - Write files: Use Write (NOT echo >/cat <<EOF)\n"
        "\n"
        "While the Bash tool can do similar things, the built-in tools provide "
        "better output formatting and reliability.\n"
        "\n"
        "# Instructions\n"
        " - Always quote file paths that contain spaces with double quotes.\n"
        " - Try to maintain your current working directory by using absolute paths "
        "and avoiding `cd`.\n"
        " - Timeout is in seconds (default 600, max 86400). For experiment or "
        "eval commands, Bash auto-extends omitted timeouts up to 1800s when "
        "it recognizes the command, but RunTraining is still preferred.\n"
        " - You can use the `run_in_background` parameter to run the command in "
        "the background. Use this for long-running tasks (e.g. training scripts) "
        "when you want to continue doing other work while waiting.\n"
        " - When issuing multiple commands:\n"
        "  - If independent: make multiple Bash tool calls in parallel.\n"
        "  - If dependent: chain with '&&' in a single call.\n"
        "  - Use ';' only when you don't care if earlier commands fail.\n"
        "  - DO NOT use newlines to separate commands.\n"
        " - For git commands:\n"
        "  - Prefer creating a new commit rather than amending.\n"
        "  - Before destructive operations (git reset --hard, git push --force), "
        "consider safer alternatives.\n"
        "  - Never skip hooks (--no-verify) unless explicitly asked.\n"
        " - Avoid unnecessary `sleep` commands — diagnose root causes instead.\n"
        " - If a command will create files/directories, first verify the parent "
        "directory exists."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds (default 600, max 86400). "
                    "IMPORTANT: For experiment/eval commands, use at least 1800 "
                    "or use RunTraining."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does. "
                    "Keep it brief for simple commands (e.g. 'List files'), "
                    "add more context for complex ones."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Set to true to run this command in the background. "
                    "Use for long-running tasks like training scripts. "
                    "The result will be available later."
                ),
            },
        },
        "required": ["command"],
    }
    is_read_only = False
    max_result_chars = 100_000

    def __init__(
        self,
        *,
        cwd: str,
        timeout_default: int = _DEFAULT_TIMEOUT_SECONDS,
        timeout_max: int = 86_400,
        **kwargs: Any,
    ):
        super().__init__(cwd=cwd, **kwargs)
        self.timeout_default = max(1, timeout_default)
        self.timeout_max = max(self.timeout_default, timeout_max)
        self.input_schema = copy.deepcopy(self.input_schema)
        self.input_schema["properties"]["timeout"]["description"] = (
            f"Timeout in seconds (default {self.timeout_default}, max {self.timeout_max}). "
            "IMPORTANT: For experiment/eval commands, set this generously."
        )
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._background_results: dict[str, str] = {}

    async def execute(self, **kwargs: Any) -> str:
        command: str = kwargs["command"]
        requested_timeout = kwargs.get("timeout")
        timeout_value = self.timeout_default if requested_timeout is None else int(requested_timeout)
        timeout: int = min(max(1, timeout_value), self.timeout_max)
        run_in_background: bool = kwargs.get("run_in_background", False)
        timeout_notes: list[str] = []

        if requested_timeout is not None and int(requested_timeout) > self.timeout_max:
            timeout_notes.append(
                f"NOTE: Requested timeout {int(requested_timeout)}s was clamped to "
                f"the configured max {self.timeout_max}s."
            )

        # Intercept background task status checks: echo $BACKGROUND_<task_id>.
        # Returns immediately — finished results are also delivered automatically
        # to the agent each turn (no polling needed, #8).
        m = _BACKGROUND_CHECK.match(command.strip())
        if m:
            return self._check_background(m.group(1))

        if _looks_long_running(command) and not run_in_background:
            if requested_timeout is None and timeout < _LONG_COMMAND_TIMEOUT_SECONDS:
                original_timeout = timeout
                timeout = min(_LONG_COMMAND_TIMEOUT_SECONDS, self.timeout_max)
                if timeout > original_timeout:
                    timeout_notes.append(
                        f"NOTE: Auto-increased Bash timeout from {original_timeout}s "
                        f"to {timeout}s for a likely long-running command. "
                        "Use RunTraining for full training/eval jobs."
                    )
            elif requested_timeout is not None and timeout < _LONG_COMMAND_TIMEOUT_SECONDS:
                timeout_notes.append(
                    f"NOTE: This looks like a long-running command but timeout is only {timeout}s. "
                    "Use timeout>=1800 or RunTraining if it times out."
                )

        # Check for blocked commands — refuse to execute
        blocked = _check_blocked(command)
        if blocked:
            return (
                f"BLOCKED: {blocked}. This command has been refused for safety. "
                f"If you believe this is necessary, use a safer alternative or "
                f"ask the user to run it manually."
            )

        # Check for dangerous commands — warn but still execute
        danger = _check_dangerous(command)
        warning = ""
        if timeout_notes:
            warning += "\n".join(timeout_notes) + "\n\n"
        if danger:
            warning += (
                f"WARNING: This command was flagged as potentially dangerous "
                f"({danger}). Please verify this was intentional.\n\n"
            )

        if run_in_background:
            result = await self._run_background(command, timeout)
            return warning + result if warning else result

        result = await self._run_foreground(command, timeout)
        return warning + result if warning else result

    async def _run_foreground(self, command: str, timeout: int) -> str:
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.cwd,
                env={**os.environ, "TERM": "dumb"},
                start_new_session=True,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace")

            exit_info = ""
            if proc.returncode != 0:
                exit_info = f"\n[Exit code: {proc.returncode}]"

            return self._truncate(output + exit_info)

        except asyncio.TimeoutError:
            if proc is not None:
                await terminate_process(proc)
            return (
                f"[Command timed out after {timeout}s. "
                f"Use a larger timeout (e.g. timeout=1800) or run_in_background=true for long-running commands. "
                f"Do NOT re-run with the same timeout. Sent SIGTERM before SIGKILL if needed.]"
            )
        except Exception as e:
            return f"[Error executing command: {e}]"

    async def _run_background(self, command: str, timeout: int) -> str:
        task_id = uuid.uuid4().hex[:8]
        # Background tasks are meant for long-running commands — use longer timeout
        bg_timeout = max(timeout, 86_400)

        async def _bg_worker():
            proc = None
            try:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self.cwd,
                    env={**os.environ, "TERM": "dumb"},
                    start_new_session=True,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=bg_timeout)
                output = stdout.decode("utf-8", errors="replace")
                if proc.returncode != 0:
                    output += f"\n[Exit code: {proc.returncode}]"
                self._background_results[task_id] = self._truncate(output)
            except asyncio.TimeoutError:
                await terminate_process(proc)
                self._background_results[task_id] = f"[Background task timed out after {bg_timeout}s]"
            except asyncio.CancelledError:
                # Agent is shutting down — kill the process group, don't orphan.
                if proc is not None:
                    await terminate_process(proc)
                raise
            except Exception as e:
                self._background_results[task_id] = f"[Background task error: {e}]"

        task = asyncio.create_task(_bg_worker())
        self._background_tasks[task_id] = task

        return (
            f"Command started in background (task_id: {task_id}).\n"
            f"Command: {command}\n"
            f"Continue with other work — the result is delivered to you "
            f"automatically when the task finishes (no need to poll). "
            f"For long training/eval jobs, prefer RunTraining."
        )

    def _check_background(self, task_id: str) -> str:
        """Non-blocking status of a background task (no sleep-poll, #8)."""
        if task_id in self._background_results:
            result = self._background_results.pop(task_id)
            self._background_tasks.pop(task_id, None)
            return f"[Background task {task_id} finished]\n{result}"
        if task_id not in self._background_tasks:
            return (
                f"No background task found with id: {task_id} "
                f"(it may have already been delivered automatically)."
            )
        return (
            f"Background task {task_id} is still running. You do NOT need to "
            f"check again — its result will be delivered to you automatically "
            f"when it finishes. Continue with other work."
        )

    def drain_notifications(self) -> list[str]:
        """Deliver any finished background results to the agent, then clear them.

        ``_background_results`` only ever holds completed tasks (populated by the
        worker on completion), so every entry is ready to push (#8).
        """
        out: list[str] = []
        for task_id in list(self._background_results.keys()):
            result = self._background_results.pop(task_id)
            self._background_tasks.pop(task_id, None)
            out.append(f"[Background task {task_id} finished]\n{result}")
        return out

    async def aclose(self) -> None:
        """Cancel and reap any still-running background jobs (no leaks, #8).

        Each worker kills its own process group on cancellation, so this leaves
        no orphan subprocesses and no pending-task warnings.
        """
        tasks = list(self._background_tasks.values())
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
