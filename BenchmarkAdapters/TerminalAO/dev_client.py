"""Minimal capability client exposed to Terminal AO search processes."""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from pathlib import Path


def request(
    socket_path: str,
    token: str,
    operation: str,
    *,
    files: dict[str, str] | None = None,
) -> dict[str, object]:
    payload = json.dumps(
        {"operation": operation, "token": token, **({"files": files} if files else {})}
    ).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(socket_path)
        connection.sendall(payload)
        response = b""
        while not response.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk:
                break
            response += chunk
    result = json.loads(response.decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error", "dev evaluation failed")))
    return dict(result["evaluation"])


def request_evaluation(socket_path: str, token: str) -> dict[str, object]:
    return request(socket_path, token, "evaluate-dev")


def snapshot_files(root: Path, editable_paths: tuple[str, ...]) -> dict[str, str]:
    root = root.resolve()
    files: dict[str, str] = {}
    for relative in editable_paths:
        path = root / relative
        candidates = tuple(path.rglob("*")) if path.is_dir() else (path,)
        for candidate in candidates:
            if candidate.is_symlink():
                raise RuntimeError(f"candidate snapshot contains a symlink: {candidate}")
            if candidate.is_file():
                name = candidate.relative_to(root).as_posix()
                files[name] = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument(
        "--operation",
        choices=("evaluate-dev",),
        default="evaluate-dev",
    )
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--editable", action="append", default=[])
    args = parser.parse_args()
    try:
        files = (
            snapshot_files(args.candidate_root, tuple(args.editable))
            if args.candidate_root is not None
            else None
        )
        result = request(args.socket, args.token, args.operation, files=files)
        if isinstance(result.get("pass_rate"), (int, float)):
            result["score"] = result["pass_rate"]
        print(
            json.dumps(
                result,
                sort_keys=True,
            )
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "request",
    "request_evaluation",
    "snapshot_files",
]
