"""M5 — coordinator runtime tests.

Covers the pure-data and DAG-scheduling logic with a deterministic fake
runner, plus the two shipped flows (``research.yaml`` and
``pair-review.yaml``) at the edges of the runtime contract.

Design of the fake runner
-------------------------

``FakeRunner`` is an ``AgentRunner`` that returns a ``SpawnOutput``
whose ``output`` echoes the task text and, optionally, the inputs
block.  It also records every request so tests can assert on
**ordering** (sequential vs parallel starts), **fan-out expansion**
(``$item`` substitution), and **input propagation** (which stage
outputs were visible to a later stage).

We keep tests free of ``asyncio.sleep`` for real parallelism proof
because pytest + ``asyncio_mode=auto`` already drives the event loop
deterministically; ordering assertions instead compare which requests
land in the log **before** downstream stages' requests do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from mycode.orchestration.registry.agent_registry import AgentRegistry
from mycode.orchestration.runtime import (
    Coordinator,
    CoordinatorError,
    RunContext,
    SpawnOutput,
    SpawnRequest,
    StageOutput,
    run_coordinator,
)
from mycode.orchestration.runtime.context import (
    RunContext as _RunContextCheck,  # re-export assertion
)
from mycode.orchestration.topology import (
    load_file,
    resolve_all_agents,
)
from mycode.orchestration.topology.schema import (
    AgentSpec,
    OrchestrationSpec,
    SpawnSpec,
    StageSpec,
)

# --- fixtures / helpers ----------------------------------------------------


@dataclass
class FakeRunner:
    """Deterministic ``AgentRunner`` that echoes tasks and records calls."""

    calls: list[SpawnRequest] = field(default_factory=list)
    # Optional map ``task.startswith(prefix) → output`` to simulate agents
    # producing specific content (used by fan-out assertions).
    responses: dict[str, str] = field(default_factory=dict)
    # Optional map ``agent.name → should_error`` to simulate failures.
    errors: dict[str, bool] = field(default_factory=dict)

    async def __call__(self, req: SpawnRequest) -> SpawnOutput:
        self.calls.append(req)
        if self.errors.get(req.agent.name):
            return SpawnOutput(
                agent=req.agent.name,
                task=req.task,
                output=f"forced error from {req.agent.name}",
                is_error=True,
            )
        for prefix, body in self.responses.items():
            if req.task.startswith(prefix):
                return SpawnOutput(agent=req.agent.name, task=req.task, output=body)
        # Default: echo the task + any inputs block so tests can assert
        # on propagation through the pipeline.
        body = f"ran {req.agent.name}: {req.task}"
        if req.inputs_block:
            body += f"\n--INPUTS--\n{req.inputs_block.rstrip()}"
        return SpawnOutput(agent=req.agent.name, task=req.task, output=body)


FLOWS = Path(__file__).resolve().parent.parent / "mycode" / "orchestration" / "flows"


# --- RunContext / SpawnOutput smoke ---------------------------------------


def test_run_context_collect_inputs_filters_errors():
    ctx = RunContext(flow_name="demo")
    ctx.record(StageOutput(
        stage_id="a",
        spawns=[
            SpawnOutput(agent="x", task="t1", output="OK1"),
            SpawnOutput(agent="y", task="t2", output="bad", is_error=True),
        ],
    ))
    ctx.record(StageOutput(stage_id="b", spawns=[
        SpawnOutput(agent="z", task="t3", output="OK3"),
    ]))

    # Glob ``a.*`` matches only "a"; errors filtered out.
    items = ctx.collect_inputs(["a.*"])
    assert [s.agent for s in items] == ["x"]

    # Glob ``*`` matches all stages (successful only).
    items = ctx.collect_inputs(["*"])
    assert [s.output for s in items] == ["OK1", "OK3"]

    # Markdown rendering includes agent names and task lines.
    md = ctx.collect_inputs_text(["*"])
    assert "agent=`x`" in md and "agent=`z`" in md
    assert "t1" in md and "t3" in md


def test_run_context_missing_inputs_returns_empty_string():
    ctx = RunContext(flow_name="demo")
    assert ctx.collect_inputs_text(["nope.*"]) == ""


def test_runtime_reexports():
    """The runtime package must expose the context module's symbols."""
    assert RunContext is _RunContextCheck


# --- Coordinator: basic DAG -----------------------------------------------


def _minimal_agent(name: str) -> dict[str, Any]:
    """Produce an ``AgentInfo``-compatible dict — we can't import AgentInfo
    in tests without bootstrapping the full registry; the coordinator only
    dereferences ``.name`` / ``.tools`` / ``.max_turns`` / ``.prompt`` /
    ``.permission`` etc. on the runner path, which FakeRunner bypasses."""
    from mycode.agent.agent import AgentInfo

    return AgentInfo(
        name=name,
        description=f"test agent {name}",
        mode="all",
        native=False,
        source="project",
    )


@pytest.mark.asyncio
async def test_sequential_stages_run_in_order():
    spec = OrchestrationSpec(
        name="seq",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(id="s1", spawn=[SpawnSpec(agent="w", task="first")]),
            StageSpec(id="s2", spawn=[SpawnSpec(agent="w", task="second")]),
        ],
    )
    agents = {"w": _minimal_agent("w")}
    runner = FakeRunner()

    result = await run_coordinator(spec, agents, runner=runner)

    # Implicit "previous stage" dep enforces order in the call log.
    assert [c.task for c in runner.calls] == ["first", "second"]
    assert result.context.stage_order == ["s1", "s2"]
    assert result.last_stage and result.last_stage.stage_id == "s2"


@pytest.mark.asyncio
async def test_parallel_stage_launches_all_spawns():
    spec = OrchestrationSpec(
        name="par",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(
                id="research",
                parallel=True,
                max_concurrency=4,
                spawn=[
                    SpawnSpec(agent="w", task="q1"),
                    SpawnSpec(agent="w", task="q2"),
                    SpawnSpec(agent="w", task="q3"),
                ],
            ),
        ],
    )
    runner = FakeRunner()
    result = await run_coordinator(spec, {"w": _minimal_agent("w")}, runner=runner)

    stage = result.context.stages["research"]
    assert [sp.task for sp in stage.spawns] == ["q1", "q2", "q3"]
    assert all(not sp.is_error for sp in stage.spawns)


@pytest.mark.asyncio
async def test_runs_on_stage_injects_inputs_block():
    spec = OrchestrationSpec(
        name="synth",
        agents=[
            AgentSpec(name="explorer"),
            AgentSpec(name="coordinator"),
        ],
        stages=[
            StageSpec(
                id="research",
                parallel=True,
                spawn=[
                    SpawnSpec(agent="explorer", task="find A"),
                    SpawnSpec(agent="explorer", task="find B"),
                ],
            ),
            StageSpec(
                id="synthesize",
                runs_on="coordinator",
                depends_on=["research"],
                inputs=["research.*"],
                prompt="Combine findings.",
            ),
        ],
    )
    agents = {
        "explorer": _minimal_agent("explorer"),
        "coordinator": _minimal_agent("coordinator"),
    }
    runner = FakeRunner()
    result = await run_coordinator(spec, agents, runner=runner)

    synth = result.context.stages["synthesize"]
    assert synth.coordinator_agent == "coordinator"
    # Coordinator saw both explorer outputs via the inputs block.
    assert "find A" in (synth.coordinator_output or "")
    assert "find B" in (synth.coordinator_output or "")

    # And the coordinator spawn's request was carrying the inputs block.
    coord_call = runner.calls[-1]
    assert coord_call.agent.name == "coordinator"
    assert "find A" in coord_call.inputs_block
    assert "find B" in coord_call.inputs_block


@pytest.mark.asyncio
async def test_fan_out_expands_prior_outputs_with_substitution():
    spec = OrchestrationSpec(
        name="fan",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(
                id="seed",
                parallel=True,
                spawn=[
                    SpawnSpec(agent="w", task="alpha"),
                    SpawnSpec(agent="w", task="beta"),
                ],
            ),
            StageSpec(
                id="expand",
                fan_out_from="seed",
                spawn=[SpawnSpec(agent="w", task="echo {{ $item }} at {{ $index }}")],
            ),
        ],
    )
    runner = FakeRunner(responses={"alpha": "RA", "beta": "RB"})
    result = await run_coordinator(spec, {"w": _minimal_agent("w")}, runner=runner)

    expand = result.context.stages["expand"]
    tasks = sorted(sp.task for sp in expand.spawns)
    assert tasks == ["echo RA at 0", "echo RB at 1"]


@pytest.mark.asyncio
async def test_fan_out_skips_errored_sources():
    spec = OrchestrationSpec(
        name="fan-err",
        agents=[
            AgentSpec(name="producer"),
            AgentSpec(name="consumer"),
        ],
        stages=[
            StageSpec(
                id="seed",
                parallel=True,
                spawn=[
                    SpawnSpec(agent="producer", task="A"),
                    SpawnSpec(agent="producer", task="B"),
                ],
            ),
            StageSpec(
                id="expand",
                fan_out_from="seed",
                spawn=[SpawnSpec(agent="consumer", task="use {{ $item }}")],
            ),
        ],
    )
    runner = FakeRunner(responses={"A": "GOOD"}, errors={"producer": False})
    # Second producer call errors (we decide dynamically via responses).
    calls_before: list[SpawnRequest] = []

    async def selective(req: SpawnRequest) -> SpawnOutput:
        calls_before.append(req)
        if req.task == "B":
            return SpawnOutput(agent=req.agent.name, task=req.task, output="bad", is_error=True)
        return await runner(req)

    result = await run_coordinator(
        spec,
        {"producer": _minimal_agent("producer"), "consumer": _minimal_agent("consumer")},
        runner=selective,
    )
    expand = result.context.stages["expand"]
    # Only one item was fed forward (the non-errored one).
    assert len(expand.spawns) == 1
    assert expand.spawns[0].task == "use GOOD"


@pytest.mark.asyncio
async def test_unknown_agent_raises():
    spec = OrchestrationSpec(
        name="bad",
        agents=[AgentSpec(name="present")],
        stages=[StageSpec(id="s", spawn=[SpawnSpec(agent="missing", task="x")])],
    )
    with pytest.raises(CoordinatorError, match="unknown agent 'missing'"):
        await run_coordinator(spec, {"present": _minimal_agent("present")}, runner=FakeRunner())


@pytest.mark.asyncio
async def test_depends_on_unknown_stage_raises():
    spec = OrchestrationSpec(
        name="bad-dag",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(id="a", spawn=[SpawnSpec(agent="w", task="t")]),
            StageSpec(id="b", depends_on=["nope"], spawn=[SpawnSpec(agent="w", task="t")]),
        ],
    )
    with pytest.raises(CoordinatorError, match="unknown stage 'nope'"):
        await run_coordinator(spec, {"w": _minimal_agent("w")}, runner=FakeRunner())


@pytest.mark.asyncio
async def test_topo_respects_explicit_depends_on_over_declaration_order():
    # b declared before a but depends on a → a must still run first.
    spec = OrchestrationSpec(
        name="dag",
        agents=[AgentSpec(name="w")],
        stages=[
            StageSpec(id="a", depends_on=[], spawn=[SpawnSpec(agent="w", task="first")]),
            StageSpec(id="c", depends_on=["b"], spawn=[SpawnSpec(agent="w", task="third")]),
            StageSpec(id="b", depends_on=["a"], spawn=[SpawnSpec(agent="w", task="second")]),
        ],
    )
    runner = FakeRunner()
    result = await run_coordinator(spec, {"w": _minimal_agent("w")}, runner=runner)
    assert result.context.stage_order == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_swarm_mode_rejected():
    spec = OrchestrationSpec(name="sw", mode="swarm", lead="x",
                             agents=[AgentSpec(name="x")])
    with pytest.raises(CoordinatorError, match="coordinator\\|hybrid"):
        Coordinator(spec, {"x": _minimal_agent("x")})


# --- Built-in flows: end-to-end with FakeRunner ---------------------------


@pytest.mark.asyncio
async def test_research_flow_runs_with_fake_runner(tmp_path):
    spec = load_file(FLOWS / "research.yaml")
    registry = AgentRegistry()
    agents = resolve_all_agents(spec.agents, registry, fallback_agent="build")
    runner = FakeRunner()

    result = await run_coordinator(spec, agents, runner=runner)

    # Two explorers ran first, then coordinator synthesized.
    stage_ids = result.context.stage_order
    assert stage_ids == ["research", "synthesize"]
    research = result.context.stages["research"]
    assert len(research.spawns) == 2
    assert all(sp.agent == "explorer" for sp in research.spawns)

    synth = result.context.stages["synthesize"]
    assert synth.coordinator_agent == "coordinator"
    # The synthesis spawn received both explorer outputs via inputs_block.
    coord_call = runner.calls[-1]
    assert coord_call.agent.name == "coordinator"
    assert "Describe the module layout" in coord_call.inputs_block
    assert "List all TODOs" in coord_call.inputs_block


def test_pair_review_flow_rejected_by_coordinator_runtime():
    """pair-review is swarm mode → coordinator must refuse to run it,
    surfacing a clear ``CoordinatorError`` so the CLI can map it to a
    user-friendly message."""
    spec = load_file(FLOWS / "pair-review.yaml")
    registry = AgentRegistry()
    agents = resolve_all_agents(spec.agents, registry, fallback_agent="build")

    with pytest.raises(CoordinatorError):
        Coordinator(spec, agents)
