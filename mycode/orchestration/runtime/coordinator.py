"""Coordinator runtime: drive an :class:`OrchestrationSpec` stage DAG.

Responsibilities
================

1. **Topology sorting** — respect explicit ``depends_on`` plus the
   implicit "declaration order" edge so non-dependent stages may still
   run sequentially by default.  We use Kahn's algorithm; the validator
   has already rejected cycles at load time.
2. **Per-stage dispatch** — four kinds of stages are supported:
   ``spawn``-only (sequential or parallel), ``fan_out_from`` (expand
   previous stage outputs into N spawns), and ``runs_on`` (execute
   ``stage.prompt`` as the given agent, with ``inputs`` injected).
3. **Runtime template substitution** — ``{{ $item }}`` / ``{{ $index }}``
   in fan-out spawn tasks/vars, resolved just-in-time.
4. **Output aggregation** — every spawn's ``SpawnOutput`` is recorded
   on the :class:`RunContext`; callers read it after ``run()`` returns
   to obtain the synthesised result.

What this module does **not** do
--------------------------------
- Swarm mailbox scheduling (that's M6).
- Persistence / SSE — the orchestration layer is event-agnostic for now;
  a thin event bus hook can be added later without disturbing the DAG
  engine.
- Worktree/process isolation — ``agent.isolation`` is currently honoured
  only to the extent that ``LiteLLMAgentRunner`` runs each spawn with
  fresh tool context; actual process isolation is a future milestone.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mycode.orchestration.runtime.context import (
    RunContext,
    SpawnOutput,
    StageOutput,
)
from mycode.orchestration.runtime.spawn import (
    AgentRunner,
    LiteLLMAgentRunner,
    SpawnRequest,
)

if TYPE_CHECKING:
    from mycode.agent.agent import AgentInfo
    from mycode.orchestration.runtime.events import OrchestrationEventEmitter
    from mycode.orchestration.topology.schema import (
        OrchestrationSpec,
        SpawnSpec,
        StageSpec,
    )


class CoordinatorError(RuntimeError):
    """Raised when the orchestration cannot run (missing agent, bad DAG,
    fan-out against a non-existent source, etc.).  Runtime errors inside
    a spawn do **not** raise — they surface as ``SpawnOutput.is_error``.
    """


# --- Template substitution --------------------------------------------------

_FAN_OUT_RE = re.compile(r"\{\{\s*(\$[a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute_fanout(s: str, *, item: str, index: int) -> str:
    """Replace ``{{ $item }}`` / ``{{ $index }}`` in ``s``.

    Kept tiny and regex-based on purpose: the full ``vars``/``{{foo}}``
    renderer already ran at load time (see :mod:`topology.loader`), so
    only runtime tokens remain here.
    """

    def sub(m: re.Match[str]) -> str:
        tok = m.group(1)
        if tok == "$item":
            return item
        if tok == "$index":
            return str(index)
        return m.group(0)

    return _FAN_OUT_RE.sub(sub, s)


# --- Coordinator ------------------------------------------------------------


@dataclass
class CoordinatorResult:
    """Return value from :meth:`Coordinator.run`."""

    context: RunContext
    # Convenience: the *last* stage's output (commonly the synthesis one).
    last_stage: StageOutput | None


class Coordinator:
    """Execute an :class:`OrchestrationSpec` in ``coordinator`` mode.

    Parameters
    ----------
    spec:
        The resolved orchestration spec (already loaded + validated).
    agents:
        ``{name → AgentInfo}`` produced by
        :func:`orchestration.topology.agent_resolver.resolve_all_agents`.
    runner:
        Callable executing one spawn.  Defaults to a
        :class:`LiteLLMAgentRunner`; tests inject a deterministic fake.
    """

    def __init__(
        self,
        spec: OrchestrationSpec,
        agents: dict[str, AgentInfo],
        *,
        runner: AgentRunner | None = None,
        events: OrchestrationEventEmitter | None = None,
    ) -> None:
        if spec.mode not in ("coordinator", "hybrid"):
            raise CoordinatorError(
                f"Coordinator runtime requires mode=coordinator|hybrid, got {spec.mode!r}"
            )
        self.spec = spec
        self.agents = agents
        self.runner: AgentRunner = runner or LiteLLMAgentRunner()
        # Optional lifecycle emitter.  ``None`` means "zero overhead" —
        # every emission is gated on ``if self.events`` so turning it on
        # does not require touching production code paths.
        self.events: OrchestrationEventEmitter | None = events

    # --- DAG ----------------------------------------------------------------

    def _topo_sort(self) -> list[StageSpec]:
        """Kahn's algorithm with declaration-order as an implicit edge.

        Every stage has an implicit "depends on prior stage" edge unless
        it declares explicit ``depends_on`` (then only those count).  The
        author of the flow can always break the chain by saying
        ``depends_on: []``, which we treat the same as "root".
        """
        stages = list(self.spec.stages)
        by_id = {s.id: s for s in stages}

        incoming: dict[str, set[str]] = defaultdict(set)
        outgoing: dict[str, set[str]] = defaultdict(set)

        for i, s in enumerate(stages):
            deps: list[str]
            if s.depends_on:
                deps = list(s.depends_on)
            elif i > 0:
                deps = [stages[i - 1].id]
            else:
                deps = []
            # fan_out_from is a data-dep: ensure it's honoured even if
            # depends_on was hand-written and forgot it.
            if s.fan_out_from and s.fan_out_from not in deps:
                deps.append(s.fan_out_from)
            for d in deps:
                if d not in by_id:
                    raise CoordinatorError(
                        f"stage {s.id!r}: depends on unknown stage {d!r}"
                    )
                incoming[s.id].add(d)
                outgoing[d].add(s.id)

        ready: deque[str] = deque(s.id for s in stages if not incoming[s.id])
        order: list[StageSpec] = []
        seen: set[str] = set()

        while ready:
            sid = ready.popleft()
            if sid in seen:
                continue
            seen.add(sid)
            order.append(by_id[sid])
            for nxt in outgoing[sid]:
                incoming[nxt].discard(sid)
                if not incoming[nxt] and nxt not in seen:
                    ready.append(nxt)

        if len(order) != len(stages):
            missing = [s.id for s in stages if s.id not in seen]
            raise CoordinatorError(f"cyclic or unreachable stages: {missing}")

        return order

    # --- Stage execution ----------------------------------------------------

    async def run(self) -> CoordinatorResult:
        """Execute every stage in topological order; return aggregated state."""
        ctx = RunContext(flow_name=self.spec.name, vars=dict(self.spec.vars))
        flow_start = time.monotonic()
        if self.events:
            await self.events.flow_started(
                mode=self.spec.mode,
                agents=sorted(self.agents.keys()),
                extra={"stage_count": len(self.spec.stages)},
            )

        last_was_error = False
        try:
            for stage in self._topo_sort():
                stage_start = time.monotonic()
                if self.events:
                    await self.events.stage_started(stage.id, extra={
                        "parallel": stage.parallel,
                        "runs_on": stage.runs_on,
                        "fan_out_from": stage.fan_out_from,
                    })
                result = await self._run_stage(stage, ctx)
                ctx.record(result)
                if self.events:
                    await self.events.stage_finished(
                        result,
                        duration_seconds=time.monotonic() - stage_start,
                    )
                # Short-circuit on coordinator-body errors; spawn errors
                # are non-fatal because parallel stages commonly have
                # partial successes and the coordinator synthesis can
                # still report on what did succeed.
                if result.is_error and stage.runs_on:
                    last_was_error = True
                    break
        finally:
            if self.events:
                await self.events.flow_finished(
                    ok=not last_was_error,
                    duration_seconds=time.monotonic() - flow_start,
                    extra={"stages_run": len(ctx.stage_order)},
                )

        last = ctx.stages[ctx.stage_order[-1]] if ctx.stage_order else None
        return CoordinatorResult(context=ctx, last_stage=last)

    async def _run_stage(self, stage: StageSpec, ctx: RunContext) -> StageOutput:
        # 1) ``runs_on`` stages: coordinator authors the body itself.
        if stage.runs_on:
            return await self._run_coordinator_stage(stage, ctx)

        # 2) ``fan_out_from`` stages: expand the source stage's outputs.
        if stage.fan_out_from:
            return await self._run_fanout_stage(stage, ctx)

        # 3) Plain spawn stages (sequential or parallel).
        return await self._run_spawn_stage(stage, ctx, stage.spawn)

    async def _run_spawn_stage(
        self,
        stage: StageSpec,
        ctx: RunContext,
        spawns: list[SpawnSpec],
    ) -> StageOutput:
        inputs_block = ctx.collect_inputs_text(stage.inputs) if stage.inputs else ""

        async def _one(idx: int, sp: SpawnSpec) -> SpawnOutput:
            agent = self._require_agent(sp.agent, stage.id)
            if self.events:
                await self.events.spawn_started(
                    stage_id=stage.id, spawn_index=idx, agent=agent.name, task=sp.task,
                )
            t0 = time.monotonic()
            out = await self.runner(SpawnRequest(
                agent=agent,
                task=sp.task,
                inputs_block=inputs_block,
                vars=dict(sp.vars) if sp.vars else None,
                timeout_seconds=sp.timeout_seconds,
            ))
            if self.events:
                await self.events.spawn_finished(
                    stage_id=stage.id,
                    spawn_index=idx,
                    spawn=out,
                    duration_seconds=time.monotonic() - t0,
                )
            return out

        if stage.parallel and len(spawns) > 1:
            # Bound concurrency via a semaphore so ``max_concurrency``
            # actually limits in-flight work (not just Python tasks).
            sem = asyncio.Semaphore(max(1, stage.max_concurrency))

            async def _guarded(idx: int, sp: SpawnSpec) -> SpawnOutput:
                async with sem:
                    return await _one(idx, sp)

            results = await asyncio.gather(
                *(_guarded(i, sp) for i, sp in enumerate(spawns)),
                return_exceptions=False,
            )
        else:
            results = [await _one(i, sp) for i, sp in enumerate(spawns)]

        return StageOutput(stage_id=stage.id, spawns=list(results))

    async def _run_fanout_stage(self, stage: StageSpec, ctx: RunContext) -> StageOutput:
        if not stage.fan_out_from:
            raise CoordinatorError(f"stage {stage.id!r}: internal — fan_out_from unset")
        source = ctx.stages.get(stage.fan_out_from)
        if source is None:
            raise CoordinatorError(
                f"stage {stage.id!r}: fan_out_from source {stage.fan_out_from!r} not yet run"
            )
        if not stage.spawn:
            raise CoordinatorError(
                f"stage {stage.id!r}: fan_out stages must declare exactly one spawn template"
            )
        if len(stage.spawn) != 1:
            raise CoordinatorError(
                f"stage {stage.id!r}: fan_out stages support exactly one spawn template, got {len(stage.spawn)}"
            )

        template = stage.spawn[0]
        items = source.ok_spawns()  # Feed only successful predecessors forward.
        expanded: list[SpawnSpec] = []

        # Re-build SpawnSpec objects with substituted task/vars.  Using
        # model_copy keeps validation semantics consistent with load-time.
        for idx, src in enumerate(items):
            expanded.append(template.model_copy(update={
                "task": _substitute_fanout(template.task, item=src.output, index=idx),
                "vars": {
                    k: _substitute_fanout(str(v), item=src.output, index=idx) if isinstance(v, str) else v
                    for k, v in (template.vars or {}).items()
                },
            }))

        if not expanded:
            # Nothing upstream succeeded — record an empty stage rather
            # than failing the whole run; a synthesis stage can surface it.
            return StageOutput(stage_id=stage.id, spawns=[])

        return await self._run_spawn_stage(stage, ctx, expanded)

    async def _run_coordinator_stage(self, stage: StageSpec, ctx: RunContext) -> StageOutput:
        if not stage.runs_on:
            raise CoordinatorError(f"stage {stage.id!r}: internal — runs_on unset")
        agent = self._require_agent(stage.runs_on, stage.id)
        inputs_block = ctx.collect_inputs_text(stage.inputs) if stage.inputs else ""
        prompt_body = (stage.prompt or "").strip() or (
            f"Synthesize the outputs of stage(s) {stage.inputs!r} into a cohesive report."
        )
        if self.events:
            await self.events.spawn_started(
                stage_id=stage.id, spawn_index=0, agent=agent.name, task=prompt_body,
            )
        t0 = time.monotonic()
        spawn = await self.runner(SpawnRequest(
            agent=agent,
            task=prompt_body,
            inputs_block=inputs_block,
        ))
        if self.events:
            await self.events.spawn_finished(
                stage_id=stage.id,
                spawn_index=0,
                spawn=spawn,
                duration_seconds=time.monotonic() - t0,
            )
        return StageOutput(
            stage_id=stage.id,
            spawns=[spawn],
            coordinator_output=spawn.output,
            coordinator_agent=agent.name,
            is_error=spawn.is_error,
        )

    # --- helpers ------------------------------------------------------------

    def _require_agent(self, name: str, stage_id: str) -> AgentInfo:
        agent = self.agents.get(name)
        if agent is None:
            raise CoordinatorError(
                f"stage {stage_id!r}: references unknown agent {name!r}; "
                f"available: {sorted(self.agents.keys())}"
            )
        return agent


# --- Convenience top-level API ---------------------------------------------


async def run_coordinator(
    spec: OrchestrationSpec,
    agents: dict[str, AgentInfo],
    *,
    runner: AgentRunner | None = None,
    events: OrchestrationEventEmitter | None = None,
) -> CoordinatorResult:
    """One-shot helper: build a :class:`Coordinator` and call ``run()``."""
    return await Coordinator(spec, agents, runner=runner, events=events).run()
