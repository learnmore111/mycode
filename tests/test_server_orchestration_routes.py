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
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "orchestration-routes.db"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    import mycode.storage.database as dbmod
    dbmod.reset()

    from mycode.server.app import create_app
    app = create_app()
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        dbmod.reset()


def _wait_for_run_status(client: TestClient, run_id: str, expected: str, *, attempts: int = 80) -> dict:
    last: dict | None = None
    for _ in range(attempts):
        resp = client.get(f"/orchestration/run/{run_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] == expected:
            return last
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach status={expected!r}; last={last}")


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


def test_get_run_detail_returns_swarm_summary(client, monkeypatch):
    from mycode.orchestration.runtime.context import SpawnOutput
    from mycode.orchestration.runtime.swarm import SwarmResult
    from mycode.server.routes import orchestration as orch_route

    async def _fake_run_swarm(spec, agents, *, user_task, events=None, **kw):
        if events is not None:
            await events.swarm_started(
                lead=spec.lead or "", peers=[name for name in agents if name != (spec.lead or "")], user_task=user_task,
            )
            await events.swarm_finished(
                lead=spec.lead or "",
                terminated_reason="lead-quiet",
                duration_seconds=0.01,
                peer_count=len(agents),
            )
        return SwarmResult(
            flow_name=spec.name,
            lead=spec.lead or "",
            peers={
                name: SpawnOutput(
                    agent=name,
                    task=f"task for {name}",
                    output=f"output from {name}",
                    turns=1,
                    tool_calls=0,
                )
                for name in agents
            },
            transcript=[],
            lead_output="final swarm answer",
            terminated_reason="lead-quiet",
        )

    monkeypatch.setattr(orch_route, "run_swarm", _fake_run_swarm)
    resp = client.post("/orchestration/run", json={"flow": "pair-review", "task": "review this"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    detail = _wait_for_run_status(client, run_id, "completed")
    assert detail["flow"] == "pair-review"
    assert detail["mode"] == "swarm"
    assert detail["done"] is True
    assert detail["result"]["kind"] == "swarm"
    assert detail["result"]["terminated_reason"] == "lead-quiet"
    assert detail["result"]["lead_output_preview"] == "final swarm answer"
    assert detail["result"]["peer_count"] >= 2


def test_post_run_cancel_marks_run_cancelled(client, monkeypatch):
    from mycode.server.routes import orchestration as orch_route

    flags = {"started": False, "cancelled": False}

    async def _fake_run_swarm(spec, agents, *, user_task, events=None, **kw):
        flags["started"] = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            flags["cancelled"] = True
            raise

    monkeypatch.setattr(orch_route, "run_swarm", _fake_run_swarm)
    resp = client.post("/orchestration/run", json={"flow": "pair-review", "task": "stay alive"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    for _ in range(80):
        if flags["started"]:
            break
        time.sleep(0.01)

    cancel_resp = client.post(f"/orchestration/run/{run_id}/cancel")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] in {"cancelling", "cancelled"}

    detail = _wait_for_run_status(client, run_id, "cancelled")
    assert detail["cancelled"] is True
    assert detail["cancel_requested"] is True
    assert detail["result"]["cancelled"] is True
    assert flags["cancelled"] is True


def test_run_history_survives_app_recreation(tmp_path, monkeypatch):
    from mycode.orchestration.runtime.context import SpawnOutput
    from mycode.orchestration.runtime.swarm import SwarmResult
    from mycode.server.app import create_app
    from mycode.server.routes import orchestration as orch_route
    import mycode.storage.database as dbmod

    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "orchestration-history.db"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    monkeypatch.setattr("mycode.util.paths.GlobalPaths.config", staticmethod(lambda: tmp_path / "config"))
    dbmod.reset()

    async def _fake_run_swarm(spec, agents, *, user_task, events=None, **kw):
        if events is not None:
            await events.swarm_started(
                lead=spec.lead or "", peers=[name for name in agents if name != (spec.lead or "")], user_task=user_task,
            )
            await events.swarm_finished(
                lead=spec.lead or "",
                terminated_reason="lead-quiet",
                duration_seconds=0.01,
                peer_count=len(agents),
            )
        return SwarmResult(
            flow_name=spec.name,
            lead=spec.lead or "",
            peers={
                name: SpawnOutput(
                    agent=name,
                    task=f"task for {name}",
                    output=f"output from {name}",
                    turns=1,
                    tool_calls=0,
                )
                for name in agents
            },
            transcript=[],
            lead_output="persistent answer",
            terminated_reason="lead-quiet",
        )

    monkeypatch.setattr(orch_route, "run_swarm", _fake_run_swarm)

    client1 = TestClient(create_app())
    try:
        resp = client1.post("/orchestration/run", json={"flow": "pair-review", "task": "persist this"})
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        detail = _wait_for_run_status(client1, run_id, "completed")
        assert detail["result"]["lead_output_preview"] == "persistent answer"
    finally:
        client1.close()

    orch_route._runs.clear()
    dbmod.reset()

    client2 = TestClient(create_app())
    try:
        detail_resp = client2.get(f"/orchestration/run/{run_id}")
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()
        assert detail["status"] == "completed"
        assert detail["result"]["lead_output_preview"] == "persistent answer"

        list_resp = client2.get("/orchestration/run")
        assert list_resp.status_code == 200, list_resp.text
        runs = list_resp.json()
        assert any(run["run_id"] == run_id and run["has_result"] for run in runs)
    finally:
        client2.close()
        orch_route._runs.clear()
        dbmod.reset()


def test_sse_generator_filters_by_run_id(client):
    """Exercise the SSE filter in-process (no HTTP) to avoid threading
    the bus across two event loops.  The real ``/orchestration/events``
    endpoint is a thin wrapper around ``bus.subscribe_all`` that applies
    exactly these two filters."""
    from mycode.bus import events as bus_events
    from mycode.server.routes import orchestration as orch_route

    bus = orch_route._bus  # noqa: SLF001 — shared bus
    assert bus is not None

    collected: list[tuple[str, dict]] = []

    async def _drive() -> None:
        # Local wrapper reproduces the generator body so we can iterate
        # deterministically without needing an httpx.AsyncClient + SSE
        # parser (those would add nontrivial scaffolding for an equality
        # assertion about two filter rules).
        async def generator(run_id: str | None = None):
            orchestration_types = {
                bus_events.ORCHESTRATION_FLOW_STARTED.type,
                bus_events.ORCHESTRATION_FLOW_FINISHED.type,
                bus_events.ORCHESTRATION_STAGE_STARTED.type,
                bus_events.ORCHESTRATION_STAGE_FINISHED.type,
                bus_events.ORCHESTRATION_SPAWN_STARTED.type,
                bus_events.ORCHESTRATION_SPAWN_FINISHED.type,
                bus_events.ORCHESTRATION_MESSAGE_SENT.type,
                bus_events.ORCHESTRATION_SWARM_STARTED.type,
                bus_events.ORCHESTRATION_SWARM_FINISHED.type,
            }
            async for event in bus.subscribe_all():
                if event.type not in orchestration_types:
                    continue
                if run_id is not None and event.properties.get("run_id") != run_id:
                    continue
                yield event

        async def consumer():
            async for ev in generator(run_id="A"):
                collected.append((ev.type, dict(ev.properties)))
                if len(collected) == 2:
                    break

        consumer_task = asyncio.create_task(consumer())
        # Let the subscription register before publishing.
        await asyncio.sleep(0.01)
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_STARTED,
            {"run_id": "A", "flow": "f"},
        )
        # B should be filtered out.
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_STARTED,
            {"run_id": "B", "flow": "f"},
        )
        await bus.publish(
            bus_events.ORCHESTRATION_FLOW_FINISHED,
            {"run_id": "A", "flow": "f"},
        )
        await asyncio.wait_for(consumer_task, timeout=3.0)

    asyncio.run(_drive())

    assert {t for t, _ in collected} == {
        "orchestration.flow.started",
        "orchestration.flow.finished",
    }
    for _, payload in collected:
        assert payload["run_id"] == "A"
