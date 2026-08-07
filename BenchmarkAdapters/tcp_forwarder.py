"""Forward one local TCP port to a host and port."""

from __future__ import annotations

import argparse
import selectors
import socket
import socketserver


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        upstream = socket.create_connection(self.server.target)  # type: ignore[attr-defined]
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
                    else:
                        destination.sendall(chunk)
        finally:
            selector.close()
            upstream.close()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, listen, target):
        self.target = target
        super().__init__(listen, _Handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    args = parser.parse_args()
    with _Server(
        (args.listen_host, args.listen_port),
        (args.target_host, args.target_port),
    ) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
