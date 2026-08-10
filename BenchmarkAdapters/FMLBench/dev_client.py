"""Client for the FML development-only evaluator capability."""

from __future__ import annotations

import argparse
import base64
import json
import socket
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--editable", action="append", default=[])
    args = parser.parse_args(argv)
    request = {"operation": "evaluate-current", "token": args.token}
    if args.candidate_root is not None:
        root = args.candidate_root.resolve()
        files: dict[str, str] = {}
        for relative in args.editable:
            path = root / relative
            candidates = tuple(path.rglob("*")) if path.is_dir() else (path,)
            for candidate in candidates:
                if candidate.is_symlink():
                    raise RuntimeError(
                        f"FML candidate snapshot contains a symlink: {candidate}"
                    )
                if candidate.is_file():
                    name = candidate.relative_to(root).as_posix()
                    files[name] = base64.b64encode(candidate.read_bytes()).decode("ascii")
        request["files"] = files
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(args.socket))
        client.sendall((json.dumps(request, sort_keys=True) + "\n").encode("utf-8"))
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    payload = json.loads(response)
    metric = payload.get("metric")
    if isinstance(metric, (int, float)):
        payload["score"] = metric
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
