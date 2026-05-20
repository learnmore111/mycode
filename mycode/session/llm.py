"""使用 litellm 的 LLM 流式接口。

包装 litellm.acompletion 以提供统一的流式接口。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging as _logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import litellm

from mycode.provider.provider import litellm_model_name
from mycode.util import log as logmod
from mycode.util import metrics

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mycode.provider.schema import Model

logger = logmod.create(service="llm")

# 抑制 litellm 的冗长日志记录
litellm.suppress_debug_info = True
litellm.set_verbose = False  # type: ignore[attr-defined]
_logging.getLogger("LiteLLM").setLevel(_logging.WARNING)
_logging.getLogger("litellm").setLevel(_logging.WARNING)
_logging.getLogger("httpx").setLevel(_logging.WARNING)


@dataclass
class StreamInput:
    """LLM 流式输入。"""

    model: Model
    messages: list[dict[str, Any]]
    system: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | None = None  # "auto" | "required" | "none"
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    api_key: str | None = None
    api_base: str | None = None
    # 设置后，stream() 在块之间监视此事件，并在触发时立即
    # 终止 HTTP 响应 — 让处理器在一个块内响应用户发起的
    # 中止，而不是等待 LLM 自行停止生成。
    abort_event: asyncio.Event | None = None


@dataclass
class TextDelta:
    type: str = "text-delta"
    text: str = ""


@dataclass
class ReasoningDelta:
    type: str = "reasoning-delta"
    text: str = ""


@dataclass
class ToolCallDelta:
    type: str = "tool-call"
    tool_call_id: str = ""
    tool_name: str = ""
    args: str = ""  # 参数的 JSON 字符串


@dataclass
class ToolCallPartial:
    type: str = "tool-call-streaming-start"
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class ToolCallArgsPartial:
    type: str = "tool-call-delta"
    tool_call_id: str = ""
    args_delta: str = ""


@dataclass
class FinishEvent:
    type: str = "finish"
    reason: str = "stop"  # "stop" | "tool-calls" | "length"
    usage: dict[str, int] = field(default_factory=dict)
    raw_usage: dict[str, Any] | None = None
    cost: float = 0.0


@dataclass
class ErrorEvent:
    type: str = "error"
    error: str = ""
    # 分类代码，以便调用方（处理器、UI、重试逻辑）可以在
    # 不匹配错误字符串的情况下做出决策。值遵循常见的 HTTP 风格分类：
    #   "rate_limit"       — 429 / 限流，可安全退避重试
    #   "auth"             — 凭据无效/过期，不要重试
    #   "bad_request"      — 参数无效、提示词过长等
    #   "context_overflow" — 提示词超出模型上下文窗口
    #   "content_filter"   — 提供商因内容政策拒绝
    #   "not_found"        — 模型 / 端点缺失
    #   "timeout"          — 请求超时
    #   "connection"       — 临时网络错误，可重试
    #   "server"           — 5xx 上游错误，可重试
    #   "unknown"          — 其他所有情况
    error_code: str = "unknown"
    retryable: bool = False
    status_code: int | None = None


async def _with_abort(
    response: Any, abort_event: asyncio.Event | None,
) -> AsyncGenerator[Any, None]:
    """迭代 ``response``，但如果 ``abort_event`` 触发则提前停止。

    litellm 的流式响应是一个异步迭代器。如果我们简单地
    ``async for chunk in response``，唯一的退出点是耗尽或
    上游错误 — 用户点击中止可能需要等待数十秒
    才能让 LLM 停止输出。这里我们将每个 ``__anext__`` 调用
    与中止事件竞争，并在事件设置后立即干净地中断。
    """
        if abort_event is None:
            async for chunk in response:
                yield chunk
            return

    it = response.__aiter__()
    while True:
        next_task = asyncio.ensure_future(it.__anext__())
        abort_task = asyncio.ensure_future(abort_event.wait())
        done, _pending = await asyncio.wait(
            {next_task, abort_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        if next_task in done:
            abort_task.cancel()
            try:
                chunk = next_task.result()
            except StopAsyncIteration:
                return
            yield chunk
        else:
            # 中止事件先触发。取消正在进行的块读取并
            # 关闭上游响应，以便提供商停止发送。
            next_task.cancel()
            with contextlib.suppress(BaseException):
                close = getattr(response, "aclose", None) or getattr(response, "close", None)
                if close is not None:
                    maybe = close()
                    if asyncio.iscoroutine(maybe):
                        await maybe
            logger.info("流被消费者中止")
            return


def _classify_exception(exc: BaseException) -> tuple[str, bool, int | None]:
    """将 litellm / 通用异常映射为 (error_code, retryable, status)。"""
    # 超时优先 — asyncio.TimeoutError 和 litellm 都会包装它。
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout", True, None

    # litellm 可能无法在每个平台上干净地导入；属性探测比
    # 对我们可能无法解析的类使用 isinstance 更安全。
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)

    mapping: dict[str, tuple[str, bool]] = {
        "RateLimitError": ("rate_limit", True),
        "RouterRateLimitError": ("rate_limit", True),
        "RouterRateLimitErrorBasic": ("rate_limit", True),
        "AuthenticationError": ("auth", False),
        "PermissionDeniedError": ("auth", False),
        "BadRequestError": ("bad_request", False),
        "InvalidRequestError": ("bad_request", False),
        "UnprocessableEntityError": ("bad_request", False),
        "UnsupportedParamsError": ("bad_request", False),
        "JSONSchemaValidationError": ("bad_request", False),
        "ContextWindowExceededError": ("context_overflow", False),
        "ContentPolicyViolationError": ("content_filter", False),
        "NotFoundError": ("not_found", False),
        "APIConnectionError": ("connection", True),
        "BadGatewayError": ("server", True),
        "InternalServerError": ("server", True),
        "ServiceUnavailableError": ("server", True),
        "APIError": ("server", True),
    }
    code, retryable = mapping.get(name, ("unknown", False))
    # 当类名不熟悉时，回退到 status_code 提示。
    if code == "unknown" and isinstance(status, int):
        if status == 429:
            return "rate_limit", True, status
        if status in (401, 403):
            return "auth", False, status
        if status == 404:
            return "not_found", False, status
        if 500 <= status < 600:
            return "server", True, status
        if 400 <= status < 500:
            return "bad_request", False, status
    return code, retryable, status if isinstance(status, int) else None


# 流事件的联合类型
StreamEvent = ReasoningDelta | TextDelta | ToolCallDelta | ToolCallPartial | ToolCallArgsPartial | FinishEvent | ErrorEvent


DASHSCOPE_EXPLICIT_CACHE_MODELS = frozenset({
    "qwen3-max",
    "qwen3.6-max-preview",
    "qwen-max",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen-plus",
    "qwen3.6-flash",
    "qwen3.5-flash",
    "qwen-flash",
    "qwen3-coder-plus",
    "qwen3-coder-flash",
    "qwen3-vl-plus",
    "qwen3-vl-flash",
    "deepseek-v3.2",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.1",
})

DASHSCOPE_EXPLICIT_CACHE_PREFIX_MODELS = (
    "qwen3.5-plus-",
)


def _usage_get(obj: Any, key: str, default: Any = 0) -> Any:
    """从对象或类字典负载中读取使用量字段。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _usage_get_path(obj: Any, *path: str, default: Any = 0) -> Any:
    """遍历对象和字典表示中的嵌套使用量负载。"""
    current = obj
    for key in path:
        current = _usage_get(current, key, None)
        if current is None:
            return default
    return current


def _dashscope_explicit_cache_content(text: str) -> list[dict[str, Any]]:
    """构建带有显式缓存的 DashScope OpenAI 兼容内容块。"""
    return [{
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }]


def _add_cache_control_to_content(
    content: str | list[dict[str, Any]] | None,
) -> str | list[dict[str, Any]]:
    """向消息的内容字段注入 cache_control 标记。

    处理三种格式：
      - ``None`` / 假值 → 原样返回（例如只有 tool_calls 的助手消息）
      - ``str`` → 包装为带有标记的单个内容块
      - ``list``（现有内容块）→ 将标记附加到最后一个文本块，
        如果不存在文本块则创建一个新文本块。
    """
    if not content:
        return content

    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]

    # 内容块列表 — 对浅拷贝进行修改
    blocks = list(content)
    # 查找最后一个文本类型块以附加标记
    for idx in range(len(blocks) - 1, -1, -1):
        block = blocks[idx]
        if isinstance(block, dict) and block.get("type") == "text":
            blocks[idx] = {**block, "cache_control": {"type": "ephemeral"}}
            return blocks

    # 未找到文本块；附加一个空锚点块
    blocks.append({"type": "text", "text": "", "cache_control": {"type": "ephemeral"}})
    return blocks


_MAX_CACHE_MARKERS = 4  # DashScope per-request limit


def _inject_dashscope_cache_markers(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """在最佳前缀边界处放置 DashScope 显式缓存标记。

    **策略**

    在智能体循环中，``messages`` 中的 *最后* 一条消息始终是最新的回合
    （通常是携带系统提醒或新指令的用户消息）。它之前的所有内容 —
    系统提示词、先前的对话回合、工具结果 — 形成一个稳定的 **前缀**，
    将在下一次迭代中原样重新发送。

    我们在最后一条消息 *之前* 的 *最后一条* 消息上放置 **一个** ``cache_control`` 标记。
    DashScope 随后缓存从 ``messages`` 数组开头到该标记的整个前缀，
    因此在后续请求中，只有尾部的新消息会产生完整的输入 token 成本。

    **降级方案** – 当消息少于 2 条时（例如非常第一回合），我们只标记系统消息。

    **约束** – DashScope 每个请求最多允许 :data:`_MAX_CACHE_MARKERS` 个标记。
    此函数最多使用 2 个（系统 + 边界）。
    """
    if not messages:
        return messages

    result: list[dict[str, Any]] = []
    n = len(messages)

    for i, msg in enumerate(messages):
        new_msg: dict[str, Any] = {k: v for k, v in msg.items()}

        if i == 0 and new_msg.get("role") == "system":
            # Always mark system prompt as cacheable
            new_msg["content"] = _add_cache_control_to_content(new_msg.get("content"))
        elif i == n - 2 and n >= 2:
            # Boundary: last "historical" message before the newest one.
            # This closes the cacheable prefix.
            new_msg["content"] = _add_cache_control_to_content(new_msg.get("content"))

        result.append(new_msg)

    logger.debug(
        "dashscope_cache_markers_injected",
        total_messages=n,
        marked_positions=[
            i for i, m in enumerate(result)
            if _msg_has_cache_control(m)
        ],
    )
    return result


def _msg_has_cache_control(msg: dict[str, Any]) -> bool:
    """检查消息的内容中是否包含任何 cache_control。"""
    content = msg.get("content")
    if not content:
        return False
    if isinstance(content, str):
        return False
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and "cache_control" in block
            for block in content
        )
    return False


def _should_use_dashscope_explicit_cache(stream_input: StreamInput) -> bool:
    """此请求是否应选择 DashScope 显式提示词缓存。"""
    model = stream_input.model
    if model.provider_id != "dashscope":
        return False

    model_id = model.id.lower()
    if model_id in DASHSCOPE_EXPLICIT_CACHE_MODELS:
        return True
    return any(model_id.startswith(prefix) for prefix in DASHSCOPE_EXPLICIT_CACHE_PREFIX_MODELS)


def _build_messages(stream_input: StreamInput) -> list[dict[str, Any]]:
    """构建带有前置系统提示词的消息列表。"""
    messages: list[dict[str, Any]] = []

    # 添加系统提示词（跳过仅空白字符的）
    if stream_input.system:
        system_content = "\n\n".join(stream_input.system)
        if system_content.strip():
            # 对于 DashScope 显式缓存模型，缓存标记由 _inject_dashscope_cache_markers()
            # 稍后注入，因此这里输出纯文本。
            messages.append({"role": "system", "content": system_content})

    # 添加对话消息
    messages.extend(stream_input.messages)
    return messages


def _build_tools(stream_input: StreamInput) -> list[dict[str, Any]] | None:
    """将工具定义转换为 litellm 格式。"""
    if not stream_input.tools:
        return None
    return stream_input.tools


async def _openai_stream_with_client(client: Any, response: Any) -> AsyncGenerator[Any, None]:
    """产出 OpenAI 流块，然后关闭客户端。"""
    try:
        async for chunk in response:
            yield chunk
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            maybe = close()
            if asyncio.iscoroutine(maybe):
                await maybe


async def _dashscope_explicit_cache_response(
    stream_input: StreamInput,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> AsyncGenerator[Any, None]:
    """创建一个保留 cache_control 块的 DashScope OpenAI 兼容流。

    在请求发送前，缓存控制标记由 :func:`_inject_dashscope_cache_markers`
    注入到最佳前缀边界，以便稳定的对话历史（系统 + 先前回合）被缓存，
    只有最新的消息支付完整的输入 token 成本。
    """
    from openai import AsyncOpenAI

    from mycode.provider.transform import build_litellm_kwargs

    # 在系统提示词和历史边界处注入 cache_control 标记
    marked_messages = _inject_dashscope_cache_markers(messages)

    client = AsyncOpenAI(api_key=stream_input.api_key, base_url=stream_input.api_base)
    kwargs: dict[str, Any] = {
        "model": stream_input.model.api.id,
        "messages": marked_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    kwargs.update(build_litellm_kwargs(stream_input.model))

    if tools:
        kwargs["tools"] = tools
    if stream_input.tool_choice:
        kwargs["tool_choice"] = stream_input.tool_choice
    if stream_input.temperature is not None:
        kwargs["temperature"] = stream_input.temperature
    if stream_input.top_p is not None:
        kwargs["top_p"] = stream_input.top_p
    if stream_input.max_tokens is not None:
        kwargs["max_tokens"] = stream_input.max_tokens
    if stream_input.stop:
        kwargs["stop"] = stream_input.stop

    response = await client.chat.completions.create(**kwargs)
    return _openai_stream_with_client(client, response)


async def stream(stream_input: StreamInput) -> AsyncGenerator[StreamEvent, None]:
    """使用 litellm 流式传输 LLM 响应。

    在模型生成 token 时产出 StreamEvent 对象。
    """
    model_name = litellm_model_name(stream_input.model)
    messages = _build_messages(stream_input)
    tools = _build_tools(stream_input)

    logger.info(
        "stream",
        model=model_name,
        provider=stream_input.model.provider_id,
        message_count=len(messages),
        tool_count=len(tools) if tools else 0,
    )

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # 应用提供商特定的转换
    from mycode.provider.transform import build_litellm_kwargs
    provider_kwargs = build_litellm_kwargs(stream_input.model)
    kwargs.update(provider_kwargs)

    if tools:
        kwargs["tools"] = tools
    if stream_input.tool_choice:
        kwargs["tool_choice"] = stream_input.tool_choice
    if stream_input.temperature is not None:
        kwargs["temperature"] = stream_input.temperature
    if stream_input.top_p is not None:
        kwargs["top_p"] = stream_input.top_p
    if stream_input.max_tokens is not None:
        kwargs["max_tokens"] = stream_input.max_tokens
    if stream_input.stop:
        kwargs["stop"] = stream_input.stop
    if stream_input.api_key:
        kwargs["api_key"] = stream_input.api_key
    if stream_input.api_base:
        kwargs["api_base"] = stream_input.api_base

    # 跟踪跨块构建的工具调用
    tool_calls_in_progress: dict[int, dict[str, Any]] = {}
    # 跨块累加使用量（某些提供商在单独的最后一个块中发送使用量）
    accumulated_usage: dict[str, int] = {}
    raw_usage_payload: dict[str, Any] | None = None
    # 延迟 FinishEvent 直到流结束（使用量可能在 finish_reason 之后到达）
    pending_finish_reason: str | None = None

    try:
        if _should_use_dashscope_explicit_cache(stream_input):
            response = await asyncio.wait_for(
                _dashscope_explicit_cache_response(stream_input, messages, tools),
                timeout=300,
            )
        else:
            response = await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=300)

        # 消费者发起的中止：将下一块与中止事件竞争。
        # 没有此功能，用户必须等待整个 LLM 响应完成后，
        # 代理循环才能退出。我们仍然让当前块落地，以避免在帧中间撕裂 SSE 解析器。
        async for chunk in _with_abort(response, stream_input.abort_event):
            # 从任何包含使用量的块中收集使用量
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                raw_usage_payload = _serialize_usage(u)
                logger.info(
                    "usage_received",
                    usage_type=type(u).__name__,
                    raw_usage=raw_usage_payload,
                    input_tokens=_usage_get(u, "prompt_tokens", 0) or _usage_get(u, "input_tokens", 0) or 0,
                    output_tokens=_usage_get(u, "completion_tokens", 0) or _usage_get(u, "output_tokens", 0) or 0,
                    cache_read=_get_cache_read_tokens(u),
                    cache_write=_get_cache_write_tokens(u),
                )
                accumulated_usage = {
                    "input_tokens": _usage_get(u, "prompt_tokens", 0) or _usage_get(u, "input_tokens", 0) or 0,
                    "output_tokens": _usage_get(u, "completion_tokens", 0) or _usage_get(u, "output_tokens", 0) or 0,
                    "total_tokens": _usage_get(u, "total_tokens", 0) or 0,
                    "reasoning_tokens": _get_reasoning_tokens(u),
                    "cache_read_tokens": _get_cache_read_tokens(u),
                    "cache_write_tokens": _get_cache_write_tokens(u),
                }

            # 检查 finish_reason（可能带或不带 delta）
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None
            if finish_reason:
                pending_finish_reason = finish_reason
                # Emit completed tool calls when finish_reason arrives
                for entry in tool_calls_in_progress.values():
                    if entry["name"]:
                        yield ToolCallDelta(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                            args=entry["args"],
                        )
                tool_calls_in_progress.clear()

            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # 提供商推理 / 思考内容
            for reasoning_text in _extract_reasoning_segments(delta):
                if reasoning_text:
                    yield ReasoningDelta(text=reasoning_text)

            # 用户可见的文本内容
            for text in _extract_text_segments(delta):
                if text:
                    yield TextDelta(text=text)

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if hasattr(tc, "index") else 0

                    if idx not in tool_calls_in_progress:
                        tool_calls_in_progress[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "args": "",
                        }

                    entry = tool_calls_in_progress[idx]
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] = tc.function.name
                        yield ToolCallPartial(
                            tool_call_id=entry["id"],
                            tool_name=entry["name"],
                        )
                    if tc.function and tc.function.arguments:
                        entry["args"] += tc.function.arguments
                        yield ToolCallArgsPartial(
                            tool_call_id=entry["id"],
                            args_delta=tc.function.arguments,
                        )

        # 流结束 — 发出带有完整使用量数据的 FinishEvent
        if pending_finish_reason:
            cost = _calc_cost(model_name, accumulated_usage)
            metrics.counter("llm_request_total", model=model_name, outcome="ok")
            # 降级方案：如果提供商未发送原始使用量，则从累积量合成，
            # 以便前端始终有内容可显示。
            final_raw_usage = raw_usage_payload if raw_usage_payload is not None else dict(accumulated_usage)
            yield FinishEvent(
                reason=_map_finish_reason(pending_finish_reason),
                usage=accumulated_usage,
                raw_usage=final_raw_usage,
                cost=cost,
            )

    except asyncio.CancelledError:
        # 传播取消 — 消费者将运行自己的清理。
        # 确保我们不会在下面添加新的 yield 时吞没取消信号。
        raise
    except Exception as e:
        code, retryable, status = _classify_exception(e)
        metrics.counter("llm_request_total", model=model_name, outcome="error", code=code)
        logger.error(
            "stream error",
            error=str(e),
            error_type=type(e).__name__,
            error_code=code,
            status_code=status,
            retryable=retryable,
            model=model_name,
        )
        # 在错误事件之前，将任何正在进行的工具调用作为尽力而为的 delta 发出。
        # 没有此功能，处理器层将永远不会看到提供商故障时正在组装中的工具调用，
        # 因此后续重试无法将失败归因于特定调用，并且会愉快地重新发出它。
        for entry in list(tool_calls_in_progress.values()):
            if entry.get("name"):
                yield ToolCallDelta(
                    tool_call_id=entry["id"],
                    tool_name=entry["name"],
                    args=entry.get("args", ""),
                )
        tool_calls_in_progress.clear()
        yield ErrorEvent(
            error=str(e),
            error_code=code,
            retryable=retryable,
            status_code=status,
        )
        # 确保始终发出 FinishEvent，以便消费者不会挂起
        if not pending_finish_reason:
            cost = _calc_cost(model_name, accumulated_usage)
            yield FinishEvent(reason="error", usage=accumulated_usage, raw_usage=raw_usage_payload, cost=cost)


def _get_reasoning_tokens(usage: Any) -> int:
    """从各种提供商格式中提取推理 token。"""
    # OpenAI: usage.completion_tokens_details.reasoning_tokens
    val = _usage_get_path(usage, "completion_tokens_details", "reasoning_tokens", default=0)
    if val:
        return val
    # Some providers use prompt_tokens_details
    return 0


def _get_cache_read_tokens(usage: Any) -> int:
    """提取缓存读取 token。"""
    # DeepSeek: usage.prompt_cache_hit_tokens
    val = _usage_get(usage, "prompt_cache_hit_tokens", 0)
    if val:
        return val
    # Anthropic: usage.cache_read_input_tokens
    val = _usage_get(usage, "cache_read_input_tokens", 0)
    if val:
        return val
    # DashScope/OpenAI chat.completions: usage.prompt_tokens_details.cached_tokens
    val = _usage_get_path(usage, "prompt_tokens_details", "cached_tokens", default=0)
    if val:
        return val
    # DashScope/OpenAI responses: usage.input_tokens_details.cached_tokens
    val = _usage_get_path(usage, "input_tokens_details", "cached_tokens", default=0)
    if val:
        return val
    # Some DashScope model/region variants expose cached_tokens at top level.
    val = _usage_get(usage, "cached_tokens", 0)
    if val:
        return val
    return 0


def _get_cache_write_tokens(usage: Any) -> int:
    """提取缓存写入 / 未命中 token。"""
    # DeepSeek: usage.prompt_cache_miss_tokens
    val = _usage_get(usage, "prompt_cache_miss_tokens", 0)
    if val:
        return val
    # OpenAI/Anthropic style
    val = _usage_get(usage, "cache_creation_input_tokens", 0)
    if val:
        return val
    # DashScope/OpenAI chat.completions explicit cache accounting.
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "prompt_tokens_details", "cache_creation", "ephemeral_5m_input_tokens", default=0)
    if val:
        return val
    # DashScope/OpenAI responses explicit cache accounting.
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation", "cache_creation_input_tokens", default=0)
    if val:
        return val
    val = _usage_get_path(usage, "input_tokens_details", "cache_creation", "ephemeral_5m_input_tokens", default=0)
    if val:
        return val
    return 0


def _calc_cost(model_name: str, usage: dict[str, int]) -> float:
    """使用 litellm 的定价数据计算成本。"""
    if not usage or not usage.get("input_tokens"):
        return 0.0
    try:
        cost = litellm.completion_cost(
            model=model_name,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )
        return float(cost)
    except Exception:
        return 0.0


def _map_finish_reason(reason: str) -> str:
    """将提供商的完成原因映射到我们的标准格式。"""
    if reason == "tool_calls":
        return "tool-calls"
    if reason == "length":
        return "length"
    return "stop"


def _extract_reasoning_segments(delta: Any) -> list[str]:
    """从 delta 块中提取提供商特定的推理/思考文本。"""
    return _extract_delta_segments(
        delta,
        field_names=("reasoning_content", "reasoning", "thinking"),
    )


def _extract_text_segments(delta: Any) -> list[str]:
    """从 delta 块中提取正常的助手文本。"""
    return _extract_delta_segments(delta, field_names=("content",))


def _extract_delta_segments(delta: Any, *, field_names: tuple[str, ...]) -> list[str]:
    segments: list[str] = []
    for name in field_names:
        value = _delta_get(delta, name)
        segments.extend(_coerce_delta_segments(value))
    return [segment for segment in segments if segment]


def _delta_get(delta: Any, key: str) -> Any:
    if delta is None:
        return None
    if isinstance(delta, dict):
        return delta.get(key)
    value = getattr(delta, key, None)
    if value is not None:
        return value
    model_extra = getattr(delta, "model_extra", None)
    if isinstance(model_extra, dict) and key in model_extra:
        return model_extra[key]
    extra = getattr(delta, "__dict__", None)
    if isinstance(extra, dict) and key in extra:
        return extra[key]
    return None


def _coerce_delta_segments(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        segments: list[str] = []
        for item in value:
            segments.extend(_coerce_delta_segments(item))
        return segments
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            nested = value.get(key)
            if isinstance(nested, str):
                return [nested]
            if nested is not None:
                nested_segments = _coerce_delta_segments(nested)
                if nested_segments:
                    return nested_segments
        return []
    for key in ("text", "content", "value"):
        nested = getattr(value, key, None)
        if isinstance(nested, str):
            return [nested]
        if nested is not None:
            nested_segments = _coerce_delta_segments(nested)
            if nested_segments:
                return nested_segments
    return []


def _serialize_usage(value: Any) -> dict[str, Any] | None:
    """将提供商使用量负载转换为纯 JSON 安全对象。"""
    serialized = _serialize_jsonable(value)
    if isinstance(serialized, dict):
        return serialized
    # Fallback: wrap string representation so we never lose data entirely
    return {"_raw": str(value)} if value is not None else None


def _serialize_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(k): _serialize_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_serialize_jsonable(v) for v in value]
    # pydantic v2
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _serialize_jsonable(model_dump())
        except Exception:
            pass
    # pydantic v1
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        try:
            return _serialize_jsonable(dict_method())
        except Exception:
            pass
    # dataclass / plain object
    as_dict = getattr(value, "__dict__", None)
    if isinstance(as_dict, dict) and as_dict:
        return {
            str(k): _serialize_jsonable(v)
            for k, v in as_dict.items()
            if not str(k).startswith("_")
        }
    # Some litellm internals expose .json()
    json_method = getattr(value, "json", None)
    if callable(json_method):
        try:
            import json as _json
            return _serialize_jsonable(_json.loads(json_method()))
        except Exception:
            pass
    return str(value)
