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
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml as _yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from mycode.bus import events as bus_events
from mycode.orchestration.registry import (
    get_default_agent_registry,
    get_default_registry,
)
from mycode.orchestration.run_store import (
    OrchestrationRunInfo,
    get_run_record,
    list_run_records,
    save_run_record,
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

@dataclass
class _RunRecord(OrchestrationRunInfo):
    task: asyncio.Task[Any] | None = None


# In-memory registry of active orchestration runs for this server process.
# Durable history lives in SQLite via ``mycode.orchestration.run_store``.
_runs: dict[str, _RunRecord] = {}


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


def _preview(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _persist_run(run: OrchestrationRunInfo) -> None:
    save_run_record(run)


def _get_run_or_404(run_id: str) -> OrchestrationRunInfo:
    run = _runs.get(run_id)
    if run is not None:
        return run
    stored = get_run_record(run_id)
    if stored is not None:
        return stored
    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")


def _stage_output_preview(stage: Any) -> str:
    coordinator_output = getattr(stage, "coordinator_output", None)
    if coordinator_output:
        return _preview(coordinator_output)
    spawns = getattr(stage, "spawns", []) or []
    if spawns:
        return _preview(getattr(spawns[0], "output", ""))
    return ""


def _envelope_preview(env: Any, limit: int = 160) -> str:
    summary = (getattr(env, "summary", "") or "").strip()
    content = (getattr(env, "content", "") or "").strip()
    return _preview(summary or content, limit)


def _summarize_coordinator_result(result: Any) -> dict[str, Any] | None:
    context = getattr(result, "context", None)
    stage_order = getattr(context, "stage_order", None)
    stages_by_id = getattr(context, "stages", None)
    if context is None or stage_order is None or stages_by_id is None:
        return None

    stages: list[dict[str, Any]] = []
    total_spawn_count = 0
    total_error_count = 0
    for stage_id in stage_order:
        stage = stages_by_id[stage_id]
        spawn_count = len(stage.spawns)
        error_count = sum(1 for spawn in stage.spawns if spawn.is_error)
        total_spawn_count += spawn_count
        total_error_count += error_count
        stages.append({
            "stage_id": stage.stage_id,
            "is_error": stage.is_error,
            "spawn_count": spawn_count,
            "ok_count": len(stage.ok_spawns()),
            "error_count": error_count,
            "coordinator_agent": stage.coordinator_agent,
            "output_preview": _stage_output_preview(stage),
        })

    last_stage = getattr(result, "last_stage", None)
    return {
        "kind": "coordinator",
        "stage_count": len(stages),
        "stage_order": list(stage_order),
        "total_spawn_count": total_spawn_count,
        "total_error_count": total_error_count,
        "has_errors": any(stage["is_error"] or stage["error_count"] > 0 for stage in stages),
        "last_stage_id": getattr(last_stage, "stage_id", None),
        "last_output_preview": _stage_output_preview(last_stage) if last_stage is not None else "",
        "stages": stages,
    }


def _summarize_swarm_result(result: Any) -> dict[str, Any] | None:
    peers = getattr(result, "peers", None)
    if peers is None:
        return None

    transcript = list(getattr(result, "transcript", []) or [])
    delivered_messages = [env for env in transcript if getattr(env, "recipient", "") != "*"]
    peer_activity: dict[str, dict[str, Any]] = {
        name: {
            "sent_count": 0,
            "received_count": 0,
            "last_sent_to": "",
            "last_sent_preview": "",
            "last_received_from": "",
            "last_received_preview": "",
        }
        for name in peers
    }
    route_counts: dict[tuple[str, str], int] = {}
    recent_messages: list[dict[str, Any]] = []

    for env in delivered_messages:
        sender = getattr(env, "sender", "")
        recipient = getattr(env, "recipient", "")
        preview = _envelope_preview(env)
        route_counts[(sender, recipient)] = route_counts.get((sender, recipient), 0) + 1

        if sender in peer_activity:
            peer_activity[sender]["sent_count"] += 1
            peer_activity[sender]["last_sent_to"] = recipient
            peer_activity[sender]["last_sent_preview"] = preview
        if recipient in peer_activity:
            peer_activity[recipient]["received_count"] += 1
            peer_activity[recipient]["last_received_from"] = sender
            peer_activity[recipient]["last_received_preview"] = preview

        recent_messages.append({
            "seq": getattr(env, "seq", 0),
            "kind": getattr(env, "kind", ""),
            "sender": sender,
            "recipient": recipient,
            "preview": preview,
        })

    peer_summaries = []
    for name in sorted(peers):
        out = peers[name]
        activity = peer_activity[name]
        output_preview = _preview(out.output)
        if activity["last_sent_preview"]:
            activity_direction = "sent"
            activity_partner = activity["last_sent_to"]
            activity_preview = activity["last_sent_preview"]
        elif activity["last_received_preview"]:
            activity_direction = "received"
            activity_partner = activity["last_received_from"]
            activity_preview = activity["last_received_preview"]
        elif output_preview:
            activity_direction = "output"
            activity_partner = ""
            activity_preview = output_preview
        else:
            activity_direction = "none"
            activity_partner = ""
            activity_preview = ""
        peer_summaries.append({
            "name": name,
            "agent": out.agent,
            "is_error": out.is_error,
            "turns": out.turns,
            "tool_calls": out.tool_calls,
            "output_preview": output_preview,
            "sent_count": activity["sent_count"],
            "received_count": activity["received_count"],
            "recent_activity_direction": activity_direction,
            "recent_activity_partner": activity_partner,
            "recent_activity_preview": activity_preview,
        })

    active_peer_count = sum(
        1
        for peer in peer_summaries
        if peer["sent_count"] > 0
        or peer["received_count"] > 0
        or peer["tool_calls"] > 0
        or bool(peer["output_preview"])
    )
    message_routes = [
        {"sender": sender, "recipient": recipient, "count": count}
        for (sender, recipient), count in sorted(
            route_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
    ]

    return {
        "kind": "swarm",
        "lead": getattr(result, "lead", ""),
        "entry": getattr(result, "entry", "") or getattr(result, "lead", ""),
        "peer_count": len(peers),
        "terminated_reason": getattr(result, "terminated_reason", ""),
        "message_count": len(transcript),
        "collaboration_count": len(delivered_messages),
        "active_peer_count": active_peer_count,
        "has_errors": any(peer["is_error"] for peer in peer_summaries),
        "lead_output_preview": _preview(getattr(result, "lead_output", "")),
        "entry_output_preview": _preview(getattr(result, "lead_output", "")),
        "message_routes": message_routes[:6],
        "recent_messages": recent_messages[-6:],
        "peers": peer_summaries,
    }


# --- Read endpoints --------------------------------------------------------


def _resolve_project_dir(directory: str | None) -> str | None:
    """Pick the effective project directory for discovery/resolution.

    Precedence:
      1. Explicit ``directory`` query parameter from the client.
      2. The active :class:`InstanceContext` (set by request middleware or
         the CLI when a project is already active).
      3. The server process's current working directory — matches the
         write side (``_agent_dir`` / ``_flow_dir``), so any file that the
         UI just created in ``<cwd>/.mycode/...`` is visible to the list
         endpoints without forcing the caller to pass the directory.
    """
    from mycode.project.instance import current_or_none

    if directory:
        return os.path.abspath(directory)
    inst = current_or_none()
    if inst is not None and inst.directory:
        return os.path.abspath(inst.directory)
    return os.path.abspath(os.getcwd())


@router.get("/flow")
async def list_flows(directory: str | None = Query(default=None)) -> Any:
    """List flows discovered in builtin + global + project scope."""
    project_dir = _resolve_project_dir(directory)
    reg = get_default_registry(project_dir=project_dir, refresh=True)
    return [
        {"name": f.name, "source": f.source, "path": str(f.path)}
        for f in reg.list_flows()
    ]


@router.get("/flow/{name}")
async def get_flow(name: str, directory: str | None = Query(default=None)) -> Any:
    """Resolve a flow by name and return the parsed spec as JSON."""
    project_dir = _resolve_project_dir(directory)
    reg = get_default_registry(project_dir=project_dir, refresh=True)
    try:
        spec = reg.load(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OrchestrationLoadError, OrchestrationValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "name": spec.name,
        "description": spec.description,
        "mode": spec.mode,
        "extends": spec.extends,
        "model": spec.model,
        "max_depth": spec.max_depth,
        "lead": spec.lead,
        "entry": spec.entry or spec.lead,
        "coordinator": spec.coordinator,
        "agents": [
            {
                "name": a.name,
                "extends": a.extends,
                "role": a.role,
                "description": a.description,
                "prompt": a.prompt,
                "model": a.model,
                "temperature": a.temperature,
                "top_p": a.top_p,
                "tools": list(a.tools or []),
                "disallowed_tools": list(a.disallowed_tools),
                "permission": [rule.model_dump() for rule in a.permission],
                "isolation": a.isolation,
                "max_turns": a.max_turns,
                "background": a.background,
                "omit_claudemd": a.omit_claudemd,
            }
            for a in spec.agents
        ],
        "stages": [
            {
                "id": s.id,
                "description": s.description,
                "parallel": s.parallel,
                "max_concurrency": s.max_concurrency,
                "runs_on": s.runs_on,
                "fan_out_from": s.fan_out_from,
                "depends_on": list(s.depends_on),
                "inputs": list(s.inputs),
                "prompt": s.prompt,
                "spawns": [
                    {
                        "agent": sp.agent,
                        "task": sp.task,
                        "vars": dict(sp.vars),
                        "timeout_seconds": sp.timeout_seconds,
                    }
                    for sp in s.spawn
                ],
            }
            for s in spec.stages
        ],
        "vars": dict(spec.vars),
        "backend": spec.backend.model_dump() if spec.backend is not None else None,
    }


@router.get("/agent")
async def list_agents(directory: str | None = Query(default=None)) -> Any:
    """List agents discovered in builtin + global + project scope."""
    project_dir = _resolve_project_dir(directory)
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


@router.get("/agent/{name}")
async def get_agent(name: str, directory: str | None = Query(default=None)) -> Any:
    """Resolve one agent and return the full editable definition."""
    project_dir = _resolve_project_dir(directory)
    reg = get_default_agent_registry(project_dir=project_dir, refresh=True)

    entry = next((item for item in reg.list_entries() if item.name == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        info = reg.resolve(name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "name": info.name,
        "source": entry.source,
        "description": info.description or "",
        "extends": info.extends,
        "role": info.role,
        "mode": info.mode,
        "hidden": info.hidden,
        "tools": list(info.tools or []),
        "prompt": info.prompt or "",
        "model": (
            f"{info.model['providerID']}/{info.model['modelID']}"
            if info.model and info.model.get("providerID") and info.model.get("modelID")
            else None
        ),
        "temperature": info.temperature,
        "top_p": info.top_p,
        "color": info.color,
        "variant": info.variant,
        "options": dict(info.options or {}),
        "steps": info.steps,
        "max_turns": info.max_turns,
        "isolation": info.isolation,
        "omit_claudemd": info.omit_claudemd,
        "permission": list(info.permission or []),
        "scope": entry.source if entry.source in {"project", "global"} else "project",
    }


# --- Agent CRUD ------------------------------------------------------------


class _AgentBody(BaseModel):
    """Request body for creating / updating an agent .md file."""

    name: str
    description: str = ""
    extends: str | None = None
    role: str | None = None
    mode: str = "all"
    hidden: bool = False
    tools: list[str] | None = None
    prompt: str = ""
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    variant: str | None = None
    options: dict[str, Any] | None = None
    steps: int | None = None
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
    if body.hidden:
        fm["hidden"] = True
    if body.tools is not None:
        fm["tools"] = body.tools
    if body.model:
        fm["model"] = body.model
    if body.temperature is not None:
        fm["temperature"] = body.temperature
    if body.top_p is not None:
        fm["top_p"] = body.top_p
    if body.color:
        fm["color"] = body.color
    if body.variant:
        fm["variant"] = body.variant
    if body.options:
        fm["options"] = body.options
    if body.steps is not None:
        fm["steps"] = body.steps
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
    extends: str | None = None
    model: str | None = None
    max_depth: int | None = None
    # ``entry`` is the preferred field for the swarm initial task receiver.
    # ``lead`` is kept for backwards-compat and mirrored to ``entry`` on
    # persist.  Clients may send either; the server normalizes.
    entry: str | None = None
    lead: str | None = None
    # ``coordinator`` names the leader agent in coordinator/hybrid mode
    # (orchestrator-worker pattern).  Required for coordinator mode unless
    # exactly one agent already has ``role: coordinator`` — in which case
    # the schema layer derives it automatically.
    coordinator: str | None = None
    agents: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    vars: dict[str, str] = {}
    backend: dict[str, str] | None = None
    scope: str = "project"

    def resolved_entry(self) -> str | None:
        """Return the effective entry-agent name, regardless of field used."""
        return self.entry or self.lead


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
    name = body.name.strip()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, f"Invalid flow name: '{name}'")

    d = _flow_dir(body.scope)
    Path(d).mkdir(parents=True, exist_ok=True)
    fp = os.path.join(d, f"{name}.yaml")

    spec: dict[str, Any] = {"name": name, "mode": body.mode}
    if body.description:
        spec["description"] = body.description
    if body.extends:
        spec["extends"] = body.extends
    if body.model:
        spec["model"] = body.model
    if body.max_depth is not None:
        spec["max_depth"] = body.max_depth
    entry_name = body.resolved_entry()
    if entry_name:
        # Persist as ``entry`` (the canonical key).  Loaders still accept
        # legacy ``lead`` aliases.
        spec["entry"] = entry_name
    if body.coordinator:
        spec["coordinator"] = body.coordinator
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
    if body.extends:
        spec["extends"] = body.extends
    if body.model:
        spec["model"] = body.model
    if body.max_depth is not None:
        spec["max_depth"] = body.max_depth
    entry_name = body.resolved_entry()
    if entry_name:
        spec["entry"] = entry_name
    if body.coordinator:
        spec["coordinator"] = body.coordinator
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
    project_dir = _resolve_project_dir(body.directory)

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
    entry = _RunRecord(
        run_id=run_id,
        flow=spec.name,
        mode=spec.mode,
        directory=project_dir,
        task_text=body.task,
        vars=dict(body.vars or {}),
        max_turns=body.max_turns,
        walltime_seconds=body.walltime_seconds,
    )
    _runs[run_id] = entry
    _persist_run(entry)
    emitter = BusOrchestrationEmitter(bus=bus, flow_name=spec.name, run_id=run_id)

    async def _run() -> None:
        try:
            if spec.mode == "swarm":
                result = await run_swarm(
                    spec, agents,
                    user_task=body.task or "",
                    max_turns=body.max_turns,
                    walltime_seconds=body.walltime_seconds,
                    events=emitter,
                )
                entry.result = _summarize_swarm_result(result)
            else:
                result = await run_coordinator(spec, agents, events=emitter)
                entry.result = _summarize_coordinator_result(result)
            entry.status = "completed"
            entry.error = None
        except asyncio.CancelledError:
            entry.status = "cancelled"
            entry.error = None
            entry.result = {
                "kind": spec.mode,
                "cancelled": True,
            }
        except Exception as exc:  # noqa: BLE001
            entry.status = "failed"
            entry.error = f"{type(exc).__name__}: {exc}"
        finally:
            entry.finished_at = time.time()
            _persist_run(entry)

    task = asyncio.create_task(_run(), name=f"orchestration-run-{run_id}")
    entry.task = task

    return {
        "run_id": run_id,
        "flow": spec.name,
        "mode": spec.mode,
        "status": entry.status,
    }


@router.get("/run")
async def list_runs() -> Any:
    """List known orchestration runs, newest first."""
    merged: dict[str, OrchestrationRunInfo] = {
        run.run_id: run for run in list_run_records()
    }
    for run_id, run in _runs.items():
        merged[run_id] = run
    runs = sorted(merged.values(), key=lambda run: run.started_at, reverse=True)
    return [run.to_summary() for run in runs]


@router.get("/run/{run_id}")
async def get_run(run_id: str) -> Any:
    """Return detailed status and result summary for one run."""
    return _get_run_or_404(run_id).to_detail()


@router.post("/run/{run_id}/cancel")
async def cancel_run(run_id: str) -> Any:
    """Request cancellation of a running orchestration."""
    run = _get_run_or_404(run_id)
    if run.status == "cancelled":
        run.cancel_requested = True
        _persist_run(run)
        return {"ok": True, "run_id": run_id, "status": run.status, "already_finished": True}
    if run.is_done():
        return {"ok": True, "run_id": run_id, "status": run.status, "already_finished": True}
    if not isinstance(run, _RunRecord) or run.task is None:
        raise HTTPException(status_code=409, detail=f"Run '{run_id}' is not active in this server process")

    run.cancel_requested = True
    run.status = "cancelling"
    _persist_run(run)
    run.task.cancel()
    return {"ok": True, "run_id": run_id, "status": "cancelling"}


# --- SSE stream ------------------------------------------------------------


_ORCHESTRATION_TYPES = frozenset({
    bus_events.ORCHESTRATION_FLOW_STARTED.type,
    bus_events.ORCHESTRATION_FLOW_FINISHED.type,
    bus_events.ORCHESTRATION_STAGE_STARTED.type,
    bus_events.ORCHESTRATION_STAGE_FINISHED.type,
    bus_events.ORCHESTRATION_SPAWN_STARTED.type,
    bus_events.ORCHESTRATION_SPAWN_FINISHED.type,
    bus_events.ORCHESTRATION_AGENT_MESSAGE.type,
    bus_events.ORCHESTRATION_AGENT_TOOL.type,
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
