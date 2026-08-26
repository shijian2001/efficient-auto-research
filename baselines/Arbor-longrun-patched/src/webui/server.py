"""Read-only WebUI server (#7): mirror the event bus to a browser over SSE.

Zero third-party deps — a threaded ``http.server`` with a Server-Sent Events
stream. The same ``EventBus`` the terminal dashboard consumes is fanned out to
any connected browser; the page renders a snapshot of ``RunState`` plus the live
thinking/tool stream. Read-only: the browser only observes.

Engine safety: the bus subscriber runs in the orchestrator's emit thread, so it
must never block. It does a non-blocking put onto each client's *bounded* queue
and drops the oldest frame on overflow — a slow browser can't stall the run.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .snapshot import empty_state_dict, state_to_dict

log = logging.getLogger(__name__)

_INDEX_HTML = Path(__file__).parent / "index.html"
_CLIENT_QUEUE_MAX = 256
_HEARTBEAT_SECONDS = 1.5


class _Broadcast:
    """Thread-safe fan-out hub. Each SSE client gets a bounded queue; publish
    drops the oldest frame rather than block when a client falls behind."""

    def __init__(self) -> None:
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=_CLIENT_QUEUE_MAX)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, frame: str) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(frame)
            except queue.Full:
                try:
                    q.get_nowait()        # drop oldest, make room
                    q.put_nowait(frame)
                except queue.Empty:
                    pass

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


class WebUIServer:
    """Serve the monitor at ``http://<host>:<port>``.

    Read-only by default (SSE stream + snapshots). When ``enable_input`` is set
    and a ``companion`` is supplied, it also accepts a small POST channel so the
    browser can ask the read-only companion, steer the research agent, and
    answer review gates — the same actions the terminal dashboard offers. That
    channel is gated by a per-run random token (carried in the URL the user
    opens) so a stray local page can't drive the run via CSRF; the socket itself
    only ever binds ``127.0.0.1``.
    """

    def __init__(self, run_state: Any, bus: Any, *, port: int,
                 host: str = "127.0.0.1", companion: Any | None = None,
                 enable_input: bool = True,
                 snapshot_fn: "Callable[[], dict[str, Any]] | None" = None) -> None:
        self.run_state = run_state
        self.bus = bus
        self.port = port
        self.host = host
        self.companion = companion
        # ``snapshot_fn`` supports the keyless, file-backed mode: when set, the
        # server polls it for state instead of subscribing to a live EventBus +
        # RunState. ``bus`` may then be None. Always read-only in this mode.
        self.snapshot_fn = snapshot_fn
        # Interactive iff the caller wants input AND we have the wiring for it
        # (never in file-backed mode — there is no run to steer).
        self.interactive = bool(enable_input) and snapshot_fn is None
        self.token = secrets.token_urlsafe(16)
        self.broadcast = _Broadcast()
        self._httpd: ThreadingHTTPServer | None = None
        self._stop = threading.Event()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def browser_url(self) -> str:
        """The URL to actually open: carries the interactive token when input is
        enabled (read-only runs get the plain URL)."""
        if self.interactive:
            return f"{self.url}/?t={self.token}"
        return self.url

    def start(self) -> bool:
        """Bind, subscribe to the bus, and spawn the HTTP + heartbeat threads.
        Returns False (and logs) if the port is unavailable — never raises."""
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError as exc:
            log.warning("WebUI could not bind %s: %s", self.url, exc)
            return False
        self._httpd.daemon_threads = True
        self._httpd.webui = self  # type: ignore[attr-defined]
        # File-backed mode has no bus to subscribe to; the heartbeat loop polls
        # ``snapshot_fn`` instead.
        if self.bus is not None:
            self.bus.on_all(self._on_event)
        threading.Thread(target=self._httpd.serve_forever,
                         name="webui-http", daemon=True).start()
        threading.Thread(target=self._heartbeat_loop,
                         name="webui-heartbeat", daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._httpd is not None:
            try:
                if self.bus is not None:
                    self.bus.off("*", self._on_event)   # stop receiving events
            except Exception:  # pragma: no cover - best effort
                pass
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:  # pragma: no cover - best effort
                pass

    # ── frame builders ──

    def snapshot_frame(self) -> str:
        # File-backed mode: poll the session directory; live mode: flatten the
        # in-memory RunState.
        if self.snapshot_fn is not None:
            state = self.snapshot_fn()
        else:
            state = state_to_dict(self.run_state)
        state["interactive"] = self.interactive
        return json.dumps({"kind": "snapshot", "state": state})

    # ── input channel (browser → run), token-gated ──

    def handle_input(self, msg: dict[str, Any]) -> tuple[bool, str]:
        """Route a browser input message into the run. Returns (ok, error).

        Mirrors the terminal dashboard: ``ask`` → read-only companion,
        ``steer`` → inject into the research agent, ``gate`` → answer the open
        review gate. Safe to call from the HTTP handler thread."""
        if not self.interactive:
            return False, "read-only"
        kind = str(msg.get("type") or "").lower()
        payload = str(msg.get("payload") or "").strip()
        if kind == "ask":
            if self.companion is None:
                return False, "companion unavailable"
            if not payload:
                return False, "empty question"
            self.companion.ask(payload)
            return True, ""
        if kind == "steer":
            if not payload:
                return False, "empty message"
            self.run_state.push_user_message(payload)
            return True, ""
        if kind == "gate":
            value = str(msg.get("value") or "").strip()
            if not value:
                return False, "empty value"
            self.bus.emit("user.input_received",
                          {"node_id": str(msg.get("node_id") or ""), "value": value})
            return True, ""
        return False, "unknown input type"

    def _on_event(self, event: Any) -> None:
        # Runs in the orchestrator's emit thread — keep it cheap: one dump, then
        # a non-blocking publish. Drop the payload (not the frame) if unserializable.
        data = getattr(event, "data", {}) or {}
        try:
            frame = json.dumps({"kind": "event", "type": event.type, "data": data})
        except (TypeError, ValueError):
            frame = json.dumps({"kind": "event", "type": event.type,
                                "data": {"_unserializable": True}})
        self.broadcast.publish(frame)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.broadcast.publish(self.snapshot_frame())
            except Exception:  # pragma: no cover - never kill the heartbeat
                log.debug("webui heartbeat snapshot failed", exc_info=True)
            self._stop.wait(_HEARTBEAT_SECONDS)


class _Handler(BaseHTTPRequestHandler):
    server_version = "arbor-webui/1.0"

    def log_message(self, *_a: Any) -> None:  # silence default stderr logging
        return

    @property
    def _webui(self) -> WebUIServer:
        return self.server.webui  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._serve_index()
        elif path == "/events":
            self._serve_events()
        elif path == "/healthz":
            self._serve_text(200, "ok")
        else:
            self._serve_text(404, "not found")

    def do_POST(self) -> None:  # noqa: N802 (stdlib name)
        webui = self._webui
        path = self.path.split("?", 1)[0]
        if path != "/input":
            self._serve_text(404, "not found")
            return
        if not webui.interactive:
            self._serve_json(403, {"ok": False, "error": "read-only"})
            return
        # Token gate (CSRF defence): the token rides the URL the user opened, so
        # a random local page driving the browser can't know it.
        supplied = self.headers.get("X-Arbor-Token") or _query_param(self.path, "t")
        if not supplied or not secrets.compare_digest(supplied, webui.token):
            self._serve_json(403, {"ok": False, "error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        # Cap the body: a localhost client should never need more than a small
        # JSON control message, and an unbounded Content-Length could OOM us.
        length = min(length, 64 * 1024)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            msg = json.loads(raw or b"{}")
            if not isinstance(msg, dict):
                raise ValueError("not an object")
        except (ValueError, json.JSONDecodeError):
            self._serve_json(400, {"ok": False, "error": "bad json"})
            return
        try:
            ok, err = webui.handle_input(msg)
        except Exception as exc:  # never let a bad input kill the handler thread
            log.debug("webui input failed", exc_info=True)
            ok, err = False, f"{type(exc).__name__}"
        self._serve_json(200 if ok else 400, {"ok": ok, "error": err})

    def _serve_index(self) -> None:
        try:
            body = _INDEX_HTML.read_bytes()
        except OSError:
            self._serve_text(500, "index.html missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Harden the one HTML response: no MIME sniffing, no framing (clickjacking).
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _serve_text(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self) -> None:
        webui = self._webui
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # No CORS header: the browser page is served from this same origin, so its
        # EventSource doesn't need one — and dropping the previous wildcard stops
        # any other local page from reading the session stream cross-origin.
        self.end_headers()
        q = webui.broadcast.register()
        try:
            try:
                self._sse_send(webui.snapshot_frame())   # immediate state on connect
            except Exception:
                self._sse_send(json.dumps({"kind": "snapshot", "state": empty_state_dict()}))
            while not webui._stop.is_set():
                try:
                    frame = q.get(timeout=1.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keep-alive / detect drop
                    self.wfile.flush()
                    continue
                self._sse_send(frame)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            webui.broadcast.unregister(q)

    def _sse_send(self, frame: str) -> None:
        self.wfile.write(b"data: " + frame.encode("utf-8") + b"\n\n")
        self.wfile.flush()


def _query_param(path: str, key: str) -> str | None:
    """Pull a single query-string value out of a request path."""
    try:
        return parse_qs(urlparse(path).query).get(key, [None])[0]
    except Exception:
        return None
