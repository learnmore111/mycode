"""Tests for ``/orchestration/*`` HTTP + SSE routes (M7).

We exercise:

- ``GET /orchestration/flow``           lists shipped flows.
- ``GET /orchestration/flow/{name}``    returns a parsed spec.
- ``GET /orchestration/agent``          lists agents.
- ``POST /orchestration/run`` (swarm)   starts a background run backed
                                        by a stubbed ``run_swarm`` so
                                        we stay LLM-free.
- ``GET /orchestration/events?run_id``  streams SSE for a specific run.

The stubbed runner publishes a few orchestration events through the
server-wide :class:`Bus`, which the client observes via the SSE endpoint.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", ":memory:")
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    import mycode.storage.database as dbmod
    dbmod.reset()

    from mycode.server.app import create_app
    app = create_app()
    return TestClient(app)


def test_list_flows_returns_shipped_flows(client):
    resp = client.get("/orchestration/flow")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    names = {f["name"] for f in data}
    # ``research`` and ``pair-review`` are the two flows shipped with the
    # package since M1; both must surface here.
    assert "research" in names
    assert "pair-review" in names


def test_get_flow_returns_parsed_spec(client):
    resp = client.get("/orchestration/flow/pair-review")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "pair-review"
    assert data["mode"] == "swarm"
    assert data["lead"]  # non-empty
    agents = {a["name"] for a in data["agents"]}
    assert len(agents) >= 2


def test_get_flow_unknown_returns_404(client):
    resp = client.get("/orchestration/flow/does-not-exist")
    assert resp.status_code == 404


def test_list_agents(client):
    resp = client.get("/orchestration/agent")
    assert resp.status_code == 200
    agents = resp.json()
    assert isinstance(agents, list)
    # Every entry should carry 'name' + 'source'.
    for a in agents:
        assert "name" in a
        assert "source" in a


def test_post_run_rejects_swarm_without_task(client):
    resp = client.post("/orchestration/run", json={"flow": "pair-review"})
    assert resp.status_code == 400
    assert "task" in resp.json()["detail"].lower()


def test_post_run_rejects_unknown_flow(client):
    resp = client.post("/orchestration/run", json={"flow": "nope"})
    assert resp.status_code == 404


def test_post_run_returns_run_id_for_swarm(client, monkeypatch):
    """Stub ``run_swarm`` so the route returns immediately without an LLM."""
    from mycode.server.routes import orchestration as orch_route

    async def _fake_run_swarm(spec, agents, *, user_task, events=None, **kw):
        if events is not None:
            await events.swarm_started(
                lead=spec.lead or "", peers=list(agents), user_task=user_task,
            )
            await events.swarm_finished(
                lead=spec.lead or "",
                terminated_reason="lead-quiet",
                duration_seconds=0.01,
                peer_count=len(agents),
            )
        # Minimal SwarmResult shape isn't needed — the HTTP path ignores it.
        return None

    monkeypatch.setattr(orch_route, "run_swarm", _fake_run_swarm)

    resp = client.post("/orchestration/run", json={
        "flow": "pair-review",
        "task": "hi team",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "run_id" in data
    assert data["mode"] == "swarm"
    assert data["flow"] == "pair-review"


def test_post_run_coordinator_flow_no_task_required(client, monkeypatch):
    """Coordinator-mode runs should not require a ``task`` body field."""
    from mycode.server.routes import orchestration as orch_route

    seen: dict[str, object] = {}

    async def _fake_run_coordinator(spec, agents, *, events=None, **kw):
        seen["mode"] = spec.mode
        if events is not None:
            await events.flow_started(mode=spec.mode, agents=list(agents))
            await events.flow_finished(ok=True, duration_seconds=0.001)

    monkeypatch.setattr(orch_route, "run_coordinator", _fake_run_coordinator)
    resp = client.post("/orchestration/run", json={"flow": "research"})
    assert resp.status_code == 200, resp.text
    # Give the background task a chance to finish.
    for _ in range(20):
        if "mode" in seen:
            break
    assert resp.json()["mode"] == "coordinator"


def test_sse_stream_filters_by_run_id(client, monkeypatch):
    """Publish events for two different run_ids and confirm the SSE
    stream with a specific ``run_id`` only surfaces matching events."""
    from mycode.bus import events as bus_events
    from mycode.server.routes import orchestration as orch_route

    bus = orch_route._bus  # noqa: SLF001 — shared bus
    assert bus is not None

    # Fire-and-forget a few events on the bus in the background so that
    # when we open the SSE stream they arrive promptly.
    async def _pump():
        await asyncio.sleep(0.05)
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_STARTED,
            {"run_id": "A", "flow": "f", "mode": "coordinator", "agents": []},
        )
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_STARTED,
            {"run_id": "B", "flow": "f", "mode": "coordinator", "agents": []},
        )
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_FINISHED,
            {"run_id": "A", "flow": "f", "ok": True, "duration_seconds": 0.01},
        )

    # Running the SSE consumer in the TestClient is synchronous — so
    # we kick the publisher off on the event loop the TestClient uses
    # by scheduling it from within the streaming context.  Instead,
    # we use httpx's stream directly via the low-level TestClient.
    import threading

    pump_thread_done = threading.Event()

    def _run_pump_in_thread():
        asyncio.run(_pump())
        pump_thread_done.set()

    thread = threading.Thread(target=_run_pump_in_thread)
    thread.start()

    with client.stream("GET", "/orchestration/events?run_id=A", timeout=3.0) as resp:
        assert resp.status_code == 200
        collected: list[dict] = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            if raw.startswith("event: "):
                event_type = raw[len("event: "):].strip()
            elif raw.startswith("data: "):
                payload = json.loads(raw[len("data: "):])
                collected.append({"type": event_type, "payload": payload})
                # Stop after we see both run_id=A events.
                types_seen = [e["type"] for e in collected]
                if (
                    "orchestration.flow.started" in types_seen
                    and "orchestration.flow.finished" in types_seen
                ):
                    break

    thread.join(timeout=2.0)

    # Must only have A's events — B was filtered out.
    assert all(e["payload"]["run_id"] == "A" for e in collected)
    types = {e["type"] for e in collected}
    assert "orchestration.flow.started" in types
    assert "orchestration.flow.finished" in types
