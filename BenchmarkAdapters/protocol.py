"""Frozen benchmark protocol contracts shared by all adapters."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .contracts import AdapterError


class BenchmarkMode(str, Enum):
    MLE = "mle"
    AUTORESEARCH = "autoresearch"
    OPTIMIZER_DESIGN = "optimizer-design"
    TERMINAL_AO = "terminal-ao"
    TERMINAL_DIRECT_SMOKE = "terminal-direct-smoke"
    FML = "fml-bench"


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FormalProtocol:
    protocol_id: str
    mode: BenchmarkMode
    task_ids: tuple[str, ...]
    asset_digests: Mapping[str, str]
    model: str
    reasoning_effort: str
    temperature: float | None
    wall_clock_seconds: int
    seeds: tuple[int, ...]
    retry_policy: str
    failure_policy: str
    artifact_policy: str
    aggregation_policy: str
    hardware_policy: str
    formal: bool = True
    schema_version: int = 1

    def validate(self) -> None:
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported formal protocol schema")
        if not self.protocol_id.strip():
            raise AdapterError("protocol_id must not be empty")
        if not self.task_ids or len(set(self.task_ids)) != len(self.task_ids):
            raise AdapterError("protocol task_ids must be non-empty and unique")
        if not self.asset_digests:
            raise AdapterError("formal protocol requires asset digests")
        for name, digest in self.asset_digests.items():
            if not name or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise AdapterError(f"invalid SHA-256 digest for protocol asset {name!r}")
        if not self.model or self.wall_clock_seconds < 1:
            raise AdapterError("protocol requires a model and positive wall-clock budget")
        if not self.seeds or len(set(self.seeds)) != len(self.seeds):
            raise AdapterError("protocol seeds must be non-empty and unique")
        if self.formal and len(self.seeds) not in {1, 3}:
            raise AdapterError("formal protocol outer repetitions must be 1 or 3")
        required_policies = (
            self.retry_policy,
            self.failure_policy,
            self.artifact_policy,
            self.aggregation_policy,
            self.hardware_policy,
        )
        if any(not value.strip() for value in required_policies):
            raise AdapterError("formal protocol policies must not be empty")
        if self.mode is BenchmarkMode.TERMINAL_DIRECT_SMOKE and self.formal:
            raise AdapterError("Terminal direct solving is a smoke mode, never a formal protocol")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.schema_version == 1:
            payload.pop("schema_version")
        payload["mode"] = self.mode.value
        payload["task_ids"] = list(self.task_ids)
        payload["seeds"] = list(self.seeds)
        payload["asset_digests"] = dict(sorted(self.asset_digests.items()))
        return payload

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def write(self, path: Path) -> None:
        self.validate()
        payload = {**self.to_dict(), "protocol_digest": self.digest}
        write_json_exclusive(path, payload)

    @classmethod
    def load(cls, path: Path) -> "FormalProtocol":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("protocol_digest", None)
        protocol = cls(
            protocol_id=str(payload["protocol_id"]),
            mode=BenchmarkMode(payload["mode"]),
            task_ids=tuple(payload["task_ids"]),
            asset_digests=dict(payload["asset_digests"]),
            model=str(payload["model"]),
            reasoning_effort=str(payload["reasoning_effort"]),
            temperature=(
                None if payload.get("temperature") is None else float(payload["temperature"])
            ),
            wall_clock_seconds=int(payload["wall_clock_seconds"]),
            seeds=tuple(int(seed) for seed in payload["seeds"]),
            retry_policy=str(payload["retry_policy"]),
            failure_policy=str(payload["failure_policy"]),
            artifact_policy=str(payload["artifact_policy"]),
            aggregation_policy=str(payload["aggregation_policy"]),
            hardware_policy=str(payload["hardware_policy"]),
            formal=bool(payload.get("formal", True)),
            schema_version=int(payload.get("schema_version", 1)),
        )
        protocol.validate()
        if expected != protocol.digest:
            raise AdapterError(f"protocol digest mismatch: {path}")
        return protocol


def write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise AdapterError(f"refusing to overwrite immutable record: {path}") from exc
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite immutable record: {path}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


__all__ = [
    "BenchmarkMode",
    "FormalProtocol",
    "canonical_json",
    "sha256_file",
    "write_json_exclusive",
]
