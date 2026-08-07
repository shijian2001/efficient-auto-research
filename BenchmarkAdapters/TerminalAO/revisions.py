"""Isolated, allowlisted candidate revisions for terminus-2."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json
from .baseline import BaselineManifest, materialize_baseline, tree_digest, tree_manifest


MAX_FILE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Revision:
    revision_id: str
    parent_id: str | None
    tree_digest: str
    changed_files: tuple[str, ...]
    path: Path


class RevisionStore:
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
        if self.state_dir.exists():
            raise AdapterError(f"revision store already exists: {self.state_dir}")
        self.revisions_dir = self.state_dir / "revisions"
        self.workspaces_dir = self.state_dir / "workspaces"
        self.revisions_dir.mkdir(parents=True)
        self.workspaces_dir.mkdir()
        baseline = self.revisions_dir / "baseline"
        materialize_baseline(self.baseline_source, baseline, self.baseline_manifest)
        self._write_revision(Revision("baseline", None, tree_digest(baseline), (), baseline))

    def checkout(self, revision_id: str, workspace_name: str) -> Path:
        source = self._revision_path(revision_id)
        workspace = self.workspaces_dir / workspace_name
        if workspace.exists() or workspace.is_symlink():
            raise AdapterError(f"candidate workspace already exists: {workspace}")
        shutil.copytree(source, workspace, symlinks=True)
        return workspace

    def commit(self, workspace: Path, *, parent_id: str, revision_id: str) -> Revision:
        workspace = workspace.resolve()
        parent = self._revision_path(parent_id)
        changed = self._validate_tree(workspace, parent)
        destination = self.revisions_dir / revision_id
        if destination.exists():
            raise AdapterError(f"revision already exists: {revision_id}")
        shutil.copytree(
            workspace,
            destination,
            symlinks=False,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.pyo"),
        )
        revision = Revision(revision_id, parent_id, tree_digest(destination), tuple(changed), destination)
        self._write_revision(revision)
        return revision

    def replay(self, revision_id: str, destination: Path) -> Path:
        source = self._revision_path(revision_id)
        baseline = self._revision_path("baseline")
        self._validate_tree(source, baseline)
        if destination.exists() or destination.is_symlink():
            raise AdapterError(f"final replay destination already exists: {destination}")
        shutil.copytree(baseline, destination, symlinks=False)
        for relative in self.changed_files(revision_id):
            source_path = source / relative
            target = destination / relative
            if source_path.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target)
            elif target.exists():
                target.unlink()
        if tree_digest(destination) != tree_digest(source):
            raise AdapterError("final replay does not match selected revision")
        return destination

    def changed_files(self, revision_id: str) -> tuple[str, ...]:
        payload = json.loads((self.state_dir / f"{revision_id}.json").read_text())
        return tuple(payload["changed_files"])

    def _revision_path(self, revision_id: str) -> Path:
        path = self.revisions_dir / revision_id
        if not path.is_dir():
            raise AdapterError(f"unknown revision: {revision_id}")
        return path

    def _validate_tree(self, candidate: Path, parent: Path) -> list[str]:
        candidate_files = self._safe_files(candidate)
        parent_files = tree_manifest(parent)
        changed = sorted(
            relative
            for relative in set(candidate_files) | set(parent_files)
            if candidate_files.get(relative) != parent_files.get(relative)
        )
        for relative in changed:
            if not self._editable(relative):
                raise AdapterError(f"candidate changed a non-editable path: {relative}")
        return changed

    def _safe_files(self, root: Path) -> dict[str, str]:
        root = root.resolve()
        manifest: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if ".git" in path.parts or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            file_stat = path.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                raise AdapterError(f"candidate contains a symlink: {relative}")
            if path.is_dir():
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                raise AdapterError(f"candidate contains a non-regular file: {relative}")
            if file_stat.st_size > MAX_FILE_BYTES:
                raise AdapterError(f"candidate file exceeds size limit: {relative}")
            manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return manifest

    def _editable(self, relative: str) -> bool:
        return any(
            relative == prefix or relative.startswith(prefix.rstrip("/") + "/")
            for prefix in self.baseline_manifest.editable_paths
        )

    def _write_revision(self, revision: Revision) -> None:
        payload = {
            "revision_id": revision.revision_id,
            "parent_id": revision.parent_id,
            "tree_digest": revision.tree_digest,
            "changed_files": list(revision.changed_files),
        }
        payload["revision_digest"] = hashlib.sha256(canonical_json(payload)).hexdigest()
        with (self.state_dir / f"{revision.revision_id}.json").open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")


__all__ = ["MAX_FILE_BYTES", "Revision", "RevisionStore"]
