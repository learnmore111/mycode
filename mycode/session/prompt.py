"""会话提示词 — 消息发送入口点和智能体循环编排。

流程：验证 → 创建消息 → 构建系统提示词 → 加载工具 → 运行智能体循环

功能：
- 三层循环守卫（硬限制、模式检测、接近限制智能）
- 带有检查点数据的每步原子状态
- 只读工具去重结果缓存
- 用于 CLI 显示的守卫裁决事件
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mycode.agent import agent as agentmod
from mycode.bus.events import SESSION_ERROR
from mycode.permission.permission import PermissionManager
from mycode.permission.schema import Rule
from mycode.provider import provider as providermod
from mycode.session import compaction
from mycode.session import llm as llmmod
from mycode.session import processor as proc
from mycode.session.context import build_context_snapshot
from mycode.session.loop_guard import GuardAction, LoopGuard, LoopGuardConfig
from mycode.session.message import (
    Part,
    ToolPart,
    create_assistant_message,
    create_file_part,
    create_text_part,
    create_user_message,
    get_last_assistant_time,
    persist_turn,
    save_message,
    save_part,
)
from mycode.session.system import build as build_system
from mycode.tool import registry as tool_registry
from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.bus.bus import Bus

logger = logmod.create(service="session.prompt")

# 跟踪繁忙会话 — 为每个会话使用 asyncio.Lock 以确保 TOCTOU 安全
import asyncio as _aio  # noqa: E402  # noqa: E402

_session_locks: dict[str, _aio.Lock] = {}
_locks_mutex = _aio.Lock()
# Tracks last-use timestamp for stale lock garbage collection.
_session_lock_last_used: dict[str, float] = {}
# Idle locks older than this are swept from _session_locks.
_SESSION_LOCK_GC_IDLE_SECONDS = 3600.0


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _language_alignment_instruction(user_text: str) -> str | None:
    """返回从用户文本派生的轻量级语言指令。

    仅当用户当前回合明显是中文时才添加此指令，
    以便基础提供商提示词对其他语言保持稳定。
    """
    if not user_text.strip():
        return None
    if _contains_cjk(user_text):
        return (
            "The user is writing in Chinese. Reply in Chinese. "
            "If you explicitly surface reasoning or thinking content to the user, "
            "write that surfaced content in Chinese as well. "
            "Prefer Simplified Chinese unless the user indicates otherwise."
        )
    return None


def is_session_busy(session_id: str) -> bool:
    """检查会话当前是否正在处理中。"""
    lock = _session_locks.get(session_id)
    return lock is not None and lock.locked()


def _gc_session_locks_locked() -> None:
    """删除空闲、未锁定的会话锁条目。必须在持有 _locks_mutex 时调用。"""
    now = time.time()
    stale: list[str] = []
    for sid, last in list(_session_lock_last_used.items()):
        if now - last < _SESSION_LOCK_GC_IDLE_SECONDS:
            continue
        lock = _session_locks.get(sid)
        if lock is None or not lock.locked():
            stale.append(sid)
    for sid in stale:
        _session_locks.pop(sid, None)
        _session_lock_last_used.pop(sid, None)


async def _acquire_session(session_id: str) -> bool:
    """尝试获取会话以进行处理。

    如果获取成功则返回 True，如果已在忙则返回 False。
    使用 asyncio.Lock 防止 TOCTOU 竞态条件。
    locked() 检查和 acquire() 都在 _locks_mutex 内部，
    以防止两个协程同时通过检查。
    """
    async with _locks_mutex:
        _gc_session_locks_locked()
        if session_id not in _session_locks:
            _session_locks[session_id] = _aio.Lock()
        lock = _session_locks[session_id]

        if lock.locked():
            return False
        await lock.acquire()
        _session_lock_last_used[session_id] = time.time()
        logger.debug("session acquired", session_id=session_id)
        return True


def _release_session(session_id: str) -> None:
    """处理后释放会话。可以安全地多次调用。"""
    lock = _session_locks.get(session_id)
    if lock is None:
        return
    if lock.locked():
        try:
            lock.release()
            logger.debug("session released", session_id=session_id)
        except RuntimeError:
            # Lock already released (idempotent guard for cleanup paths).
            logger.debug("session release ignored — already released", session_id=session_id)
    _session_lock_last_used[session_id] = time.time()

@dataclass
class PromptInput:
    session_id: str
    parts: list[dict[str, Any]]
    model: str | None = None  # "provider/model"
    agent: str | None = None
    message_id: str | None = None
    variant: str | None = None
    system: str | None = None
    # 可选的中止信号。设置后，智能体循环和底层 llm.stream()
    # 都会监视它，并在一个块内退出，而不是等待当前 LLM 响应完成。
    abort_event: _aio.Event | None = None

@dataclass
class PromptEvent:
    """产生给 CLI 层的事件。

    类型：
      - "started":    会话已启动。data 包含模型/代理信息。
      - "reasoning_delta": LLM 的增量思考文本。data["content"] 是增量。
      - "text_delta": LLM 的增量文本。data["content"] 是增量。
      - "tool_start": 已识别工具调用。data["tool"]、data["call_id"]。
      - "tool_running": 工具执行已开始。data["tool"]、data["call_id"]、data["input"]。
      - "tool_done":  工具已完成。data["tool"]、data["status"]、data["output"]、data["input"]。
      - "error":      发生错误。data["message"]。
      - "compact":    上下文压缩进行中。
      - "guard_warn": 循环守卫警告。data["reason"]、data["layer"]。
      - "guard_stop": 循环守卫停止了循环。data["reason"]、data["layer"]。
      - "context_snapshot": LLM 调用前的完整上下文快照。data 包含 system/tools/messages/summary。
      - "done":       所有迭代已完成。data 包含 tokens/cost/context 统计信息。
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)


async def prompt(
    prompt_input: PromptInput,
    bus: Bus,
    *,
    history: list[dict[str, Any]] | None = None,
    debug: bool = False,
    permission_manager: PermissionManager | None = None,
) -> AsyncGenerator[PromptEvent, None]:
    """发送消息并流式传输 AI 响应。

    这是智能体循环的主要入口点。
    在模型生成文本和执行工具时实时产生 PromptEvent。

    参数:
        permission_manager: 外部 PermissionManager 实例。提供时
            （例如由 HTTP 服务器），权限 "ask" 请求通过此管理器发布，
            以便前端可以回复。为 None 时，创建本地实例（适用于 CLI 或 headless 模式）。
    """
    session_id = prompt_input.session_id

    if not await _acquire_session(session_id):
        yield PromptEvent(type="error", data={"message": f"Session {session_id} is busy"})
        return

    try:
        # Resolve model
        if prompt_input.model:
            provider_id, model_id = providermod.parse_model(prompt_input.model)
        else:
            provider_id, model_id = await providermod.default_model()

        try:
            model = await providermod.get_model(provider_id, model_id)
        except Exception as e:
            yield PromptEvent(type="error", data={"message": f"Model not found: {e}"})
            return

        # Resolve agent
        agent_name = prompt_input.agent or await agentmod.default_agent()
        agent = await agentmod.get(agent_name)
        if not agent:
            yield PromptEvent(type="error", data={"message": f"Agent not found: {agent_name}"})
            return

        # Build system prompt
        system = build_system(model=model, agent_prompt=agent.prompt, instructions=None)
        if prompt_input.system:
            system.append(prompt_input.system)

        # 内存和技能现在作为系统提醒消息注入（不在系统提示词中）
        # 这使系统提示词保持静态，以便跨会话复用前缀缓存

        # Load tools
        tool_registry.register_builtins()
        tools = tool_registry.to_llm_tools()

        # 预计算系统 + 工具 token 估计值，用于压缩检查和上下文栏回退。
        # 缓存变体避免了每次回合重新执行约 80KB 的 JSON 到字节编码；
        # 负载仅在代理提示词或工具注册表更改时才会变化。
        system_tokens_est = compaction.estimate_tokens_cached("\n\n".join(system))
        tools_tokens_est = (
            compaction.estimate_tokens_cached(json.dumps(tools, ensure_ascii=False))
            if tools else 0
        )

        # 构建用户消息内容。
        #
        # 向后兼容：如果所有部分都是纯文本，我们保留传统字符串形式。
        # 如果任何部分是图片 / PDF / 音频，我们发出 OpenAI 兼容的内容列表，
        # 供接受多模态输入的 LLM 提供商使用（litellm 会在下游为 Anthropic/Gemini 规范化形状）。
        user_text = ""
        attachment_parts: list[dict[str, Any]] = []
        for part in prompt_input.parts:
            ptype = part.get("type")
            if ptype == "text":
                user_text += part.get("content", "")
            elif ptype == "image":
                if not getattr(model.capabilities.input, "image", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept image input; drop the attachment or switch model.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)
                if url:
                    attachment_parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    logger.warn("dropping image part with no usable content", keys=list(part.keys()))
            elif ptype in ("pdf", "file"):
                if not getattr(model.capabilities.input, "pdf", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept PDF input.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)  # same URL/base64 handling
                if url:
                    attachment_parts.append({"type": "file", "file": {"file_data": url}})
            elif ptype == "audio":
                if not getattr(model.capabilities.input, "audio", False):
                    yield PromptEvent(type="error", data={
                        "message": f"Model {model_id} does not accept audio input.",
                        "code": "bad_request",
                        "retryable": False,
                    })
                    return
                url = _normalize_image_url(part)
                if url:
                    attachment_parts.append({"type": "input_audio", "input_audio": {"data": url}})

        user_content: Any
        if attachment_parts:
            content_list: list[dict[str, Any]] = []
            if user_text:
                content_list.append({"type": "text", "text": user_text})
            content_list.extend(attachment_parts)
            user_content = content_list
        else:
            user_content = user_text

        language_instruction = _language_alignment_instruction(user_text)
        if language_instruction:
            system.append(language_instruction)

        # 持久化此回合使用的确切系统提示词，以便历史视图可以
        # 在不依赖前端猜测的情况下重建上下文窗口。
        assistant_system = list(system)

        # Build conversation messages
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_content})

        # Proactive pruning: if cache likely expired since last interaction,
        # prune old tool outputs now to reduce re-fill cost on the next LLM call.
        if history:
            last_time = get_last_assistant_time(session_id)
            if compaction.is_cache_likely_expired(model, last_time):
                messages, freed = compaction.prune_tool_outputs(messages)
                if freed > 0:
                    logger.info("proactive cache-expiry prune", tokens_freed=freed)

        # 创建助手消息
        user_msg = create_user_message(session_id, prompt_input.message_id)
        assistant_msg = create_assistant_message(
            session_id, user_msg.id, provider_id, model_id, agent_name,
        )
        assistant_msg.system = assistant_system
        # 标记此回合，以便回滚 API 可以识别截断点。
        # ``next_turn_number`` 是数据库查询，不是免费的 — 但它每个 prompt() 调用只运行一次。
        try:
            from mycode.session.message import next_turn_number
            assistant_msg.turn_number = next_turn_number(session_id)  # type: ignore[attr-defined]
        except Exception:
            logger.debug("turn_number lookup failed; continuing without tag", session_id=session_id)

        yield PromptEvent(type="started", data={
            "session_id": session_id,
            "model": f"{provider_id}/{model_id}",
            "agent": agent_name,
        })

        # 使用三层保护初始化循环守卫
        max_iterations = agent.steps or 50
        guard_config = LoopGuardConfig(max_iterations=max_iterations)
        guard = LoopGuard(config=guard_config)

        # 构建权限管理器和代理权限规则集
        perm_manager = permission_manager or PermissionManager(bus, project_id=session_id)
        agent_ruleset: list[Rule] = [
            Rule(
                permission=r.get("permission", "*"),
                pattern=r.get("pattern", "*"),
                action=r.get("action", "ask"),
            )
            for r in (agent.permission or [])
        ]

        # 带循环守卫的智能体循环
        ctx = proc.ProcessorContext(
            session_id=session_id,
            model=model,
            assistant_message=assistant_msg,
            bus=bus,
            loop_guard=guard,
            permission_manager=perm_manager,
            agent_permission=agent_ruleset,
        )

        # 预构建压缩 kwargs，以便两个调用点共享相同的参数
        compact_kwargs: dict[str, Any] = {
            "system": system,
            "tools": tools if model.capabilities.toolcall else None,
            "model": model,
            "api_key": await providermod.get_api_key(provider_id),
            "api_base": model.api.url or None,
        }

        all_parts: list[Part] = []
        iterations_done = 0
        stop_reason = ""
        prev_iter_usage: dict[str, int | float] | None = None  # 来自上一次迭代的每轮使用量
        _reminder_user_messages: list[dict[str, Any]] = []  # 要持久化的系统提醒字典

        # 从历史记录初始化增量提醒状态，以便当会话已包含先前提醒时，
        # 我们不会在每次新的 prompt() 调用时重新发送完整的技能/日期提醒。
        prev_skills, prev_date, prev_memory_index_hash = _extract_reminder_state_from_history(history)

        for iteration in range(max_iterations):
            iterations_done = iteration + 1

            # === 循环守卫检查（每次迭代之前）===
            verdict = guard.check(iteration)

            if verdict.action == GuardAction.FORCE_STOP:
                logger.warn("guard force stop", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_stop", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })
                stop_reason = verdict.reason
                break

            if verdict.action == GuardAction.STOP:
                logger.info("guard stop", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_stop", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })
                stop_reason = verdict.reason
                break

            if verdict.action == GuardAction.WARN:
                logger.info("guard warn", reason=verdict.reason, layer=verdict.layer)
                yield PromptEvent(type="guard_warn", data={
                    "reason": verdict.reason, "layer": verdict.layer,
                })

            # === 步骤开始（原子状态）===
            step = guard.begin_step(iteration)

            # 上下文压缩检查
            # 如果未配置模型上下文限制，则回退到 32K（防止无限制增长）
            context_limit = model.limit.context if model.limit.context > 0 else 32_000
            if context_limit > 0 and compaction.should_compact(
                messages=messages,
                model_context=context_limit,
                system_tokens=system_tokens_est,
                tools_tokens=tools_tokens_est,
            ):
                logger.info("context overflow detected, compacting")
                messages, compact_metrics = await compaction.compact(messages, **compact_kwargs)
                yield PromptEvent(type="compact", data={
                    "session_id": session_id,
                    "old_message_count": compact_metrics.old_message_count,
                    "old_message_tokens": compact_metrics.old_message_tokens,
                    "summary_length": compact_metrics.summary_length,
                    "removed_turn_count": compact_metrics.removed_turn_count,
                })
                # Save compaction event for audit trail (background)
                from mycode.session.message import save_compaction_event
                def _save_compact_event(
                    _sid: str = session_id,
                    _iter: int = iteration,
                    _metrics: object = compact_metrics,
                ) -> None:
                    save_compaction_event(
                        session_id=_sid,
                        iteration=_iter,
                        metrics={
                            'old_message_count': _metrics.old_message_count,  # type: ignore[attr-defined]
                            'old_message_tokens': _metrics.old_message_tokens,  # type: ignore[attr-defined]
                            'summary_length': _metrics.summary_length,  # type: ignore[attr-defined]
                            'removed_turn_count': _metrics.removed_turn_count,  # type: ignore[attr-defined]
                        },
                        old_messages=_metrics.old_messages,  # type: ignore[attr-defined]
                        summary=_metrics.summary,  # type: ignore[attr-defined]
                    )
                await _aio.to_thread(_save_compact_event)

            # 构建系统提醒（技能 + 记忆 + 日期）并将其附加到当前用户消息，
            # 而不是注入单独的消息。
            # 这保持 ContextViewer 清洁并避免视觉分离。
            # 仍然跟踪提醒字典，作为元消息进行数据库持久化。
            reminder_text, prev_skills, prev_date, prev_memory_index_hash = _build_system_reminders(
                prompt_input,
                prev_skills,
                prev_date,
                prev_memory_index_hash,
            )
            if reminder_text:
                _attach_reminder_to_last_user_message(messages, reminder_text)
                _reminder_user_messages.append({"content": reminder_text})
            iter_messages = messages

            stream_input = llmmod.StreamInput(
                model=model,
                messages=iter_messages,
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                temperature=agent.temperature,
                top_p=agent.top_p,
                api_key=await providermod.get_api_key(provider_id),
                api_base=model.api.url or None,
                abort_event=prompt_input.abort_event,
            )

            # === 在此迭代之前快照累积 token ===
            # process_stream 之后，delta = 当前 - 快照给出每轮使用量。
            tokens_snap_input = assistant_msg.tokens_input
            tokens_snap_output = assistant_msg.tokens_output
            tokens_snap_cache_read = assistant_msg.tokens_cache_read
            tokens_snap_cache_write = assistant_msg.tokens_cache_write
            tokens_snap_reasoning = assistant_msg.tokens_reasoning

            # === 用于 UI 上下文查看器的上下文快照 ===
            # actual_usage 显示上一次迭代的每轮值
            #（在迭代 0 时没有先前数据，因此为 None）
            snapshot_data = build_context_snapshot(
                system=system,
                tools=tools if model.capabilities.toolcall else None,
                messages=iter_messages,
                model_id=f"{provider_id}/{model_id}",
                context_limit=model.limit.context if model.limit.context > 0 else 0,
                iteration=iteration,
                has_history=bool(history),
                actual_usage=prev_iter_usage,
            )
            yield PromptEvent(type="context_snapshot", data=snapshot_data)

            # === 调试：在 LLM 调用前转储输入 ===
            if debug:
                # 从上一轮迭代的片段中收集缓存的 tool_call_ids
                cached_call_ids = []
                if iteration > 0 and all_parts:
                    for p in all_parts:
                        if isinstance(p, ToolPart) and p.state.get("metadata", {}).get("cached", False):
                            cached_call_ids.append(p.tool_call_id)
                debug_file = _debug_dump(
                    session_id, iteration, "input",
                    messages=messages, system=system,
                    model=f"{provider_id}/{model_id}",
                    tool_count=len(tools) if tools else 0,
                    cached_tool_call_ids=cached_call_ids,
                )
                yield PromptEvent(type="debug_iter", data={
                    "iteration": iteration, "phase": "input",
                    "message_count": len(messages), "file": debug_file,
                })

            # Stream events from processor
            result: proc.Result = "stop"
            iteration_parts: list[Part] = []
            iter_text_length = 0
            iter_text_content = ""

            async for event in proc.process_stream(ctx, stream_input):
                if event.type == "reasoning_delta":
                    yield PromptEvent(type="reasoning_delta", data=event.data)

                elif event.type == "text_delta":
                    iter_text_content += event.data.get("content", "")
                    yield PromptEvent(type="text_delta", data=event.data)

                elif event.type == "tool_start":
                    yield PromptEvent(type="tool_start", data=event.data)

                elif event.type == "tool_running":
                    yield PromptEvent(type="tool_running", data=event.data)

                elif event.type == "tool_done":
                    yield PromptEvent(type="tool_done", data=event.data)

                elif event.type == "error":
                    yield PromptEvent(type="error", data=event.data)
                    step.fail(event.data.get("message", "unknown"))

                elif event.type == "finish":
                    result = event.data.get("result", "stop")
                    iteration_parts = event.data.get("parts", [])
                    iter_text_length = event.data.get("text_length", 0)

            # === 步骤完成 ===
            if step.status.value != "failed":
                guard.complete_step(step, text_length=iter_text_length)
                # Record tool calls for this step
                for p in iteration_parts:
                    if isinstance(p, ToolPart):
                        step.tool_calls.append({
                            "tool": p.tool,
                            "status": p.state.get("status", "?"),
                            "cached": p.state.get("metadata", {}).get("cached", False),
                        })

            # === 调试：在 LLM 调用后转储输出 ===
            if debug:
                tool_outputs = []
                for p in iteration_parts:
                    if isinstance(p, ToolPart):
                        tool_outputs.append({
                            "tool": p.tool, "call_id": p.tool_call_id,
                            "input": p.state.get("input", {}),
                            "output": p.state.get("output", "")[:2000],
                            "status": p.state.get("status", "?"),
                            "cached": p.state.get("metadata", {}).get("cached", False),
                        })
                debug_file = _debug_dump(
                    session_id, iteration, "output",
                    text=iter_text_content, text_length=iter_text_length,
                    tool_calls=tool_outputs, result=result,
                )
                yield PromptEvent(type="debug_iter", data={
                    "iteration": iteration, "phase": "output",
                    "message_count": len(messages), "file": debug_file,
                })

            all_parts.extend(iteration_parts)

            # === 计算每轮 token 增量 ===
            iter_input_tokens = assistant_msg.tokens_input - tokens_snap_input
            prev_iter_usage = {
                "input_tokens": iter_input_tokens,
                "output_tokens": assistant_msg.tokens_output - tokens_snap_output,
                "cache_read_tokens": assistant_msg.tokens_cache_read - tokens_snap_cache_read,
                "cache_write_tokens": assistant_msg.tokens_cache_write - tokens_snap_cache_write,
                "reasoning_tokens": assistant_msg.tokens_reasoning - tokens_snap_reasoning,
                "total_cost": assistant_msg.cost,  # cumulative cost is still useful
            }

            # Token 估计遥测 — 比较启发式与 API 实际使用量
            if iter_input_tokens > 0:
                est = (
                    compaction.estimate_messages_tokens(iter_messages)
                    + system_tokens_est
                    + tools_tokens_est
                )
                compaction.log_token_accuracy(est, iter_input_tokens, f"{provider_id}/{model_id}")

            if result == "stop":
                break

            if result == "continue":
                tool_messages = proc.build_tool_results_messages(iteration_parts)
                messages.extend(tool_messages)
                continue

        # 最终确定 — 在后台持久化（不要阻塞完成事件）
        assistant_msg.time_completed = int(time.time() * 1000)

        # context.used = 上一轮迭代的 input_tokens（= 实际上下文窗口占用）
        # 如果没有 API 数据，则回退到包含 system+tools 的启发式估计。
        last_iter_input = prev_iter_usage["input_tokens"] if prev_iter_usage else 0
        fallback_est = (
            compaction.estimate_messages_tokens(messages) + system_tokens_est + tools_tokens_est
        )

        yield PromptEvent(type="done", data={
            "session_id": session_id,
            "tokens": {
                "input": assistant_msg.tokens_input,
                "output": assistant_msg.tokens_output,
                "reasoning": assistant_msg.tokens_reasoning,
                "cache_read": assistant_msg.tokens_cache_read,
                "cache_write": assistant_msg.tokens_cache_write,
            },
            "cost": assistant_msg.cost,
            "context": {
                "used": last_iter_input if last_iter_input > 0 else fallback_est,
                "limit": model.limit.context,
            },
            "iterations": iterations_done,
            "parts": len(all_parts),
            "messages": messages,
            "stop_reason": stop_reason,
            "checkpoint": guard.checkpoint,
            "raw_usage": getattr(assistant_msg, "raw_usage", None),
        })

        # 在产生 done 之后持久化（用户立即看到结果）

        # 保存用户消息 + 文本片段 + 附件文件片段，然后是助手回合
        user_text_part = create_text_part(session_id, user_msg.id)
        user_text_part.content = user_text

        # 为每个附件创建 FilePart，以便它们在 rebuild_history 中保留
        user_file_parts: list[Part] = []
        for part in prompt_input.parts:
            ptype = part.get("type")
            if ptype in ("image", "pdf", "file", "audio"):
                mime = part.get("mime") or part.get("mime_type") or ""
                content_val = part.get("content") or part.get("url") or ""
                fname = part.get("filename") or part.get("name") or ""
                fp = create_file_part(
                    session_id,
                    user_msg.id,
                    mime_type=mime,
                    content=content_val,
                    filename=fname,
                )
                user_file_parts.append(fp)

        # 准备用于持久化的系统提醒用户消息
        reminder_persist: list[tuple[Any, Any]] = []
        for rmd in _reminder_user_messages:
            r_msg = create_user_message(session_id, is_meta=True, origin="system")
            r_part = create_text_part(session_id, r_msg.id)
            r_part.content = rmd["content"]
            reminder_persist.append((r_msg, r_part))

        def _persist_all() -> None:
            save_message(user_msg)
            save_part(user_text_part)
            for fp in user_file_parts:
                save_part(fp)
            for r_msg, r_part in reminder_persist:
                save_message(r_msg)
                save_part(r_part)
            persist_turn(session_id, assistant_msg, all_parts)

        await _aio.to_thread(_persist_all)

    except Exception as e:
        logger.error("prompt failed", error=str(e))
        yield PromptEvent(type="error", data={"message": str(e)})
        await bus.publish(SESSION_ERROR, {"session_id": session_id, "error": {"message": str(e)}})
    finally:
        _release_session(session_id)


def _normalize_image_url(part: dict[str, Any]) -> str | None:
    """将客户端提供的图片部分强制转换为 ``image_url`` URL。

    接受三种输入形状：
      1) ``{"type": "image", "url": "https://…"}``              — 直传
      2) ``{"type": "image", "content": "data:image/…;base64,…"}`` — 直传
      3) ``{"type": "image", "content": "<raw-b64>", "mime": "image/png"}``
         — 包装为 ``data:<mime>;base64,<raw>`` 以便提供商接受。
    如果没有可用的负载，则返回 None。我们故意不验证 base64 —
    litellm / 提供商会拒绝错误数据，并给出比我们的更可操作的具体错误。
    """
    url = part.get("url")
    if isinstance(url, str) and url:
        return url
    content = part.get("content")
    if not isinstance(content, str) or not content:
        return None
    if content.startswith("data:") or content.startswith(("http://", "https://")):
        return content
    mime = part.get("mime") or part.get("mime_type") or "image/png"
    return f"data:{mime};base64,{content}"


def _debug_dump(session_id: str, iteration: int, phase: str, **data: Any) -> str:
    """将调试数据作为 JSON 文件写入 .mycode/debug/。

    返回用于显示的文件路径。
    """
    import json
    from pathlib import Path

    debug_dir = Path(".mycode") / "debug" / session_id[:12]
    debug_dir.mkdir(parents=True, exist_ok=True)
    filename = f"iter{iteration:02d}_{phase}.json"
    filepath = debug_dir / filename

    # For messages, truncate long content to keep files readable
    dump_data = {"session_id": session_id, "iteration": iteration, "phase": phase, "timestamp": time.time()}
    for key, value in data.items():
        if key == "messages" and isinstance(value, list):
            # Truncate each message's content for readability
            truncated = []
            for msg in value:
                m = dict(msg)
                if isinstance(m.get("content"), str) and len(m["content"]) > 3000:
                    m["content"] = m["content"][:3000] + f"\n... ({len(msg['content'])} chars total)"
                truncated.append(m)
            dump_data[key] = truncated
        else:
            dump_data[key] = value

    try:
        filepath.write_text(
            json.dumps(dump_data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warn("debug dump failed", error=str(e))

    return str(filepath)


def _extract_reminder_state_from_history(
    history: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, str]] | None, str | None, str | None]:
    """Scan history for previously persisted system-reminder messages.

    Returns ``(prev_skills, prev_date)`` extracted from the **last** reminder
    that contained each section.  If the history has no reminders at all,
    returns ``(None, None)`` so that ``_build_system_reminders`` treats the
    next call as the initial (full) send.
    """
    if not history:
        return None, None, None


    prev_skills: list[dict[str, str]] | None = None
    prev_date: str | None = None
    memory_index_hash: str | None = None

    # Walk history in order; later reminders overwrite earlier ones.
    for msg in history:
        if msg.get("role") != "user":
            continue
        content: str = msg.get("content") or ""
        if "<system-reminder>" not in content:
            continue
        hash_match = re.search(r'<memory_index[^>]*hash="([^"]+)"', content)
        if hash_match:
            memory_index_hash = hash_match.group(1)

        # --- Extract skills ---
        # Full list format: "The following skills are available ..."
        # Incremental format: "New skills available:"
        # Both list skills as "- name: description" lines.
        # We look for the *full list* pattern to capture the complete state.
        full_match = re.search(
            r"<system-reminder>\s*The following skills are available[^\n]*\n(.*?)</system-reminder>",
            content,
            re.DOTALL,
        )
        if full_match:
            skills: list[dict[str, str]] = []
            for line in full_match.group(1).strip().splitlines():
                line = line.strip()
                if line.startswith("- "):
                    line = line[2:]
                    if ": " in line:
                        name, desc = line.split(": ", 1)
                        skills.append({"name": name.strip(), "description": desc.strip()})
                    else:
                        skills.append({"name": line.strip(), "description": ""})
            if skills:
                prev_skills = skills
        else:
            # Check for incremental "New skills available:" — means prev_skills
            # existed and was extended; we can't reconstruct the full list from
            # just an incremental update, but we know skills *were* sent before.
            # Build a merged view by adding new skills to existing prev_skills.
            inc_match = re.search(
                r"<system-reminder>\s*New skills available:\s*\n(.*?)</system-reminder>",
                content,
                re.DOTALL,
            )
            if inc_match and prev_skills is not None:
                existing_names = {s["name"] for s in prev_skills}
                for line in inc_match.group(1).strip().splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        line = line[2:]
                        if ": " in line:
                            name, desc = line.split(": ", 1)
                            name, desc = name.strip(), desc.strip()
                        else:
                            name, desc = line.strip(), ""
                        if name not in existing_names:
                            prev_skills.append({"name": name, "description": desc})
                            existing_names.add(name)

        # --- Extract date ---
        date_match = re.search(
            r"<system-reminder>\s*(?:Today's date|Date has changed[^:]*?):\s*(.+?)\s*</system-reminder>",
            content,
        )
        if date_match:
            prev_date = date_match.group(1).strip()

    return prev_skills, prev_date, memory_index_hash


def _build_system_reminders(
    prompt_input: PromptInput,
    prev_skills: list[dict[str, str]] | None,
    prev_date: str | None,
    prev_memory_index_hash: str | None,
) -> tuple[str, list[dict[str, str]], str, str | None]:
    """Build <system-reminder> content for skills, memory, and date.

    All sections are merged into a **single** <system-reminder> block so
    the UI can display it as one clean unit.

    Returns (reminder_text, current_skills_snapshot, current_date).

    Skills use an incremental strategy:
    - First call (prev_skills is None) or modifications/deletions -> full list
    - Only additions -> only the new skills
    - No change -> reuse previous text (empty skills section)

    Date uses an incremental strategy:
    - First call (prev_date is None) -> full date
    - Date changed -> date update reminder
    - No change -> omit

    Memory is always included if found.
    """
    inner_sections: list[str] = []

    # --- Skills section ---
    try:
        from mycode.tool.skill import list_skills_with_descriptions

        current_skills = list_skills_with_descriptions()
    except Exception:
        current_skills = []

    skills_text = _build_skills_reminder(current_skills, prev_skills)
    if skills_text:
        inner_sections.append(skills_text)

    # --- Date section ---
    current_date = time.strftime("%A, %b %d, %Y")
    date_text = _build_date_reminder(current_date, prev_date)
    if date_text:
        inner_sections.append(date_text)

    # --- Memory section ---
    memory_text, current_memory_index_hash = _build_memory_reminder(prev_memory_index_hash)
    if memory_text:
        inner_sections.append(memory_text)
    if current_memory_index_hash:
        prev_memory_index_hash = current_memory_index_hash

    if not inner_sections:
        return "", current_skills, current_date, prev_memory_index_hash

    # Wrap all sections in a single <system-reminder> tag
    body = "\n\n".join(inner_sections)
    reminder = f"<system-reminder>\n{body}\n</system-reminder>"
    return reminder, current_skills, current_date, prev_memory_index_hash


def _attach_reminder_to_last_user_message(
    messages: list[dict[str, Any]],
    reminder_text: str,
) -> None:
    """Append system-reminder text to the last user message's content.

    Supports both plain-string and multimodal content-list formats.
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            content = messages[i]["content"]
            if isinstance(content, str):
                messages[i]["content"] = content + "\n\n" + reminder_text
            elif isinstance(content, list):
                # Multimodal (image/pdf/audio): append a text part
                content.append({"type": "text", "text": reminder_text})
            else:
                # Fallback: coerce to string
                messages[i]["content"] = str(content) + "\n\n" + reminder_text
            return
    # No user message found — should not happen in normal flow, but
    # inject as fallback to preserve the reminder information.
    messages.append({"role": "user", "content": reminder_text})


def _build_skills_reminder(
    current: list[dict[str, str]],
    prev: list[dict[str, str]] | None,
) -> str:
    """Build the skills reminder section (plain content, no wrapper tags).

    Returns empty string if no skills exist or nothing changed.
    """
    if not current:
        return ""

    # First call — full list
    if prev is None:
        lines = ["The following skills are available for use with the `skill` tool:", ""]
        for s in current:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
        return "\n".join(lines)

    # No change
    if current == prev:
        return ""

    # Check if purely additive (current is a superset of prev)
    prev_names = {s["name"] for s in prev}
    prev_map = {s["name"]: s["description"] for s in prev}
    current_map = {s["name"]: s["description"] for s in current}

    # Superset check: all prev items still present and unchanged
    is_superset = all(current_map.get(n) == d for n, d in prev_map.items())

    if is_superset:
        new_skills = [s for s in current if s["name"] not in prev_names]
        if not new_skills:
            return ""
        lines = ["New skills available:", ""]
        for s in new_skills:
            desc = s["description"]
            lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
        return "\n".join(lines)

    # Modification or deletion — full list
    lines = ["The following skills are available for use with the `skill` tool:", ""]
    for s in current:
        desc = s["description"]
        lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
    return "\n".join(lines)


def _build_date_reminder(current: str, prev: str | None) -> str:
    """Build the date reminder section (plain content, no wrapper tags).

    Incremental strategy:
    - First call (prev is None) -> full date
    - Date changed -> date update reminder
    - No change -> empty string (omit)
    """
    if prev is None:
        return f"Today's date: {current}"
    if current != prev:
        return f"Date has changed. Today's date is now: {current}"
    return ""


def _build_memory_reminder(prev_index_hash: str | None) -> tuple[str, str | None]:
    """Build the memory reminder section (plain content, no wrapper tags).

    Returns ``(text, current_index_hash)``. The MEMORY.md index is included
    on the first turn and whenever it changes after a memory tool write/update.
    """
    try:
        from mycode.project.instance import current_or_none
        from mycode.session.memory.memdir import load_memory_index, memory_index_path

        inst = current_or_none()
        if not inst:
            return "", None

        index_text = load_memory_index(inst.directory)
        if not index_text:
            return _memory_tool_guidance(inst.directory), None

        current_hash = hashlib.sha256(index_text.encode("utf-8")).hexdigest()[:16]
        if current_hash == prev_index_hash:
            return "", current_hash

        path = memory_index_path(inst.directory)
        reason = "updated" if prev_index_hash else "initial"
        text = (
            f'<memory_index hash="{current_hash}" status="{reason}">\n'
            f"Contents of {path} (user's auto-memory, persists across conversations):\n\n"
            f"{index_text}\n"
            "</memory_index>\n\n"
            f"{_memory_tool_guidance(inst.directory)}"
        )
        return text, current_hash
    except Exception:
        pass  # Memory injection is best-effort
    return "", prev_index_hash


def _memory_tool_guidance(project_path: str) -> str:
    from mycode.session.memory.memdir import memdir_path

    return (
        "<memory_tool_guidance>\n"
        "Use the `memory` tool to inspect or maintain long-term memories. "
        "The index above is only a directory; call `memory` with action=\"read\" before relying on a specific memory's details. "
        "Call `memory` with action=\"write\" or \"update\" only for durable user preferences, feedback, project facts, or references "
        "that cannot be derived from code, git history, or CLAUDE.md. "
        f"The memory directory already exists at {memdir_path(project_path)}; write to it through the memory tool rather than checking or creating it.\n"
        "</memory_tool_guidance>"
    )
