"""Frozen MLE-Bench Lite task membership and data preflight."""

from __future__ import annotations

import hashlib
import json
import subprocess
import stat
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file
from ..protocol import write_json_exclusive


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


def _tree_file_hashes(root: Path) -> dict[str, str]:
    root = root.resolve()
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AdapterError(f"MLE prepared asset contains a symlink: {path}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            raise AdapterError(f"MLE prepared asset is not a regular file: {path}")
        hashes[relative] = sha256_file(path)
    if not hashes:
        raise AdapterError(f"MLE prepared asset tree is empty: {root}")
    return hashes


def freeze_data_manifest(
    *,
    data_root: Path,
    output_path: Path,
    split_file: Path = DEFAULT_SPLIT_FILE,
) -> dict[str, object]:
    """Explicit maintenance operation; never called by formal preflight."""

    task_ids = load_lite_task_ids(split_file)
    source = ROOT / "mle-bench-lite/source"
    completed = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or len(completed.stdout.strip()) != 40:
        raise AdapterError("cannot freeze MLE assets without an immutable benchmark commit")
    records = []
    for task_id in task_ids:
        task_root = data_root.resolve() / task_id
        archive = task_root / f"{task_id}.zip"
        public = task_root / "prepared/public"
        private = task_root / "prepared/private"
        if not archive.is_file() or archive.is_symlink():
            raise AdapterError(f"MLE source archive is missing or unsafe: {task_id}")
        records.append(
            {
                "task_id": task_id,
                "archive_size_bytes": archive.stat().st_size,
                "archive_sha256": sha256_file(archive),
                "prepared_public_files": _tree_file_hashes(public),
                "prepared_private_files": _tree_file_hashes(private),
            }
        )
    payload: dict[str, object] = {
        "schema_version": 2,
        "mlebench_source_commit": completed.stdout.strip(),
        "split_digest": split_digest(split_file),
        "grader_assets": {
            "grader_worker": sha256_file(Path(__file__).with_name("grader_worker.py")),
            "dependency_lock": sha256_file(ROOT / "mle-bench-lite/uv.lock"),
        },
        "tasks": records,
    }
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    write_json_exclusive(output_path, {**payload, "manifest_digest": digest})
    return {**payload, "manifest_digest": digest}


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


def upstream_checksums_path(task_id: str) -> Path:
    """Where mle-bench keeps its own per-file checksums for one competition."""
    return (
        ROOT
        / "mle-bench-lite/source/mlebench/competitions"
        / task_id
        / "checksums.yaml"
    )


def validate_lite_data_root(
    data_root: Path,
    *,
    split_file: Path = DEFAULT_SPLIT_FILE,
) -> tuple[str, ...]:
    """Check the frozen split is present and structurally usable.

    This deliberately does NOT re-hash the prepared trees. Upstream mle-bench
    already generates and verifies per-file checksums for every competition's
    public and private directories during ``mlebench prepare``
    (``mlebench/data.py::download_and_prepare_dataset``), storing them in each
    competition's ``checksums.yaml``. Re-hashing the same read-only, already
    frozen trees before every cell duplicated that work: one pass reads about
    135 GB, and a 22x7 campaign would have repaid it 154 times, which on this
    host's array cost longer than some Agents' actual solve budget.

    What the campaign is bound to is unchanged: ``data_manifest.json`` still
    records the identity written into every manifest, and each cell still
    verifies its own source archive by content in ``verify_task_archive``.
    To re-verify the prepared trees on demand, run the upstream check
    (``mlebench prepare`` without ``--skip-verification``) or the explicit
    ``mle-freeze-assets`` maintenance command.
    """
    data_root = data_root.resolve()
    expected = load_lite_task_ids(split_file)
    manifest = load_data_manifest()
    if manifest.get("schema_version") != 2:
        raise AdapterError(
            "formal MLE assets require schema_version 2; "
            "run the explicit mle-freeze-assets maintenance command"
        )
    records = {str(item["task_id"]): item for item in manifest["tasks"]}
    for task_id in expected:
        if task_id not in records:
            raise AdapterError(f"MLE data manifest is missing task: {task_id}")
        public = data_root / task_id / "prepared/public"
        private = data_root / task_id / "prepared/private"
        for directory, label in ((public, "public"), (private, "private")):
            if not directory.is_dir():
                raise AdapterError(
                    f"MLE prepared {label} data is missing for {task_id}: {directory}"
                )
            if directory.is_symlink():
                raise AdapterError(
                    f"MLE prepared {label} data is a symlink for {task_id}: {directory}"
                )
            if not any(directory.iterdir()):
                raise AdapterError(
                    f"MLE prepared {label} data is empty for {task_id}: {directory}"
                )
        if not upstream_checksums_path(task_id).is_file():
            raise AdapterError(
                f"upstream mle-bench checksums are missing for {task_id}; "
                "prepared data integrity is owned by mlebench prepare"
            )
    return expected


def verify_task_archive(data_root: Path, task_id: str, *, verify_hash: bool = True) -> None:
    manifest = load_data_manifest()
    record = next(item for item in manifest["tasks"] if item["task_id"] == task_id)
    archive = data_root.resolve() / task_id / f"{task_id}.zip"
    if not archive.is_file() or archive.stat().st_size != int(record["archive_size_bytes"]):
        raise AdapterError(f"MLE source archive differs from frozen data manifest: {task_id}")
    if not record.get("archive_sha256"):
        raise AdapterError(f"MLE formal archive hash is missing: {task_id}")
    if verify_hash and sha256_file(archive) != record["archive_sha256"]:
        raise AdapterError(f"MLE source archive SHA-256 mismatch: {task_id}")


def require_lite_task(task_id: str, split_file: Path = DEFAULT_SPLIT_FILE) -> None:
    if task_id not in load_lite_task_ids(split_file):
        raise AdapterError(f"competition is not in the frozen MLE-Bench Lite split: {task_id}")


__all__ = [
    "DEFAULT_SPLIT_FILE",
    "DEFAULT_DATA_MANIFEST",
    "data_manifest_digest",
    "freeze_data_manifest",
    "load_data_manifest",
    "load_lite_task_ids",
    "require_lite_task",
    "split_digest",
    "validate_lite_data_root",
    "validate_mlebench_source_identity",
    "verify_task_archive",
]
