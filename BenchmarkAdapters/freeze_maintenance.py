"""Generate review candidates for frozen implementation and Terminal dataset assets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import AdapterError
from .protocol import canonical_json, sha256_file, write_json_exclusive
from .registry import ROOT
from .TerminalAO.split import (
    build_reconstruction_split,
    dataset_tree_digest,
    read_task_metadata,
)


def freeze_implementation_manifest_candidate(
    *, source_manifest: Path, output_path: Path
) -> dict[str, object]:
    payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    payload.pop("manifest_digest", None)
    files = payload.get("implementation_files")
    if not isinstance(files, dict) or not files:
        raise AdapterError("source manifest has no implementation_files allowlist")
    updated: dict[str, str] = {}
    for relative in sorted(files):
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise AdapterError(f"frozen implementation candidate is missing: {relative}")
        updated[str(relative)] = sha256_file(path)
    payload["implementation_files"] = updated
    if "protocol_id" in payload:
        payload["manifest_digest"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    write_json_exclusive(output_path, payload)
    return payload


def freeze_terminal_dataset_candidate(
    *, dataset_dir: Path, source_split: Path, output_path: Path
) -> dict[str, object]:
    source = json.loads(source_split.read_text(encoding="utf-8"))
    metadata = read_task_metadata(dataset_dir)
    actual_digest = dataset_tree_digest(dataset_dir)
    split = build_reconstruction_split(
        metadata,
        dataset_digest=actual_digest,
        seed=int(source["seed"]),
        protocol_id=str(source["protocol_id"]),
    )
    payload = {
        **split.to_dict(),
        "split_digest": split.digest,
        "maintenance": {
            "action": "generated-for-review",
            "previous_dataset_digest": source.get("dataset_digest"),
            "membership_changed": (
                tuple(source.get("dev", ())) != split.dev
                or tuple(source.get("test", ())) != split.test
            ),
            "automatic_promotion": False,
        },
    }
    write_json_exclusive(output_path, payload)
    return payload


__all__ = [
    "freeze_implementation_manifest_candidate",
    "freeze_terminal_dataset_candidate",
]
