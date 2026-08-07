"""Minimal capability client exposed to Terminal AO search processes."""

from __future__ import annotations

import argparse
import json
import socket
import sys


def request_evaluation(socket_path: str, token: str) -> dict[str, object]:
    payload = json.dumps({"operation": "evaluate-dev", "token": token}).encode() + b"\n"
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(request_evaluation(args.socket, args.token), sort_keys=True))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
