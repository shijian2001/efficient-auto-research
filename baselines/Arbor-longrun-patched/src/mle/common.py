"""Shared helpers for the Arbor MLE-Bench adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


class AdapterError(RuntimeError):
    """Raised when an adapter contract cannot be satisfied."""


_METRIC_RE = re.compile(
    r"(?m)^\s*METRIC\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_metric_with_text(output: str) -> tuple[str, float]:
    matches = _METRIC_RE.findall(output)
    if not matches:
        raise AdapterError("solution.py must print a final line in the form METRIC=<finite-float>")
    raw_text = matches[-1]
    metric = float(raw_text)
    if not math.isfinite(metric):
        raise AdapterError("METRIC must be finite")
    return raw_text, metric


def parse_metric(output: str) -> float:
    return parse_metric_with_text(output)[1]


def infer_data_root(public_dir: Path, competition_id: str) -> Path:
    public_dir = public_dir.resolve()
    expected = (competition_id, "prepared", "public")
    if tuple(public_dir.parts[-3:]) != expected:
        raise AdapterError(
            f"data directory must end with {competition_id}/prepared/public: {public_dir}"
        )
    return public_dir.parents[2]


def infer_metric_direction(
    competition_id: str,
    public_dir: Path,
    explicit: str | None = None,
) -> str:
    if explicit and explicit != "auto":
        if explicit not in {"maximize", "minimize"}:
            raise AdapterError("metric direction must be auto, maximize, or minimize")
        return explicit

    try:
        from mlebench.data import get_leaderboard
        from mlebench.registry import registry

        competition = registry.set_data_dir(infer_data_root(public_dir, competition_id)).get_competition(
            competition_id
        )
        leaderboard = get_leaderboard(competition)
        return "minimize" if competition.grader.is_lower_better(leaderboard) else "maximize"
    except Exception as exc:  # noqa: BLE001
        raise AdapterError(
            "could not infer metric direction from MLE-Bench; pass --metric-direction explicitly"
        ) from exc


def discover_public_sample(public_dir: Path, destination: Path) -> Path:
    exact_names = (
        "sample_submission.csv",
        "sampleSubmission.csv",
        "sample_submission_null.csv",
    )
    for name in exact_names:
        candidate = public_dir / name
        if candidate.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, destination)
            return destination

    csv_candidates = sorted(
        path
        for path in public_dir.rglob("*.csv")
        if "submission" in path.name.lower() or "sample" in path.name.lower()
    )
    if csv_candidates:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(csv_candidates[0], destination)
        return destination

    zip_candidates = sorted(
        path
        for path in public_dir.rglob("*.zip")
        if "submission" in path.name.lower() or "sample" in path.name.lower()
    )
    for archive_path in zip_candidates:
        with zipfile.ZipFile(archive_path) as archive:
            members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
            if not members:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(members[0]) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            return destination

    raise AdapterError(f"no public sample submission found under {public_dir}")


def validate_submission_remote(
    validation_url: str,
    competition_id: str,
    submission: Path,
    *,
    timeout: float = 120.0,
) -> tuple[bool, str]:
    endpoint = validation_url.rstrip("/") + "/validate"
    request = urllib.request.Request(
        endpoint,
        data=submission.read_bytes(),
        method="POST",
        headers={
            "Content-Type": "text/csv",
            "X-Competition-Id": competition_id,
            "X-Filename": submission.name,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"valid": False, "message": body or str(exc)}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise AdapterError(f"submission validation server request failed: {exc}") from exc
    return bool(payload.get("valid")), str(payload.get("message", "validation failed"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
