"""Expose one host-owned Unix relay socket on sandbox-local TCP loopback."""

from __future__ import annotations

import argparse
import selectors
import socket
import socketserver
import subprocess
import time


class _ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.connect(self.server.unix_socket)  # type: ignore[attr-defined]
        selector = selectors.DefaultSelector()
        selector.register(self.request, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, self.request)
        try:
            while selector.get_map():
                for key, _ in selector.select():
                    source = key.fileobj
                    destination = key.data
                    chunk = source.recv(65536)
                    if not chunk:
                        selector.unregister(source)
                        try:
                            destination.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                    destination.sendall(chunk)
        finally:
            selector.close()
            upstream.close()


class _ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, unix_socket: str):
        self.unix_socket = unix_socket
        super().__init__(address, _ForwardHandler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=6200)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide an Agent command after --")
    server = None
    for _ in range(100):
        try:
            server = _ForwardServer(("127.0.0.1", args.port), args.socket)
            break
        except OSError:
            time.sleep(0.1)
    if server is None:
        raise RuntimeError("sandbox loopback did not become ready")
    with server:
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.run(command, check=False).returncode
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
