"""Local capture relay for deterministic FML launcher validation."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator


@dataclass(frozen=True)
class CapturedRequest:
    path: str
    model: str | None
    body: dict[str, Any]


class _Server(ThreadingHTTPServer):
    requests: list[CapturedRequest]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        model = payload.get("model") if isinstance(payload, dict) else None
        self.server.requests.append(
            CapturedRequest(path=self.path, model=None if model is None else str(model), body=payload)
        )
        response = {
            "id": "fml-fake-response",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "synthetic relay response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@dataclass
class CaptureRelay:
    base_url: str
    requests: list[CapturedRequest]


@contextmanager
def capture_relay() -> Iterator[CaptureRelay]:
    server = _Server(("127.0.0.1", 0), _Handler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield CaptureRelay(base_url=f"http://{host}:{port}/v1", requests=server.requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = ["CaptureRelay", "CapturedRequest", "capture_relay"]
