"""Explicit, hash-bound publication of final benchmark artifacts."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .contracts import AdapterError
from .protocol import sha256_file


@dataclass(frozen=True)
class PublishedArtifact:
    path: Path
    sha256: str
    size_bytes: int
    source_path: Path


def publish_artifact(source: Path, destination: Path) -> PublishedArtifact:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file() or source.is_symlink():
        raise AdapterError(f"artifact source must be a regular file: {source}")
    if source.stat().st_size <= 0:
        raise AdapterError(f"artifact source is empty: {source}")
    if destination.exists() or destination.is_symlink():
        raise AdapterError(f"refusing to overwrite published artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as target, source.open("rb") as handle:
            shutil.copyfileobj(handle, target)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite published artifact: {destination}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return PublishedArtifact(
        path=destination,
        sha256=sha256_file(destination),
        size_bytes=destination.stat().st_size,
        source_path=source,
    )


__all__ = ["PublishedArtifact", "publish_artifact"]
