"""EventBus — decoupling layer between core logic and consumers (CLI, dashboard, report).

Design contract:
- emit() is fire-and-forget: callers never block on subscribers
- Subscriber exceptions never propagate to callers
- NullBus is a no-op drop-in for code paths that have no bus
- Both sync and async subscribers are supported
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

WILDCARD = "*"


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = {}

    def on(self, event_type: str, callback: Callable) -> None:
        """Register a subscriber. callback may be sync or async."""
        self._subscribers.setdefault(event_type, []).append(callback)

    def on_all(self, callback: Callable) -> None:
        """Subscribe to every event."""
        self.on(WILDCARD, callback)

    def off(self, event_type: str, callback: Callable) -> None:
        """Remove one registration of ``callback`` from ``event_type``.

        Removes the first matching registration (``list.remove`` semantics) and
        is a no-op if absent. Enables one-shot request/response handshakes (e.g.
        AWAIT_USER → USER_INPUT_RECEIVED) without leaking subscribers.
        """
        subs = self._subscribers.get(event_type)
        if subs:
            try:
                subs.remove(callback)
            except ValueError:
                pass

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire-and-forget. Safe to call from sync or async code.

        Sync subscribers run inline. Async subscribers are scheduled on the
        running loop if one exists; otherwise they are skipped (no event loop
        to host them). Subscriber exceptions are swallowed — core must never
        crash because a logger blew up.
        """
        event = Event(type=event_type, data=data or {})
        for cb in self._collect(event_type):
            try:
                if asyncio.iscoroutinefunction(cb):
                    self._schedule_async(cb, event)
                else:
                    cb(event)
            except Exception:
                log.debug("event subscriber failed for %s", event_type, exc_info=True)

    async def aemit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Awaitable variant. Awaits async subscribers in order before returning.

        Use only when callers truly need the back-pressure (e.g. writing to a
        sink that must flush before the next emit). Default to emit().
        """
        event = Event(type=event_type, data=data or {})
        for cb in self._collect(event_type):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception:
                log.debug("async event subscriber failed for %s", event_type, exc_info=True)

    def _collect(self, event_type: str) -> list[Callable]:
        return (
            self._subscribers.get(event_type, [])
            + self._subscribers.get(WILDCARD, [])
        )

    @staticmethod
    def _schedule_async(cb: Callable, event: Event) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No loop; async subscribers cannot run from this context
        loop.create_task(_safe_run(cb, event))


async def _safe_run(cb: Callable, event: Event) -> None:
    try:
        await cb(event)
    except Exception:
        log.debug("async event subscriber failed for %s", event.type, exc_info=True)


class NullBus:
    """No-op bus. Drop-in default for orchestrators with no observers."""

    def on(self, *_a, **_kw) -> None:
        pass

    def on_all(self, *_a, **_kw) -> None:
        pass

    def off(self, *_a, **_kw) -> None:
        pass

    def emit(self, *_a, **_kw) -> None:
        pass

    async def aemit(self, *_a, **_kw) -> None:
        pass
