from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def directory_digest(dataset_dir: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for file_path in sorted(path for path in dataset_dir.rglob("*") if path.is_file()):
        relative_path = file_path.relative_to(dataset_dir).as_posix()
        digest.update(relative_path.encode())
        with file_path.open("rb") as file_handle:
            while chunk := file_handle.read(1024 * 1024):
                digest.update(chunk)
                total_bytes += len(chunk)
        file_count += 1
    return digest.hexdigest(), file_count, total_bytes


def parse_size_to_mb(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT])B?\s*", value.upper())
    if match is None:
        return None
    magnitude = float(match.group(1))
    unit = match.group(2)
    multipliers = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    return round(magnitude * multipliers[unit])


def main() -> None:
    args = parse_args()
    dataset_dir = args.dataset.resolve()
    task_files = sorted(dataset_dir.glob("*/task.toml"))
    required_paths = (
        Path("instruction.md"),
        Path("environment/Dockerfile"),
        Path("tests/test.sh"),
    )
    tasks = []
    missing_required_files = []
    resource_values: dict[str, list[int]] = {
        "cpus": [],
        "memory_mb": [],
        "storage_mb": [],
        "gpus": [],
    }

    for task_file in task_files:
        task_dir = task_file.parent
        task_config = tomllib.loads(task_file.read_text())
        environment = task_config.get("environment", {})
        missing = [
            required_path.as_posix()
            for required_path in required_paths
            if not (task_dir / required_path).is_file()
        ]
        if missing:
            missing_required_files.append({"task": task_dir.name, "missing": missing})
        resources = {
            "cpus": environment.get("cpus"),
            "memory_mb": environment.get("memory_mb")
            or parse_size_to_mb(environment.get("memory")),
            "storage_mb": environment.get("storage_mb")
            or parse_size_to_mb(environment.get("storage")),
            "gpus": environment.get("gpus") or 0,
        }
        for resource_name, value in resources.items():
            if isinstance(value, int):
                resource_values[resource_name].append(value)
        tasks.append(
            {
                "name": task_dir.name,
                "resources": resources,
                "missing_required_files": missing,
            }
        )

    dataset_sha256, file_count, total_bytes = directory_digest(dataset_dir)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "Terminal-Bench 2.0",
        "source_identifier": "terminal-bench@2.0",
        "dataset_path": str(dataset_dir),
        "task_count": len(tasks),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "sha256": dataset_sha256,
        "missing_required_files": missing_required_files,
        "resource_summary": {
            resource_name: {
                "minimum": min(values) if values else None,
                "maximum": max(values) if values else None,
                "values": sorted(set(values)),
            }
            for resource_name, values in resource_values.items()
        },
        "tasks": tasks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    if len(tasks) != 89:
        raise SystemExit(f"Expected 89 tasks, found {len(tasks)}")
    if missing_required_files:
        raise SystemExit("Dataset contains tasks with missing required files")
    if manifest["resource_summary"]["gpus"]["maximum"] != 0:
        raise SystemExit("Terminal-Bench 2.0 contains a task requesting a GPU")

    print(json.dumps({key: manifest[key] for key in (
        "dataset",
        "source_identifier",
        "task_count",
        "file_count",
        "total_bytes",
        "sha256",
        "resource_summary",
    )}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
