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


# --- Agent CRUD ------------------------------------------------------------


class _AgentBody(BaseModel):
    """Request body for creating / updating an agent .md file."""

    name: str
    description: str = ""
    extends: str | None = None
    role: str | None = None
    mode: str = "all"
    tools: list[str] | None = None
    prompt: str = ""
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_turns: int | None = None
    isolation: str = "none"
    omit_claudemd: bool = False
    permission: list[dict[str, str]] | None = None
    scope: str = "project"  # "project" or "global"


def _agent_to_md(body: _AgentBody) -> str:
    """Serialize agent body to .md with YAML frontmatter."""
    import yaml as _yaml

    fm: dict[str, Any] = {}
    if body.description:
        fm["description"] = body.description
    if body.extends:
        fm["extends"] = body.extends
    if body.role:
        fm["role"] = body.role
    if body.mode and body.mode != "all":
        fm["mode"] = body.mode
    if body.tools is not None:
        fm["tools"] = body.tools
    if body.model:
        fm["model"] = body.model
    if body.temperature is not None:
        fm["temperature"] = body.temperature
    if body.top_p is not None:
        fm["top_p"] = body.top_p
    if body.max_turns is not None:
        fm["max_turns"] = body.max_turns
    if body.isolation and body.isolation != "none":
        fm["isolation"] = body.isolation
    if body.omit_claudemd:
        fm["omit_claudemd"] = True
    if body.permission:
        fm["permission"] = body.permission

    parts: list[str] = []
    if fm:
        parts.append("---")
        parts.append(_yaml.dump(fm, allow_unicode=True, default_flow_style=False).strip())
        parts.append("---")
    if body.prompt:
        parts.append(body.prompt)
    return "\n".join(parts) + "\n"


def _agent_dir(scope: str) -> str:
    """Return the agent directory for the given scope."""
    from pathlib import Path

    from mycode.project.instance import current_or_none

    if scope == "global":
        return str(Path.home() / ".mycode" / "agents")
    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    return str(os.path.join(base, ".mycode", "agents"))


@router.post("/agent")
async def create_agent(body: _AgentBody) -> Any:
    """Create a new agent .md file."""
    from pathlib import Path

    name = body.name.strip()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, f"Invalid agent name: '{name}'")

    d = _agent_dir(body.scope)
    Path(d).mkdir(parents=True, exist_ok=True)
    fp = os.path.join(d, f"{name}.md")
    if os.path.exists(fp):
        raise HTTPException(409, f"Agent '{name}' already exists at {fp}")

    content = _agent_to_md(body)
    Path(fp).write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": fp, "scope": body.scope}


@router.put("/agent/{name}")
async def update_agent(name: str, body: _AgentBody) -> Any:
    """Update an existing agent .md file."""
    from pathlib import Path

    body.name = name  # ensure consistency
    d = _agent_dir(body.scope)
    fp = os.path.join(d, f"{name}.md")

    # Also check the other scope
    other_scope = "global" if body.scope == "project" else "project"
    other_fp = os.path.join(_agent_dir(other_scope), f"{name}.md")

    if not os.path.exists(fp) and os.path.exists(other_fp):
        fp = other_fp  # update wherever it actually lives

    Path(os.path.dirname(fp)).mkdir(parents=True, exist_ok=True)
    content = _agent_to_md(body)
    Path(fp).write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": fp}


@router.delete("/agent/{name}")
async def delete_agent(name: str, scope: str = Query(default="project")) -> Any:
    """Delete an agent .md file."""
    d = _agent_dir(scope)
    fp = os.path.join(d, f"{name}.md")
    if not os.path.isfile(fp):
        # Try other scope
        other = "global" if scope == "project" else "project"
        fp2 = os.path.join(_agent_dir(other), f"{name}.md")
        if os.path.isfile(fp2):
            fp = fp2
        else:
            raise HTTPException(404, f"Agent '{name}' not found")
    os.remove(fp)
    return {"ok": True, "name": name, "path": fp}


# --- Flow CRUD -------------------------------------------------------------


class _FlowBody(BaseModel):
    """Request body for creating / updating a flow YAML."""

    name: str
    description: str = ""
    mode: str = "coordinator"
    lead: str | None = None
    agents: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    vars: dict[str, str] = {}
    backend: dict[str, str] | None = None
    scope: str = "project"


def _flow_dir(scope: str) -> str:
    from pathlib import Path

    from mycode.project.instance import current_or_none

    if scope == "global":
        home = os.environ.get("MYCODE_HOME")
        if home:
            return str(Path(home).expanduser() / "orchestrations")
        return str(Path.home() / ".mycode" / "orchestrations")
    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    return str(os.path.join(base, ".mycode", "orchestrations"))


@router.post("/flow")
async def create_flow(body: _FlowBody) -> Any:
    """Create a new flow YAML file."""
    import yaml as _yaml
    from pathlib import Path

    name = body.name.strip()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, f"Invalid flow name: '{name}'")

    d = _flow_dir(body.scope)
    Path(d).mkdir(parents=True, exist_ok=True)
    fp = os.path.join(d, f"{name}.yaml")

    spec: dict[str, Any] = {"name": name, "mode": body.mode}
    if body.description:
        spec["description"] = body.description
    if body.lead:
        spec["lead"] = body.lead
    if body.vars:
        spec["vars"] = dict(body.vars)
    if body.agents:
        spec["agents"] = body.agents
    if body.stages:
        spec["stages"] = body.stages
    if body.backend:
        spec["backend"] = body.backend

    content = _yaml.dump(spec, allow_unicode=True, default_flow_style=False, sort_keys=False)
    Path(fp).write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": fp, "scope": body.scope}


@router.put("/flow/{name}")
async def update_flow(name: str, body: _FlowBody) -> Any:
    """Update an existing flow YAML file."""
    import yaml as _yaml
    from pathlib import Path

    body.name = name
    d = _flow_dir(body.scope)
    fp = os.path.join(d, f"{name}.yaml")

    # Check both scopes + extensions
    if not os.path.exists(fp):
        for ext in [".yaml", ".yml", ".json"]:
            candidate = os.path.join(d, f"{name}{ext}")
            if os.path.exists(candidate):
                fp = candidate
                break
        else:
            other = "global" if body.scope == "project" else "project"
            for ext in [".yaml", ".yml", ".json"]:
                candidate = os.path.join(_flow_dir(other), f"{name}{ext}")
                if os.path.exists(candidate):
                    fp = candidate
                    break

    Path(os.path.dirname(fp)).mkdir(parents=True, exist_ok=True)
    if not fp.endswith(".yaml"):
        fp = os.path.join(os.path.dirname(fp), f"{name}.yaml")

    spec: dict[str, Any] = {"name": name, "mode": body.mode}
    if body.description:
        spec["description"] = body.description
    if body.lead:
        spec["lead"] = body.lead
    if body.vars:
        spec["vars"] = dict(body.vars)
    if body.agents:
        spec["agents"] = body.agents
    if body.stages:
        spec["stages"] = body.stages
    if body.backend:
        spec["backend"] = body.backend

    content = _yaml.dump(spec, allow_unicode=True, default_flow_style=False, sort_keys=False)
    Path(fp).write_text(content, encoding="utf-8")
    return {"ok": True, "name": name, "path": fp}


@router.delete("/flow/{name}")
async def delete_flow(name: str, scope: str = Query(default="project")) -> Any:
    """Delete a flow file."""
    d = _flow_dir(scope)
    for ext in [".yaml", ".yml", ".json"]:
        fp = os.path.join(d, f"{name}{ext}")
        if os.path.isfile(fp):
            os.remove(fp)
            return {"ok": True, "name": name, "path": fp}

    # Try other scope
    other = "global" if scope == "project" else "project"
    d2 = _flow_dir(other)
    for ext in [".yaml", ".yml", ".json"]:
        fp = os.path.join(d2, f"{name}{ext}")
        if os.path.isfile(fp):
            os.remove(fp)
            return {"ok": True, "name": name, "path": fp}

    raise HTTPException(404, f"Flow '{name}' not found")


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
