"""Freeze and verify the installed Harbor terminus-2 source tree."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json


EDITABLE_PATHS = (
    "terminus_2.py",
    "terminus_json_plain_parser.py",
    "terminus_xml_plain_parser.py",
    "tmux_session.py",
    "templates",
)


def tree_manifest(root: Path) -> dict[str, str]:
    root = root.resolve()
    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if not path.is_file() or path.is_symlink():
            raise AdapterError(f"terminus baseline contains a non-regular file: {path}")
        relative = path.relative_to(root).as_posix()
        manifest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not manifest or "terminus_2.py" not in manifest:
        raise AdapterError(f"invalid terminus-2 baseline source: {root}")
    return manifest


def tree_digest(root: Path) -> str:
    return hashlib.sha256(canonical_json(tree_manifest(root))).hexdigest()


@dataclass(frozen=True)
class BaselineManifest:
    source_identifier: str
    harbor_version: str
    source_tree_digest: str
    files: dict[str, str]
    editable_paths: tuple[str, ...] = EDITABLE_PATHS

    def validate(self) -> None:
        if self.harbor_version != "0.20.0":
            raise AdapterError("Terminal AO requires Harbor 0.20.0")
        if len(self.source_tree_digest) != 64 or not self.files:
            raise AdapterError("invalid terminus baseline manifest")
        if not set(EDITABLE_PATHS).issubset(set(self.editable_paths)):
            raise AdapterError("terminus baseline editable allowlist is incomplete")

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(
            canonical_json(
                {
                    "source_identifier": self.source_identifier,
                    "harbor_version": self.harbor_version,
                    "source_tree_digest": self.source_tree_digest,
                    "files": dict(sorted(self.files.items())),
                    "editable_paths": list(self.editable_paths),
                }
            )
        ).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "BaselineManifest":
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("baseline_manifest_digest", None)
        manifest = cls(
            source_identifier=str(payload["source_identifier"]),
            harbor_version=str(payload["harbor_version"]),
            source_tree_digest=str(payload["source_tree_digest"]),
            files=dict(payload["files"]),
            editable_paths=tuple(payload["editable_paths"]),
        )
        if manifest.digest != expected:
            raise AdapterError(f"terminus baseline manifest digest mismatch: {path}")
        return manifest

    def verify_source(self, source: Path) -> None:
        actual = tree_manifest(source)
        if actual != self.files or tree_digest(source) != self.source_tree_digest:
            raise AdapterError("installed terminus-2 source does not match frozen baseline")


def materialize_baseline(source: Path, destination: Path, manifest: BaselineManifest) -> None:
    manifest.verify_source(source)
    if destination.exists() or destination.is_symlink():
        raise AdapterError(f"baseline destination already exists: {destination}")
    shutil.copytree(
        source,
        destination,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    if tree_digest(destination) != manifest.source_tree_digest:
        raise AdapterError("materialized terminus baseline digest mismatch")


__all__ = [
    "BaselineManifest",
    "EDITABLE_PATHS",
    "materialize_baseline",
    "tree_digest",
    "tree_manifest",
]
