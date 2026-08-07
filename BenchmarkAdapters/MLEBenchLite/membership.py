"""Frozen MLE-Bench Lite task membership and data preflight."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT_FILE = ROOT / "mle-bench-lite/source/experiments/splits/low.txt"
DEFAULT_DATA_MANIFEST = Path(__file__).with_name("data_manifest.json")


def load_lite_task_ids(split_file: Path = DEFAULT_SPLIT_FILE) -> tuple[str, ...]:
    split_file = split_file.resolve()
    if not split_file.is_file():
        raise AdapterError(f"MLE-Bench Lite split file is missing: {split_file}")
    task_ids = tuple(
        line.strip()
        for line in split_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(task_ids) != 22 or len(set(task_ids)) != 22:
        raise AdapterError(
            f"frozen MLE-Bench Lite split must contain 22 unique tasks, got {len(task_ids)}"
        )
    return task_ids


def split_digest(split_file: Path = DEFAULT_SPLIT_FILE) -> str:
    task_ids = load_lite_task_ids(split_file)
    return hashlib.sha256(("\n".join(task_ids) + "\n").encode("utf-8")).hexdigest()


def load_data_manifest(path: Path = DEFAULT_DATA_MANIFEST) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_digest = payload.pop("manifest_digest", None)
    actual_digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected_digest != actual_digest:
        raise AdapterError(f"MLE data manifest digest mismatch: {path}")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or tuple(item.get("task_id") for item in tasks) != load_lite_task_ids():
        raise AdapterError("MLE data manifest task set/order differs from the frozen Lite split")
    if payload.get("split_digest") != split_digest():
        raise AdapterError("MLE data manifest split digest differs from the frozen Lite split")
    return {**payload, "manifest_digest": expected_digest}


def data_manifest_digest(path: Path = DEFAULT_DATA_MANIFEST) -> str:
    return str(load_data_manifest(path)["manifest_digest"])


def validate_mlebench_source_identity(path: Path = DEFAULT_DATA_MANIFEST) -> None:
    manifest = load_data_manifest(path)
    source = ROOT / "mle-bench-lite/source"
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or completed.stdout.strip() != manifest["mlebench_source_commit"]:
        raise AdapterError("installed MLE-Bench source commit differs from the frozen data manifest")


def validate_lite_data_root(
    data_root: Path,
    *,
    split_file: Path = DEFAULT_SPLIT_FILE,
) -> tuple[str, ...]:
    data_root = data_root.resolve()
    expected = load_lite_task_ids(split_file)
    missing: list[str] = []
    for task_id in expected:
        public = data_root / task_id / "prepared/public"
        private = data_root / task_id / "prepared/private"
        if (
            not public.is_dir()
            or not (public / "description.md").is_file()
            or not private.is_dir()
            or not any(path.is_file() for path in private.iterdir())
        ):
            missing.append(task_id)
    if missing:
        raise AdapterError(
            "MLE-Bench Lite data root is incomplete; missing prepared public/private tasks: "
            + ", ".join(missing)
        )
    return expected


def verify_task_archive(data_root: Path, task_id: str, *, verify_hash: bool = False) -> None:
    manifest = load_data_manifest()
    record = next(item for item in manifest["tasks"] if item["task_id"] == task_id)
    archive = data_root.resolve() / task_id / f"{task_id}.zip"
    if not archive.is_file() or archive.stat().st_size != int(record["archive_size_bytes"]):
        raise AdapterError(f"MLE source archive differs from frozen data manifest: {task_id}")
    if verify_hash and sha256_file(archive) != record["archive_sha256"]:
        raise AdapterError(f"MLE source archive SHA-256 mismatch: {task_id}")


def require_lite_task(task_id: str, split_file: Path = DEFAULT_SPLIT_FILE) -> None:
    if task_id not in load_lite_task_ids(split_file):
        raise AdapterError(f"competition is not in the frozen MLE-Bench Lite split: {task_id}")


__all__ = [
    "DEFAULT_SPLIT_FILE",
    "DEFAULT_DATA_MANIFEST",
    "data_manifest_digest",
    "load_data_manifest",
    "load_lite_task_ids",
    "require_lite_task",
    "split_digest",
    "validate_lite_data_root",
    "validate_mlebench_source_identity",
    "verify_task_archive",
]
