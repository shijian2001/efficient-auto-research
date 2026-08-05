"""Experiment tracking and Git management."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shlex
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .git_artifacts import filter_commit_paths

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: run a shell command
# ---------------------------------------------------------------------------

async def _run_cmd(cmd: str, cwd: str, timeout: int = 30) -> tuple[str, int]:
    """Run a command and return (stdout, exit_code)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode("utf-8", errors="replace").strip(), proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        return f"[timed out after {timeout}s]", -1


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a URL/branch-friendly slug.

    Examples:
        "Add dropout regularization" -> "add-dropout-regularization"
        "Switch optimizer from SGD to AdamW" -> "switch-optimizer-from-sgd-to-adamw"
        "改进搜索策略" -> "a3f8c1d2e4b6" (hash fallback for non-Latin text)
    """
    # Normalize unicode and lowercase
    text_normalized = unicodedata.normalize("NFKD", text).lower()
    # Replace non-alphanumeric (including CJK) with hyphens
    slug = re.sub(r"[^a-z0-9]+", "-", text_normalized)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    # If empty (e.g. all CJK chars), use hash of original text
    if not slug:
        slug = hashlib.sha256(text.encode()).hexdigest()[:12]
    # Truncate
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "unnamed"


# ---------------------------------------------------------------------------
# Git Manager
# ---------------------------------------------------------------------------

class GitManager:
    """Automatic Git operations for experiment tracking.

    Creates a dedicated branch from main, auto-commits after file changes,
    and supports rollback on experiment failure.
    """

    def __init__(
        self,
        cwd: str,
        branch_prefix: str = "arbor",
        enabled: bool = True,
        idea: str = "",
    ):
        self.cwd = cwd
        self.branch_prefix = branch_prefix
        self.enabled = enabled
        self.idea = idea
        self._initialized = False
        self.branch_name: str | None = None

    async def ensure_initialized(self) -> None:
        """Create a git repo if needed, checkout main, then create an experiment branch."""
        if self._initialized or not self.enabled:
            return
        self._initialized = True

        # Check if already in a git repo
        _, rc = await _run_cmd("git rev-parse --is-inside-work-tree", self.cwd)
        if rc != 0:
            log.info("Not a git repo. Initializing one at %s", self.cwd)
            await _run_cmd("git init", self.cwd)
            untracked, _ = await _run_cmd("git ls-files --others --exclude-standard", self.cwd)
            commit_paths, artifact_paths = filter_commit_paths(
                [line.strip() for line in untracked.splitlines() if line.strip()]
            )
            if artifact_paths:
                log.info("Skipping generated artifacts in initial commit: %s", ", ".join(artifact_paths[:20]))
            if commit_paths:
                quoted_paths = " ".join(shlex.quote(path) for path in commit_paths)
                await _run_cmd(f"git add -- {quoted_paths}", self.cwd)
                await _run_cmd("git commit -m 'arbor: initial workspace snapshot'", self.cwd)

        # Always start from main branch
        # Try 'main' first, then 'master' as fallback
        main_branch = await self._find_main_branch()
        current, _ = await _run_cmd("git branch --show-current", self.cwd)
        if current.strip() != main_branch:
            out, rc = await _run_cmd(f"git checkout {shlex.quote(main_branch)}", self.cwd)
            if rc != 0:
                log.warning("Failed to checkout %s: %s", main_branch, out)

        # Create experiment branch with descriptive name from idea
        slug = _slugify(self.idea) if self.idea else datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"{self.branch_prefix}/{slug}"
        self.branch_name = branch

        out, rc = await _run_cmd(f"git checkout -b {shlex.quote(branch)}", self.cwd)
        if rc == 0:
            log.info("Created branch: %s", branch)
        else:
            # If branch exists, add a short timestamp suffix
            ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
            branch = f"{self.branch_prefix}/{slug}-{ts}"
            self.branch_name = branch
            out, rc = await _run_cmd(f"git checkout -b {shlex.quote(branch)}", self.cwd)
            if rc == 0:
                log.info("Created branch (with suffix): %s", branch)
            else:
                log.warning("Failed to create branch: %s", out)

    async def _find_main_branch(self) -> str:
        """Detect the main branch name (main or master)."""
        out, rc = await _run_cmd("git branch --list main", self.cwd)
        if rc == 0 and out.strip():
            return "main"
        out, rc = await _run_cmd("git branch --list master", self.cwd)
        if rc == 0 and out.strip():
            return "master"
        # Default to main
        return "main"

    async def auto_commit(self, message: str) -> str | None:
        """Stage all changes and commit. Returns commit hash or None."""
        if not self.enabled:
            return None

        await self.ensure_initialized()

        current, _ = await _run_cmd("git branch --show-current", self.cwd)
        if current.strip() in {"main", "master"}:
            log.warning("Refusing auto-commit on protected branch: %s", current.strip())
            return None

        # Check for changes
        await _run_cmd("git reset --", self.cwd)
        diff, _ = await _run_cmd("git diff --name-only", self.cwd)
        untracked, _ = await _run_cmd("git ls-files --others --exclude-standard", self.cwd)
        changed_paths = [line.strip() for line in (diff + "\n" + untracked).splitlines() if line.strip()]
        commit_paths, artifact_paths = filter_commit_paths(changed_paths)

        if artifact_paths:
            log.info("Skipping generated artifacts in auto-commit: %s", ", ".join(artifact_paths[:20]))

        if not commit_paths:
            return None

        # Stage and commit
        quoted_paths = " ".join(shlex.quote(path) for path in commit_paths)
        await _run_cmd(f"git add -- {quoted_paths}", self.cwd)
        out, rc = await _run_cmd(f"git commit -m {shlex.quote(message)}", self.cwd)
        if rc == 0:
            hash_out, _ = await _run_cmd("git rev-parse --short HEAD", self.cwd)
            log.info("Committed: %s (%s)", message, hash_out.strip())
            return hash_out.strip()
        else:
            log.warning("Commit failed: %s", out)
            return None

    async def rollback(self, steps: int = 1) -> str:
        """Roll back N commits. Returns status message."""
        if not self.enabled:
            return "Git management is disabled."

        out, rc = await _run_cmd(f"git reset --hard HEAD~{steps}", self.cwd)
        if rc == 0:
            return f"Rolled back {steps} commit(s)."
        return f"Rollback failed: {out}"

    async def get_status(self) -> str:
        """Return current git status summary."""
        branch, _ = await _run_cmd("git branch --show-current", self.cwd)
        log_out, _ = await _run_cmd("git log --oneline -5", self.cwd)
        return f"Branch: {branch}\nRecent commits:\n{log_out}"


# ---------------------------------------------------------------------------
# Experiment Tracker
# ---------------------------------------------------------------------------

class ExperimentTracker:
    """Track experiment iterations with metrics and code changes.

    Logs are persisted to a JSONL file for post-analysis.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "experiments.jsonl"
        self.iterations: list[dict[str, Any]] = []
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing experiment log."""
        if self.log_path.exists():
            with open(self.log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.iterations.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

    def log_iteration(
        self,
        *,
        turn: int,
        action: str,
        command: str | None = None,
        metrics: dict[str, Any] | None = None,
        code_changes: list[str] | None = None,
        status: str = "unknown",
        notes: str = "",
        git_commit: str | None = None,
    ) -> None:
        """Record one experiment iteration."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn": turn,
            "action": action,
            "command": command,
            "metrics": metrics or {},
            "code_changes": code_changes or [],
            "status": status,
            "notes": notes,
            "git_commit": git_commit,
        }
        self.iterations.append(entry)

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_summary(self) -> str:
        """Return a formatted summary of all experiment iterations."""
        if not self.iterations:
            return "No experiments recorded yet."

        lines: list[str] = ["# Experiment History", ""]
        for i, it in enumerate(self.iterations, 1):
            metrics_str = ", ".join(f"{k}={v}" for k, v in it.get("metrics", {}).items())
            lines.append(
                f"**Iteration {i}** (turn {it.get('turn', '?')}, {it.get('status', '?')}): "
                f"{it.get('action', '')}"
            )
            if metrics_str:
                lines.append(f"  Metrics: {metrics_str}")
            if it.get("command"):
                lines.append(f"  Command: `{it['command']}`")
            if it.get("notes"):
                lines.append(f"  Notes: {it['notes']}")
            lines.append("")

        return "\n".join(lines)
