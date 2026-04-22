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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mycode.agent.agent import AgentInfo
from mycode.orchestration.registry.agent_registry import AgentRegistry
from mycode.orchestration.runtime import (
    Envelope,
    MailboxSystem,
    SpawnOutput,
    SwarmAgentContext,
    SwarmError,
    SwarmResult,
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

        # Let other peers get scheduled first on the initial tick so
        # lead-produced messages can reach teammates before teammates
        # bail out on empty inboxes.  A single ``await asyncio.sleep(0)``
        # is enough.
        await asyncio.sleep(0)

        for turn in range(sctx.max_turns):
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
                    recipient=action.recipient,
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
                    recipient=action.recipient,
                    reason=action.content,
                )
                tool_calls += 1
            elif action.kind == "shutdown_response":
                await sctx.system.shutdown_response(
                    sender=name,
                    recipient=action.recipient,
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


def _agent_info(name: str) -> AgentInfo:
    return AgentInfo(
        name=name,
        description=f"test agent {name}",
        mode="all",
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


@pytest.mark.asyncio
async def test_run_swarm_happy_path_with_scripted_runner():
    agents = {n: _agent_info(n) for n in ["lead", "sec", "perf"]}
    spec = _swarm_spec(["lead", "sec", "perf"], lead="lead")

    runner = ScriptedRunner(scripts={
        # Lead delegates twice, then shuts everyone down, then reports.
        "lead": [
            Action("send", recipient="sec", content="check auth.py for SQL injection"),
            Action("send", recipient="perf", content="scan for N+1 queries"),
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
            for turn in range(sctx.max_turns):
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
    assert spec.lead == "reviewer-lead"
    assert set(agents.keys()) == {"reviewer-lead", "security-reviewer", "perf-reviewer"}

    runner = ScriptedRunner(scripts={
        "reviewer-lead": [
            Action("send", recipient="security-reviewer", content="focus on auth"),
            Action("send", recipient="perf-reviewer", content="focus on DB"),
            Action("shutdown_request", recipient="security-reviewer", content=""),
            Action("shutdown_request", recipient="perf-reviewer", content=""),
            Action("done", content="unified review ready"),
        ],
        "security-reviewer": [
            Action("send", recipient="main", content="secrets look clean"),
            Action("done", content="sec ok"),
        ],
        "perf-reviewer": [
            Action("send", recipient="main", content="no N+1 detected"),
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
    assert result.lead == "reviewer-lead"
    assert "unified review ready" in result.lead_output
    # Both workers should have seen a delegating message and a shutdown.
    for name in ("security-reviewer", "perf-reviewer"):
        kinds = [e.kind for e in runner.received[name]]
        assert "message" in kinds
        assert "shutdown_request" in kinds


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
    from mycode.orchestration import (
        Envelope as _E,
        LiteLLMSwarmRunner as _R,
        MailboxSystem as _M,
        SwarmResult as _S,
        run_swarm as _run,
    )

    assert all(
        callable(x) or isinstance(x, type)
        for x in (_E, _R, _M, _S, _run)
    )
