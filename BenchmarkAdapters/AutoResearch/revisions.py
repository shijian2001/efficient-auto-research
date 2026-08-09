"""Isolated, train.py-only revisions for Autoresearch Architecture Design."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from .baseline import BaselineManifest


MAX_TRAIN_BYTES = 2 * 1024 * 1024
_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_IGNORED_PARTS = {".git", "__pycache__"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def _validate_relative_path(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise AdapterError(f"unsafe Autoresearch baseline path: {relative}")


def _safe_tree_manifest(root: Path) -> dict[str, str]:
    root = root.resolve()
    if not root.is_dir():
        raise AdapterError(f"Autoresearch revision tree is missing: {root}")
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise AdapterError(f"Autoresearch candidate contains a symlink: {relative}")
        if any(part in _IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_dir():
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise AdapterError(f"Autoresearch candidate contains a non-regular file: {relative}")
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        if relative == "train.py" and file_stat.st_size > MAX_TRAIN_BYTES:
            raise AdapterError("Autoresearch candidate train.py exceeds the size limit")
        manifest[relative] = sha256_file(path)
    return manifest


def tree_digest(files: Mapping[str, str]) -> str:
    return hashlib.sha256(canonical_json(dict(sorted(files.items())))).hexdigest()


@dataclass(frozen=True)
class TrainRevision:
    revision_id: str
    parent_id: str | None
    tree_digest: str
    train_sha256: str
    changed_files: tuple[str, ...]
    unified_diff: str
    created_at: str
    creation_metadata: Mapping[str, Any]
    path: Path


class TrainRevisionStore:
    """Creates immutable candidates and replays them from the frozen baseline."""

    def __init__(
        self,
        *,
        baseline_source: Path,
        baseline_manifest: BaselineManifest,
        state_dir: Path,
    ) -> None:
        self.baseline_source = baseline_source.resolve()
        self.baseline_manifest = baseline_manifest
        self.state_dir = state_dir.resolve()
        if self.state_dir.exists() or self.state_dir.is_symlink():
            raise AdapterError(f"Autoresearch revision store already exists: {self.state_dir}")
        baseline_manifest.validate(self.baseline_source)
        self.revisions_dir = self.state_dir / "revisions"
        self.workspaces_dir = self.state_dir / "workspaces"
        self.revisions_dir.mkdir(parents=True)
        self.workspaces_dir.mkdir()
        baseline_path = self.revisions_dir / "baseline"
        self._materialize_baseline(baseline_path)
        files = _safe_tree_manifest(baseline_path)
        baseline = TrainRevision(
            revision_id="baseline",
            parent_id=None,
            tree_digest=tree_digest(files),
            train_sha256=files["train.py"],
            changed_files=(),
            unified_diff="",
            created_at=datetime.now(timezone.utc).isoformat(),
            creation_metadata={"kind": "frozen_baseline"},
            path=baseline_path,
        )
        self._write_revision(baseline)

    def _materialize_baseline(self, destination: Path) -> None:
        destination.mkdir()
        for relative, expected in self.baseline_manifest.source_files.items():
            _validate_relative_path(relative)
            source = self.baseline_source / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256_file(target) != expected:
                raise AdapterError(f"Autoresearch baseline copy drift: {relative}")

    def _validate_revision_id(self, revision_id: str) -> None:
        if not _REVISION_ID.fullmatch(revision_id):
            raise AdapterError(f"invalid Autoresearch revision ID: {revision_id!r}")

    def get(self, revision_id: str) -> TrainRevision:
        self._validate_revision_id(revision_id)
        metadata_path = self.state_dir / f"{revision_id}.json"
        revision_path = self.revisions_dir / revision_id
        if not metadata_path.is_file() or not revision_path.is_dir():
            raise AdapterError(f"unknown Autoresearch revision: {revision_id}")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_digest = payload.pop("revision_digest", None)
        if expected_digest != hashlib.sha256(canonical_json(payload)).hexdigest():
            raise AdapterError(f"Autoresearch revision metadata drift: {revision_id}")
        files = _safe_tree_manifest(revision_path)
        actual_tree_digest = tree_digest(files)
        if payload["tree_digest"] != actual_tree_digest:
            raise AdapterError(f"Autoresearch revision tree drift: {revision_id}")
        if payload["train_sha256"] != files.get("train.py"):
            raise AdapterError(f"Autoresearch revision train.py drift: {revision_id}")
        return TrainRevision(
            revision_id=revision_id,
            parent_id=payload["parent_id"],
            tree_digest=actual_tree_digest,
            train_sha256=payload["train_sha256"],
            changed_files=tuple(payload["changed_files"]),
            unified_diff=str(payload["unified_diff"]),
            created_at=str(payload["created_at"]),
            creation_metadata=dict(payload["creation_metadata"]),
            path=revision_path,
        )

    def checkout(self, revision_id: str, workspace_name: str) -> Path:
        self._validate_revision_id(workspace_name)
        revision = self.get(revision_id)
        workspace = self.workspaces_dir / workspace_name
        if workspace.exists() or workspace.is_symlink():
            raise AdapterError(f"Autoresearch workspace already exists: {workspace}")
        shutil.copytree(revision.path, workspace, symlinks=False)
        return workspace

    def commit(
        self,
        workspace: Path,
        *,
        parent_id: str,
        revision_id: str,
        creation_metadata: Mapping[str, Any] | None = None,
    ) -> TrainRevision:
        self._validate_revision_id(revision_id)
        if revision_id == "baseline":
            raise AdapterError("Autoresearch baseline revision is reserved")
        parent = self.get(parent_id)
        workspace = workspace.resolve()
        candidate_files = _safe_tree_manifest(workspace)
        parent_files = _safe_tree_manifest(parent.path)
        changed = tuple(
            sorted(
                relative
                for relative in set(candidate_files) | set(parent_files)
                if candidate_files.get(relative) != parent_files.get(relative)
            )
        )
        illegal = [relative for relative in changed if relative != "train.py"]
        if illegal:
            raise AdapterError(f"Autoresearch candidate changed a non-editable path: {illegal[0]}")
        self._validate_train(workspace / "train.py")
        destination = self.revisions_dir / revision_id
        if destination.exists() or (self.state_dir / f"{revision_id}.json").exists():
            raise AdapterError(f"Autoresearch revision already exists: {revision_id}")
        shutil.copytree(
            workspace,
            destination,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo"),
        )
        destination_files = _safe_tree_manifest(destination)
        parent_source = (parent.path / "train.py").read_text(encoding="utf-8").splitlines(keepends=True)
        candidate_source = (destination / "train.py").read_text(encoding="utf-8").splitlines(keepends=True)
        unified_diff = "".join(
            difflib.unified_diff(
                parent_source,
                candidate_source,
                fromfile=f"{parent_id}/train.py",
                tofile=f"{revision_id}/train.py",
            )
        )
        revision = TrainRevision(
            revision_id=revision_id,
            parent_id=parent_id,
            tree_digest=tree_digest(destination_files),
            train_sha256=destination_files["train.py"],
            changed_files=changed,
            unified_diff=unified_diff,
            created_at=datetime.now(timezone.utc).isoformat(),
            creation_metadata=dict(creation_metadata or {}),
            path=destination,
        )
        self._write_revision(revision)
        return revision

    def commit_train_source(
        self,
        source: str,
        *,
        parent_id: str,
        revision_id: str,
        creation_metadata: Mapping[str, Any] | None = None,
    ) -> TrainRevision:
        workspace = self.checkout(parent_id, f"work-{revision_id}")
        (workspace / "train.py").write_text(source, encoding="utf-8")
        return self.commit(
            workspace,
            parent_id=parent_id,
            revision_id=revision_id,
            creation_metadata=creation_metadata,
        )

    def replay(self, revision_id: str, destination: Path) -> Path:
        revision = self.get(revision_id)
        destination = destination.resolve()
        if destination.exists() or destination.is_symlink():
            raise AdapterError(f"Autoresearch replay destination already exists: {destination}")
        self._materialize_baseline(destination)
        if revision.revision_id != "baseline":
            shutil.copy2(revision.path / "train.py", destination / "train.py")
        replay_files = _safe_tree_manifest(destination)
        if replay_files["train.py"] != revision.train_sha256:
            raise AdapterError("Autoresearch replay train.py differs from selected revision")
        baseline_files = self.baseline_manifest.source_files
        for relative in self.baseline_manifest.protected_paths:
            if replay_files.get(relative) != baseline_files[relative]:
                raise AdapterError(f"Autoresearch replay changed protected path: {relative}")
        return destination

    def _validate_train(self, path: Path) -> None:
        if not path.exists() or path.is_symlink():
            raise AdapterError("Autoresearch candidate train.py is missing or unsafe")
        file_stat = path.stat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size <= 0:
            raise AdapterError("Autoresearch candidate train.py must be a non-empty regular file")
        if file_stat.st_size > MAX_TRAIN_BYTES:
            raise AdapterError("Autoresearch candidate train.py exceeds the size limit")
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise AdapterError("Autoresearch candidate train.py is not valid UTF-8 Python") from exc

    def _write_revision(self, revision: TrainRevision) -> None:
        payload = {
            "revision_id": revision.revision_id,
            "parent_id": revision.parent_id,
            "tree_digest": revision.tree_digest,
            "train_sha256": revision.train_sha256,
            "changed_files": list(revision.changed_files),
            "unified_diff": revision.unified_diff,
            "created_at": revision.created_at,
            "creation_metadata": dict(revision.creation_metadata),
        }
        write_json_exclusive(
            self.state_dir / f"{revision.revision_id}.json",
            {
                **payload,
                "revision_digest": hashlib.sha256(canonical_json(payload)).hexdigest(),
            },
        )


__all__ = ["MAX_TRAIN_BYTES", "TrainRevision", "TrainRevisionStore", "tree_digest"]
