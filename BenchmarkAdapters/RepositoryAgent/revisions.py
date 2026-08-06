"""External Git revision store for isolated repository candidates."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..contracts import AdapterError


GIT_ENV = {
    "GIT_AUTHOR_NAME": "BenchmarkAdapters",
    "GIT_AUTHOR_EMAIL": "benchmark-adapters@local",
    "GIT_COMMITTER_NAME": "BenchmarkAdapters",
    "GIT_COMMITTER_EMAIL": "benchmark-adapters@local",
}


class RevisionStore:
    def __init__(
        self,
        repository: Path,
        state_dir: Path,
        *,
        protected_paths: tuple[Path, ...] = (),
    ):
        self.repository = repository.resolve()
        self.state_dir = state_dir.resolve()
        self.git_dir = self.state_dir / "revisions.git"
        self.workspace_root = self.state_dir / "workspaces"
        self.protected_paths = tuple(path.resolve() for path in protected_paths)

    def initialize(self) -> str:
        if self.state_dir.exists():
            shutil.rmtree(self.state_dir)
        self.workspace_root.mkdir(parents=True)
        self._run(["git", "init", "--bare", str(self.git_dir)])
        baseline = self.workspace_root / "baseline"
        self._copy_repository(self.repository, baseline)
        self._remove_protected_files(baseline)
        self._git(baseline, "add", "-A")
        self._git(baseline, "commit", "--allow-empty", "-m", "baseline")
        revision = self._git(baseline, "rev-parse", "HEAD").strip()
        shutil.rmtree(baseline)
        return revision

    def checkout(self, revision: str, name: str) -> Path:
        workspace = self.workspace_root / name
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        if not self._run(
            ["git", "--git-dir", str(self.git_dir), "ls-tree", "-r", "--name-only", revision]
        ).strip():
            return workspace
        self._git(workspace, "checkout", "--force", revision, "--", ".")
        return workspace

    def commit(self, workspace: Path, message: str) -> str:
        self._remove_runtime_artifacts(workspace)
        self._git(workspace, "add", "-A")
        self._git(workspace, "commit", "--allow-empty", "-m", message)
        return self._git(workspace, "rev-parse", "HEAD").strip()

    def changed_files(self, revision: str, baseline: str) -> list[str]:
        output = self._run_bytes(
            [
                "git",
                "--git-dir",
                str(self.git_dir),
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                baseline,
                revision,
            ]
        )
        return [os.fsdecode(value) for value in output.split(b"\0") if value]

    def materialize(self, revision: str, destination: Path, baseline: str) -> None:
        destination = destination.resolve()
        source = self.checkout(revision, "materialized-best")
        changed = [Path(value) for value in self.changed_files(revision, baseline)]
        for relative in changed:
            self._validate_relative_path(relative, source, destination)
        deletions = sorted(
            (relative for relative in changed if not (source / relative).exists() and not (source / relative).is_symlink()),
            key=lambda value: len(value.parts),
            reverse=True,
        )
        creations = sorted(
            (relative for relative in changed if (source / relative).exists() or (source / relative).is_symlink()),
            key=lambda value: len(value.parts),
        )
        for relative in (*deletions, *creations):
            target = destination / relative
            candidate = source / relative
            if not candidate.exists() and not candidate.is_symlink():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                target.unlink()
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.copytree(candidate, target, symlinks=True)
            elif candidate.is_symlink():
                link_target = os.readlink(candidate)
                if Path(link_target).is_absolute():
                    raise AdapterError(f"best revision contains an absolute symlink: {relative}")
                resolved_link = (candidate.parent / link_target).resolve(strict=False)
                try:
                    resolved_link.relative_to(source)
                except ValueError as exc:
                    raise AdapterError(
                        f"best revision contains a symlink escaping the repository: {relative}"
                    ) from exc
                target.symlink_to(link_target)
            else:
                shutil.copy2(candidate, target)

    def _validate_relative_path(self, relative: Path, source: Path, destination: Path) -> None:
        if relative.is_absolute() or ".." in relative.parts:
            raise AdapterError(f"invalid changed path in best revision: {relative}")
        target = destination / relative
        if self._is_protected(target):
            raise AdapterError(f"best revision attempted to modify protected path: {relative}")
        for parent in target.parents:
            if parent == destination:
                break
            if parent.is_symlink():
                raise AdapterError(f"best revision crosses a destination symlink: {relative}")
        candidate = source / relative
        if candidate.exists() or candidate.is_symlink():
            resolved_candidate = candidate.resolve(strict=False)
            try:
                resolved_candidate.relative_to(source)
            except ValueError as exc:
                raise AdapterError(
                    f"best revision contains a path escaping the repository: {relative}"
                ) from exc

    def _copy_repository(self, source: Path, destination: Path) -> None:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".venv", ".uv-cache", "__pycache__"),
        )

    def _remove_protected_files(self, workspace: Path) -> None:
        for protected in self.protected_paths:
            try:
                relative = protected.relative_to(self.repository)
            except ValueError:
                continue
            candidate = workspace / relative
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.rmtree(candidate)
            elif candidate.exists() or candidate.is_symlink():
                candidate.unlink()

    def _remove_runtime_artifacts(self, workspace: Path) -> None:
        for directory_name in ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"):
            for directory in workspace.rglob(directory_name):
                if directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)
        for pattern in ("*.pyc", "*.pyo"):
            for artifact in workspace.rglob(pattern):
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()

    def _is_protected(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(
            resolved == protected
            or protected in resolved.parents
            or resolved in protected.parents
            for protected in self.protected_paths
        )

    def _git(self, workspace: Path, *arguments: str) -> str:
        return self._run(
            [
                "git",
                "--git-dir",
                str(self.git_dir),
                "--work-tree",
                str(workspace.resolve()),
                *arguments,
            ]
        )

    def _run(self, command: list[str]) -> str:
        environment = os.environ.copy()
        environment.update(GIT_ENV)
        completed = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode:
            raise AdapterError(f"revision command failed: {' '.join(command)}\n{completed.stdout}")
        return completed.stdout or ""

    def _run_bytes(self, command: list[str]) -> bytes:
        environment = os.environ.copy()
        environment.update(GIT_ENV)
        completed = subprocess.run(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode:
            output = completed.stderr.decode("utf-8", errors="replace")
            raise AdapterError(f"revision command failed: {' '.join(command)}\n{output}")
        return completed.stdout or b""


__all__ = ["RevisionStore"]
