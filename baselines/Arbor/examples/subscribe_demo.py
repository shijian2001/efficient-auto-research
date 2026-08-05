"""Demo: subscribe to events emitted by the orchestrator.

Usage:
    python examples/subscribe_demo.py

This script demonstrates the EventBus decoupling layer. It does not run a
real research session — it just builds a minimal IdeaTree and mutates it
to show events firing. For a real run, pass an EventBus into
CoordinatorOrchestrator(config, provider, bus=bus).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Make the package importable when running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from arbor.events import EventBus
from arbor.events import types as ev
from arbor.coordinator.idea_tree import IdeaTree, Node


def make_event_printer(bus: EventBus) -> None:
    """Print every event in a one-line, scannable format."""

    def on_any(event):
        payload = json.dumps(event.data, ensure_ascii=False, default=str)
        if len(payload) > 80:
            payload = payload[:77] + "..."
        print(f"[{event.timestamp:.3f}] {event.type:<22} {payload}")

    bus.on_all(on_any)


def main() -> None:
    bus = EventBus()
    make_event_printer(bus)

    print("── EventBus demo: simulating idea tree mutations ──\n")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        root = Node(id="root", parent_id=None, depth=0,
                    hypothesis="baseline", status="done")
        tree = IdeaTree(
            root=root,
            json_path=tmp_path / "tree.json",
            md_path=tmp_path / "tree.md",
            bus=bus,
        )

        # Should fire: idea.proposed
        child1 = Node(id="1.1", parent_id="root", depth=1,
                      hypothesis="add reranker", status="pending")
        tree.add_node(child1)

        # Should fire: idea.proposed
        child2 = Node(id="1.2", parent_id="root", depth=1,
                      hypothesis="bigger model", status="pending")
        tree.add_node(child2)

        # Should fire: idea.completed
        tree.update_node("1.1", status="done", score=0.85)

        # Should fire: idea.pruned
        tree.prune_node("1.2", reason="score too low")

    print("\n── Demo complete. ──")
    print("If you saw 4 events above, EventBus decoupling works.")


if __name__ == "__main__":
    main()
