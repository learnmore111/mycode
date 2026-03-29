"""Tests for the event bus."""
import asyncio
import pytest
from opencode.bus.bus import Bus, global_emit, global_on
from opencode.bus.events import EventDef, Event, SESSION_CREATED


@pytest.mark.asyncio
async def test_publish_and_callback():
    bus = Bus()
    received = []
    bus.on(SESSION_CREATED, lambda e: received.append(e))
    await bus.publish(SESSION_CREATED, {"id": "s1"})
    assert len(received) == 1
    assert received[0].type == "session.created"
    assert received[0].properties["id"] == "s1"
    await bus.close()


@pytest.mark.asyncio
async def test_wildcard_callback():
    bus = Bus()
    received = []
    bus.on_all(lambda e: received.append(e))
    await bus.publish(SESSION_CREATED, {"id": "s1"})
    await bus.publish(EventDef(type="custom.event"), {"data": 42})
    assert len(received) == 2
    await bus.close()


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = Bus()
    received = []
    unsub = bus.on(SESSION_CREATED, lambda e: received.append(e))
    await bus.publish(SESSION_CREATED, {"id": "1"})
    unsub()
    await bus.publish(SESSION_CREATED, {"id": "2"})
    assert len(received) == 1
    await bus.close()


@pytest.mark.asyncio
async def test_subscribe_generator():
    bus = Bus()
    events = []

    async def collect():
        async for event in bus.subscribe(SESSION_CREATED):
            events.append(event)
            if len(events) >= 2:
                break

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    await bus.publish(SESSION_CREATED, {"id": "a"})
    await bus.publish(SESSION_CREATED, {"id": "b"})
    await asyncio.wait_for(task, timeout=3)
    assert len(events) == 2
    await bus.close()


@pytest.mark.asyncio
async def test_close_stops_bus():
    bus = Bus()
    await bus.publish(SESSION_CREATED, {"id": "1"})
    await bus.close()
    # Publishing after close should be a no-op
    await bus.publish(SESSION_CREATED, {"id": "2"})


def test_global_emit():
    received = []
    unsub = global_on(lambda e: received.append(e))
    global_emit(Event(type="test", properties={"key": "val"}))
    assert len(received) == 1
    assert received[0].properties["key"] == "val"
    unsub()
    global_emit(Event(type="test", properties={}))
    assert len(received) == 1  # unsubscribed


def test_event_def():
    ed = EventDef(type="my.event")
    assert ed.type == "my.event"
    e = Event(type="my.event", properties={"a": 1})
    assert e.type == "my.event"
    assert e.properties["a"] == 1
