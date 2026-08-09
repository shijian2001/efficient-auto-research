"""Frozen Autoresearch baseline and prepared-asset manifests."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file


def manifest_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid Autoresearch manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Autoresearch manifest must be an object: {path}")
    return payload


@dataclass(frozen=True)
class BaselineManifest:
    source_commit: str
    source_files: Mapping[str, str]
    editable_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    baseline_train_sha256: str

    def validate(self, source_root: Path) -> None:
        source_root = source_root.resolve()
        if len(self.source_commit) != 40:
            raise AdapterError("Autoresearch baseline source commit is invalid")
        if self.editable_paths != ("train.py",):
            raise AdapterError("Autoresearch formal editable allowlist must be train.py only")
        if "train.py" not in self.source_files:
            raise AdapterError("Autoresearch baseline manifest is missing train.py")
        if self.baseline_train_sha256 != self.source_files["train.py"]:
            raise AdapterError("Autoresearch baseline train.py digest is inconsistent")
        for relative, expected in self.source_files.items():
            path = source_root / relative
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"Autoresearch baseline file is missing or unsafe: {relative}")
            if sha256_file(path) != expected:
                raise AdapterError(f"Autoresearch baseline source drift: {relative}")
        if set(self.protected_paths) != set(self.source_files) - {"train.py"}:
            raise AdapterError("Autoresearch protected path set differs from frozen source files")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_commit": self.source_commit,
            "source_files": dict(sorted(self.source_files.items())),
            "editable_paths": list(self.editable_paths),
            "protected_paths": list(self.protected_paths),
            "baseline_train_sha256": self.baseline_train_sha256,
        }

    @property
    def digest(self) -> str:
        return manifest_digest(self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "BaselineManifest":
        payload = _load_json(path)
        try:
            expected = payload.pop("manifest_digest", None)
            manifest = cls(
                source_commit=str(payload["source_commit"]),
                source_files=dict(payload["source_files"]),
                editable_paths=tuple(payload["editable_paths"]),
                protected_paths=tuple(payload["protected_paths"]),
                baseline_train_sha256=str(payload["baseline_train_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError(f"invalid Autoresearch baseline manifest schema: {path}") from exc
        if expected != manifest.digest:
            raise AdapterError(f"Autoresearch baseline manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class PreparedAssetManifest:
    protocol_id: str
    scope: str
    files: Mapping[str, str]

    def validate(self, prepared_root: Path) -> None:
        prepared_root = prepared_root.resolve()
        if not self.files:
            raise AdapterError("Autoresearch prepared manifest contains no files")
        required = {
            "tokenizer/tokenizer.pkl",
            "tokenizer/token_bytes.pt",
        }
        if not required.issubset(self.files):
            raise AdapterError("Autoresearch prepared manifest is missing tokenizer assets")
        if not any(name.startswith("data/") and name.endswith(".parquet") for name in self.files):
            raise AdapterError("Autoresearch prepared manifest is missing parquet data")
        actual_files: set[str] = set()
        for path in sorted(prepared_root.rglob("*")):
            relative = path.relative_to(prepared_root).as_posix()
            file_stat = path.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                raise AdapterError(f"Autoresearch prepared asset contains a symlink: {relative}")
            if path.is_dir():
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                raise AdapterError(f"Autoresearch prepared asset is not a regular file: {relative}")
            actual_files.add(relative)
        if actual_files != set(self.files):
            unexpected = sorted(actual_files - set(self.files))
            missing = sorted(set(self.files) - actual_files)
            raise AdapterError(
                f"Autoresearch prepared asset tree differs from manifest: "
                f"unexpected={unexpected}, missing={missing}"
            )
        for relative, expected in self.files.items():
            path = prepared_root / relative
            if not path.exists() or path.is_symlink():
                raise AdapterError(f"Autoresearch prepared asset is missing or unsafe: {relative}")
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                raise AdapterError(f"Autoresearch prepared asset is not a regular file: {relative}")
            if sha256_file(path) != expected:
                raise AdapterError(f"Autoresearch prepared asset drift: {relative}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "scope": self.scope,
            "files": dict(sorted(self.files.items())),
        }

    @property
    def digest(self) -> str:
        return manifest_digest(self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "PreparedAssetManifest":
        payload = _load_json(path)
        try:
            expected = payload.pop("manifest_digest", None)
            manifest = cls(
                protocol_id=str(payload["protocol_id"]),
                scope=str(payload["scope"]),
                files=dict(payload["files"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError(f"invalid Autoresearch prepared manifest schema: {path}") from exc
        if expected != manifest.digest:
            raise AdapterError(f"Autoresearch prepared manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class KernelCacheManifest:
    protocol_id: str
    repository: str
    revision: str
    variant: str
    files: Mapping[str, str]
    symlinks: Mapping[str, str]

    def validate(self, cache_root: Path) -> None:
        cache_root = cache_root.resolve()
        if not cache_root.is_dir():
            raise AdapterError(f"Autoresearch kernel cache is missing: {cache_root}")
        if self.repository != "varunneal/flash-attention-3":
            raise AdapterError("Autoresearch kernel cache repository is invalid")
        if len(self.revision) != 40 or not self.variant.startswith("torch29-cxx11-cu128-"):
            raise AdapterError("Autoresearch kernel cache revision or variant is invalid")
        if not self.files or not self.symlinks:
            raise AdapterError("Autoresearch kernel cache manifest is incomplete")
        actual_files: set[str] = set()
        actual_symlinks: set[str] = set()
        for path in sorted(cache_root.rglob("*")):
            relative = path.relative_to(cache_root).as_posix()
            file_stat = path.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                actual_symlinks.add(relative)
            elif path.is_dir():
                continue
            elif stat.S_ISREG(file_stat.st_mode):
                actual_files.add(relative)
            else:
                raise AdapterError(f"Autoresearch kernel cache contains a special file: {relative}")
        if actual_files != set(self.files) or actual_symlinks != set(self.symlinks):
            raise AdapterError("Autoresearch kernel cache tree differs from the frozen manifest")
        for relative, expected in self.files.items():
            path = cache_root / relative
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"Autoresearch kernel cache file is missing or unsafe: {relative}")
            if sha256_file(path) != expected:
                raise AdapterError(f"Autoresearch kernel cache drift: {relative}")
        for relative, expected_target in self.symlinks.items():
            path = cache_root / relative
            if not path.is_symlink() or path.readlink().as_posix() != expected_target:
                raise AdapterError(f"Autoresearch kernel cache symlink drift: {relative}")
            resolved = path.resolve()
            try:
                resolved.relative_to(cache_root)
            except ValueError as exc:
                raise AdapterError(f"Autoresearch kernel cache symlink escapes root: {relative}") from exc
            if not resolved.is_file():
                raise AdapterError(f"Autoresearch kernel cache symlink target is missing: {relative}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "repository": self.repository,
            "revision": self.revision,
            "variant": self.variant,
            "files": dict(sorted(self.files.items())),
            "symlinks": dict(sorted(self.symlinks.items())),
        }

    @property
    def digest(self) -> str:
        return manifest_digest(self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "KernelCacheManifest":
        payload = _load_json(path)
        try:
            expected = payload.pop("manifest_digest", None)
            manifest = cls(
                protocol_id=str(payload["protocol_id"]),
                repository=str(payload["repository"]),
                revision=str(payload["revision"]),
                variant=str(payload["variant"]),
                files=dict(payload["files"]),
                symlinks=dict(payload["symlinks"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError(f"invalid Autoresearch kernel cache manifest schema: {path}") from exc
        if expected != manifest.digest:
            raise AdapterError(f"Autoresearch kernel cache manifest digest mismatch: {path}")
        return manifest


__all__ = [
    "BaselineManifest",
    "KernelCacheManifest",
    "PreparedAssetManifest",
    "manifest_digest",
]
