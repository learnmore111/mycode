"""M7 — orchestration event bridge tests.

Covers three layers:

1. ``RecordingEmitter`` + ``BusOrchestrationEmitter`` payload shape.
2. ``Coordinator.run()`` emits the documented start/finish event
   sequence with correct nesting (``flow → stage → spawn``).
3. ``run_swarm()`` emits ``swarm.started`` / ``message.sent`` /
   ``swarm.finished`` and the mailbox ``on_send`` hook is wired.

All tests use deterministic fakes — no network, no LLM, no litellm.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import pytest

from mycode.agent.agent import AgentInfo
from mycode.bus import events as bus_events
from mycode.bus.bus import Bus
from mycode.orchestration.runtime import (
    BusOrchestrationEmitter,
    Coordinator,
    Envelope,
    RecordingEmitter,
    SpawnOutput,
    SpawnRequest,
    StageOutput,
    run_coordinator,
    run_swarm,
)
from mycode.orchestration.runtime.events import _preview
from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)

if TYPE_CHECKING:
    from mycode.orchestration.runtime.swarm import SwarmAgentContext


# --- Shared fakes ----------------------------------------------------------


def _agent(name: str) -> AgentInfo:
    return AgentInfo(
        name=name,
        description=f"test {name}",
        mode="all",
        native=False,
        source="project",
    )


@dataclass
class _EchoRunner:
    """Minimal coordinator AgentRunner: echoes the task."""

    calls: list[SpawnRequest] = field(default_factory=list)

    async def __call__(self, req: SpawnRequest) -> SpawnOutput:
        self.calls.append(req)
        return SpawnOutput(
            agent=req.agent.name,
            task=req.task,
            output=f"{req.agent.name}:{req.task}",
            turns=1,
            tool_calls=0,
        )


# --- Preview helper -------------------------------------------------------


def test_preview_clips_large_strings():
    short = "hello world"
    assert _preview(short) == "hello world"

    big = "x" * 600
    out = _preview(big)
    assert len(out) <= 280
    assert out.endswith("…")


def test_preview_trims_whitespace():
    assert _preview("   hi   ") == "hi"
    assert _preview("") == ""


# --- RecordingEmitter shape -----------------------------------------------


@pytest.mark.asyncio
async def test_recording_emitter_captures_all_event_types():
    rec = RecordingEmitter(flow_name="demo", run_id="run-1")

    await rec.flow_started(mode="coordinator", agents=["a", "b"])
    await rec.stage_started("s1")
    await rec.spawn_started(stage_id="s1", spawn_index=0, agent="a", task="do thing")
    await rec.spawn_finished(
        stage_id="s1",
        spawn_index=0,
        spawn=SpawnOutput(agent="a", task="do thing", output="done"),
        duration_seconds=0.05,
    )
    await rec.agent_message(
        stage_id="s1",
        spawn_index=0,
        agent="a",
        role="assistant",
        content="working on it",
        turn=1,
    )
    await rec.agent_tool(
        stage_id="s1",
        spawn_index=0,
        agent="a",
        tool_name="read",
        args_preview='{"file":"a.py"}',
        output_preview="contents",
        turn=1,
    )
    await rec.stage_finished(
        StageOutput(stage_id="s1", spawns=[
            SpawnOutput(agent="a", task="do thing", output="done"),
        ]),
        duration_seconds=0.1,
    )
    await rec.flow_finished(ok=True, duration_seconds=0.2)

    types = rec.types()
    assert types == [
        "orchestration.flow.started",
        "orchestration.stage.started",
        "orchestration.spawn.started",
        "orchestration.spawn.finished",
        "orchestration.agent.message",
        "orchestration.agent.tool",
        "orchestration.stage.finished",
        "orchestration.flow.finished",
    ]

    # Every payload carries the bookkeeping preamble.
    for _, payload in rec.events:
        assert payload["run_id"] == "run-1"
        assert payload["flow"] == "demo"


@pytest.mark.asyncio
async def test_recording_emitter_message_and_swarm_events():
    rec = RecordingEmitter(flow_name="s", run_id="r")
    await rec.swarm_started(lead="L", peers=["a", "b"], user_task="task")
    await rec.message_sent(Envelope(
        kind="message", sender="L", recipient="a",
        content="hi", seq=1, timestamp=0.0,
    ))
    await rec.swarm_finished(
        lead="L", terminated_reason="lead-quiet",
        duration_seconds=0.3, peer_count=3,
    )

    assert rec.types() == [
        "orchestration.swarm.started",
        "orchestration.message.sent",
        "orchestration.swarm.finished",
    ]
    msg_payload = rec.of_type("orchestration.message.sent")[0]
    assert msg_payload["seq"] == 1
    assert msg_payload["kind"] == "message"
    assert msg_payload["sender"] == "L"
    assert msg_payload["recipient"] == "a"


@pytest.mark.asyncio
async def test_recording_emitter_agent_detail_events():
    rec = RecordingEmitter(flow_name="demo", run_id="r")
    await rec.agent_message(
        stage_id="s1",
        spawn_index=0,
        agent="worker",
        role="assistant",
        kind="message",
        content="draft answer",
        turn=2,
    )
    await rec.agent_tool(
        stage_id="s1",
        spawn_index=0,
        agent="worker",
        tool_name="grep",
        args_preview="foo",
        output_preview="bar",
        turn=2,
    )

    assert rec.types() == [
        "orchestration.agent.message",
        "orchestration.agent.tool",
    ]
    detail = rec.of_type("orchestration.agent.message")[0]
    assert detail["agent"] == "worker"
    assert detail["turn"] == 2
    tool = rec.of_type("orchestration.agent.tool")[0]
    assert tool["tool_name"] == "grep"


# --- BusOrchestrationEmitter publishes on a real bus ----------------------


@pytest.mark.asyncio
async def test_bus_emitter_publishes_to_bus_with_full_payload():
    bus = Bus()
    collected: list[tuple[str, dict[str, Any]]] = []
    unsub = bus.on_all(lambda ev: collected.append((ev.type, ev.properties)))
    try:
        em = BusOrchestrationEmitter(bus=bus, flow_name="demo", run_id="abcd")

        await em.flow_started(mode="coordinator", agents=["a"])
        await em.stage_started("s1", extra={"parallel": False})
        await em.spawn_started(stage_id="s1", spawn_index=0, agent="a", task="task body")
        await em.spawn_finished(
            stage_id="s1", spawn_index=0,
            spawn=SpawnOutput(agent="a", task="task body", output="x" * 400,
                              is_error=False, turns=2, tool_calls=1),
            duration_seconds=0.5,
        )
        await em.stage_finished(
            StageOutput(stage_id="s1", spawns=[], coordinator_agent="a",
                        coordinator_output="synthesis"),
            duration_seconds=0.6,
        )
        await em.flow_finished(ok=True, duration_seconds=0.7)
    finally:
        unsub()
        await bus.close()

    types = [t for t, _ in collected]
    # We may trail an instance-disposed sentinel from close(); filter it.
    orch_types = [t for t in types if t.startswith("orchestration.")]
    assert orch_types == [
        "orchestration.flow.started",
        "orchestration.stage.started",
        "orchestration.spawn.started",
        "orchestration.spawn.finished",
        "orchestration.stage.finished",
        "orchestration.flow.finished",
    ]

    spawn_fin = next(p for t, p in collected if t == "orchestration.spawn.finished")
    # Output preview was clipped to 280 + ellipsis.
    assert len(spawn_fin["output_preview"]) <= 280
    assert spawn_fin["output_preview"].endswith("…")
    assert spawn_fin["turns"] == 2
    assert spawn_fin["tool_calls"] == 1
    assert spawn_fin["run_id"] == "abcd"

    stage_fin = next(p for t, p in collected if t == "orchestration.stage.finished")
    assert stage_fin["coordinator_preview"] == "synthesis"
    assert stage_fin["ok_count"] == 0


# --- Coordinator end-to-end event emission --------------------------------


@pytest.mark.asyncio
async def test_coordinator_emits_nested_lifecycle_events():
    spec = OrchestrationSpec(
        name="demo",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="alpha")]),
            StageSpec(id="s2", spawn=[SpawnSpec(agent="w", task="beta")]),
        ],
    )
    agents = {"w": _agent("w")}
    rec = RecordingEmitter(flow_name="demo", run_id="r-1")
    runner = _EchoRunner()

    result = await run_coordinator(spec, agents, runner=runner, events=rec)

    # Every spawn echoed exactly once.
    assert result.last_stage and result.last_stage.stage_id == "s2"
    assert [c.task for c in runner.calls] == ["alpha", "beta"]

    types = rec.types()
    # flow.started → (stage.started s1 → spawn * → stage.finished s1) × 2 → flow.finished
    assert types[0] == "orchestration.flow.started"
    assert types[-1] == "orchestration.flow.finished"
    # Nesting: stage.started must appear before its spawn.started; stage.finished after spawn.finished.
    stage_starts = [i for i, t in enumerate(types) if t == "orchestration.stage.started"]
    spawn_starts = [i for i, t in enumerate(types) if t == "orchestration.spawn.started"]
    assert len(stage_starts) == 2
    assert len(spawn_starts) == 2
    for ss, sp in zip(stage_starts, spawn_starts, strict=True):
        assert ss < sp

    # The first flow.started must carry mode + agents list.
    flow_started = rec.of_type("orchestration.flow.started")[0]
    assert flow_started["mode"] == "coordinator"
    assert flow_started["agents"] == ["w"]
    assert flow_started["stage_count"] == 2

    flow_finished = rec.of_type("orchestration.flow.finished")[0]
    assert flow_finished["ok"] is True
    assert flow_finished["stages_run"] == 2


@pytest.mark.asyncio
async def test_coordinator_flow_finished_reflects_coordinator_error():
    spec = OrchestrationSpec(
        name="demo",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(id="coord", runs_on="w", prompt="synthesize", inputs=["*"]),
        ],
    )
    agents = {"w": _agent("w")}

    class _ErrRunner:
        async def __call__(self, req: SpawnRequest) -> SpawnOutput:
            return SpawnOutput(
                agent=req.agent.name, task=req.task, output="boom",
                is_error=True,
            )

    rec = RecordingEmitter(flow_name="demo", run_id="rx")
    await Coordinator(spec, agents, runner=_ErrRunner(), events=rec).run()
    flow_fin = rec.of_type("orchestration.flow.finished")[0]
    assert flow_fin["ok"] is False


@pytest.mark.asyncio
async def test_coordinator_without_events_is_zero_cost():
    """Sanity: running without ``events=`` must not raise or log anything."""
    spec = OrchestrationSpec(
        name="demo",
        agents=[AgentSpec(name="w")],
        stages=[StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="t")])],
    )
    agents = {"w": _agent("w")}
    await run_coordinator(spec, agents, runner=_EchoRunner())


# --- Swarm end-to-end event emission --------------------------------------


@dataclass
class _Action:
    kind: Literal["send", "idle", "done"]
    recipient: str = ""
    content: str = ""


@dataclass
class _ScriptedRunner:
    """Deterministic swarm peer that plays back a scripted action list."""

    scripts: dict[str, list[_Action]] = field(default_factory=dict)

    async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
        name = sctx.sender_name
        script = list(self.scripts.get(name, []))
        # Nudge the event loop so peers spawned together get a chance to
        # see each other's sends during the first tick.
        await asyncio.sleep(0)

        last_text = ""
        tool_calls = 0
        turn = 0
        for _t in range(sctx.max_turns):
            turn = _t
            if sctx.should_stop():
                break
            # Drain any inbox envelopes so the system records the causal
            # chain; we don't actually need the content for this test.
            await sctx.system.inboxes[name].drain()

            if not script:
                await asyncio.sleep(0)
                break
            action = script.pop(0)
            if action.kind == "send":
                target = sctx.lead_name if action.recipient == "main" else action.recipient
                await sctx.system.send(sender=name, recipient=target, content=action.content)
                tool_calls += 1
            elif action.kind == "idle":
                await asyncio.sleep(0)
            elif action.kind == "done":
                last_text = action.content
                break
        return SpawnOutput(
            agent=sctx.agent.name,
            task=sctx.initial_task or "(peer)",
            output=last_text or "(done)",
            turns=turn + 1,
            tool_calls=tool_calls,
        )


@pytest.mark.asyncio
async def test_swarm_emits_lifecycle_and_message_events():
    spec = OrchestrationSpec(
        name="swarm-demo",
        mode="swarm",
        lead="lead",
        agents=[AgentSpec(name="lead"), AgentSpec(name="peer")],
    )
    agents = {"lead": _agent("lead"), "peer": _agent("peer")}

    rec = RecordingEmitter(flow_name="swarm-demo", run_id="r-swarm")
    runner = _ScriptedRunner(scripts={
        "lead": [
            _Action("send", recipient="peer", content="hello peer"),
            _Action("idle"),
            _Action("idle"),
            _Action("done", content="final"),
        ],
        "peer": [
            _Action("send", recipient="main", content="hi back"),
            _Action("idle"),
            _Action("done", content="peer done"),
        ],
    })

    result = await run_swarm(
        spec, agents,
        user_task="do the thing",
        runner=runner,
        max_turns=8,
        events=rec,
    )

    assert result.lead == "lead"
    # swarm.started first, swarm.finished last.
    types = rec.types()
    assert types[0] == "orchestration.swarm.started"
    assert types[-1] == "orchestration.swarm.finished"

    started = rec.of_type("orchestration.swarm.started")[0]
    assert started["lead"] == "lead"
    assert started["peers"] == ["peer"]
    assert started["user_task_preview"] == "do the thing"

    # At least two message.sent events in between (lead→peer and peer→lead).
    msg_events = rec.of_type("orchestration.message.sent")
    assert len(msg_events) >= 2
    pairs = {(e["sender"], e["recipient"]) for e in msg_events}
    assert ("lead", "peer") in pairs
    assert ("peer", "lead") in pairs

    finished = rec.of_type("orchestration.swarm.finished")[0]
    assert finished["lead"] == "lead"
    assert finished["peer_count"] == 2
    assert finished["terminated_reason"] in {"lead-quiet", "walltime"}


@pytest.mark.asyncio
async def test_swarm_message_events_match_transcript_seq_order():
    """Every envelope in ``SwarmResult.transcript`` must have produced
    exactly one ``message.sent`` event with the same ``seq``.  This
    guarantees UIs that replay SSE see an identical ordering to
    ``GET /orchestration/flow`` transcripts."""
    spec = OrchestrationSpec(
        name="ord",
        mode="swarm",
        lead="a",
        agents=[AgentSpec(name="a"), AgentSpec(name="b")],
    )
    agents = {"a": _agent("a"), "b": _agent("b")}
    runner = _ScriptedRunner(scripts={
        "a": [
            _Action("send", recipient="b", content="m1"),
            _Action("send", recipient="b", content="m2"),
            _Action("idle"),
            _Action("done", content="end"),
        ],
        "b": [
            _Action("send", recipient="main", content="r1"),
            _Action("idle"),
            _Action("done", content="end"),
        ],
    })
    rec = RecordingEmitter(flow_name="ord", run_id="ord-1")
    result = await run_swarm(
        spec, agents,
        user_task="go",
        runner=runner, max_turns=6,
        events=rec,
    )
    transcript_seqs = [e.seq for e in result.transcript]
    event_seqs = [p["seq"] for p in rec.of_type("orchestration.message.sent")]
    # Same set, same order.
    assert transcript_seqs == event_seqs


# --- Bus wiring end-to-end -------------------------------------------------


@pytest.mark.asyncio
async def test_bus_emitter_round_trips_through_bus_subscribe():
    """Prove the Bus → SSE route wiring works: publishing one event and
    then subscribing should see it exactly once."""
    bus = Bus()
    em = BusOrchestrationEmitter(bus=bus, flow_name="b", run_id="rb")

    received: list[str] = []

    async def consumer():
        async for ev in bus.subscribe(bus_events.ORCHESTRATION_FLOW_STARTED):
            received.append(ev.properties["run_id"])
            return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)  # let the consumer register
    await em.flow_started(mode="coordinator", agents=[])
    await asyncio.wait_for(task, timeout=2.0)
    await bus.close()

    assert received == ["rb"]
