#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import subprocess
import sys
import sysconfig
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "source_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source_mismatches = []
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        actual = sha256(path) if path.is_file() else None
        if actual != expected:
            source_mismatches.append(
                {"path": relative, "expected": expected, "actual": actual}
            )

    python_header = Path(sysconfig.get_paths()["include"]) / "Python.h"
    package_versions = {}
    for package in ("torch", "triton", "numpy", "pyarrow", "rustbpe", "kernels"):
        try:
            package_versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            package_versions[package] = None

    import torch

    home = Path(os.environ.get("HOME", str(Path.home())))
    cache = home / ".cache" / "autoresearch"
    training_shards = sorted((cache / "data").glob("shard_*.parquet"))
    tokenizer_files = [
        cache / "tokenizer" / "tokenizer.pkl",
        cache / "tokenizer" / "token_bytes.pt",
    ]
    uv_locked = subprocess.run(
        [
            "uv",
            "sync",
            "--project",
            str(ROOT),
            "--python",
            sys.executable,
            "--locked",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    blockers = []
    if source_mismatches:
        blockers.append("upstream source snapshot differs from source_manifest.json")
    if not python_header.is_file():
        blockers.append("Python.h is missing")
    if any(value is None for value in package_versions.values()):
        blockers.append("one or more locked packages are missing")
    if uv_locked.returncode:
        blockers.append("UV environment does not satisfy the tracked lock")
    if not training_shards:
        blockers.append("prepared parquet shards are missing")
    if not all(path.is_file() for path in tokenizer_files):
        blockers.append("prepared tokenizer files are missing")
    if args.require_cuda and not torch.cuda.is_available():
        blockers.append("CUDA is required but unavailable")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": manifest["commit"],
        "source_mismatches": source_mismatches,
        "python": sys.version.split()[0],
        "python_header": str(python_header),
        "python_header_exists": python_header.is_file(),
        "packages": package_versions,
        "uv_locked": uv_locked.returncode == 0,
        "cache_root": str(cache),
        "prepared_parquet_shard_count": len(training_shards),
        "tokenizer_complete": all(path.is_file() for path in tokenizer_files),
        "cuda_available": torch.cuda.is_available(),
        "cuda_device_count": torch.cuda.device_count(),
        "blockers": blockers,
        "ready": not blockers,
    }
    output = Path(
        os.environ.get(
            "AUTORESEARCH_VERIFICATION_PATH",
            str(ROOT / ".runtime" / "install_verification.json"),
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
