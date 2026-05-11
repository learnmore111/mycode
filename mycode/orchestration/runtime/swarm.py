"""Swarm runtime: mailbox-driven peer agents.

A swarm execution is the peer-to-peer, message-oriented counterpart to
coordinator mode. There is no pre-declared DAG and no central controller;
instead one agent — the **entry agent** — is seeded with the user task and
every agent can send direct or broadcast messages to other peers via the
``send_message`` tool.

``entry`` vs ``lead``
=====================

Historically this module called the entry agent ``lead``. The name is
retained as a backward-compatible alias (``spec.lead`` / ``SwarmResult.lead``
/ ``SwarmAgentContext.lead_name`` / ``'main'`` recipient alias), but
semantically the role is closer to ``default_active_agent`` in LangGraph
Swarm or ``initial_agent`` in OpenAI Agents SDK: just the **initial task
receiver**, not a central coordinator. The entry agent is optional; when
a spec omits it, the runtime uses the first declared agent.

Execution model
===============

1. :func:`run_swarm` builds a :class:`MailboxSystem` with one inbox per
   agent, seeds the entry agent's inbox with the user's task, then spawns
   one :class:`asyncio.Task` per peer running :class:`LiteLLMSwarmRunner`
   (or any :class:`SwarmAgentRunner` injected by a test).
2. Each runner loops:
   - ``drain`` its mailbox for any new envelopes;
   - if any are shutdown-responses the entry agent has gathered enough
     acknowledgements, the runner exits;
   - otherwise append envelopes as ``user`` messages into the local
     conversation and take **one** LLM turn;
   - tool calls are executed in-line: ``send_message`` routes through
     the mailbox, every other tool goes through the normal registry.
3. The swarm terminates when:
   a. the entry agent returns without any pending tool call *and* its
      mailbox is empty (peaceful quiescence), or
   b. any peer sends a ``shutdown_request`` that is accepted by all
      teammates (graceful drain), or
   c. the global turn budget or wall-clock limit is reached (safety).

Design notes
============

- **``SwarmAgentRunner`` is a Protocol** mirroring
  :class:`AgentRunner`. Tests inject a deterministic fake that
  produces a scripted sequence of messages without ever touching
  litellm.
- **Send-message is per-swarm**: we do *not* register a global
  ``send_message`` tool; the runner is handed a :class:`_SendMessageTool`
  bound to the mailbox system and the sender's name. This keeps the
  tool registry free of stateful singletons and lets two concurrent
  swarm runs coexist without cross-talk.
- **Termination fairness**: any peer can request shutdown; teammates
  can refuse by responding ``approve=False``. This mirrors how real
  collaborators negotiate endings.
- **No shared state** between peers other than the mailbox log — each
  agent has its own conversation buffer and its own ``ToolContext``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from mycode.orchestration.runtime.context import SpawnOutput
from mycode.orchestration.runtime.mailbox import Envelope, MailboxSystem
from mycode.orchestration.runtime.spawn import DEFAULT_MAX_TURNS
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from mycode.agent.agent import AgentInfo
    from mycode.orchestration.runtime.events import OrchestrationEventEmitter
    from mycode.orchestration.topology.schema import OrchestrationSpec


class SwarmError(RuntimeError):
    """Raised when a swarm run cannot start (unknown entry agent, no agents)."""


# ---------------------------------------------------------------------------
# send_message tool — bound to one swarm session
# ---------------------------------------------------------------------------


class SendMessageParams(BaseModel):
    """Parameters for the per-swarm ``send_message`` tool."""

    type: str = Field(
        default="message",
        description="message | broadcast | shutdown_request | shutdown_response",
    )
    recipient: str = Field(
        default="",
        description="Target agent name. Use 'main' to address the swarm entry "
        "agent. Required for message / shutdown_* kinds.",
    )
    content: str = Field(default="", description="Message body.")
    summary: str = Field(
        default="",
        description="5–10 word gist used by UIs / transcripts.",
    )
    approve: bool = Field(
        default=True,
        description="For shutdown_response only: whether to approve the shutdown.",
    )


class _SendMessageTool(CallableTool[SendMessageParams]):
    """A :class:`CallableTool` bound to one swarm's mailbox.

    The bound variant is instantiated once per swarm peer so that the
    ``sender`` is implicit (agents never lie about who they are) and
    the tool never appears in the global registry — it only exists for
    the duration of the swarm run.
    """

    id = "send_message"
    description = (
        "Send a message to a swarm teammate.  'recipient' is another "
        "agent's name, or 'main' for the swarm entry agent.  Use "
        "type='broadcast' to fan-out to every teammate.  Use "
        "type='shutdown_request' to propose ending the collaboration."
    )

    def __init__(self, system: MailboxSystem, sender: str, lead_name: str) -> None:
        self._system = system
        self._sender = sender
        self._lead_name = lead_name

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def _resolve_recipient(self, name: str) -> str:
        if name == "main":
            return self._lead_name
        return name

    async def call(self, params: SendMessageParams, ctx: ToolContext) -> ToolResult:
        kind = params.type.lower().strip()
        try:
            if kind == "broadcast":
                envs = await self._system.broadcast(
                    sender=self._sender,
                    content=params.content,
                    summary=params.summary,
                )
                return ToolOk(
                    f"Broadcast delivered to {len(envs)} peer(s).",
                    title="send_message (broadcast)",
                    metadata={"delivered": [e.recipient for e in envs]},
                )

            if not params.recipient:
                return ToolError(
                    "recipient is required for type!=broadcast",
                    title="send_message",
                )
            recipient = self._resolve_recipient(params.recipient)

            if kind == "message":
                env = await self._system.send(
                    sender=self._sender,
                    recipient=recipient,
                    content=params.content,
                    summary=params.summary,
                )
                return ToolOk(
                    f"Message delivered to {recipient!r} (seq={env.seq}).",
                    title="send_message",
                    metadata={"recipient": recipient, "seq": env.seq},
                )

            if kind == "shutdown_request":
                env = await self._system.shutdown_request(
                    sender=self._sender,
                    recipient=recipient,
                    reason=params.content,
                )
                return ToolOk(
                    f"Shutdown requested of {recipient!r} (seq={env.seq}).",
                    title="send_message (shutdown_request)",
                    metadata={"recipient": recipient, "seq": env.seq},
                )

            if kind == "shutdown_response":
                env = await self._system.shutdown_response(
                    sender=self._sender,
                    recipient=recipient,
                    approve=params.approve,
                    note=params.content,
                )
                return ToolOk(
                    f"Shutdown {'approved' if params.approve else 'declined'} "
                    f"for {recipient!r} (seq={env.seq}).",
                    title="send_message (shutdown_response)",
                    metadata={"recipient": recipient, "seq": env.seq, "approve": params.approve},
                )

            return ToolError(
                f"Unknown type={params.type!r}; "
                f"expected message|broadcast|shutdown_request|shutdown_response",
                title="send_message",
            )
        except KeyError as exc:
            return ToolError(str(exc), title="send_message")


# ---------------------------------------------------------------------------
# SwarmAgentRunner — per-peer loop contract
# ---------------------------------------------------------------------------


@dataclass
class SwarmAgentContext:
    """Input passed to a :class:`SwarmAgentRunner`."""

    agent: AgentInfo
    sender_name: str  # == agent.name; kept distinct for readability
    system: MailboxSystem
    # Name of the swarm entry agent (the initial task receiver).  Kept as
    # ``lead_name`` for backwards compatibility with existing fakes/tests;
    # new code should read ``entry_name`` which aliases the same field.
    lead_name: str
    initial_task: str | None  # only the entry agent gets a seed task
    max_turns: int
    # Called by the runner after every turn so the orchestrator can
    # decide whether the global budget is exhausted.  Returning True
    # tells the runner to stop cleanly.
    should_stop: Callable[[], bool]
    events: OrchestrationEventEmitter | None = None

    @property
    def entry_name(self) -> str:
        """Alias for :attr:`lead_name` — the swarm entry agent's name."""
        return self.lead_name


class SwarmAgentRunner(Protocol):
    """Per-peer async loop.  Returns the :class:`SpawnOutput` summarising
    everything this peer produced during the run."""

    async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput: ...


# ---------------------------------------------------------------------------
# Default litellm-backed swarm runner
# ---------------------------------------------------------------------------


# Tools the swarm layer always strips (coordinator-only or interactive).
_EXCLUDED_SWARM_TOOLS = frozenset({
    "subagent", "todo", "question", "batch",
})


class LiteLLMSwarmRunner:
    """Reference swarm-peer runner, backed by litellm.

    Each invocation drives **one agent** for up to ``max_turns`` turns.
    Between turns the runner drains its inbox; delivered envelopes are
    appended as ``user`` messages so the model naturally sees "who
    said what".  A receive-then-quiet loop (no pending messages **and**
    no outgoing tool call) lets the peer idle without wasting tokens.
    """

    def __init__(
        self,
        *,
        session_id: str = "orchestration",
        message_id: str = "orchestration",
        idle_poll_seconds: float = 0.05,
        max_idle_polls: int = 40,  # ≈2s of idle waiting before a peer exits
    ) -> None:
        self._session_id = session_id
        self._message_id = message_id
        self._idle_poll_seconds = idle_poll_seconds
        self._max_idle_polls = max_idle_polls

    async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
        from mycode.provider import provider as providermod
        from mycode.session import llm as llmmod
        from mycode.session.system import build as build_system
        from mycode.tool import registry as tool_registry
        from mycode.util.subagent import build_agent_ruleset, check_tool_permission

        agent = sctx.agent
        try:
            provider_id, model_id = await providermod.default_model()
            model = await providermod.get_model(provider_id, model_id)
            api_key = await providermod.get_api_key(provider_id)
        except Exception as exc:  # pragma: no cover
            return SpawnOutput(
                agent=agent.name,
                task=sctx.initial_task or "(swarm peer)",
                output=f"Model resolution failed: {exc}",
                is_error=True,
            )

        system_prompt = build_system(agent_prompt=agent.prompt)
        agent_ruleset = build_agent_ruleset(agent)

        # Build the per-peer tool allow-list and inject the bound send_message.
        all_tools = tool_registry.to_llm_tools()
        if agent.tools is not None:
            allowed = set(agent.tools)
            llm_tools = [t for t in all_tools if t["function"]["name"] in allowed]
        else:
            llm_tools = [t for t in all_tools if t["function"]["name"] not in _EXCLUDED_SWARM_TOOLS]

        send_tool = _SendMessageTool(sctx.system, sctx.sender_name, sctx.lead_name)
        if agent.tools is None or "send_message" in set(agent.tools):
            llm_tools.append(send_tool.to_llm_tool())

        ctx = ToolContext(
            session_id=self._session_id,
            message_id=self._message_id,
            agent=agent.name,
        )

        messages: list[dict[str, Any]] = []
        if sctx.initial_task:
            messages.append({"role": "user", "content": sctx.initial_task})
            if sctx.events is not None:
                await sctx.events.agent_message(
                    stage_id=None,
                    spawn_index=None,
                    agent=agent.name,
                    role="user",
                    kind="task",
                    content=sctx.initial_task,
                    turn=0,
                )

        output_parts: list[str] = []
        total_tool_calls = 0
        last_text = ""
        turn = 0
        idle_polls = 0
        awaiting_tool_followup = False

        effective_max_turns = min(
            sctx.max_turns,
            agent.max_turns or sctx.max_turns,
        )

        for turn in range(effective_max_turns):
            if sctx.should_stop():
                break

            # 1) Drain the inbox; shutdown requests short-circuit the loop.
            envs = await sctx.system.inboxes[sctx.sender_name].drain()
            received_shutdown = False
            for env in envs:
                messages.append({"role": "user", "content": env.format_for_llm()})
                if env.kind == "shutdown_request":
                    received_shutdown = True
                if sctx.events is not None:
                    await sctx.events.agent_message(
                        stage_id=None,
                        spawn_index=None,
                        agent=agent.name,
                        role="user",
                        kind=env.kind,
                        recipient=env.sender,
                        content=env.format_for_llm(),
                        turn=turn + 1,
                    )

            # After a peer has already seen earlier messages, its history
            # remains non-empty forever. We only want another LLM turn when
            # there is *fresh* inbox input, a seed task, or a pending
            # follow-up after tool execution.
            has_seed_input = turn == 0 and bool(sctx.initial_task)
            should_take_turn = bool(envs) or has_seed_input or awaiting_tool_followup
            if not should_take_turn:
                if idle_polls >= self._max_idle_polls:
                    break
                idle_polls += 1
                await asyncio.sleep(self._idle_poll_seconds)
                continue
            idle_polls = 0
            awaiting_tool_followup = False

            # 2) Take one LLM turn.
            stream_input = llmmod.StreamInput(
                model=model,
                messages=messages,
                system=system_prompt,
                tools=llm_tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                api_key=api_key,
                api_base=model.api.url or None,
            )

            text_parts: list[str] = []
            pending: list[llmmod.ToolCallDelta] = []
            finish = "stop"
            async for event in llmmod.stream(stream_input):
                if isinstance(event, llmmod.TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, llmmod.ToolCallDelta):
                    pending.append(event)
                elif isinstance(event, llmmod.FinishEvent):
                    finish = event.reason
                elif isinstance(event, llmmod.ErrorEvent):
                    return SpawnOutput(
                        agent=agent.name,
                        task=sctx.initial_task or "(swarm peer)",
                        output="".join(output_parts) + f"\n\nError: {event.error}",
                        is_error=True,
                        turns=turn + 1,
                        tool_calls=total_tool_calls,
                    )

            assistant_text = "".join(text_parts)
            if assistant_text:
                output_parts.append(assistant_text)
                last_text = assistant_text
                if sctx.events is not None:
                    await sctx.events.agent_message(
                        stage_id=None,
                        spawn_index=None,
                        agent=agent.name,
                        role="assistant",
                        content=assistant_text,
                        turn=turn + 1,
                    )

            # 3) Execute tool calls (incl. send_message).
            if pending and finish == "tool-calls":
                messages.append({
                    "role": "assistant",
                    "content": assistant_text or None,
                    "tool_calls": [
                        {
                            "id": tc.tool_call_id,
                            "type": "function",
                            "function": {"name": tc.tool_name, "arguments": tc.args},
                        }
                        for tc in pending
                    ],
                })

                for tc in pending:
                    total_tool_calls += 1
                    if tc.tool_name == "send_message":
                        impl: Any = send_tool
                    else:
                        perm_error = check_tool_permission(tc.tool_name, agent_ruleset)
                        if perm_error:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.tool_call_id,
                                "content": perm_error,
                            })
                            continue
                        impl = tool_registry.get(tc.tool_name)
                        if impl is None:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.tool_call_id,
                                "content": f"Unknown tool: {tc.tool_name}",
                            })
                            continue

                    try:
                        args = json.loads(tc.args) if tc.args and tc.args.strip() else {}
                    except json.JSONDecodeError as exc:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.tool_call_id,
                            "content": f"Invalid JSON arguments: {exc}",
                        })
                        continue

                    try:
                        result = await impl.execute(args, ctx)
                        tool_output = result.output
                    except Exception as exc:  # noqa: BLE001
                        tool_output = f"Error: {exc}"

                    if sctx.events is not None:
                        await sctx.events.agent_tool(
                            stage_id=None,
                            spawn_index=None,
                            agent=agent.name,
                            tool_name=tc.tool_name,
                            args_preview=tc.args or "",
                            output_preview=tool_output,
                            turn=turn + 1,
                        )

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "content": tool_output,
                    })
                # Continue loop: maybe more responses land in the inbox.
                awaiting_tool_followup = True
                continue

            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})

            # 4) No tool call this turn. If we received a shutdown request
            #    already delivered to the LLM and the LLM chose not to act,
            #    treat this peer as settled.
            if received_shutdown:
                break

            # No tool call and no new messages → peaceful idle; back off
            # a tick so other peers get a chance before we reconsider.
            await asyncio.sleep(self._idle_poll_seconds)

        return SpawnOutput(
            agent=agent.name,
            task=sctx.initial_task or "(swarm peer)",
            output=last_text or "".join(output_parts) or "(no output)",
            is_error=False,
            turns=turn + 1,
            tool_calls=total_tool_calls,
        )


# ---------------------------------------------------------------------------
# SwarmResult + run_swarm top-level API
# ---------------------------------------------------------------------------


@dataclass
class SwarmResult:
    """Aggregated outcome of a swarm run."""

    flow_name: str
    # Name of the entry agent (initial task receiver).  Kept as ``lead`` for
    # backwards compatibility; new code may read the ``entry`` alias.
    lead: str
    # Per-peer final outputs (the peer's last assistant text, plus
    # token accounting).  Keyed by agent name.
    peers: dict[str, SpawnOutput] = field(default_factory=dict)
    # Full ordered message log (including broadcast fan-outs, shutdown
    # negotiation, everything).  Tests and the CLI consume this.
    transcript: list[Envelope] = field(default_factory=list)
    # Convenience: the entry agent's final text is commonly the swarm answer.
    lead_output: str = ""
    terminated_reason: str = ""  # "lead-quiet" | "turn-budget" | "walltime" | "shutdown"

    @property
    def entry(self) -> str:
        """Alias for :attr:`lead` — the swarm entry agent's name."""
        return self.lead

    @property
    def entry_output(self) -> str:
        """Alias for :attr:`lead_output` — the entry agent's final text."""
        return self.lead_output


async def run_swarm(
    spec: OrchestrationSpec,
    agents: dict[str, AgentInfo],
    *,
    user_task: str,
    runner: SwarmAgentRunner | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    walltime_seconds: float = 300.0,
    events: OrchestrationEventEmitter | None = None,
) -> SwarmResult:
    """Execute an orchestration spec in ``swarm`` mode.

    Parameters
    ----------
    spec:
        Validated spec with ``mode='swarm'``.  ``spec.entry`` (or the
        legacy alias ``spec.lead``) names the initial task receiver; when
        neither is set the runtime falls back to the first declared agent.
    agents:
        Resolved ``{name → AgentInfo}`` from :mod:`agent_resolver`.
    user_task:
        The initial prompt delivered to the entry agent's inbox.
    runner:
        Per-peer loop.  Defaults to :class:`LiteLLMSwarmRunner`; tests
        inject a deterministic fake.
    max_turns:
        Per-peer turn budget.  A peer that hits it is politely stopped.
    walltime_seconds:
        Hard deadline for the whole swarm; blown budgets terminate the
        run even if peers are still chatting.
    events:
        Optional lifecycle emitter.  When supplied, the runtime publishes
        ``orchestration.swarm.started`` / ``.swarm.finished`` and hooks
        the mailbox so every routed envelope produces an
        ``orchestration.message.sent`` event.
    """
    if spec.mode != "swarm":
        raise SwarmError(f"run_swarm requires mode=swarm, got {spec.mode!r}")
    if not agents:
        raise SwarmError("swarm requires at least 1 agent")
    if len(agents) < 2:
        raise SwarmError("swarm requires at least 2 agents")

    # Resolve the entry agent.  Prefer the new ``entry`` field; fall back
    # to the legacy ``lead`` alias; finally use the first declared agent
    # as a sensible default so a fully-decentralized swarm spec (no entry
    # pinned) still has a deterministic task-seeding target.
    entry_name = spec.entry or spec.lead
    if not entry_name:
        # ``spec.agents`` preserves declaration order; ``agents`` is a dict
        # rebuilt from it, but we re-derive from the spec to be explicit.
        entry_name = spec.agents[0].name if spec.agents else next(iter(agents))
    if entry_name not in agents:
        raise SwarmError(
            f"entry agent {entry_name!r} not in resolved agents: {sorted(agents)}"
        )
    lead = entry_name  # kept for readability below (callbacks, events)

    peer_runner = runner or LiteLLMSwarmRunner()
    # Honour the backend hint from the spec.  ``None`` / ``auto`` /
    # ``inprocess`` all collapse to the cheapest backend (in-process
    # asyncio queues); the file- and terminal-backed modes are opt-in
    # and require a writable ``root_dir`` — the factory creates a fresh
    # temp directory when the spec does not supply one.
    backend_spec = spec.backend
    prefer: str = backend_spec.prefer if backend_spec else "auto"
    root_dir = backend_spec.root_dir if backend_spec else None
    system = MailboxSystem.for_backend(
        prefer,  # type: ignore[arg-type]
        list(agents.keys()),
        root_dir=root_dir,
    )
    # Wire the mailbox → emitter bridge so every message is visible on
    # the bus.  We keep it on the system (not each peer) to get one
    # emission per routed envelope even for broadcast fan-outs.
    if events is not None:
        system.on_send = events.message_sent

    # Seed the lead's inbox with the user task as a plain user-role
    # message (the runner treats ``initial_task`` specially so it
    # doesn't appear in the transcript as an envelope).
    deadline = time.monotonic() + walltime_seconds
    terminated_reason = {"value": ""}

    def should_stop() -> bool:
        if terminated_reason["value"]:
            return True
        if time.monotonic() >= deadline:
            terminated_reason["value"] = "walltime"
            return True
        return False

    async def _run_peer(name: str) -> tuple[str, SpawnOutput]:
        sctx = SwarmAgentContext(
            agent=agents[name],
            sender_name=name,
            system=system,
            lead_name=lead,
            initial_task=user_task if name == lead else None,
            max_turns=max_turns,
            events=events,
            should_stop=should_stop,
        )
        out = await peer_runner(sctx)
        return name, out

    t0 = time.monotonic()
    if events is not None:
        peer_names = [n for n in agents if n != lead]
        await events.swarm_started(lead=lead, peers=peer_names, user_task=user_task)

    tasks = [asyncio.create_task(_run_peer(name)) for name in agents]
    try:
        done_results = await asyncio.gather(*tasks, return_exceptions=False)
    finally:
        await system.close()

    peers = dict(done_results)
    if not terminated_reason["value"]:
        terminated_reason["value"] = "lead-quiet"

    lead_out = peers.get(lead)

    if events is not None:
        await events.swarm_finished(
            lead=lead,
            terminated_reason=terminated_reason["value"],
            duration_seconds=time.monotonic() - t0,
            peer_count=len(peers),
        )

    return SwarmResult(
        flow_name=spec.name,
        lead=lead,
        peers=peers,
        transcript=list(system.event_log),
        lead_output=(lead_out.output if lead_out else ""),
        terminated_reason=terminated_reason["value"],
    )


__all__ = [
    "LiteLLMSwarmRunner",
    "SendMessageParams",
    "SwarmAgentContext",
    "SwarmAgentRunner",
    "SwarmError",
    "SwarmResult",
    "run_swarm",
]
