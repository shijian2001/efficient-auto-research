"""Launch helper for the read-only WebUI.

Centralizes the "pick a port and bind" logic so the CLI can offer a zero-config
default (try 8765, roll forward if it's taken) while staying easy to unit-test
without standing up a real run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .server import WebUIServer
from .session_source import build_session_snapshot

log = logging.getLogger(__name__)


def start_webui(
    run_state: Any,
    bus: Any,
    *,
    preferred: int,
    enabled: bool = True,
    auto: bool = False,
    scan: int = 1,
    companion: Any | None = None,
    enable_input: bool = False,
) -> WebUIServer | None:
    """Start a ``WebUIServer`` and return it, or ``None`` if disabled/no port.

    - ``enabled=False`` → returns ``None`` immediately (opt-out).
    - tries ``preferred`` first; when ``auto`` is set, walks up to ``scan`` ports
      (``preferred`` … ``preferred+scan-1``) until one binds, so a busy 8765
      silently rolls to 8766. ``WebUIServer.start()`` returns False on bind
      failure rather than raising, which makes the scan a simple loop.
    - explicit (non-auto) ports try exactly once: a taken port is surfaced as
      ``None`` rather than silently moved.
    - ``enable_input`` + ``companion`` make the browser interactive (ask / steer
      / answer gates), behind a per-run token. Default is read-only.
    """
    if not enabled or preferred is None:
        return None
    span = max(1, scan) if auto else 1
    for port in range(preferred, preferred + span):
        server = WebUIServer(run_state, bus, port=port,
                             companion=companion, enable_input=enable_input)
        if server.start():
            return server
    log.warning("WebUI could not bind any port in %d..%d", preferred, preferred + span - 1)
    return None


def start_session_webui(
    session_dir: Path,
    *,
    run_name: str | None = None,
    preferred: int = 8765,
    scan: int = 16,
) -> WebUIServer | None:
    """Start a read-only WebUI backed by an on-disk session directory.

    This is the keyless monitor: it has no live ``RunState`` or ``EventBus`` —
    the server polls :func:`build_session_snapshot` on its heartbeat, so the
    browser tracks whatever the host agent writes to *session_dir* via the
    ``arbor mcp`` tools. Walks up to ``scan`` ports from ``preferred`` to find a
    free one. Returns the running server (inspect ``.url``) or ``None`` if no
    port is available.
    """
    session_dir = Path(session_dir)
    label = run_name or session_dir.name

    def _snapshot() -> dict[str, Any]:
        return build_session_snapshot(session_dir, label)

    for port in range(preferred, preferred + max(1, scan)):
        # No run_state, no bus: snapshot_fn drives everything; read-only.
        server = WebUIServer(None, None, port=port, enable_input=False, snapshot_fn=_snapshot)
        if server.start():
            return server
    log.warning("session WebUI could not bind any port in %d..%d", preferred, preferred + scan - 1)
    return None
