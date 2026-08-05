"""Minimal format-only validation service backed by MLE-Bench."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def validate_submission_file(data_root: Path, competition_id: str, submission: Path) -> tuple[bool, str]:
    from mlebench.grade import validate_submission
    from mlebench.registry import registry

    competition = registry.set_data_dir(data_root).get_competition(competition_id)
    return validate_submission(submission, competition)


def _multipart_file(body: bytes, content_type: str) -> bytes:
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("multipart request has no boundary")
    boundary = content_type.split(marker, 1)[1].strip().strip('"').encode()
    for part in body.split(b"--" + boundary):
        if b"filename=" not in part:
            continue
        _, separator, payload = part.partition(b"\r\n\r\n")
        if separator:
            return payload[:-2] if payload.endswith(b"\r\n") else payload
    raise ValueError("multipart request contains no file")


def create_handler(data_root: Path, default_competition_id: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ArborMLEFormat/1"

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/health":
                self._json(404, {"ok": False})
                return
            self._json(200, {"ok": True})

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/validate":
                self._json(404, {"valid": False, "message": "not found"})
                return
            competition_id = (
                self.headers.get("X-Competition-Id")
                or self.headers.get("exp-id")
                or parse_qs(urlparse(self.path).query).get("competition_id", [None])[0]
                or default_competition_id
            )
            if not competition_id:
                self._json(400, {"valid": False, "message": "competition id is required"})
                return
            if default_competition_id and competition_id != default_competition_id:
                self._json(403, {"valid": False, "message": "competition id is not allowed"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 512 * 1024 * 1024:
                    raise ValueError("invalid submission size")
                body = self.rfile.read(length)
                content_type = self.headers.get("Content-Type", "")
                if content_type.startswith("multipart/form-data"):
                    body = _multipart_file(body, content_type)
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
                    handle.write(body)
                    temporary = Path(handle.name)
                try:
                    valid, message = validate_submission_file(data_root, competition_id, temporary)
                finally:
                    temporary.unlink(missing_ok=True)
                self._json(200 if valid else 422, {"valid": bool(valid), "message": str(message)})
            except Exception as exc:  # noqa: BLE001
                self._json(400, {"valid": False, "message": f"validation failed: {exc}"})

        def log_message(self, format: str, *args: object) -> None:
            if os.environ.get("ARBOR_MLE_SERVER_VERBOSE"):
                super().log_message(format, *args)

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--competition-id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("GRADING_SERVER_PORT", "5005")))
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(
        (args.host, args.port), create_handler(args.data_root.resolve(), args.competition_id)
    )
    print(f"Arbor MLE format server listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
