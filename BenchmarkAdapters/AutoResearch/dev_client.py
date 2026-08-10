"""Minimal local capability client exposed to Autoresearch native Agent processes."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Mapping


def request(socket_path: str, token: str, operation: str, **payload: object) -> object:
    message = json.dumps({"operation": operation, "token": token, **payload}).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(socket_path)
        connection.sendall(message)
        response = b""
        while not response.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            response += chunk
    result = json.loads(response.decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error", "Autoresearch dev broker request failed")))
    return result.get("result")


def _read_state(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Autoresearch client state must be a JSON object")
    return payload


def _write_state(path: Path, payload: Mapping[str, object]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def evaluate_current(
    socket_path: str,
    token: str,
    train_path: Path,
    state_path: Path,
    parent_revision_id: str | None = None,
) -> dict[str, object]:
    train_path = train_path.resolve()
    if not train_path.is_file() or train_path.is_symlink():
        raise RuntimeError(f"train.py must be a regular file: {train_path}")
    state = _read_state(state_path)
    parent_id = parent_revision_id or str(state.get("revision_id", "baseline"))
    created = request(
        socket_path,
        token,
        "create-candidate",
        train_source=train_path.read_text(encoding="utf-8"),
        parent_revision_id=parent_id,
    )
    if not isinstance(created, dict):
        raise RuntimeError("Autoresearch broker returned an invalid candidate response")
    feedback = request(
        socket_path,
        token,
        "evaluate-dev",
        revision_id=str(created["revision_id"]),
    )
    if not isinstance(feedback, dict):
        raise RuntimeError("Autoresearch broker returned invalid development feedback")
    _write_state(
        state_path,
        {
            "revision_id": created["revision_id"],
            "candidate_sha256": created["candidate_sha256"],
            "last_feedback": feedback,
        },
    )
    return feedback


def declare_current(socket_path: str, token: str, state_path: Path) -> dict[str, object]:
    state = _read_state(state_path)
    revision_id = state.get("revision_id")
    if not isinstance(revision_id, str):
        raise RuntimeError("no evaluated Autoresearch revision is available to declare")
    declared = request(socket_path, token, "declare-final", revision_id=revision_id)
    if not isinstance(declared, dict):
        raise RuntimeError("Autoresearch broker returned an invalid final declaration")
    _write_state(state_path, {**state, "declared": declared})
    return declared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("evaluate-current", "declare-current", "best-dev"))
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--train", type=Path, default=Path("train.py"))
    parser.add_argument("--state", type=Path, default=Path(".autoresearch-candidate.json"))
    parser.add_argument("--parent-revision-id")
    args = parser.parse_args(argv)
    try:
        if args.operation == "evaluate-current":
            result = evaluate_current(
                args.socket,
                args.token,
                args.train,
                args.state,
                args.parent_revision_id,
            )
        elif args.operation == "declare-current":
            result = declare_current(args.socket, args.token, args.state)
        else:
            result = request(args.socket, args.token, "best-dev")
        if isinstance(result, dict):
            for metric_name in ("val_bpb", "score_steps", "pass_rate", "metric"):
                metric = result.get(metric_name)
                if isinstance(metric, (int, float)):
                    result = {**result, "score": metric}
                    break
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["declare_current", "evaluate_current", "request"]
