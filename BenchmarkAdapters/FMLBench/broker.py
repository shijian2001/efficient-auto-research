"""Development-only Unix capability for FML Agent launchers."""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import socketserver
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..contracts import AdapterError
from .evaluator import FMLSharedEvaluator


@dataclass(frozen=True)
class FMLDevCapability:
    socket_path: Path
    token: str


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: str, evaluator: FMLSharedEvaluator, token: str):
        self.evaluator = evaluator
        self.token = token
        super().__init__(socket_path, _Handler)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            payload = json.loads(self.rfile.readline().decode("utf-8"))
            if payload.get("token") != self.server.token:
                raise AdapterError("invalid FML development capability token")
            if payload.get("operation") != "evaluate-current":
                raise AdapterError("unsupported FML development capability operation")
            response = self.server.evaluator.evaluate_development()
        except Exception as exc:
            response = {"status": "error", "failure_reason": f"{type(exc).__name__}: {exc}"}
        self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))


@contextmanager
def fml_dev_broker(evaluator: FMLSharedEvaluator, socket_path: Path) -> Iterator[FMLDevCapability]:
    socket_path = socket_path.resolve()
    if len(os.fsencode(socket_path)) >= 100:
        digest = hashlib.sha256(str(socket_path).encode("utf-8")).hexdigest()[:24]
        socket_path = Path("/tmp") / f"fml-dev-{digest}.sock"
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    token = secrets.token_urlsafe(32)
    server = _Server(str(socket_path), evaluator, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FMLDevCapability(socket_path=socket_path, token=token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        socket_path.unlink(missing_ok=True)


__all__ = ["FMLDevCapability", "fml_dev_broker"]
