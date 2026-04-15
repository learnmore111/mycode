"""Event bus — asyncio-based pub/sub system.

Provides typed event publishing and subscription.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from opencode.bus.events import Event, EventDef
from opencode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

logger = logmod.create(service="bus")


class Bus:
    """Per-instance event bus with typed pub/sub."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}
        self._wildcard: list[asyncio.Queue[Event]] = []
        self._callbacks: dict[str, list[Callable[[Event], Any]]] = {}
        self._wildcard_callbacks: list[Callable[[Event], Any]] = []
        self._closed = False

    async def publish(self, event_def: EventDef, properties: dict[str, Any] | None = None) -> None:
        """Publish an event to all subscribers."""
        if self._closed:
            return
        event = Event(type=event_def.type, properties=properties or {})
        logger.debug("publishing", type=event_def.type)

        # Typed subscribers — copy list to avoid mutation during iteration
        for q in list(self._subscribers.get(event_def.type, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warn("subscriber queue full, dropping event", type=event_def.type)

        # Wildcard subscribers — copy list, log drops
        for q in list(self._wildcard):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warn("wildcard subscriber queue full, dropping event", type=event_def.type)

        # Callbacks — copy list to avoid mutation during iteration
        for cb in list(self._callbacks.get(event_def.type, [])):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("subscriber callback failed", type=event_def.type, error=str(e))

        for cb in list(self._wildcard_callbacks):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error("wildcard callback failed", type=event_def.type, error=str(e))

    async def subscribe(self, event_def: EventDef) -> AsyncGenerator[Event, None]:
        """Subscribe to a specific event type. Yields events as they arrive."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        subs = self._subscribers.setdefault(event_def.type, [])
        subs.append(q)
        logger.debug("subscribing", type=event_def.type)
        try:
            while not self._closed:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield event
                except TimeoutError:
                    continue
        finally:
            subs.remove(q)
            logger.debug("unsubscribing", type=event_def.type)

    async def subscribe_all(self) -> AsyncGenerator[Event, None]:
        """Subscribe to all events."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._wildcard.append(q)
        logger.debug("subscribing", type="*")
        try:
            while not self._closed:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield event
                except TimeoutError:
                    continue
        finally:
            self._wildcard.remove(q)
            logger.debug("unsubscribing", type="*")

    def on(self, event_def: EventDef, callback: Callable[[Event], Any]) -> Callable[[], None]:
        """Register a callback for an event type. Returns an unsubscribe function."""
        cbs = self._callbacks.setdefault(event_def.type, [])
        cbs.append(callback)

        def unsub() -> None:
            cbs.remove(callback)

        return unsub

    def on_all(self, callback: Callable[[Event], Any]) -> Callable[[], None]:
        """Register a callback for all events."""
        self._wildcard_callbacks.append(callback)

        def unsub() -> None:
            self._wildcard_callbacks.remove(callback)

        return unsub

    async def close(self) -> None:
        """Shut down the bus, unblocking all subscribers."""
        self._closed = True
        # Send a sentinel to unblock waiting subscribers
        sentinel = Event(type=INSTANCE_DISPOSED.type, properties={})
        for q in self._wildcard:
            q.put_nowait(sentinel)
        for qs in self._subscribers.values():
            for q in qs:
                q.put_nowait(sentinel)


from opencode.bus.events import INSTANCE_DISPOSED  # noqa: E402

# --- Global bus (cross-instance, for server-level events) ---

_global_callbacks: list[Callable[[Event], Any]] = []


def global_emit(event: Event) -> None:
    """Emit an event on the global bus (sync, non-blocking)."""
    for cb in _global_callbacks:
        with contextlib.suppress(Exception):
            cb(event)


def global_on(callback: Callable[[Event], Any]) -> Callable[[], None]:
    """Register on the global bus."""
    _global_callbacks.append(callback)

    def unsub() -> None:
        _global_callbacks.remove(callback)

    return unsub
