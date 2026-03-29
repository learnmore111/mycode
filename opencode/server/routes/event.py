"""Event SSE route — global event subscription endpoint."""
from __future__ import annotations

import json

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from opencode.bus.bus import Bus

router = APIRouter(prefix="/event", tags=["event"])

# Shared bus — set by app startup
_bus: Bus | None = None


def set_bus(bus: Bus) -> None:
    global _bus
    _bus = bus


@router.get("")
async def event_stream(event_type: str = Query(default="*")):
    """Subscribe to server events via SSE.

    Query params:
        event_type: specific event type or "*" for all events (default: "*")
    """
    bus = _bus if _bus else Bus()

    async def generator():
        if event_type == "*":
            async for event in bus.subscribe_all():
                yield {
                    "event": event.type,
                    "data": json.dumps(event.properties),
                }
        else:
            from opencode.bus.events import EventDef
            event_def = EventDef(type=event_type)
            async for event in bus.subscribe(event_def):
                yield {
                    "event": event.type,
                    "data": json.dumps(event.properties),
                }

    return EventSourceResponse(generator())
