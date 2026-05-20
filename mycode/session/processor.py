"""会话处理器 — 核心智能体循环。

流式架构：process_stream() 是一个异步生成器，在 LLM 生成文本和工具执行时
实时产出 ProcessorEvent 对象。这使得 CLI 可以交错渲染文本和工具输出，
就像 Claude Code / Cursor / aider 一样。

增强功能：
- 只读工具的结果缓存（跳过重复调用）
- 临时故障的重试逻辑
- 读/写分离：只读工具并行运行，变异工具顺序运行
- 用于循环守卫的步骤级状态记录
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from mycode.bus.events import PART_DELTA, PART_UPDATED
from mycode.session import llm as llmmod
from mycode.session.loop_guard import MUTATING_TOOLS, LoopGuard
from mycode.session.message import (
    AssistantMessage,
    Part,
    ReasoningPart,
    TextPart,
    ToolPart,
    create_reasoning_part,
    create_text_part,
    create_tool_part,
)
from mycode.tool import registry as tool_registry
from mycode.tool.base import (
    ToolBaseError,
    ToolContext,
    ToolNotFoundError,
    ToolRuntimeError,
    ToolValidateError,
)
from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.bus.bus import Bus
    from mycode.permission.permission import PermissionManager
    from mycode.permission.schema import Rule
    from mycode.provider.schema import Model

logger = logmod.create(service="session.processor")

DOOM_LOOP_THRESHOLD = 3
Result = Literal["compact", "stop", "continue"]


@dataclass
class ProcessorEvent:
    """流式处理期间产生的事件。

    类型：
      - "text_delta":  来自 LLM 的增量文本。data["content"] 是增量字符串。
      - "tool_start":  已识别工具调用。data["tool"]、data["call_id"]。
      - "tool_running": 工具执行已开始。data["tool"]、data["call_id"]。
      - "tool_done":   工具执行完成。data["tool"]、data["call_id"]、data["status"]、
                       data["output"]、data["input"]。
      - "error":       LLM 或处理错误。data["message"]。
      - "finish":      单次迭代处理完成。data["result"] 是 Result 字符串，
                       data["parts"] 是产生的 Part 对象列表。
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessorContext:
    session_id: str
    model: Model
    assistant_message: AssistantMessage
    bus: Bus
    toolcalls: dict[str, ToolPart] = field(default_factory=dict)
    parts: list[Part] = field(default_factory=list)
    should_break: bool = False
    doom_count: int = 0
    permission_manager: PermissionManager | None = None
    agent_permission: list[Rule] = field(default_factory=list)
    loop_guard: LoopGuard | None = None  # 由 prompt.py 注入


def _surface_llm_error(
    ctx: ProcessorContext,
    error_event: llmmod.ErrorEvent,
    tool_calls_pending: list[ToolPart],
    parts: list[Part],
) -> None:
    """在所有重试耗尽后将 LLM 错误呈现到上下文。"""
    logger.error(
        "LLM error (all retries exhausted)",
        error=error_event.error,
        error_code=error_event.error_code,
        retryable=error_event.retryable,
        status_code=error_event.status_code,
    )
    ctx.assistant_message.error = {
        "message": error_event.error,
        "code": error_event.error_code,
        "retryable": error_event.retryable,
        "status_code": error_event.status_code,
    }
    ctx.should_break = True
    # 错误前已部分流式传输的任何工具调用必须作为失败呈现，
    # 以便厄运循环守卫和 UI 能看到它们。
    for partial_tp in list(ctx.toolcalls.values()):
        if partial_tp.state.get("status") in (None, "pending"):
            partial_tp.state["status"] = "error"
            partial_tp.state["is_error"] = True
            partial_tp.state["output"] = f"LLM stream aborted before tool args finalised: {error_event.error}"
            partial_tp.time_completed = int(time.time() * 1000)
            if partial_tp not in ctx.parts:
                ctx.parts.append(partial_tp)
    # 清空待处理列表，以便执行阶段不会尝试运行部分形成的调用。
    tool_calls_pending.clear()


async def process_stream(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> AsyncGenerator[ProcessorEvent, None]:
    """运行智能体循环的一次迭代，实时产生事件。"""
    MAX_LLM_RETRIES = 3
    LLM_RETRY_DELAY = 1.0  # seconds between retries

    current_reasoning: ReasoningPart | None = None
    current_text: TextPart | None = None
    tool_calls_pending: list[ToolPart] = []
    parts: list[Part] = []
    text_length = 0

    last_error_event: llmmod.ErrorEvent | None = None
    last_exception: str | None = None

    for attempt in range(1, MAX_LLM_RETRIES + 1):
        # 在重试时重置每次尝试的状态（attempt > 1）
        if attempt > 1:
            logger.info("重试 LLM 流", attempt=attempt, max_retries=MAX_LLM_RETRIES)
            await asyncio.sleep(LLM_RETRY_DELAY)
            current_reasoning = None
            current_text = None
            tool_calls_pending.clear()
            parts.clear()
            text_length = 0
            last_error_event = None
            last_exception = None
            # 从上下文中清除先前失败尝试的部分工具调用
            ctx.toolcalls.clear()

        try:
            async for event in llmmod.stream(stream_input):
                if isinstance(event, llmmod.ReasoningDelta):
                    if current_reasoning is None:
                        current_reasoning = create_reasoning_part(ctx.session_id, ctx.assistant_message.id)
                        parts.append(current_reasoning)
                    current_reasoning.content += event.text
                    await ctx.bus.publish(PART_DELTA, {
                        "session_id": ctx.session_id,
                        "message_id": ctx.assistant_message.id,
                        "part_id": current_reasoning.id,
                        "field": "content",
                        "delta": event.text,
                    })
                    yield ProcessorEvent(type="reasoning_delta", data={"content": event.text})

                elif isinstance(event, llmmod.TextDelta):
                    if current_text is None:
                        current_text = create_text_part(ctx.session_id, ctx.assistant_message.id)
                        parts.append(current_text)
                    current_text.content += event.text
                    text_length += len(event.text)
                    await ctx.bus.publish(PART_DELTA, {
                        "session_id": ctx.session_id,
                        "message_id": ctx.assistant_message.id,
                        "part_id": current_text.id,
                        "field": "content",
                        "delta": event.text,
                    })
                    yield ProcessorEvent(type="text_delta", data={"content": event.text})

                elif isinstance(event, llmmod.ToolCallPartial):
                    tp = create_tool_part(ctx.session_id, ctx.assistant_message.id, event.tool_name, event.tool_call_id)
                    tp.state = {"status": "pending", "input": {}}
                    ctx.toolcalls[event.tool_call_id] = tp
                    parts.append(tp)
                    current_reasoning = None
                    current_text = None
                    yield ProcessorEvent(type="tool_start", data={
                        "tool": event.tool_name,
                        "call_id": event.tool_call_id,
                    })

                elif isinstance(event, llmmod.ToolCallArgsPartial):
                    tp_partial = ctx.toolcalls.get(event.tool_call_id)
                    if tp_partial:
                        raw = tp_partial.state.get("_raw_args", "") + event.args_delta
                        tp_partial.state["_raw_args"] = raw

                elif isinstance(event, llmmod.ToolCallDelta):
                    tp_delta = ctx.toolcalls.get(event.tool_call_id)
                    if tp_delta:
                        try:
                            parsed = json.loads(event.args) if event.args else {}
                            if not isinstance(parsed, dict):
                                logger.warn("tool args parsed to non-dict", tool=tp_delta.tool, type=type(parsed).__name__)
                                tp_delta.state["input"] = {}
                            else:
                                tp_delta.state["input"] = parsed
                        except json.JSONDecodeError as e:
                            logger.error("malformed tool arguments", tool=tp_delta.tool, error=str(e))
                            tp_delta.state["input"] = {}
                            tp_delta.state["_parse_error"] = str(e)
                        tool_calls_pending.append(tp_delta)

                elif isinstance(event, llmmod.FinishEvent):
                    # input_tokens / cache_* are absolute totals per call (contain full context),
                    # so we keep the *last* call's value rather than accumulating.
                    # output_tokens / reasoning_tokens / cost are truly additive per call.
                    ctx.assistant_message.tokens_input = event.usage.get("input_tokens", 0)
                    ctx.assistant_message.tokens_output += event.usage.get("output_tokens", 0)
                    ctx.assistant_message.tokens_reasoning += event.usage.get("reasoning_tokens", 0)
                    ctx.assistant_message.tokens_cache_read = event.usage.get("cache_read_tokens", 0)
                    ctx.assistant_message.tokens_cache_write = event.usage.get("cache_write_tokens", 0)
                    ctx.assistant_message.cost += event.cost
                    ctx.assistant_message.raw_usage = event.raw_usage

                elif isinstance(event, llmmod.ErrorEvent):
                    last_error_event = event
                    logger.error(
                        "LLM stream error",
                        attempt=attempt, max_retries=MAX_LLM_RETRIES,
                        error=event.error,
                        error_code=event.error_code,
                        retryable=event.retryable,
                    )
                    # Break inner stream loop — will decide whether to retry below
                    break
            else:
                # 流完成且没有 ErrorEvent → 成功
                # 进入工具执行阶段
                break  # 中断外层重试循环

            # If we got here via ErrorEvent break (not else), handle retry decision
            if last_error_event is not None:
                if attempt < MAX_LLM_RETRIES:
                    continue  # retry
                # All retries exhausted — surface the error
                _surface_llm_error(ctx, last_error_event, tool_calls_pending, parts)
                yield ProcessorEvent(type="error", data={
                    "message": last_error_event.error,
                    "code": last_error_event.error_code,
                    "retryable": last_error_event.retryable,
                    "status_code": last_error_event.status_code,
                })
                return

        except Exception as e:
            last_exception = str(e)
            logger.error(
                "LLM call exception",
                attempt=attempt, max_retries=MAX_LLM_RETRIES,
                error=last_exception,
            )
            if attempt < MAX_LLM_RETRIES:
                continue  # retry
            # All retries exhausted
            ctx.assistant_message.error = {"message": last_exception, "retryable": True}
            ctx.should_break = True
            yield ProcessorEvent(type="error", data={
                "message": last_exception,
                "code": None,
                "retryable": True,
                "status_code": None,
            })
            return
    else:
        # All retries exhausted without success — already handled above, but safety net
        return

    # === 执行工具调用 ===
    if tool_calls_pending:
        has_failure = False
        blocked = False
        doom_detected = False
        cache = ctx.loop_guard.cache if ctx.loop_guard else None

        # 阶段 1：预检 — 权限、厄运循环、缓存检查
        executable: list[tuple[ToolPart, Any, ToolContext]] = []
        cached_results: list[tuple[ToolPart, str]] = []

        for tp in tool_calls_pending:
            try:
                tool = tool_registry.get_or_raise(tp.tool)
            except ToolNotFoundError as e:
                tp.state["status"] = "error"
                tp.state["output"] = str(e)
                tp.state["is_error"] = True
                tp.time_completed = int(time.time() * 1000)
                has_failure = True  # Count tool-not-found as a failure for doom detection
                ctx.parts.append(tp)
                yield ProcessorEvent(type="tool_done", data={
                    "tool": tp.tool, "call_id": tp.tool_call_id,
                    "status": "error", "output": str(e), "input": {},
                })
                continue

            tool_ctx = ToolContext(
                session_id=ctx.session_id,
                message_id=ctx.assistant_message.id,
                agent=ctx.assistant_message.agent,
                call_id=tp.tool_call_id,
            )

            # 权限检查
            if ctx.permission_manager:
                try:
                    from mycode.permission.schema import DeniedError, RejectedError
                    await ctx.permission_manager.ask(
                        session_id=ctx.session_id,
                        permission=tp.tool,
                        patterns=["*"],
                        ruleset=ctx.agent_permission,
                        metadata={"tool": tp.tool, "input": tp.state.get("input", {})},
                        always=["*"],
                    )
                except (RejectedError, DeniedError) as e:
                    tp.state["status"] = "error"
                    tp.state["output"] = str(e)
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    blocked = True
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": str(e), "input": tp.state.get("input", {}),
                    })
                    continue
                except Exception as e:
                    # 故障安全：在意外权限错误时阻止工具执行
                    logger.error(
                        "permission check failed unexpectedly",
                        tool=tp.tool,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    tp.state["status"] = "error"
                    tp.state["output"] = f"Permission check failed: {e}"
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    blocked = True
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": f"Permission check failed: {e}",
                        "input": tp.state.get("input", {}),
                    })
                    continue

            # 厄运循环检测（传统方法，loop_guard 有更高级的检测）
            recent_tool_parts = [p for p in ctx.parts if isinstance(p, ToolPart) and p.tool == tp.tool]
            if len(recent_tool_parts) >= DOOM_LOOP_THRESHOLD:
                last_inputs = [json.dumps(p.state.get("input", {}), sort_keys=True) for p in recent_tool_parts[-DOOM_LOOP_THRESHOLD:]]
                current_input = json.dumps(tp.state.get("input", {}), sort_keys=True)
                if all(inp == current_input for inp in last_inputs):
                    logger.warn("doom loop detected", tool=tp.tool)
                    tp.state["status"] = "error"
                    tp.state["output"] = "Doom loop detected: same tool with same input called repeatedly"
                    tp.state["is_error"] = True
                    tp.time_completed = int(time.time() * 1000)
                    doom_detected = True
                    ctx.parts.append(tp)
                    yield ProcessorEvent(type="tool_done", data={
                        "tool": tp.tool, "call_id": tp.tool_call_id,
                        "status": "error", "output": tp.state["output"],
                        "input": tp.state.get("input", {}),
                    })
                    break

            # 缓存检查 — 如果有缓存结果则跳过执行
            if cache:
                cached = cache.get(tp.tool, tp.state.get("input", {}))
                if cached is not None:
                    cached_results.append((tp, cached))
                    logger.debug("cache hit", tool=tp.tool)
                    continue

            executable.append((tp, tool, tool_ctx))

        if doom_detected:
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        # 阶段 1.5：立即提供缓存结果
        for tp, cached_output in cached_results:
            tp.state["status"] = "completed"
            tp.state["output"] = cached_output
            tp.state["is_error"] = False
            tp.state["metadata"] = {"cached": True}
            tp.time_completed = int(time.time() * 1000)
            ctx.parts.append(tp)
            yield ProcessorEvent(type="tool_done", data={
                "tool": tp.tool, "call_id": tp.tool_call_id,
                "status": "completed", "output": cached_output[:500],
                "input": tp.state.get("input", {}),
            })

        # 阶段 2：使用读/写分离执行工具
        if executable:
            # 使用能力声明将只读工具与变异工具分开
            readonly_tasks: list[tuple[ToolPart, Any, ToolContext]] = []
            mutating_tasks: list[tuple[ToolPart, Any, ToolContext]] = []
            for tp, tool_impl, tool_ctx in executable:
                # 如果可用则使用能力声明，否则回退到硬编码集合
                if hasattr(tool_impl, "is_concurrency_safe") and hasattr(tool_impl, "is_read_only"):
                    tool_input = tp.state.get("input", {})
                    if tool_impl.is_read_only(tool_input) and tool_impl.is_concurrency_safe(tool_input):
                        readonly_tasks.append((tp, tool_impl, tool_ctx))
                    else:
                        mutating_tasks.append((tp, tool_impl, tool_ctx))
                elif tp.tool in MUTATING_TOOLS:
                    mutating_tasks.append((tp, tool_impl, tool_ctx))
                else:
                    readonly_tasks.append((tp, tool_impl, tool_ctx))

            # 安全：如果批次混合了只读和变异调用，先运行
            # 变异调用，以便同一次迭代中产生的任何缓存只读结果
            # 观察变异后的文件系统。
            # 以前只读在变异之前运行，这允许像 [read(foo.py), edit(foo.py)] 这样的混合批次
            # 缓存 foo.py 的编辑前快照并将其交给下一次迭代。
            mutating_first = bool(mutating_tasks) and bool(readonly_tasks)

            # 产生运行事件
            for tp, _, _ in executable:
                tp.state["status"] = "running"
                yield ProcessorEvent(type="tool_running", data={
                    "tool": tp.tool, "call_id": tp.tool_call_id,
                    "input": tp.state.get("input", {}),
                })

            all_results: list[tuple[bool, ProcessorEvent]] = []

            async def _run_mutating() -> None:
                for tp, impl, tctx in mutating_tasks:
                    result = await _run_tool_with_retry(tp, impl, tctx, ctx)
                    all_results.append(result)

            async def _run_readonly() -> None:
                if not readonly_tasks:
                    return
                ro_results = await asyncio.gather(
                    *[_run_tool_with_retry(tp, impl, tctx, ctx) for tp, impl, tctx in readonly_tasks],
                    return_exceptions=True,
                )
                for i, result in enumerate(ro_results):
                    if isinstance(result, BaseException):
                        tp_err, _, _ = readonly_tasks[i]
                        logger.error(
                            "read-only tool raised unexpected exception",
                            tool=tp_err.tool,
                            error=str(result),
                            error_type=type(result).__name__,
                        )
                        tp_err.state["status"] = "error"
                        tp_err.state["output"] = f"Tool execution failed: {result}"
                        tp_err.state["is_error"] = True
                        tp_err.time_completed = int(time.time() * 1000)
                        ctx.parts.append(tp_err)
                        all_results.append((False, ProcessorEvent(type="tool_done", data={
                            "tool": tp_err.tool, "call_id": tp_err.tool_call_id,
                            "status": "error", "output": f"Tool execution failed: {result}",
                            "input": tp_err.state.get("input", {}),
                        })))
                    else:
                        all_results.append(result)

            if mutating_first:
                await _run_mutating()
                await _run_readonly()
            else:
                await _run_readonly()
                await _run_mutating()

            # 产生结果并跟踪失败
            all_success = True
            for success, tool_event in all_results:
                yield tool_event
                if not success:
                    all_success = False

            if not all_success:
                has_failure = True

        if blocked:
            # 重置 doom_count 以避免陈旧状态污染下一次迭代
            ctx.doom_count = 0
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        if has_failure:
            ctx.doom_count += 1
        else:
            ctx.doom_count = 0

        if ctx.doom_count >= DOOM_LOOP_THRESHOLD:
            logger.warn("doom loop threshold reached, stopping")
            yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts})
            return

        yield ProcessorEvent(type="finish", data={
            "result": "continue", "parts": parts, "text_length": text_length,
        })
        return

    if ctx.should_break:
        yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts, "text_length": text_length})
        return

    yield ProcessorEvent(type="finish", data={"result": "stop", "parts": parts, "text_length": text_length})


async def _run_tool_with_retry(
    tp: ToolPart, tool_impl: Any, tool_ctx: ToolContext, ctx: ProcessorContext,
) -> tuple[bool, ProcessorEvent]:
    """使用临时故障重试逻辑执行工具。"""
    guard = ctx.loop_guard
    max_retries = guard.config.max_retries if guard else 0
    last_error = ""

    for attempt in range(max_retries + 1):
        success, event = await _run_tool(tp, tool_impl, tool_ctx, ctx)

        if success:
            # Record in loop guard and cache
            if guard:
                guard.record_tool_call(
                    tp.tool, tp.state.get("input", {}),
                    output=tp.state.get("output", ""), is_error=False,
                )
            return success, event

        last_error = tp.state.get("output", "")

        # 检查是否应该重试
        if guard and attempt < max_retries and guard.should_retry(tp.tool, last_error, attempt):
            logger.info("重试工具", tool=tp.tool, attempt=attempt + 1, error=last_error[:100])
            # 为重试重置工具状态
            tp.state["status"] = "running"
            tp.state.pop("output", None)
            tp.state.pop("is_error", None)
            await asyncio.sleep(0.5 * (attempt + 1))  # 指数退避
            continue

        # No retry — record failure and return
        if guard:
            guard.record_tool_call(
                tp.tool, tp.state.get("input", {}),
                output=last_error, is_error=True,
            )
        return success, event

    # 防御性回退：应该是不可达的，因为每次循环迭代都会返回，
    # 但防止将来可能破坏不变量的重构。
    if guard:
        guard.record_tool_call(tp.tool, tp.state.get("input", {}), output=last_error, is_error=True)
    return False, ProcessorEvent(type="tool_done", data={
        "tool": tp.tool, "call_id": tp.tool_call_id,
        "status": "error", "output": f"Failed after {max_retries + 1} attempts: {last_error}",
        "input": tp.state.get("input", {}),
    })


async def _run_tool(
    tp: ToolPart, tool_impl: Any, tool_ctx: ToolContext, ctx: ProcessorContext,
) -> tuple[bool, ProcessorEvent]:
    """执行单个工具。返回 (success, event)。"""
    from mycode.util import metrics as _metrics

    try:
        with _metrics.span("tool_call", tool=tp.tool, session_id=ctx.session_id):
            result = await tool_impl.execute(tp.state.get("input", {}), tool_ctx)
        _metrics.counter("tool_call_total", tool=tp.tool, outcome="error" if result.is_error else "ok")

        if result.is_error:
            tp.state["status"] = "error"
            tp.state["is_error"] = True
        else:
            tp.state["status"] = "completed"
            tp.state["is_error"] = False

        tp.state["output"] = result.output
        tp.state["title"] = result.title
        tp.state["metadata"] = result.metadata

        if result.display:
            tp.state["display"] = result.display
        if result.message:
            tp.state["message"] = result.message

        tp.time_completed = int(time.time() * 1000)
        ctx.parts.append(tp)
        await ctx.bus.publish(PART_UPDATED, {
            "session_id": ctx.session_id,
            "part": {
                "id": tp.id, "tool": tp.tool,
                "status": tp.state["status"], "is_error": result.is_error,
            },
        })
        event = ProcessorEvent(type="tool_done", data={
            "tool": tp.tool, "call_id": tp.tool_call_id,
            "status": tp.state["status"],
            "output": result.output[:500],
            "input": tp.state.get("input", {}),
        })
        return not result.is_error, event

    except ToolValidateError as e:
        return _tool_error(tp, str(e), "tool validation failed")

    except ToolBaseError as e:
        return _tool_error(tp, str(e), "tool error")

    except Exception as e:
        return _tool_error(tp, str(ToolRuntimeError(tp.tool, e)), "tool execution failed")


def _tool_error(tp: ToolPart, error_msg: str, log_msg: str) -> tuple[bool, ProcessorEvent]:
    """统一处理工具错误的辅助函数。"""
    tp.state["status"] = "error"
    tp.state["output"] = error_msg
    tp.state["is_error"] = True
    tp.time_completed = int(time.time() * 1000)
    logger.error(log_msg, tool=tp.tool, error=error_msg[:200])
    event = ProcessorEvent(type="tool_done", data={
        "tool": tp.tool, "call_id": tp.tool_call_id,
        "status": "error", "output": error_msg,
        "input": tp.state.get("input", {}),
    })
    return False, event


# 向后兼容的包装器
async def process(
    ctx: ProcessorContext,
    stream_input: llmmod.StreamInput,
    messages_for_tools: list[Any] | None = None,
) -> tuple[Result, list[Part]]:
    """Backward-compatible wrapper around process_stream()."""
    result: Result = "stop"
    parts: list[Part] = []
    async for event in process_stream(ctx, stream_input, messages_for_tools):
        if event.type == "finish":
            result = event.data.get("result", "stop")
            parts = event.data.get("parts", [])
    return result, parts


def build_tool_results_messages(parts: list[Part]) -> list[dict[str, Any]]:
    """Convert tool parts to assistant + tool_result messages for the next LLM call."""
    tool_calls = [p for p in parts if isinstance(p, ToolPart)]
    if not tool_calls:
        return []

    assistant_tool_calls = []
    for tp in tool_calls:
        assistant_tool_calls.append({
            "id": tp.tool_call_id,
            "type": "function",
            "function": {"name": tp.tool, "arguments": json.dumps(tp.state.get("input", {}))},
        })

    messages: list[dict[str, Any]] = []

    text_parts = [p for p in parts if isinstance(p, TextPart)]
    text_content = "".join(p.content for p in text_parts)
    messages.append({
        "role": "assistant",
        "content": text_content or None,
        "tool_calls": assistant_tool_calls,
    })

    for tp in tool_calls:
        output = tp.state.get("output", "")
        tool_message = tp.state.get("message", "")
        if tool_message:
            output = f"{output}\n\n{tool_message}"
        messages.append({
            "role": "tool",
            "tool_call_id": tp.tool_call_id,
            "content": output,
        })

    return messages
