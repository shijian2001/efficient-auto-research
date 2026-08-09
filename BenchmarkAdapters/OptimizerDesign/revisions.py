"""Immutable single-file revisions for Optimizer Design candidates."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from .evaluator import MAX_CANDIDATE_BYTES


_REVISION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class OptimizerRevision:
    revision_id: str
    parent_id: str | None
    candidate_sha256: str
    unified_diff: str
    created_at: str
    creation_metadata: Mapping[str, Any]
    path: Path


class OptimizerRevisionStore:
    def __init__(self, *, baseline_path: Path, state_dir: Path) -> None:
        self.baseline_path = baseline_path.resolve()
        self.state_dir = state_dir.resolve()
        if not self.baseline_path.is_file() or self.baseline_path.is_symlink():
            raise AdapterError("Optimizer Design baseline candidate is missing or unsafe")
        if self.state_dir.exists() or self.state_dir.is_symlink():
            raise AdapterError(f"Optimizer Design revision store already exists: {self.state_dir}")
        self.revisions_dir = self.state_dir / "revisions"
        self.revisions_dir.mkdir(parents=True)
        baseline = self.revisions_dir / "baseline.py"
        shutil.copy2(self.baseline_path, baseline)
        self._validate_source(baseline)
        self._write(
            OptimizerRevision(
                revision_id="baseline",
                parent_id=None,
                candidate_sha256=sha256_file(baseline),
                unified_diff="",
                created_at=datetime.now(timezone.utc).isoformat(),
                creation_metadata={"kind": "frozen_baseline"},
                path=baseline,
            )
        )

    @staticmethod
    def _validate_id(revision_id: str) -> None:
        if not _REVISION_ID.fullmatch(revision_id):
            raise AdapterError(f"invalid Optimizer Design revision ID: {revision_id!r}")

    @staticmethod
    def _validate_source(path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            raise AdapterError("Optimizer Design candidate must be a regular file")
        if not 0 < path.stat().st_size <= MAX_CANDIDATE_BYTES:
            raise AdapterError("Optimizer Design candidate is empty or exceeds the size limit")
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise AdapterError("Optimizer Design candidate is not valid UTF-8 Python") from exc

    def get(self, revision_id: str) -> OptimizerRevision:
        self._validate_id(revision_id)
        path = self.revisions_dir / f"{revision_id}.py"
        metadata_path = self.state_dir / f"{revision_id}.json"
        if not path.is_file() or not metadata_path.is_file():
            raise AdapterError(f"unknown Optimizer Design revision: {revision_id}")
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected = payload.pop("revision_digest", None)
        if expected != hashlib.sha256(canonical_json(payload)).hexdigest():
            raise AdapterError(f"Optimizer Design revision metadata drift: {revision_id}")
        self._validate_source(path)
        actual = sha256_file(path)
        if actual != payload["candidate_sha256"]:
            raise AdapterError(f"Optimizer Design revision source drift: {revision_id}")
        return OptimizerRevision(
            revision_id=revision_id,
            parent_id=payload["parent_id"],
            candidate_sha256=actual,
            unified_diff=str(payload["unified_diff"]),
            created_at=str(payload["created_at"]),
            creation_metadata=dict(payload["creation_metadata"]),
            path=path,
        )

    def commit_source(
        self,
        source: str,
        *,
        parent_id: str,
        revision_id: str,
        creation_metadata: Mapping[str, Any] | None = None,
    ) -> OptimizerRevision:
        self._validate_id(revision_id)
        if revision_id == "baseline":
            raise AdapterError("Optimizer Design baseline revision is reserved")
        parent = self.get(parent_id)
        destination = self.revisions_dir / f"{revision_id}.py"
        if destination.exists() or (self.state_dir / f"{revision_id}.json").exists():
            raise AdapterError(f"Optimizer Design revision already exists: {revision_id}")
        destination.write_text(source, encoding="utf-8")
        self._validate_source(destination)
        parent_lines = parent.path.read_text(encoding="utf-8").splitlines(keepends=True)
        candidate_lines = source.splitlines(keepends=True)
        revision = OptimizerRevision(
            revision_id=revision_id,
            parent_id=parent_id,
            candidate_sha256=sha256_file(destination),
            unified_diff="".join(
                difflib.unified_diff(
                    parent_lines,
                    candidate_lines,
                    fromfile=f"{parent_id}.py",
                    tofile=f"{revision_id}.py",
                )
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
            creation_metadata=dict(creation_metadata or {}),
            path=destination,
        )
        self._write(revision)
        return revision

    def replay(self, revision_id: str, destination: Path) -> Path:
        revision = self.get(revision_id)
        destination = destination.resolve()
        if destination.exists() or destination.is_symlink():
            raise AdapterError(f"Optimizer Design replay destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(revision.path, destination)
        if sha256_file(destination) != revision.candidate_sha256:
            raise AdapterError("Optimizer Design replay differs from the selected revision")
        return destination

    def _write(self, revision: OptimizerRevision) -> None:
        payload = {
            "revision_id": revision.revision_id,
            "parent_id": revision.parent_id,
            "candidate_sha256": revision.candidate_sha256,
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


__all__ = ["OptimizerRevision", "OptimizerRevisionStore"]
