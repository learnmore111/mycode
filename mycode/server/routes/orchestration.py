"""Orchestration HTTP + SSE routes (M7).

Endpoints
---------

- ``GET  /orchestration/flow``     — list discovered flows.
- ``GET  /orchestration/flow/{name}`` — resolve + return the parsed spec.
- ``GET  /orchestration/agent``    — list discovered agents.
- ``POST /orchestration/run``      — start a run (coordinator or swarm)
                                     in the background; returns ``run_id``.
- ``GET  /orchestration/events``   — SSE stream, optionally filtered by
                                     ``run_id``.

The run is executed inside the same event loop as the API server; the
emitter publishes to the shared :class:`Bus` instance that the existing
``/event`` SSE route already consumes, so UI clients can either use the
dedicated ``/orchestration/events`` endpoint (which filters to
orchestration events and matches ``run_id``) or tap into the generic one
and filter locally.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from mycode.bus import events as bus_events
from mycode.orchestration.registry import (
    get_default_agent_registry,
    get_default_registry,
)
from mycode.orchestration.runtime.coordinator import run_coordinator
from mycode.orchestration.runtime.events import BusOrchestrationEmitter
from mycode.orchestration.runtime.swarm import run_swarm
from mycode.orchestration.topology import resolve_all_agents
from mycode.orchestration.topology.loader import OrchestrationLoadError
from mycode.orchestration.topology.validator import OrchestrationValidationError

if TYPE_CHECKING:
    from mycode.bus.bus import Bus

router = APIRouter(prefix="/orchestration", tags=["orchestration"])

# --- Shared bus, set by app startup ----------------------------------------

_bus: Bus | None = None

# In-memory registry of currently-running orchestrations.  Keyed by
# ``run_id``; value is the asyncio Task so we can surface status and
# avoid dangling background work on server shutdown.  Kept tiny — no
# persistence is the point for M7.
_runs: dict[str, asyncio.Task[Any]] = {}


def set_bus(bus: Bus) -> None:
    """Called by ``app.create_app`` to share the server-wide Bus."""
    global _bus
    _bus = bus


def _require_bus() -> Bus:
    if _bus is None:
        raise HTTPException(
            status_code=503,
            detail="orchestration bus not initialised — server still starting up?",
        )
    return _bus


# --- Read endpoints --------------------------------------------------------


@router.get("/flow")
async def list_flows(directory: str | None = Query(default=None)) -> Any:
    """List flows discovered in builtin + global + project scope."""
    project_dir = os.path.abspath(directory) if directory else None
    reg = get_default_registry(project_dir=project_dir, refresh=True)
    return [
        {"name": f.name, "source": f.source, "path": str(f.path)}
        for f in reg.list_flows()
    ]


@router.get("/flow/{name}")
async def get_flow(name: str, directory: str | None = Query(default=None)) -> Any:
    """Resolve a flow by name and return the parsed spec as JSON."""
    project_dir = os.path.abspath(directory) if directory else None
    reg = get_default_registry(project_dir=project_dir, refresh=True)
    try:
        spec = reg.load(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OrchestrationLoadError, OrchestrationValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "name": spec.name,
        "mode": spec.mode,
        "lead": spec.lead,
        "agents": [
            {"name": a.name, "extends": a.extends, "prompt": a.prompt}
            for a in spec.agents
        ],
        "stages": [
            {
                "id": s.id,
                "parallel": s.parallel,
                "runs_on": s.runs_on,
                "fan_out_from": s.fan_out_from,
                "depends_on": list(s.depends_on),
                "inputs": list(s.inputs),
                "spawns": [{"agent": sp.agent, "task": sp.task} for sp in s.spawn],
            }
            for s in spec.stages
        ],
        "vars": dict(spec.vars),
    }


@router.get("/agent")
async def list_agents(directory: str | None = Query(default=None)) -> Any:
    """List agents discovered in builtin + global + project scope."""
    project_dir = os.path.abspath(directory) if directory else None
    reg = get_default_agent_registry(project_dir=project_dir, refresh=True)
    entries = []
    for entry in reg.list_entries():
        try:
            info = reg.resolve(entry.name)
        except Exception as exc:  # noqa: BLE001
            entries.append({"name": entry.name, "source": entry.source, "error": str(exc)})
            continue
        entries.append({
            "name": info.name,
            "source": entry.source,
            "description": info.description,
            "extends": info.extends or "",
            "tools": ",".join(info.tools) if info.tools else "",
            "mode": info.mode,
        })
    return entries


# --- Run endpoint ----------------------------------------------------------


class _RunBody(BaseModel):
    """Request body for ``POST /orchestration/run``."""

    flow: str
    task: str | None = None  # required for swarm mode
    vars: dict[str, str] | None = None
    max_turns: int = 8
    walltime_seconds: float = 300.0
    directory: str | None = None  # project dir override


@router.post("/run")
async def start_run(body: _RunBody) -> Any:
    """Kick off an orchestration run in the background.

    Returns immediately with the ``run_id`` so the client can switch to
    ``GET /orchestration/events?run_id=...`` to observe progress.
    """
    bus = _require_bus()
    project_dir = os.path.abspath(body.directory) if body.directory else None

    flow_reg = get_default_registry(project_dir=project_dir, refresh=True)
    agent_reg = get_default_agent_registry(project_dir=project_dir, refresh=True)

    try:
        spec = flow_reg.load(body.flow, vars_override=body.vars or {})
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OrchestrationLoadError, OrchestrationValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        agents = resolve_all_agents(spec.agents, agent_reg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"agent resolution failed: {exc}") from exc

    if spec.mode == "swarm" and not body.task:
        raise HTTPException(status_code=400, detail="swarm mode requires 'task' in body")

    run_id = uuid.uuid4().hex[:16]
    emitter = BusOrchestrationEmitter(bus=bus, flow_name=spec.name, run_id=run_id)

    async def _run() -> None:
        try:
            if spec.mode == "swarm":
                await run_swarm(
                    spec, agents,
                    user_task=body.task or "",
                    max_turns=body.max_turns,
                    walltime_seconds=body.walltime_seconds,
                    events=emitter,
                )
            else:
                await run_coordinator(spec, agents, events=emitter)
        finally:
            _runs.pop(run_id, None)

    task = asyncio.create_task(_run(), name=f"orchestration-run-{run_id}")
    _runs[run_id] = task

    return {"run_id": run_id, "flow": spec.name, "mode": spec.mode}


@router.get("/run")
async def list_runs() -> Any:
    """List currently-tracked runs (in-memory; cleared on finish)."""
    return [
        {"run_id": rid, "done": task.done(), "cancelled": task.cancelled()}
        for rid, task in _runs.items()
    ]


# --- SSE stream ------------------------------------------------------------


_ORCHESTRATION_TYPES = frozenset({
    bus_events.ORCHESTRATION_FLOW_STARTED.type,
    bus_events.ORCHESTRATION_FLOW_FINISHED.type,
    bus_events.ORCHESTRATION_STAGE_STARTED.type,
    bus_events.ORCHESTRATION_STAGE_FINISHED.type,
    bus_events.ORCHESTRATION_SPAWN_STARTED.type,
    bus_events.ORCHESTRATION_SPAWN_FINISHED.type,
    bus_events.ORCHESTRATION_MESSAGE_SENT.type,
    bus_events.ORCHESTRATION_SWARM_STARTED.type,
    bus_events.ORCHESTRATION_SWARM_FINISHED.type,
})


@router.get("/events")
async def orchestration_events(run_id: str | None = Query(default=None)) -> Any:
    """SSE stream of orchestration lifecycle events.

    Query params:
        run_id: filter to a single run.  Omit to see every live run.
    """
    bus = _require_bus()

    async def generator() -> Any:
        # Using subscribe_all + local filter is cheaper than registering
        # 9 typed subscriptions.  The bus copies event payloads anyway so
        # the cost is just the extra membership test per event.
        async for event in bus.subscribe_all():
            if event.type not in _ORCHESTRATION_TYPES:
                continue
            if run_id is not None and event.properties.get("run_id") != run_id:
                continue
            yield {
                "event": event.type,
                "data": json.dumps(event.properties, ensure_ascii=False),
            }

    return EventSourceResponse(generator())
