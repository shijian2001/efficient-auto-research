"""Demo: drive BOTH surfaces offline from one mock event stream.

Usage:
    python examples/full_demo.py                 # CLI + WebUI on :8765
    python examples/full_demo.py --port 9000     # pick the WebUI port
    python examples/full_demo.py --rounds 5       # replay the script N times
    python examples/full_demo.py --no-webui       # terminal dashboard only

Starts the read-only WebUI server, prints its URL, gives you a few seconds to
open it in a browser, then mounts the real terminal RunDashboard and replays the
canned MOCK_SCRIPT (arbor.events.mock) onto a single EventBus. Because
the dashboard updates RunState and the WebUI mirrors the same bus, the terminal
tree/thinking panels and the browser monitor light up from the *same* events —
no engine, no API key. Looping a few rounds gives you time to watch both.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Make the package importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from arbor.cli.run_dashboard import RunDashboard
from arbor.cli.run_state import RunState
from arbor.events import EventBus
from arbor.events.mock import emit_mock_run


def _parse_args(argv: list[str]) -> tuple[int | None, int]:
    """Tiny arg parse — (webui_port | None, rounds). Avoids argparse noise."""
    port: int | None = 8765
    rounds = 3
    it = iter(argv)
    for a in it:
        if a == "--no-webui":
            port = None
        elif a == "--port":
            port = int(next(it))
        elif a == "--rounds":
            rounds = max(1, int(next(it)))
    return port, rounds


def main() -> None:
    port, rounds = _parse_args(sys.argv[1:])

    bus = EventBus()
    state = RunState(
        run_name="mock_run",
        task="Improve validation accuracy",
        cwd=".",
        model="claude-sonnet-4-6",
        total_cycles=3,
    )

    webui = None
    if port is not None:
        from arbor.webui import WebUIServer

        webui = WebUIServer(state, bus, port=port)
        if webui.start():
            print(f"\n  WebUI:  {webui.url}")
            print("  Open it in a browser now — the terminal dashboard starts in 5s.\n")
            time.sleep(5)
        else:
            print(f"\n  WebUI could not bind port {port} — running terminal-only.\n")
            webui = None

    try:
        # enable_input=False: passive demo, no raw-stdin reader.
        with RunDashboard(state, bus, enable_input=False):
            for _ in range(rounds):
                emit_mock_run(bus, delay=0.25)
            # Hold the final frame so the completed panels stay visible.
            time.sleep(3.0)
    finally:
        if webui is not None:
            webui.stop()


if __name__ == "__main__":
    main()
