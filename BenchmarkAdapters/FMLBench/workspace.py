"""Disposable FML workspaces and replayable final artifacts."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file
from .task import FMLTaskSpec


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise AdapterError(f"FML workspace contains a prohibited symlink: {path}")
        if path.is_file():
            yield path


def tree_manifest(root: Path) -> dict[str, str]:
    root = root.resolve()
    if not root.is_dir():
        raise AdapterError(f"FML workspace is missing: {root}")
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in _iter_files(root)
    }


def tree_digest(root: Path) -> str:
    return hashlib.sha256(canonical_json(tree_manifest(root))).hexdigest()


@dataclass(frozen=True)
class FMLWorkspace:
    root: Path
    initial_manifest: dict[str, str]
    initial_digest: str

    @classmethod
    def create(cls, *, upstream_root: Path, task: FMLTaskSpec, destination: Path) -> "FMLWorkspace":
        destination = destination.resolve()
        if destination.exists() or destination.is_symlink():
            raise AdapterError(f"FML workspace already exists: {destination}")
        task_config = upstream_root.resolve() / "ml_tasks" / task.upstream_task_name / "config.json"
        try:
            config = __import__("json").loads(task_config.read_text(encoding="utf-8"))
            source_repo = upstream_root.resolve() / str(config["repo_dir"])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise AdapterError(f"cannot resolve FML workspace template for {task.task_id}") from exc
        template_base = source_repo
        while template_base.parent.name != "workspace":
            if template_base.parent == template_base:
                raise AdapterError("FML workspace template is outside upstream workspace/")
            template_base = template_base.parent
        repository_relative = source_repo.relative_to(template_base)
        shutil.copytree(template_base, destination, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        root = destination / repository_relative
        manifest = tree_manifest(root)
        return cls(
            root=root,
            initial_manifest=manifest,
            initial_digest=hashlib.sha256(canonical_json(manifest)).hexdigest(),
        )

    def changed_paths(self) -> tuple[str, ...]:
        current = tree_manifest(self.root)
        names = set(self.initial_manifest) | set(current)
        return tuple(sorted(name for name in names if self.initial_manifest.get(name) != current.get(name)))

    def validate_changes(self, task: FMLTaskSpec) -> tuple[str, ...]:
        changed = self.changed_paths()
        allowed = tuple(PurePosixPath(value) for value in task.editable_paths)
        for value in changed:
            path = PurePosixPath(value)
            if not any(path == item or item in path.parents for item in allowed):
                raise AdapterError(f"FML Agent modified a non-editable path: {value}")
        return changed

    def create_artifact(self, task: FMLTaskSpec, destination: Path) -> tuple[Path, str, tuple[str, ...]]:
        changed = self.validate_changes(task)
        destination = destination.resolve()
        if destination.exists() or destination.is_symlink():
            raise AdapterError(f"refusing to overwrite FML final artifact: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as raw:
            with tarfile.open(fileobj=raw, mode="w") as archive:
                for relative in sorted(task.editable_paths):
                    source = self.root / relative
                    if not source.is_file() or source.is_symlink():
                        raise AdapterError(f"FML editable artifact is missing or unsafe: {relative}")
                    data = source.read_bytes()
                    info = tarfile.TarInfo(relative)
                    info.size = len(data)
                    info.mode = stat.S_IMODE(source.stat().st_mode) & 0o755
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(data))
        return destination, sha256_file(destination), changed


def disposable_evaluation_workspace(source: Path, destination: Path) -> Path:
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise AdapterError(f"FML evaluation workspace already exists: {destination}")
    shutil.copytree(source.resolve(), destination, symlinks=False)
    return destination


__all__ = ["FMLWorkspace", "disposable_evaluation_workspace", "tree_digest", "tree_manifest"]
