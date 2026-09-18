"""Preserve evidence when explicitly retrying a relay that never started."""

from __future__ import annotations

import fcntl
import hashlib
import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive


@contextmanager
def cell_execution_lock(run_dir: Path):
    """Keep the lock outside the cell so archiving cannot release it."""
    run_dir = run_dir.resolve()
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    with (run_dir.parent / f".{run_dir.name}.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdapterError(f"MLE cell is already running: {run_dir}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def has_cell_evidence(run_dir: Path) -> bool:
    return run_dir.exists() and any(run_dir.iterdir())


def validate_relay_startup_retry(
    run_dir: Path, *, run_id: str, protocol_digest: str, model_config_digest: str,
    agent_variant: str,
) -> None:
    """Fail closed: this is infrastructure recovery, never a score-based retry.

    The legacy AI Scientist failures predate a typed infrastructure status. Only
    accept their exact local socket-startup failure, with no agent work or LLM
    usage. Other failure classes need a separately reviewed recovery policy.
    """
    try:
        result = json.loads((run_dir / "result.json").read_text())
        manifest = json.loads((run_dir / "manifest.json").read_text())
        relay_log = (run_dir / "agent-output/relay.log").read_text()
    except (OSError, ValueError) as exc:
        raise AdapterError(f"relay retry requires intact failure evidence: {run_dir}") from exc
    if not isinstance(result, dict) or not isinstance(manifest, dict):
        raise AdapterError("relay retry requires JSON object records")
    recorded_digest = manifest.get("manifest_digest")
    contents = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    if hashlib.sha256(canonical_json(contents)).hexdigest() != recorded_digest:
        raise AdapterError("relay retry manifest digest mismatch")
    if (
        result.get("run_id") != run_id or manifest.get("run_id") != run_id
        or result.get("protocol_digest") != protocol_digest
        or manifest.get("protocol_digest") != protocol_digest
        or result.get("manifest_digest") != recorded_digest
        or manifest.get("model_config_digest") != model_config_digest
        or manifest.get("agent_variant") != agent_variant
        or manifest.get("agent") != "ai-scientist"
        or result.get("agent") != "ai-scientist"
    ):
        raise AdapterError("relay retry identity/configuration differs from the original run")
    if (
        result.get("status") != "failed" or result.get("score_valid") is not False
        or result.get("score") is not None or result.get("artifact_path") is not None
        or result.get("artifact_sha256") is not None
        or not str(result.get("failure_reason", "")).startswith("RuntimeError: relay exited early;")
        or "OSError: AF_UNIX path too long" not in relay_log
    ):
        raise AdapterError("only an unstarted AI Scientist relay socket failure can be retried")
    if result.get("tokens") or result.get("cost"):
        raise AdapterError("relay retry refused: the previous attempt recorded usage")
    # All known startup files are small host-owned evidence. Any additional file
    # may be agent work, even if the recorded failure reason looks retryable.
    allowed = {"manifest.json", "result.json", "agent-output/relay.log", "agent-output/.gitignore"}
    for path in run_dir.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and path.relative_to(run_dir).as_posix() not in allowed):
            raise AdapterError(f"relay retry refused: previous attempt contains work: {path}")


def archive_relay_startup_failure(run_dir: Path) -> Path:
    """Move the failed attempt intact, outside the canonical result namespace.

    Caller must hold the cell lock and have validated the original evidence.
    Nothing is deleted, and the same run identity is used for the fresh attempt.
    """
    run_dir = run_dir.resolve()
    archive_root = run_dir.parent / ".relay-startup-failures" / run_dir.name
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix="attempt-", dir=archive_root))
    write_json_exclusive(archive / "retry.json", {
        "reason": "explicit retry of AI Scientist relay socket startup failure",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_run_dir": str(run_dir),
        "archived_run_dir": str(archive / "cell"),
        "original_manifest_sha256": sha256_file(run_dir / "manifest.json"),
        "original_result_sha256": sha256_file(run_dir / "result.json"),
    })
    run_dir.rename(archive / "cell")
    return archive
