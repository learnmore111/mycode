"""M6 — swarm runtime tests.

Validates mailbox routing, swarm orchestration, and the per-spawn
``send_message`` tool without ever touching litellm.

Strategy
--------

- :class:`MailboxSystem` is exercised directly by unit tests.  It has
  no LLM dependency, so we assert on ordering, broadcast fan-out, and
  event-log shape with plain ``pytest.mark.asyncio``.

- The full :func:`run_swarm` API is tested via a :class:`ScriptedRunner`
  implementing :class:`SwarmAgentRunner`.  Each peer is scripted with a
  list of ``Action`` objects (send message, broadcast, sleep, done)
  played out across "turns".  The runner drives the mailbox exactly as
  the real :class:`LiteLLMSwarmRunner` would, just without the LLM.

- ``pair-review.yaml`` — the flagship swarm flow — runs through the
  scripted runner end-to-end to exercise the loader → resolver →
  runtime pipeline that shipped throughout M1–M6.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from mycode.agent.agent import AgentInfo
from mycode.orchestration.registry.agent_registry import AgentRegistry
from mycode.orchestration.runtime import (
    Envelope,
    LiteLLMAgentRunner,
    LiteLLMSwarmRunner,
    MailboxSystem,
    SpawnOutput,
    SpawnRequest,
    SwarmAgentContext,
    SwarmError,
    SwarmResult,
    run_supervisor_collaboration,
    run_swarm,
)
from mycode.orchestration.runtime.swarm import SendMessageParams, _SendMessageTool
from mycode.orchestration.topology import load_file, resolve_all_agents
from mycode.orchestration.topology.schema import (
    AgentSpec,
    BackendSpec,
    OrchestrationSpec,
)
from mycode.tool.base import ToolContext

FLOWS = Path(__file__).resolve().parent.parent / "mycode" / "orchestration" / "flows"


# ---------------------------------------------------------------------------
# MailboxSystem unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mailbox_inprocess_send_and_drain():
    sys = MailboxSystem.inprocess(["a", "b"])
    env = await sys.send(sender="a", recipient="b", content="hi", summary="s")
    assert env.seq == 1
    assert env.sender == "a" and env.recipient == "b"

    # Drain b's inbox.
    drained = await sys.inboxes["b"].drain()
    assert [e.content for e in drained] == ["hi"]
    # A's inbox is empty.
    assert await sys.inboxes["a"].drain() == []
    # Event log carries one envelope.
    assert [e.kind for e in sys.event_log] == ["message"]


@pytest.mark.asyncio
async def test_mailbox_broadcast_fans_out_to_peers_only():
    sys = MailboxSystem.inprocess(["lead", "peer1", "peer2"])
    delivered = await sys.broadcast(sender="lead", content="all hands", summary="")
    assert {e.recipient for e in delivered} == {"peer1", "peer2"}
    # Sender should not receive their own broadcast.
    assert await sys.inboxes["lead"].drain() == []
    assert len(await sys.inboxes["peer1"].drain()) == 1
    assert len(await sys.inboxes["peer2"].drain()) == 1
    # Event log: 1 summary envelope (recipient="*") + 2 per-peer deliveries.
    kinds = [(e.kind, e.recipient) for e in sys.event_log]
    assert kinds == [
        ("broadcast", "*"),
        ("broadcast", "peer1"),
        ("broadcast", "peer2"),
    ]


@pytest.mark.asyncio
async def test_mailbox_send_unknown_recipient_raises_keyerror():
    sys = MailboxSystem.inprocess(["a", "b"])
    with pytest.raises(KeyError):
        await sys.send(sender="a", recipient="ghost", content="")


@pytest.mark.asyncio
async def test_mailbox_global_seq_is_monotonic():
    sys = MailboxSystem.inprocess(["a", "b", "c"])
    await sys.send(sender="a", recipient="b", content="1")
    await sys.broadcast(sender="b", content="2")
    await sys.send(sender="c", recipient="a", content="3")
    seqs = [e.seq for e in sys.event_log]
    assert seqs == sorted(seqs)  # strictly increasing


@pytest.mark.asyncio
async def test_mailbox_duplicate_owner_rejected():
    with pytest.raises(ValueError):
        MailboxSystem.inprocess(["a", "a"])


def test_envelope_format_for_llm_includes_header_and_body():
    env = Envelope(
        kind="message",
        sender="perf",
        recipient="lead",
        content="found hot loop",
        summary="hot loop in foo()",
        seq=1,
    )
    rendered = env.format_for_llm()
    assert "Message from `perf`" in rendered
    assert "hot loop in foo()" in rendered
    assert "found hot loop" in rendered


# ---------------------------------------------------------------------------
# send_message tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_tool_routes_to_main_alias():
    sys = MailboxSystem.inprocess(["lead", "worker"])
    tool = _SendMessageTool(sys, sender="worker", lead_name="lead")
    ctx = ToolContext(session_id="t", message_id="m", agent="worker")
    result = await tool.execute(
        {"type": "message", "recipient": "main", "content": "done"},
        ctx,
    )
    assert not result.is_error
    # The envelope landed in the lead's inbox, not a phantom "main" one.
    drained = await sys.inboxes["lead"].drain()
    assert [e.content for e in drained] == ["done"]
    assert await sys.inboxes["worker"].drain() == []


@pytest.mark.asyncio
async def test_send_message_tool_rejects_self_target_after_main_alias_resolution():
    sys = MailboxSystem.inprocess(["lead", "worker"])
    tool = _SendMessageTool(sys, sender="lead", lead_name="lead")
    ctx = ToolContext(session_id="t", message_id="m", agent="lead")
    result = await tool.execute(
        {"type": "message", "recipient": "main", "content": "loop"},
        ctx,
    )
    assert result.is_error
    assert "sending to yourself" in result.output
    assert await sys.inboxes["lead"].drain() == []


@pytest.mark.asyncio
async def test_send_message_tool_unknown_recipient_returns_tool_error():
    sys = MailboxSystem.inprocess(["lead", "worker"])
    tool = _SendMessageTool(sys, sender="worker", lead_name="lead")
    ctx = ToolContext(session_id="t", message_id="m", agent="worker")
    result = await tool.execute(
        {"type": "message", "recipient": "ghost", "content": "hi"},
        ctx,
    )
    assert result.is_error
    assert "unknown swarm recipient" in result.output


@pytest.mark.asyncio
async def test_send_message_tool_broadcast_reports_delivery_count():
    sys = MailboxSystem.inprocess(["lead", "p1", "p2"])
    tool = _SendMessageTool(sys, sender="lead", lead_name="lead")
    ctx = ToolContext(session_id="t", message_id="m", agent="lead")
    result = await tool.execute(
        {"type": "broadcast", "content": "start"},
        ctx,
    )
    assert not result.is_error
    assert "2 peer(s)" in result.output


@pytest.mark.asyncio
async def test_send_message_tool_shutdown_request_routes_as_special_kind():
    sys = MailboxSystem.inprocess(["lead", "worker"])
    tool = _SendMessageTool(sys, sender="lead", lead_name="lead")
    ctx = ToolContext(session_id="t", message_id="m", agent="lead")
    result = await tool.execute(
        {"type": "shutdown_request", "recipient": "worker", "content": "wrap up"},
        ctx,
    )
    assert not result.is_error
    drained = await sys.inboxes["worker"].drain()
    assert drained[0].kind == "shutdown_request"


@pytest.mark.asyncio
async def test_send_message_tool_params_pydantic_defaults():
    # Missing recipient + non-broadcast → error, not crash.
    sys = MailboxSystem.inprocess(["a", "b"])
    tool = _SendMessageTool(sys, sender="a", lead_name="a")
    ctx = ToolContext(session_id="t", message_id="m", agent="a")
    result = await tool.execute({"type": "message"}, ctx)
    assert result.is_error and "recipient is required" in result.output
    # Broadcast with empty params still works.
    ok = await tool.execute({"type": "broadcast"}, ctx)
    assert not ok.is_error


def test_send_message_params_schema_has_all_fields():
    schema = SendMessageParams.model_json_schema()
    assert set(schema["properties"]) == {
        "type", "recipient", "content", "summary", "approve",
    }


# ---------------------------------------------------------------------------
# ScriptedRunner — a fake SwarmAgentRunner
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """One scripted step for a swarm peer."""

    kind: str  # "send" | "broadcast" | "shutdown_request" | "shutdown_response" | "idle" | "done"
    recipient: str = ""
    content: str = ""
    approve: bool = True


@dataclass
class ScriptedRunner:
    """Deterministic :class:`SwarmAgentRunner`.

    Each peer has a script (list of :class:`Action`) consumed one item
    per "turn"; on ``done`` the peer exits with a success
    :class:`SpawnOutput` whose ``output`` is the content of the final
    assistant reply.  Receives everything from the inbox at the start
    of every turn (so tests can assert on what each peer saw).
    """

    scripts: dict[str, list[Action]] = field(default_factory=dict)
    received: dict[str, list[Envelope]] = field(default_factory=dict)

    async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
        name = sctx.sender_name
        script = list(self.scripts.get(name, []))
        self.received.setdefault(name, [])
        tool_calls = 0
        last_text = ""
        turn = 0
        # Remember if we ever saw a shutdown_request so we can exit
        # right after processing any trailing script actions (mirrors
        # the real runner's short-circuit).
        saw_shutdown = False

        def _resolve(target: str) -> str:
            # Mirror the real send_message tool: "main" → lead.
            return sctx.lead_name if target == "main" else target

        # Let other peers get scheduled first on the initial tick so
        # lead-produced messages can reach teammates before teammates
        # bail out on empty inboxes.  A single ``await asyncio.sleep(0)``
        # is enough.
        await asyncio.sleep(0)

        for _turn in range(sctx.max_turns):
            turn = _turn
            if sctx.should_stop():
                break

            envs = await sctx.system.inboxes[name].drain()
            self.received[name].extend(envs)
            for e in envs:
                if e.kind == "shutdown_request":
                    saw_shutdown = True

            if not script:
                # Nothing more to do from this peer's POV — either wait
                # for the lead to call shutdown (non-lead peers) or exit
                # (lead when its script ran out).
                if name == sctx.lead_name or saw_shutdown:
                    break
                await asyncio.sleep(0)
                # Give the lead more chances, but bail after the turn
                # budget so we never deadlock tests.
                continue

            action = script.pop(0)
            if action.kind == "send":
                await sctx.system.send(
                    sender=name,
                    recipient=_resolve(action.recipient),
                    content=action.content,
                )
                tool_calls += 1
            elif action.kind == "broadcast":
                await sctx.system.broadcast(
                    sender=name,
                    content=action.content,
                )
                tool_calls += 1
            elif action.kind == "shutdown_request":
                await sctx.system.shutdown_request(
                    sender=name,
                    recipient=_resolve(action.recipient),
                    reason=action.content,
                )
                tool_calls += 1
            elif action.kind == "shutdown_response":
                await sctx.system.shutdown_response(
                    sender=name,
                    recipient=_resolve(action.recipient),
                    approve=action.approve,
                    note=action.content,
                )
                tool_calls += 1
            elif action.kind == "idle":
                await asyncio.sleep(0)
            elif action.kind == "done":
                last_text = action.content or f"{name} done"
                break
            else:
                raise AssertionError(f"unknown action kind: {action.kind}")

        return SpawnOutput(
            agent=name,
            task=sctx.initial_task or "(peer)",
            output=last_text or f"{name} finished",
            is_error=False,
            turns=turn + 1,
            tool_calls=tool_calls,
        )


# ---------------------------------------------------------------------------
# run_swarm — integration
# ---------------------------------------------------------------------------


def _agent_info(name: str, tools: list[str] | None = None) -> AgentInfo:
    return AgentInfo(
        name=name,
        description=f"test agent {name}",
        mode="all",
        tools=tools,
        native=False,
        source="project",
    )


def _swarm_spec(names: list[str], lead: str) -> OrchestrationSpec:
    return OrchestrationSpec(
        name="fake-swarm",
        mode="swarm",
        lead=lead,
        agents=[AgentSpec(name=n) for n in names],
        backend=BackendSpec(prefer="inprocess"),
    )


def _hybrid_spec(names: list[str], coordinator: str) -> OrchestrationSpec:
    return OrchestrationSpec(
        name="fake-hybrid",
        mode="hybrid",
        coordinator=coordinator,
        agents=[AgentSpec(name=n) for n in names],
        backend=BackendSpec(prefer="inprocess"),
    )


@pytest.mark.asyncio
async def test_run_swarm_happy_path_with_scripted_runner():
    agents = {n: _agent_info(n) for n in ["lead", "sec", "perf"]}
    spec = _swarm_spec(["lead", "sec", "perf"], lead="lead")

    runner = ScriptedRunner(scripts={
        # Lead delegates twice, idles a few turns to let replies land,
        # then shuts everyone down, then reports.
        "lead": [
            Action("send", recipient="sec", content="check auth.py for SQL injection"),
            Action("send", recipient="perf", content="scan for N+1 queries"),
            Action("idle"),
            Action("idle"),
            Action("idle"),
            Action("shutdown_request", recipient="sec", content="wrap"),
            Action("shutdown_request", recipient="perf", content="wrap"),
            Action("done", content="Final report: 2 findings."),
        ],
        "sec": [
            Action("send", recipient="main", content="no injection found"),
            Action("done", content="sec done"),
        ],
        "perf": [
            Action("send", recipient="main", content="hot loop in user.py"),
            Action("done", content="perf done"),
        ],
    })

    result: SwarmResult = await run_swarm(
        spec, agents, user_task="review the codebase", runner=runner, max_turns=12,
    )

    # Each peer saw at least the delegating envelope from the lead.
    assert any("auth.py" in e.content for e in runner.received["sec"])
    assert any("N+1" in e.content for e in runner.received["perf"])
    # The lead received both replies (via "main" alias).
    lead_replies = [e.content for e in runner.received["lead"]]
    assert "no injection found" in lead_replies
    assert "hot loop in user.py" in lead_replies

    # Final lead output surfaces.
    assert "Final report" in result.lead_output
    assert result.lead == "lead"
    # Transcript holds everything the test cares about.
    kinds = [e.kind for e in result.transcript]
    assert "shutdown_request" in kinds
    assert kinds.count("message") >= 4  # 2 deliveries + 2 replies


@pytest.mark.asyncio
async def test_run_supervisor_collaboration_uses_coordinator_as_main():
    agents = {n: _agent_info(n) for n in ["supervisor", "sec", "perf"]}
    spec = _hybrid_spec(["supervisor", "sec", "perf"], coordinator="supervisor")

    runner = ScriptedRunner(scripts={
        "supervisor": [
            Action("send", recipient="sec", content="check auth boundaries"),
            Action("send", recipient="perf", content="check slow queries"),
            Action("idle"),
            Action("idle"),
            Action("done", content="Supervisor final: ship with two follow-ups."),
        ],
        "sec": [
            Action("send", recipient="main", content="auth boundary is okay"),
            Action("done", content="sec done"),
        ],
        "perf": [
            Action("send", recipient="main", content="query needs index"),
            Action("done", content="perf done"),
        ],
    })

    result = await run_supervisor_collaboration(
        spec,
        agents,
        user_task="coordinate this review",
        runner=runner,
        max_turns=10,
    )

    assert result.kind == "hybrid"
    assert result.lead == "supervisor"
    assert result.entry == "supervisor"
    assert "Supervisor final" in result.lead_output
    supervisor_replies = [e.content for e in runner.received["supervisor"]]
    assert "auth boundary is okay" in supervisor_replies
    assert "query needs index" in supervisor_replies
    assert any(e.sender == "supervisor" and e.recipient == "sec" for e in result.transcript)


@pytest.mark.asyncio
async def test_run_swarm_unknown_recipient_surfaces_via_tool_output():
    """A scripted send to a ghost recipient raises KeyError inside the
    mailbox; the scripted runner is deliberately simpler than the real
    one and propagates that, so we assert via ``pytest.raises``.  The
    *real* runner instead returns a ToolError to the LLM — that path is
    already covered by the send_message tool tests above."""
    agents = {n: _agent_info(n) for n in ["lead", "p"]}
    spec = _swarm_spec(["lead", "p"], lead="lead")
    runner = ScriptedRunner(scripts={
        "lead": [Action("send", recipient="ghost", content="oops")],
        "p": [Action("done", content="p idle")],
    })
    with pytest.raises(KeyError):
        await run_swarm(spec, agents, user_task="go", runner=runner, max_turns=4)


@pytest.mark.asyncio
async def test_run_swarm_rejects_non_swarm_mode():
    # Build a coordinator-mode spec on purpose.
    spec = OrchestrationSpec(name="c", mode="coordinator", agents=[AgentSpec(name="a")])
    with pytest.raises(SwarmError):
        await run_swarm(
            spec,
            {"a": _agent_info("a")},
            user_task="t",
            runner=ScriptedRunner(),
        )


@pytest.mark.asyncio
async def test_run_swarm_rejects_single_agent():
    spec = _swarm_spec(["solo"], lead="solo")
    with pytest.raises(SwarmError):
        await run_swarm(
            spec,
            {"solo": _agent_info("solo")},
            user_task="t",
            runner=ScriptedRunner(),
        )


@pytest.mark.asyncio
async def test_run_swarm_rejects_missing_lead_in_agents():
    spec = _swarm_spec(["a", "b"], lead="nonexistent")
    # Topology validation would catch this earlier, but the runtime also
    # defends itself — tests the defensive check directly.
    spec.lead = "nonexistent"
    with pytest.raises(SwarmError):
        await run_swarm(
            spec,
            {"a": _agent_info("a"), "b": _agent_info("b")},
            user_task="t",
            runner=ScriptedRunner(),
        )


@pytest.mark.asyncio
async def test_run_swarm_walltime_deadline_terminates_run(monkeypatch):
    """A pathologically idle swarm should still terminate via walltime."""
    agents = {n: _agent_info(n) for n in ["lead", "p"]}
    spec = _swarm_spec(["lead", "p"], lead="lead")

    class ForeverRunner:
        """Peer that never exits on its own — just idles every turn."""

        async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
            turn = 0
            for _turn in range(sctx.max_turns):
                turn = _turn
                if sctx.should_stop():
                    break
                await sctx.system.inboxes[sctx.sender_name].drain()
                await asyncio.sleep(0)
            return SpawnOutput(
                agent=sctx.sender_name,
                task="",
                output=f"idled {turn + 1} turns",
                is_error=False,
                turns=turn + 1,
            )

    result = await run_swarm(
        spec,
        agents,
        user_task="go",
        runner=ForeverRunner(),
        max_turns=50,
        walltime_seconds=0.01,  # effectively immediate
    )
    # Either walltime or lead-quiet is acceptable — the test's point is
    # that we don't hang.
    assert result.terminated_reason in ("walltime", "lead-quiet")


@pytest.mark.asyncio
async def test_run_swarm_pair_review_flow_end_to_end(tmp_path):
    """Load the shipped ``pair-review.yaml`` and drive it with scripted peers."""
    # Use a temp project dir so the registry only exposes built-in agents.
    reg = AgentRegistry(project_dir=str(tmp_path))
    spec = load_file(str(FLOWS / "pair-review.yaml"))
    agents = resolve_all_agents(spec.agents, reg)

    assert spec.mode == "swarm"
    assert spec.lead == "reviewer-starter"
    assert set(agents.keys()) == {"reviewer-starter", "security-reviewer", "perf-reviewer"}

    runner = ScriptedRunner(scripts={
        "reviewer-starter": [
            Action("send", recipient="security-reviewer", content="focus on auth"),
            Action("send", recipient="perf-reviewer", content="focus on DB"),
            Action("idle"),
            Action("idle"),
            Action("send", recipient="security-reviewer", content="cross-check perf assumptions with perf-reviewer"),
            Action("shutdown_request", recipient="security-reviewer", content=""),
            Action("shutdown_request", recipient="perf-reviewer", content=""),
            Action("done", content="unified review ready"),
        ],
        "security-reviewer": [
            Action("send", recipient="main", content="secrets look clean"),
            Action("send", recipient="perf-reviewer", content="auth boundary looks strict; does caching change threat exposure?"),
            Action("idle"),
            Action("idle"),
            Action("idle"),
            Action("done", content="sec ok"),
        ],
        "perf-reviewer": [
            Action("send", recipient="security-reviewer", content="cache layer is read-only; no extra auth bypass visible"),
            Action("send", recipient="main", content="no N+1 detected"),
            Action("idle"),
            Action("idle"),
            Action("idle"),
            Action("done", content="perf ok"),
        ],
    })

    result = await run_swarm(
        spec,
        agents,
        user_task="please review the codebase",
        runner=runner,
        max_turns=10,
    )

    assert result.flow_name == "pair-review"
    assert result.lead == "reviewer-starter"
    assert "unified review ready" in result.lead_output
    # Both workers should have seen a delegating message and a shutdown.
    for name in ("security-reviewer", "perf-reviewer"):
        kinds = [e.kind for e in runner.received[name]]
        assert "message" in kinds
        assert "shutdown_request" in kinds
    # The built-in demo should model direct peer-to-peer collaboration,
    # not just starter→reviewer dispatch and reviewer→starter replies.
    assert any(e.sender == "security-reviewer" and e.recipient == "perf-reviewer" for e in result.transcript)
    assert any(e.sender == "perf-reviewer" and e.recipient == "security-reviewer" for e in result.transcript)


@pytest.mark.asyncio
async def test_run_swarm_should_stop_cuts_peers_early():
    """The shared ``should_stop`` callback must halt every peer cleanly."""
    agents = {n: _agent_info(n) for n in ["lead", "p"]}
    spec = _swarm_spec(["lead", "p"], lead="lead")

    stop_flag = {"v": False}

    class WatchingRunner:
        async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
            if sctx.sender_name == "lead":
                # Lead flips the flag on the first tick.
                stop_flag["v"] = True
            # Everyone promptly stops.
            for _ in range(sctx.max_turns):
                if stop_flag["v"] or sctx.should_stop():
                    break
                await asyncio.sleep(0)
            return SpawnOutput(agent=sctx.sender_name, task="", output="stopped")

    # We override ``should_stop`` by spying through run_swarm's own logic.
    # Simplest way: use a very small walltime so should_stop flips.
    result = await run_swarm(
        spec,
        agents,
        user_task="",
        runner=WatchingRunner(),
        max_turns=50,
        walltime_seconds=0.01,
    )
    # Every peer returned cleanly (no error).
    assert all(not p.is_error for p in result.peers.values())


@pytest.mark.asyncio
async def test_run_swarm_transcript_orders_shutdown_after_messages():
    agents = {n: _agent_info(n) for n in ["l", "a", "b"]}
    spec = _swarm_spec(["l", "a", "b"], lead="l")
    runner = ScriptedRunner(scripts={
        "l": [
            Action("broadcast", content="start"),
            Action("shutdown_request", recipient="a"),
            Action("shutdown_request", recipient="b"),
            Action("done", content="all done"),
        ],
        "a": [Action("done", content="a out")],
        "b": [Action("done", content="b out")],
    })
    result = await run_swarm(spec, agents, user_task="go", runner=runner, max_turns=8)
    # In the transcript, the broadcast's summary envelope must come
    # before both shutdown_request envelopes.
    first_bcast = next(i for i, e in enumerate(result.transcript) if e.kind == "broadcast" and e.recipient == "*")
    shutdowns = [i for i, e in enumerate(result.transcript) if e.kind == "shutdown_request"]
    assert shutdowns and all(s > first_bcast for s in shutdowns)


# ---------------------------------------------------------------------------
# Sanity: helper types reachable from the package root
# ---------------------------------------------------------------------------


def test_swarm_types_reexported_at_package_root():
    from mycode import orchestration as orchmod

    assert orchmod.Envelope is Envelope
    assert orchmod.MailboxSystem is MailboxSystem
    assert orchmod.SwarmResult is SwarmResult
    assert orchmod.run_swarm is run_swarm
    assert callable(orchmod.LiteLLMSwarmRunner)


@pytest.mark.asyncio
async def test_litellm_swarm_runner_registers_builtin_tools_before_schema(monkeypatch):
    """Regression: orchestration can run in a fresh process before the
    normal prompt path registers built-in tools. Swarm peers should still
    expose their declared file tools plus the runtime send_message tool."""

    from mycode.session.llm import FinishEvent
    from mycode.tool import registry as tool_registry

    seen_tools: list[str] = []

    async def _fake_stream(stream_input):
        seen_tools.extend(t["function"]["name"] for t in stream_input.tools or [])
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=True),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    tool_registry.clear()
    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)

    system = MailboxSystem.inprocess(["lead", "peer"])
    runner = LiteLLMSwarmRunner(idle_poll_seconds=0.0, max_idle_polls=0)
    result = await runner(
        SwarmAgentContext(
            agent=_agent_info("peer", tools=["read", "grep", "glob", "send_message"]),
            sender_name="peer",
            system=system,
            lead_name="lead",
            initial_task="review src/auth",
            max_turns=1,
            should_stop=lambda: False,
        ),
    )

    assert result.turns == 1
    assert {"read", "grep", "glob", "send_message"}.issubset(seen_tools)


@pytest.mark.asyncio
async def test_litellm_agent_runner_registers_builtin_tools_before_schema(monkeypatch):
    """Non-swarm orchestration spawns need the same bootstrap as swarm
    peers when the registry starts empty."""

    from mycode.session.llm import FinishEvent
    from mycode.tool import registry as tool_registry

    seen_tools: list[str] = []

    async def _fake_stream(stream_input):
        seen_tools.extend(t["function"]["name"] for t in stream_input.tools or [])
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=True),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    tool_registry.clear()
    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)

    runner = LiteLLMAgentRunner(max_turns=1)
    result = await runner(
        SpawnRequest(
            agent=_agent_info("reviewer", tools=["read", "grep", "glob"]),
            task="review src/auth",
        )
    )

    assert result.turns == 1
    assert {"read", "grep", "glob"}.issubset(seen_tools)


@pytest.mark.asyncio
async def test_litellm_swarm_runner_exits_when_mailbox_goes_quiet(monkeypatch):
    """A peer that has already seen one message should not keep taking
    empty turns forever just because its conversation history is non-empty."""

    stream_calls: list[int] = []

    async def _fake_stream(_input):
        stream_calls.append(1)
        if False:
            yield  # pragma: no cover
        from mycode.session.llm import FinishEvent
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=False),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    system = MailboxSystem.inprocess(["lead", "peer"])
    await system.send(sender="lead", recipient="peer", content="please review auth.py")

    runner = LiteLLMSwarmRunner(idle_poll_seconds=0.0, max_idle_polls=0)
    result = await runner(
        SwarmAgentContext(
            agent=_agent_info("peer"),
            sender_name="peer",
            system=system,
            lead_name="lead",
            initial_task=None,
            max_turns=8,
            should_stop=lambda: False,
        ),
    )

    assert len(stream_calls) == 1
    assert result.tool_calls == 0
    assert result.turns == 1


@pytest.mark.asyncio
async def test_litellm_swarm_runner_reminds_lead_to_delegate(monkeypatch):
    """The entry peer should be nudged to use ``send_message`` before
    it can quietly continue solo."""

    from mycode.session.llm import FinishEvent, ToolCallDelta

    stream_inputs: list[list[dict[str, object]]] = []

    async def _fake_stream(stream_input):
        stream_inputs.append(stream_input.messages)
        call_no = len(stream_inputs)
        if call_no == 1:
            yield FinishEvent(reason="stop", usage={}, cost=0.0)
            return
        if call_no == 2:
            reminder = str(stream_input.messages[-1]["content"])
            assert "send_message" in reminder
            yield ToolCallDelta(
                tool_call_id="call-1",
                tool_name="send_message",
                args='{"type":"message","recipient":"peer","content":"please review auth.py"}',
            )
            yield FinishEvent(reason="tool-calls", usage={}, cost=0.0)
            return
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=True),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    system = MailboxSystem.inprocess(["lead", "peer"])
    runner = LiteLLMSwarmRunner(idle_poll_seconds=0.0, max_idle_polls=0)
    result = await runner(
        SwarmAgentContext(
            agent=_agent_info("lead"),
            sender_name="lead",
            system=system,
            lead_name="lead",
            initial_task="review src/auth",
            max_turns=6,
            should_stop=lambda: False,
        ),
    )

    delivered = await system.inboxes["peer"].drain()
    assert [env.content for env in delivered] == ["please review auth.py"]
    assert result.turns == 3
    assert result.tool_calls == 1


@pytest.mark.asyncio
async def test_litellm_swarm_runner_waits_for_first_message_before_idling_out(monkeypatch):
    """Non-entry peers should stay alive until they either receive work or
    the global stop condition fires."""

    stream_calls: list[int] = []

    async def _fake_stream(_stream_input):
        stream_calls.append(1)
        from mycode.session.llm import FinishEvent
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=False),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    system = MailboxSystem.inprocess(["lead", "peer"])
    runner = LiteLLMSwarmRunner(idle_poll_seconds=0.0, max_idle_polls=0)

    async def delayed_send():
        await asyncio.sleep(0)
        await system.send(sender="lead", recipient="peer", content="please inspect auth.py")

    send_task = asyncio.create_task(delayed_send())
    result = await runner(
        SwarmAgentContext(
            agent=_agent_info("peer"),
            sender_name="peer",
            system=system,
            lead_name="lead",
            initial_task=None,
            max_turns=8,
            should_stop=lambda: False,
        ),
    )
    await send_task

    assert len(stream_calls) == 1
    assert result.turns == 1


@pytest.mark.asyncio
async def test_litellm_swarm_runner_does_not_spend_turn_budget_waiting_for_first_message(monkeypatch):
    """Regression: peers used to exhaust ``max_turns`` while polling an
    empty inbox, so a later delegation was recorded as received but never
    processed."""

    stream_calls: list[int] = []

    async def _fake_stream(_stream_input):
        stream_calls.append(1)
        from mycode.session.llm import FinishEvent
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=False),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    system = MailboxSystem.inprocess(["lead", "peer"])
    runner = LiteLLMSwarmRunner(idle_poll_seconds=0.001, max_idle_polls=0)
    entry_done = {"value": False}

    async def delayed_send():
        await asyncio.sleep(0.02)
        await system.send(sender="lead", recipient="peer", content="late but valid delegation")

    send_task = asyncio.create_task(delayed_send())
    result = await runner(
        SwarmAgentContext(
            agent=_agent_info("peer"),
            sender_name="peer",
            system=system,
            lead_name="lead",
            initial_task=None,
            max_turns=1,
            should_stop=lambda: False,
            entry_peer_done=lambda: entry_done["value"],
        ),
    )
    await send_task

    assert len(stream_calls) == 1
    assert result.turns == 1


@pytest.mark.asyncio
async def test_run_swarm_late_delegation_reaches_live_peer(monkeypatch):
    """The full runtime should keep non-entry peers alive while the entry
    agent spends several LLM turns before sending its first delegation."""

    from mycode.session.llm import FinishEvent, ToolCallDelta

    stream_calls: dict[str, int] = {}
    lead_sent = {"value": False}

    async def _fake_stream(stream_input):
        agent_name = "peer" if any("late delegation" in str(m.get("content", "")) for m in stream_input.messages) else "lead"
        stream_calls[agent_name] = stream_calls.get(agent_name, 0) + 1
        lead_call_no = stream_calls.get("lead", 0)
        if agent_name == "lead" and lead_call_no < 4:
            yield FinishEvent(reason="stop", usage={}, cost=0.0)
            return
        if agent_name == "lead" and not lead_sent["value"]:
            lead_sent["value"] = True
            yield ToolCallDelta(
                tool_call_id="call-1",
                tool_name="send_message",
                args='{"type":"message","recipient":"peer","content":"late delegation"}',
            )
            yield FinishEvent(reason="tool-calls", usage={}, cost=0.0)
            return
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=True),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    agents = {
        "lead": AgentInfo(name="lead", mode="all", tools=["send_message"]),
        "peer": AgentInfo(name="peer", mode="all", tools=["send_message"]),
    }
    spec = _swarm_spec(["lead", "peer"], lead="lead")
    result = await run_swarm(
        spec,
        agents,
        user_task="review after several lead turns",
        max_turns=6,
        walltime_seconds=5.0,
        runner=LiteLLMSwarmRunner(idle_poll_seconds=0.001, max_idle_polls=0),
    )

    assert result.peers["lead"].tool_calls == 1
    assert result.peers["peer"].turns == 1
    assert any(env.sender == "lead" and env.recipient == "peer" for env in result.transcript)


@pytest.mark.asyncio
async def test_entry_peer_waits_until_other_peers_finish(monkeypatch):
    """The entry peer should not quietly exit while teammates are still running."""

    async def _fake_default_model():
        return ("test", "dummy")

    async def _fake_get_model(_provider_id, _model_id):
        return SimpleNamespace(
            capabilities=SimpleNamespace(toolcall=False),
            api=SimpleNamespace(url=None),
        )

    async def _fake_get_api_key(_provider_id):
        return None

    async def _fake_stream(_stream_input):
        from mycode.session.llm import FinishEvent
        yield FinishEvent(reason="stop", usage={}, cost=0.0)

    monkeypatch.setattr("mycode.provider.provider.default_model", _fake_default_model)
    monkeypatch.setattr("mycode.provider.provider.get_model", _fake_get_model)
    monkeypatch.setattr("mycode.provider.provider.get_api_key", _fake_get_api_key)
    monkeypatch.setattr("mycode.session.llm.stream", _fake_stream)
    monkeypatch.setattr("mycode.tool.registry.to_llm_tools", lambda: [])

    lead_runner = LiteLLMSwarmRunner(idle_poll_seconds=0.0, max_idle_polls=0)

    class CompositeRunner:
        async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
            if sctx.sender_name == sctx.lead_name:
                return await lead_runner(sctx)
            await asyncio.sleep(0)
            await sctx.system.send(sender=sctx.sender_name, recipient=sctx.lead_name, content="peer finished review")
            return SpawnOutput(
                agent=sctx.sender_name,
                task="",
                output="peer done",
                is_error=False,
                turns=1,
            )

    agents = {n: _agent_info(n) for n in ["lead", "peer"]}
    spec = _swarm_spec(["lead", "peer"], lead="lead")
    result = await run_swarm(
        spec,
        agents,
        user_task="",
        runner=CompositeRunner(),
        max_turns=8,
    )

    assert result.peers["lead"].turns == 1
    assert any(env.sender == "peer" and env.recipient == "lead" for env in result.transcript)
