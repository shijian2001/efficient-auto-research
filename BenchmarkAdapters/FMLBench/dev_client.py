"""Client for the FML development-only evaluator capability."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    request = {"operation": "evaluate-current", "token": args.token}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(args.socket))
        client.sendall((json.dumps(request, sort_keys=True) + "\n").encode("utf-8"))
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
    print(response.decode("utf-8").strip())
    payload = json.loads(response)
    return 0 if payload.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
