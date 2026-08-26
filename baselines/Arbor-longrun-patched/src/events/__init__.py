"""Event-driven decoupling layer.

Core code (orchestrator, idea_tree, agent) emits events on an EventBus.
Consumers (CLI display, file logger, dashboard, report) subscribe.

When core runs without an EventBus injected, NullBus makes every emit a no-op,
so importing/running core in isolation has zero overhead.
"""

from .bus import Event, EventBus, NullBus
from . import types
from . import payloads

__all__ = ["Event", "EventBus", "NullBus", "types", "payloads"]
