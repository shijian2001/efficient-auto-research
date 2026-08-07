"""Deterministic Terminal-Bench 36/53 reconstruction assets."""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json


@dataclass(frozen=True)
class TaskMetadata:
    name: str
    difficulty: str
    category: str


@dataclass(frozen=True)
class FrozenSplit:
    protocol_id: str
    dataset_digest: str
    seed: int
    dev: tuple[str, ...]
    test: tuple[str, ...]
    algorithm: str = "stratified-largest-remainder-sha256-v1"

    def validate(self, expected_tasks: set[str] | None = None) -> None:
        if len(self.dev) != 36 or len(self.test) != 53:
            raise AdapterError(
                f"Terminal AO split must be 36/53, got {len(self.dev)}/{len(self.test)}"
            )
        if len(set(self.dev)) != 36 or len(set(self.test)) != 53:
            raise AdapterError("Terminal AO split contains duplicate task IDs")
        if set(self.dev) & set(self.test):
            raise AdapterError("Terminal AO dev and test splits overlap")
        if expected_tasks is not None and set(self.dev) | set(self.test) != expected_tasks:
            raise AdapterError("Terminal AO split union does not match the frozen 89-task dataset")
        if len(self.dataset_digest) != 64:
            raise AdapterError("Terminal AO split requires a dataset SHA-256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "dataset_digest": self.dataset_digest,
            "seed": self.seed,
            "algorithm": self.algorithm,
            "dev": list(self.dev),
            "test": list(self.test),
        }

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def load(cls, path: Path, expected_tasks: set[str] | None = None) -> "FrozenSplit":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected_digest = payload.pop("split_digest", None)
        split = cls(
            protocol_id=str(payload["protocol_id"]),
            dataset_digest=str(payload["dataset_digest"]),
            seed=int(payload["seed"]),
            algorithm=str(payload["algorithm"]),
            dev=tuple(payload["dev"]),
            test=tuple(payload["test"]),
        )
        split.validate(expected_tasks)
        if split.digest != expected_digest:
            raise AdapterError(f"Terminal AO split digest mismatch: {path}")
        return split


def dataset_tree_digest(dataset_dir: Path) -> str:
    dataset_dir = dataset_dir.resolve()
    manifest: dict[str, str] = {}
    for path in sorted(dataset_dir.rglob("*")):
        if path.is_dir():
            continue
        if not path.is_file() or path.is_symlink():
            raise AdapterError(f"Terminal-Bench dataset contains a non-regular file: {path}")
        manifest[path.relative_to(dataset_dir).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    if len(list(dataset_dir.glob("*/task.toml"))) != 89 or not manifest:
        raise AdapterError("Terminal-Bench dataset tree must contain exactly 89 task definitions")
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def read_task_metadata(dataset_dir: Path) -> tuple[TaskMetadata, ...]:
    dataset_dir = dataset_dir.resolve()
    tasks: list[TaskMetadata] = []
    for task_file in sorted(dataset_dir.glob("*/task.toml")):
        payload = tomllib.loads(task_file.read_text(encoding="utf-8"))
        metadata = payload.get("metadata", {})
        tasks.append(
            TaskMetadata(
                name=task_file.parent.name,
                difficulty=str(metadata.get("difficulty", "unknown")),
                category=str(metadata.get("category", "unknown")),
            )
        )
    if len(tasks) != 89 or len({task.name for task in tasks}) != 89:
        raise AdapterError(f"Terminal-Bench dataset must contain 89 unique tasks, got {len(tasks)}")
    return tuple(tasks)


def _stable_key(seed: int, name: str) -> str:
    return hashlib.sha256(f"{seed}:{name}".encode("utf-8")).hexdigest()


def build_reconstruction_split(
    metadata: tuple[TaskMetadata, ...],
    *,
    dataset_digest: str,
    seed: int = 20260806,
    protocol_id: str = "terminal-bench-ao-reconstruction-v1",
) -> FrozenSplit:
    if len(metadata) != 89:
        raise AdapterError("Terminal AO reconstruction requires exactly 89 tasks")
    strata: dict[tuple[str, str], list[TaskMetadata]] = defaultdict(list)
    for task in metadata:
        strata[(task.difficulty, task.category)].append(task)
    quotas: dict[tuple[str, str], int] = {}
    fractions: list[tuple[float, tuple[str, str]]] = []
    for key, tasks in strata.items():
        exact = len(tasks) * 36 / 89
        quotas[key] = int(exact)
        fractions.append((exact - int(exact), key))
    remaining = 36 - sum(quotas.values())
    for _, key in sorted(fractions, key=lambda item: (-item[0], item[1]))[:remaining]:
        quotas[key] += 1
    dev: list[str] = []
    test: list[str] = []
    for key in sorted(strata):
        ordered = sorted(strata[key], key=lambda task: (_stable_key(seed, task.name), task.name))
        quota = quotas[key]
        dev.extend(task.name for task in ordered[:quota])
        test.extend(task.name for task in ordered[quota:])
    split = FrozenSplit(
        protocol_id=protocol_id,
        dataset_digest=dataset_digest,
        seed=seed,
        dev=tuple(sorted(dev)),
        test=tuple(sorted(test)),
    )
    split.validate({task.name for task in metadata})
    return split


def distribution(
    split: FrozenSplit,
    metadata: tuple[TaskMetadata, ...],
) -> dict[str, dict[str, int]]:
    by_name = {task.name: task for task in metadata}
    output: dict[str, dict[str, int]] = {}
    for split_name, task_ids in (("dev", split.dev), ("test", split.test)):
        counts: dict[str, int] = defaultdict(int)
        for task_id in task_ids:
            task = by_name[task_id]
            counts[f"difficulty:{task.difficulty}"] += 1
            counts[f"category:{task.category}"] += 1
        output[split_name] = dict(sorted(counts.items()))
    return output


__all__ = [
    "FrozenSplit",
    "TaskMetadata",
    "build_reconstruction_split",
    "dataset_tree_digest",
    "distribution",
    "read_task_metadata",
]
