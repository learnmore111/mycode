"""Orchestration → Bus bridge.

A thin wrapper that lets the coordinator / swarm runtimes fire
:class:`mycode.bus.bus.Bus` events at well-defined lifecycle points
without hard-coding the bus into the runtime.  The runtime accepts an
optional :class:`OrchestrationEventEmitter`; when it's ``None`` every
call is a no-op so existing unit tests (which never create a bus) stay
free of side effects.

Why a dedicated emitter class (and not just a bare ``Bus``)?

1. **Shape discipline** — every orchestration event shares a
   ``run_id`` / ``flow`` preamble, so centralising the payload
   construction here prevents bit-rot where one site forgets a field
   and breaks the web UI subscribers.
2. **Swappable backend** — tests substitute :class:`RecordingEmitter`
   to assert on the event stream without actually wiring a bus.
3. **Zero-cost when disabled** — the runtime does
   ``if self.events:`` before emitting, so production code paths that
   skip ``events=`` pay nothing for the feature.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from mycode.bus import events as ev

if TYPE_CHECKING:
    from mycode.bus.bus import Bus
    from mycode.orchestration.runtime.context import SpawnOutput, StageOutput
    from mycode.orchestration.runtime.mailbox import Envelope


# ---------------------------------------------------------------------------
# Emitter Protocol + default bus-backed implementation
# ---------------------------------------------------------------------------


class OrchestrationEventEmitter(Protocol):
    """Contract implemented by the bus-backed emitter and test fakes."""

    run_id: str
    flow_name: str

    async def flow_started(self, *, mode: str, agents: list[str], extra: dict[str, Any] | None = None) -> None: ...

    async def flow_finished(
        self,
        *,
        ok: bool,
        duration_seconds: float,
        extra: dict[str, Any] | None = None,
    ) -> None: ...

    async def stage_started(self, stage_id: str, *, extra: dict[str, Any] | None = None) -> None: ...

    async def stage_finished(self, stage: StageOutput, *, duration_seconds: float) -> None: ...

    async def spawn_started(
        self,
        *,
        stage_id: str | None,
        spawn_index: int,
        agent: str,
        task: str,
    ) -> None: ...

    async def spawn_finished(
        self,
        *,
        stage_id: str | None,
        spawn_index: int,
        spawn: SpawnOutput,
        duration_seconds: float,
    ) -> None: ...

    async def agent_message(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        role: str,
        content: str,
        turn: int,
        kind: str = "message",
        recipient: str | None = None,
    ) -> None: ...

    async def agent_tool(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        tool_name: str,
        args_preview: str,
        output_preview: str,
        turn: int,
    ) -> None: ...

    async def message_sent(self, env: Envelope) -> None: ...

    async def swarm_started(self, *, lead: str, peers: list[str], user_task: str) -> None: ...

    async def swarm_finished(
        self,
        *,
        lead: str,
        terminated_reason: str,
        duration_seconds: float,
        peer_count: int,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Default implementation: publish to a mycode.bus.bus.Bus
# ---------------------------------------------------------------------------


@dataclass
class BusOrchestrationEmitter:
    """Publishes orchestration lifecycle events to a shared :class:`Bus`.

    A single emitter instance is created per ``run()`` call; its
    ``run_id`` is embedded in every published payload so UIs can
    multiplex concurrent runs over one SSE stream.
    """

    bus: Bus
    flow_name: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def _base(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "flow": self.flow_name}

    async def flow_started(self, *, mode: str, agents: list[str], extra: dict[str, Any] | None = None) -> None:
        payload = {**self._base(), "mode": mode, "agents": list(agents)}
        if extra:
            payload.update(extra)
        await self.bus.publish(ev.ORCHESTRATION_FLOW_STARTED, payload)

    async def flow_finished(
        self,
        *,
        ok: bool,
        duration_seconds: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {**self._base(), "ok": ok, "duration_seconds": round(duration_seconds, 6)}
        if extra:
            payload.update(extra)
        await self.bus.publish(ev.ORCHESTRATION_FLOW_FINISHED, payload)

    async def stage_started(self, stage_id: str, *, extra: dict[str, Any] | None = None) -> None:
        payload = {**self._base(), "stage_id": stage_id}
        if extra:
            payload.update(extra)
        await self.bus.publish(ev.ORCHESTRATION_STAGE_STARTED, payload)

    async def stage_finished(self, stage: StageOutput, *, duration_seconds: float) -> None:
        payload = {
            **self._base(),
            "stage_id": stage.stage_id,
            "duration_seconds": round(duration_seconds, 6),
            "is_error": stage.is_error,
            "spawn_count": len(stage.spawns),
            "ok_count": len(stage.ok_spawns()),
            "coordinator_agent": stage.coordinator_agent,
            # Coordinator output can be long — send a short preview so SSE
            # payloads stay well under typical proxy buffer limits.  The
            # full text is available via the regular `/session/*/messages`
            # storage for coordinator stages that persist their output.
            "coordinator_preview": _preview(stage.coordinator_output or ""),
        }
        await self.bus.publish(ev.ORCHESTRATION_STAGE_FINISHED, payload)

    async def spawn_started(
        self,
        *,
        stage_id: str | None,
        spawn_index: int,
        agent: str,
        task: str,
    ) -> None:
        payload = {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "task_preview": _preview(task),
        }
        await self.bus.publish(ev.ORCHESTRATION_SPAWN_STARTED, payload)

    async def spawn_finished(
        self,
        *,
        stage_id: str | None,
        spawn_index: int,
        spawn: SpawnOutput,
        duration_seconds: float,
    ) -> None:
        payload = {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": spawn.agent,
            "is_error": spawn.is_error,
            "turns": spawn.turns,
            "tool_calls": spawn.tool_calls,
            "duration_seconds": round(duration_seconds, 6),
            "output_preview": _preview(spawn.output),
        }
        await self.bus.publish(ev.ORCHESTRATION_SPAWN_FINISHED, payload)

    async def agent_message(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        role: str,
        content: str,
        turn: int,
        kind: str = "message",
        recipient: str | None = None,
    ) -> None:
        payload = {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "role": role,
            "kind": kind,
            "turn": turn,
            "content_preview": _preview(content),
        }
        if recipient:
            payload["recipient"] = recipient
        await self.bus.publish(ev.ORCHESTRATION_AGENT_MESSAGE, payload)

    async def agent_tool(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        tool_name: str,
        args_preview: str,
        output_preview: str,
        turn: int,
    ) -> None:
        payload = {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "tool_name": tool_name,
            "args_preview": _preview(args_preview),
            "output_preview": _preview(output_preview),
            "turn": turn,
        }
        await self.bus.publish(ev.ORCHESTRATION_AGENT_TOOL, payload)

    async def message_sent(self, env: Envelope) -> None:
        payload = {
            **self._base(),
            "seq": env.seq,
            "kind": env.kind,
            "sender": env.sender,
            "recipient": env.recipient,
            "summary": env.summary,
            "content_preview": _preview(env.content),
            "timestamp": env.timestamp,
        }
        await self.bus.publish(ev.ORCHESTRATION_MESSAGE_SENT, payload)

    async def swarm_started(self, *, lead: str, peers: list[str], user_task: str) -> None:
        payload = {
            **self._base(),
            "lead": lead,
            "peers": list(peers),
            "user_task_preview": _preview(user_task),
        }
        await self.bus.publish(ev.ORCHESTRATION_SWARM_STARTED, payload)

    async def swarm_finished(
        self,
        *,
        lead: str,
        terminated_reason: str,
        duration_seconds: float,
        peer_count: int,
    ) -> None:
        payload = {
            **self._base(),
            "lead": lead,
            "terminated_reason": terminated_reason,
            "duration_seconds": round(duration_seconds, 6),
            "peer_count": peer_count,
        }
        await self.bus.publish(ev.ORCHESTRATION_SWARM_FINISHED, payload)


# ---------------------------------------------------------------------------
# Test helper: records emissions in-memory without needing a real Bus.
# ---------------------------------------------------------------------------


@dataclass
class RecordingEmitter:
    """Drop-in replacement for :class:`BusOrchestrationEmitter` in tests.

    Stores every emitted event as ``(type, payload)`` so assertions can
    inspect order, counts, and payload shape deterministically.
    """

    flow_name: str = "test-flow"
    run_id: str = "test-run"
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _base(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "flow": self.flow_name}

    async def flow_started(self, *, mode: str, agents: list[str], extra: dict[str, Any] | None = None) -> None:
        payload = {**self._base(), "mode": mode, "agents": list(agents)}
        if extra:
            payload.update(extra)
        self.events.append((ev.ORCHESTRATION_FLOW_STARTED.type, payload))

    async def flow_finished(
        self,
        *,
        ok: bool,
        duration_seconds: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {**self._base(), "ok": ok, "duration_seconds": duration_seconds}
        if extra:
            payload.update(extra)
        self.events.append((ev.ORCHESTRATION_FLOW_FINISHED.type, payload))

    async def stage_started(self, stage_id: str, *, extra: dict[str, Any] | None = None) -> None:
        payload = {**self._base(), "stage_id": stage_id}
        if extra:
            payload.update(extra)
        self.events.append((ev.ORCHESTRATION_STAGE_STARTED.type, payload))

    async def stage_finished(self, stage: StageOutput, *, duration_seconds: float) -> None:
        self.events.append((ev.ORCHESTRATION_STAGE_FINISHED.type, {
            **self._base(),
            "stage_id": stage.stage_id,
            "is_error": stage.is_error,
            "spawn_count": len(stage.spawns),
            "ok_count": len(stage.ok_spawns()),
            "duration_seconds": duration_seconds,
        }))

    async def spawn_started(self, *, stage_id: str | None, spawn_index: int, agent: str, task: str) -> None:
        self.events.append((ev.ORCHESTRATION_SPAWN_STARTED.type, {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "task_preview": _preview(task),
        }))

    async def spawn_finished(
        self,
        *,
        stage_id: str | None,
        spawn_index: int,
        spawn: SpawnOutput,
        duration_seconds: float,
    ) -> None:
        self.events.append((ev.ORCHESTRATION_SPAWN_FINISHED.type, {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": spawn.agent,
            "is_error": spawn.is_error,
            "turns": spawn.turns,
            "tool_calls": spawn.tool_calls,
            "duration_seconds": duration_seconds,
        }))

    async def agent_message(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        role: str,
        content: str,
        turn: int,
        kind: str = "message",
        recipient: str | None = None,
    ) -> None:
        payload = {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "role": role,
            "kind": kind,
            "turn": turn,
            "content_preview": _preview(content),
        }
        if recipient:
            payload["recipient"] = recipient
        self.events.append((ev.ORCHESTRATION_AGENT_MESSAGE.type, payload))

    async def agent_tool(
        self,
        *,
        stage_id: str | None,
        spawn_index: int | None,
        agent: str,
        tool_name: str,
        args_preview: str,
        output_preview: str,
        turn: int,
    ) -> None:
        self.events.append((ev.ORCHESTRATION_AGENT_TOOL.type, {
            **self._base(),
            "stage_id": stage_id,
            "spawn_index": spawn_index,
            "agent": agent,
            "tool_name": tool_name,
            "args_preview": _preview(args_preview),
            "output_preview": _preview(output_preview),
            "turn": turn,
        }))

    async def message_sent(self, env: Envelope) -> None:
        self.events.append((ev.ORCHESTRATION_MESSAGE_SENT.type, {
            **self._base(),
            "seq": env.seq,
            "kind": env.kind,
            "sender": env.sender,
            "recipient": env.recipient,
        }))

    async def swarm_started(self, *, lead: str, peers: list[str], user_task: str) -> None:
        self.events.append((ev.ORCHESTRATION_SWARM_STARTED.type, {
            **self._base(), "lead": lead, "peers": list(peers),
            "user_task_preview": _preview(user_task),
        }))

    async def swarm_finished(
        self,
        *,
        lead: str,
        terminated_reason: str,
        duration_seconds: float,
        peer_count: int,
    ) -> None:
        self.events.append((ev.ORCHESTRATION_SWARM_FINISHED.type, {
            **self._base(),
            "lead": lead,
            "terminated_reason": terminated_reason,
            "duration_seconds": duration_seconds,
            "peer_count": peer_count,
        }))

    # Assertions helpers
    def types(self) -> list[str]:
        return [t for t, _ in self.events]

    def of_type(self, type_: str) -> list[dict[str, Any]]:
        return [p for t, p in self.events if t == type_]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


_MAX_PREVIEW = 280  # Matches the old Twitter cap; plenty for a one-liner.


def _preview(text: str) -> str:
    """Clip a large string to a human-readable preview for SSE.

    Pure function so tests can exercise the clipping directly.
    """
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= _MAX_PREVIEW:
        return text
    return text[:_MAX_PREVIEW - 1] + "…"


__all__ = [
    "BusOrchestrationEmitter",
    "OrchestrationEventEmitter",
    "RecordingEmitter",
]
