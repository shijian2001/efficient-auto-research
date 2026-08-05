"""Demo: drive the live dashboard offline with mock events.

Usage:
    python examples/dashboard_demo.py

Renders the real terminal RunDashboard (header, ideas tree, reasoning panel)
and replays the canned MOCK_SCRIPT from arbor.events.mock — no engine,
no API key. Handy for working on the observability surfaces (#6 reasoning panel,
#7 WebUI) without launching a full research run.

Watch the reasoning panel fill with the two mock thinking deltas and the
``sub:n1  Bash`` tool line, then settle once the run "completes".
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


def main() -> None:
    bus = EventBus()
    state = RunState(
        run_name="mock_run",
        task="Improve validation accuracy",
        cwd=".",
        model="claude-sonnet-4-6",
        total_cycles=3,
    )
    # enable_input=False: this is a passive demo, so we skip the raw-stdin
    # reader and just paint frames as the mock events land.
    with RunDashboard(state, bus, enable_input=False):
        emit_mock_run(bus, delay=0.25)
        # Hold the final frame briefly so the completed panel is visible.
        time.sleep(2.5)


if __name__ == "__main__":
    main()
