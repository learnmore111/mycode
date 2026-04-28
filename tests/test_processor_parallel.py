"""Tests for processor parallel tool execution.

Verifies that the processor:
1. Executes multiple tool calls in parallel via asyncio.gather
2. Handles doom loop detection correctly
3. Handles permission blocking correctly
4. Handles unknown tools gracefully
5. Handles mixed success/failure cases
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from mycode.bus.bus import Bus
from mycode.bus.events import PART_UPDATED
from mycode.provider.schema import Model, ModelApi
from mycode.session import llm as llmmod
from mycode.session.message import AssistantMessage, ReasoningPart, ToolPart, create_tool_part
from mycode.session.processor import DOOM_LOOP_THRESHOLD, ProcessorContext, process
from mycode.tool import registry as tool_registry
from mycode.tool.base import ToolContext, ToolInfo, ToolOk, ToolResult

# ── Helpers ────────────────────────────────────────────────────────────


def _model() -> Model:
    return Model(id="test-model", providerID="test", api=ModelApi(id="test-api"), name="Test")


def _assistant_msg(session_id: str = "s1") -> AssistantMessage:
    return AssistantMessage(
        id="am1", session_id=session_id, parent_id="um1",
        provider_id="test", model_id="test-model", agent="build",
        time_created=int(time.time() * 1000),
    )


class SlowTool(ToolInfo):
    """A mock tool that sleeps for a given duration to verify parallelism."""
    id = "slow_tool"
    description = "A slow tool for testing"

    def __init__(self, delay: float = 0.1, output: str = "done"):
        self._delay = delay
        self._output = output

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolOk(self._output, title="slow", metadata={"ok": True})


class FailTool(ToolInfo):
    """A tool that always raises an exception."""
    id = "fail_tool"
    description = "Always fails"

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise RuntimeError("intentional failure")


class CounterTool(ToolInfo):
    """A tool that counts how many times it's been called (concurrently)."""
    id = "counter_tool"
    description = "Counts concurrent calls"

    def __init__(self):
        self.concurrent = 0
        self.max_concurrent = 0

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.concurrent += 1
        if self.concurrent > self.max_concurrent:
            self.max_concurrent = self.concurrent
        await asyncio.sleep(0.05)
        self.concurrent -= 1
        return ToolOk("ok", title="counter", metadata={"ok": True})


async def _fake_stream_with_tools(tool_calls: list[tuple[str, str, dict]]):
    """Generate fake LLM stream events that produce the given tool calls.

    Each tuple is (tool_name, tool_call_id, args_dict).
    """
    for tool_name, call_id, args in tool_calls:
        yield llmmod.ToolCallPartial(tool_name=tool_name, tool_call_id=call_id)
        import json
        yield llmmod.ToolCallDelta(tool_call_id=call_id, args=json.dumps(args))
    yield llmmod.FinishEvent(usage={}, cost=0.0)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure clean tool registry for each test."""
    saved = dict(tool_registry._tools)
    tool_registry._tools.clear()
    yield
    tool_registry._tools.clear()
    tool_registry._tools.update(saved)


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_execution_basic(monkeypatch):
    """Two slow tools should execute in parallel, finishing faster than serial."""
    slow = SlowTool(delay=0.1, output="result")
    tool_registry.register(slow)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc1", {}),
        ("slow_tool", "tc2", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    start = time.monotonic()
    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))
    elapsed = time.monotonic() - start

    assert result == "continue"
    # Both should have completed
    tool_parts = [p for p in parts if isinstance(p, ToolPart)]
    assert len(tool_parts) == 2
    # Parallel: 2 × 0.1s tools should take ~0.1s, not ~0.2s
    # Use generous threshold to avoid flakiness
    assert elapsed < 0.3, f"Expected parallel execution, took {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_parallel_concurrency_verified(monkeypatch):
    """Use CounterTool to verify tools actually run concurrently."""
    counter = CounterTool()
    tool_registry.register(counter)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("counter_tool", "tc1", {}),
        ("counter_tool", "tc2", {}),
        ("counter_tool", "tc3", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "continue"
    # max_concurrent > 1 proves they ran in parallel
    assert counter.max_concurrent > 1, f"Expected concurrent execution, max_concurrent={counter.max_concurrent}"


@pytest.mark.asyncio
async def test_unknown_tool(monkeypatch):
    """Unknown tool should be marked as error without crashing."""
    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("nonexistent_xyz", "tc1", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    tool_parts = [p for p in parts if isinstance(p, ToolPart)]
    assert len(tool_parts) == 1
    assert tool_parts[0].state["status"] == "error"
    assert "Unknown tool" in tool_parts[0].state["output"]


@pytest.mark.asyncio
async def test_tool_execution_failure(monkeypatch):
    """A tool that raises should be marked as error, doom_count incremented."""
    fail = FailTool()
    tool_registry.register(fail)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("fail_tool", "tc1", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "continue"
    assert ctx.doom_count == 1


@pytest.mark.asyncio
async def test_doom_loop_detection(monkeypatch):
    """When the same tool+input is repeated DOOM_LOOP_THRESHOLD times, stop."""
    slow = SlowTool(delay=0.0, output="ok")
    tool_registry.register(slow)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    # Pre-populate ctx.parts with enough identical tool parts to trigger doom
    for i in range(DOOM_LOOP_THRESHOLD):
        tp = create_tool_part("s1", "am1", "slow_tool", f"old_tc_{i}")
        tp.state = {"input": {"key": "same"}, "status": "completed"}
        ctx.parts.append(tp)

    # Now submit one more with the same input
    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc_new", {"key": "same"}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "stop"


@pytest.mark.asyncio
async def test_doom_count_threshold(monkeypatch):
    """After DOOM_LOOP_THRESHOLD consecutive failures, processor stops."""
    fail = FailTool()
    tool_registry.register(fail)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
        doom_count=DOOM_LOOP_THRESHOLD - 1,  # one more failure → threshold
    )

    stream_events = _fake_stream_with_tools([
        ("fail_tool", "tc1", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "stop"
    assert ctx.doom_count >= DOOM_LOOP_THRESHOLD


@pytest.mark.asyncio
async def test_success_resets_doom_count(monkeypatch):
    """Successful execution should reset doom_count to 0."""
    slow = SlowTool(delay=0.0, output="ok")
    tool_registry.register(slow)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
        doom_count=2,
    )

    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc1", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "continue"
    assert ctx.doom_count == 0


@pytest.mark.asyncio
async def test_mixed_success_and_failure(monkeypatch):
    """Mix of succeeding and failing tools: has_failure should be True."""
    slow = SlowTool(delay=0.0, output="ok")
    fail = FailTool()
    tool_registry.register(slow)
    tool_registry.register(fail)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc1", {}),
        ("fail_tool", "tc2", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "continue"
    assert ctx.doom_count == 1  # failure increments doom


@pytest.mark.asyncio
async def test_no_tool_calls_stops(monkeypatch):
    """When LLM returns only text (no tool calls), result is 'stop'."""
    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    async def _text_only_stream(_):
        yield llmmod.TextDelta(text="Hello world")
        yield llmmod.FinishEvent(usage={}, cost=0.0)

    monkeypatch.setattr(llmmod, "stream", _text_only_stream)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "stop"
    assert len(parts) == 1  # one text part


@pytest.mark.asyncio
async def test_reasoning_part_is_streamed_and_persisted_in_parts(monkeypatch):
    """Reasoning deltas should produce a dedicated reasoning part before text."""
    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    async def _reasoning_stream(_):
        yield llmmod.ReasoningDelta(text="先检查前端状态。")
        yield llmmod.ReasoningDelta(text="再确认后端事件。")
        yield llmmod.TextDelta(text="最终答复")
        yield llmmod.FinishEvent(usage={}, cost=0.0)

    monkeypatch.setattr(llmmod, "stream", _reasoning_stream)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "stop"
    assert isinstance(parts[0], ReasoningPart)
    assert parts[0].content == "先检查前端状态。再确认后端事件。"
    assert len(parts) == 2


@pytest.mark.asyncio
async def test_bus_publish_on_completion(monkeypatch):
    """Verify that PART_UPDATED events are published for each completed tool."""
    slow = SlowTool(delay=0.0, output="ok")
    tool_registry.register(slow)

    bus = Bus()
    published_events: list[dict] = []

    original_publish = bus.publish

    async def _capture_publish(event_def, properties=None):
        if event_def.type == PART_UPDATED.type:
            published_events.append(properties or {})
        await original_publish(event_def, properties)

    bus.publish = _capture_publish

    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc1", {}),
        ("slow_tool", "tc2", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "continue"
    # Should have published PART_UPDATED for each completed tool
    assert len(published_events) == 2
    assert all(e.get("part", {}).get("status") == "completed" for e in published_events)


@pytest.mark.asyncio
async def test_parts_appended_to_context(monkeypatch):
    """Completed tools should be appended to ctx.parts."""
    slow = SlowTool(delay=0.0, output="ok")
    tool_registry.register(slow)

    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    stream_events = _fake_stream_with_tools([
        ("slow_tool", "tc1", {}),
        ("slow_tool", "tc2", {}),
    ])

    monkeypatch.setattr(llmmod, "stream", lambda _: stream_events)

    await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    ctx_tool_parts = [p for p in ctx.parts if isinstance(p, ToolPart)]
    assert len(ctx_tool_parts) == 2


@pytest.mark.asyncio
async def test_error_event_stops(monkeypatch):
    """An ErrorEvent from LLM should stop processing."""
    bus = Bus()
    ctx = ProcessorContext(
        session_id="s1", model=_model(),
        assistant_message=_assistant_msg(), bus=bus,
    )

    async def _error_stream(_):
        yield llmmod.ErrorEvent(error="something went wrong")

    monkeypatch.setattr(llmmod, "stream", _error_stream)

    result, parts = await process(ctx, llmmod.StreamInput(
        model="test", messages=[], tools=[],
    ))

    assert result == "stop"
    assert ctx.assistant_message.error is not None
    assert "something went wrong" in ctx.assistant_message.error["message"]
