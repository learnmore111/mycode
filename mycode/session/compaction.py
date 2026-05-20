"""会话压缩 — 上下文压缩与修剪。

处理上下文溢出检测、旧工具输出修剪和摘要生成。

缓存友好设计：
- 压缩 LLM 调用复用主代理的系统提示词 + 工具，以便
  共享 API 前缀缓存（在系统 + 工具前缀上命中缓存）。
- 旧消息中的工具输出在压缩调用之前被截断，以降低成本。
- 压缩结果将摘要作为 **用户消息**（而非系统消息）注入，
  以便主代理的下一次调用仍然具有相同的系统前缀。
"""
from __future__ import annotations

import asyncio
import copy
import re
from collections import namedtuple
from typing import TYPE_CHECKING, Any

from mycode.session import llm as llmmod
from mycode.util import log as logmod

if TYPE_CHECKING:
    from mycode.provider.schema import Model

logger = logmod.create(service="session.compaction")

CompactionMetrics = namedtuple('CompactionMetrics', [
    'old_message_count',     # 被摘要的旧消息数量
    'old_message_tokens',    # 旧消息的预估 token 数
    'summary_length',        # 生成摘要的长度
    'removed_turn_count',    # 被移除的用户回合数
    'old_messages',          # 原始旧消息（用于审计追踪）
    'summary',               # 生成的摘要文本
])


PRUNE_MINIMUM = 20_000  # tokens
PRUNE_PROTECT = 40_000  # tokens
OVERFLOW_RATIO = 0.85  # trigger at 85% of context window
COMPACT_KEEP_TURNS = 3  # number of recent user turns to preserve verbatim
SUMMARY_TOOL_OUTPUT_LIMIT = 1000  # chars — default truncate for benign tool outputs

# 错误输出 / 堆栈跟踪通常携带代理偏离原因的 *最* 强信号，
# 因此我们在压缩提示词周围保留更多错误输出。
# 通用成功的 read/grep 结果在默认限制下压缩良好。
SUMMARY_TOOL_OUTPUT_ERROR_LIMIT = 2500

# 当工具未设置显式 `is_error` 标志时，用于识别错误样式负载的启发式方法
#（例如来自 `bash` 的原始跟踪）。
_ERROR_HINTS = (
    "Traceback",
    "Error:",
    "error:",
    "Exception",
    "FATAL",
    "fatal:",
    "panic:",
    "Exit code:",
    "[error]",
)

# 提供商特定的缓存 TTL（秒）。
# 用于检测 API 前缀缓存是否可能在回合之间已过期。
# 保守值 — 倾向于假设已过期。
_CACHE_TTL: dict[str, int] = {
    "@ai-sdk/anthropic": 300,       # 5 min (default; extended TTL = 1h but not auto)
    "@ai-sdk/openai": 300,          # 5-10 min inactive; use lower bound
    "@ai-sdk/google": 3600,         # 1h default explicit cache
    "@ai-sdk/amazon-bedrock": 300,  # Bedrock Anthropic models — same as Anthropic
    "@ai-sdk/deepinfra": 300,       # conservative default
}
_CACHE_TTL_DEFAULT = 300  # fallback for unknown providers

# 用于将摘要包装为压缩结果中用户消息的模板。
COMPACT_USER_MSG_TEMPLATE = """This session is being continued from a previous conversation that ran out of context. The summary below covers the earlier portion of the conversation.

{summary}

Recent messages are preserved verbatim. Continue from where we left off without asking the user any further questions. Resume directly — do not acknowledge the summary, do not recap what was happening, do not preface with "I'll continue" or similar. Pick up the last task as if the break never happened."""

# 如果压缩代理无法加载时的降级提示词。
_COMPACTION_PROMPT_FALLBACK = """Provide a detailed summary of the conversation so far.
Focus on: what was done, what is being worked on, which files are relevant,
what needs to be done next, and key user requests or constraints."""


def estimate_tokens(text: str) -> int:
    """粗略 token 估计 — 故意保守（高估）。

    使用字节长度除以保守因子来更早触发压缩，
    降低上下文溢出风险。

    - 英文文本：约每 4 字节 1 token（ASCII）
    - 代码/JSON：约每 3-4 字节 1 token（差异很大）
    - 中文/日文/韩文：约每 3 字节 1 token（UTF-8，3 字节/字符）
    - 混合：基于字节的估计自然处理所有情况

    我们使用 //3 作为基础估计，然后添加 15% 的安全边距
    以弥补代码/JSON 的低估。
    """
    byte_len = len(text.encode("utf-8"))
    base_estimate = byte_len // 3
    # 为代码密集型内容添加 15% 安全边距
    return base_estimate + base_estimate // 7


# 内容可寻址的 token 估计缓存。
#
# 系统提示词和工具模式仅在代理/模型组合更改时才会变化，
# 但 ``prompt()`` 目前在每次回合都会重新估计它们。
# 估计本身开销很小，但它在每次迭代中都会分配一个与完整工具 JSON 大小相当的
# UTF-8 字节缓冲区（通常 30-80KB）。按内容指纹缓存意味着
# 我们为每个唯一的提示词/工具负载只支付一次该成本。
import hashlib as _hashlib  # noqa: E402 — keep module import order readable

_ESTIMATE_CACHE: dict[str, int] = {}
_ESTIMATE_CACHE_MAX = 128


def _fingerprint(text: str) -> str:
    return _hashlib.blake2b(text.encode("utf-8", errors="replace"), digest_size=16).hexdigest()


def estimate_tokens_cached(text: str) -> int:
    """:func:`estimate_tokens` 的缓存变体。

    可以安全地在热路径上调用（``prompt()`` 的每次回合）。缓存以输入的短 blake2 摘要
    为键，而非字符串本身，因此重复调用者不会保留对大负载的引用。
    """
    if not text:
        return 0
    fp = _fingerprint(text)
    cached = _ESTIMATE_CACHE.get(fp)
    if cached is not None:
        return cached
    value = estimate_tokens(text)
    if len(_ESTIMATE_CACHE) >= _ESTIMATE_CACHE_MAX:
        # 逐出一个任意条目 — 此缓存没有最近性信号。
        _ESTIMATE_CACHE.pop(next(iter(_ESTIMATE_CACHE)), None)
    _ESTIMATE_CACHE[fp] = value
    return value


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """估计所有消息的总 token 数。"""
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            fn = tc.get("function", {})
            total += estimate_tokens(fn.get("arguments", ""))
            total += estimate_tokens(fn.get("name", ""))
    return total


def should_compact(
    *,
    messages: list[dict[str, Any]],
    model_context: int,
    system_tokens: int = 0,
    tools_tokens: int = 0,
) -> bool:
    """根据总上下文估计检查对话是否需要压缩。

    在估计中包含系统提示词和工具定义，因为它们与消息一起占用上下文窗口空间。
    """
    if model_context <= 0:
        return False
    est = estimate_messages_tokens(messages) + system_tokens + tools_tokens
    threshold = int(model_context * OVERFLOW_RATIO)
    return est > threshold


def prune_tool_outputs(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """修剪旧工具输出以释放上下文空间。

    适用于 OpenAI 消息格式：
      - {"role": "tool", "tool_call_id": "...", "content": "..."} — 工具结果
      - {"role": "assistant", "tool_calls": [...]} — 工具调用请求

    反向遍历消息，保护最近 PRUNE_PROTECT token 值的工具输出。
    较旧的输出被替换为占位符。

    返回 (pruned_messages, tokens_freed)。
    """
    # 收集工具结果消息的索引及其 token 估计值
    tool_indices: list[tuple[int, int]] = []  # (msg_idx, estimated_tokens)
    turns = 0

    for msg_idx in range(len(messages) - 1, -1, -1):
        msg = messages[msg_idx]
        if msg.get("role") == "user":
            turns += 1
        if turns < 2:
            continue  # 保护最近 2 个回合

        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content and content != "[Old tool result content cleared]":
                est = estimate_tokens(content)
                tool_indices.append((msg_idx, est))

    # 从最新到最旧遍历工具输出，保护前 PRUNE_PROTECT 个 token
    protected_tokens = 0
    prunable: list[tuple[int, int]] = []
    for msg_idx, est in tool_indices:
        protected_tokens += est
        if protected_tokens > PRUNE_PROTECT:
            prunable.append((msg_idx, est))

    # 修剪
    pruned = 0
    for msg_idx, est in prunable:
        messages[msg_idx]["content"] = "[Old tool result content cleared]"
        pruned += est

    if pruned > PRUNE_MINIMUM:
        logger.info("pruned tool outputs", count=len(prunable), tokens_freed=pruned)

    return messages, pruned


def get_cache_ttl(model: Model) -> int:
    """返回模型提供商的缓存 TTL（秒）。"""
    return _CACHE_TTL.get(model.api.npm, _CACHE_TTL_DEFAULT)


def is_cache_likely_expired(model: Model, last_llm_time_ms: int | None) -> bool:
    """检查 API 前缀缓存是否可能已过期。

    参数:
        model: 正在使用的模型。
        last_llm_time_ms: 上次 LLM 完成的纪元毫秒数。
            None 表示没有先前的交互（第一回合）— 缓存为空。

    返回:
        如果缓存可能已过期且建议主动修剪，则返回 True。
    """
    if last_llm_time_ms is None:
        return False  # 第一回合 — 尚无缓存内容，修剪无益

    import time
    elapsed_s = (time.time() * 1000 - last_llm_time_ms) / 1000
    ttl = get_cache_ttl(model)
    expired = elapsed_s > ttl
    if expired:
        logger.info(
            "cache likely expired",
            elapsed_s=int(elapsed_s),
            ttl=ttl,
            provider=model.api.npm,
        )
    return expired


def _split_by_turns(
    messages: list[dict[str, Any]],
    keep_turns: int = COMPACT_KEEP_TURNS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按用户回合将消息拆分为（旧消息，近期消息）。

    一个 "回合" 从每条 ``role=user`` 消息开始，包括助手回复
    以及随后任何工具调用 / 工具结果消息。
    最近 *keep_turns* 个回合（及其后的所有消息）放入 *recent*；
    之前的所有内容放入 *old*。

    返回 ``(old_messages, recent_messages)``。
    """
    # 查找用户回合开始的索引
    turn_starts: list[int] = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            turn_starts.append(i)

    if len(turn_starts) <= keep_turns:
        # 回合数不足以拆分 – 保留所有内容
        return [], list(messages)

    split_idx = turn_starts[-keep_turns]
    return list(messages[:split_idx]), list(messages[split_idx:])


# ---------------------------------------------------------------------------
# 缓存友好的压缩流水线辅助函数
# ---------------------------------------------------------------------------


def _tool_output_limit(content: str) -> int:
    """为工具输出字符串选择合适的截断限制。

    错误类负载（堆栈跟踪、非零退出码）获得更高的限制，
    因为摘要 LLM 需要足够的上下文来推理 *为什么* 某事失败了。
    良性输出在默认限制下压缩良好。
    """
    if not content:
        return SUMMARY_TOOL_OUTPUT_LIMIT
    head = content[:400]
    if any(hint in head for hint in _ERROR_HINTS):
        return SUMMARY_TOOL_OUTPUT_ERROR_LIMIT
    return SUMMARY_TOOL_OUTPUT_LIMIT


def _truncate_tool_outputs_for_summary(
    messages: list[dict[str, Any]],
    limit: int = SUMMARY_TOOL_OUTPUT_LIMIT,
) -> list[dict[str, Any]]:
    """创建 *messages* 的副本，其中大型工具输出被截断。

    使用写时复制：只有实际需要截断的消息才会被深拷贝。
    在 *limit* 范围内的消息与原始列表共享（无分配）。

    *limit* 参数被视为下限 — 内容看起来像错误跟踪的单个工具消息
    可以保留 ``SUMMARY_TOOL_OUTPUT_ERROR_LIMIT`` 个字符，以保留事后分析信号。

    这 **仅** 用于压缩 LLM 调用，以便它看到更少的 token 量。
    原始消息永远不会被修改。
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        needs_copy = False
        tool_limit = limit

        # 检查工具结果内容
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str):
                tool_limit = max(limit, _tool_output_limit(content))
                if len(content) > tool_limit:
                    needs_copy = True

        # 检查助手消息中的 tool_call 参数
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            if len(fn.get("arguments", "")) > limit:
                needs_copy = True
                break

        if not needs_copy:
            result.append(msg)
            continue

        # 仅深拷贝需要截断的消息
        msg_copy = copy.deepcopy(msg)
        if msg_copy.get("role") == "tool":
            content = msg_copy.get("content", "")
            if isinstance(content, str) and len(content) > tool_limit:
                msg_copy["content"] = content[:tool_limit] + f"\n... [truncated, {len(content)} chars total]"
        for tc in msg_copy.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", "")
            if len(args) > limit:
                fn["arguments"] = args[:limit] + "..."
        result.append(msg_copy)

    return result


def _extract_summary(text: str, max_length: int = 8000) -> str:
    """提取 ``<summary>`` 内容并去除 ``<analysis>`` 草稿。

    压缩提示词要求模型输出 ``<analysis>...</analysis>``，
    后跟 ``<summary>...</summary>``。分析块是一个起草草稿，
    可以提高摘要质量，但不应保留在最终输出中（它会在后续调用中浪费 token）。

    对摘要强制执行 max_length 以防止无限制增长。
    """
    # 尝试查找 <summary>...</summary>
    match = re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
    if match:
        summary = match.group(1).strip()
        if len(summary) > max_length:
            summary = summary[:max_length] + "\n... [summary truncated]"
        return summary

    # 降级方案：去除 <analysis>...</analysis> 并返回其余部分
    stripped = re.sub(r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL)
    stripped = stripped.strip()
    if stripped and len(stripped) < len(text):
        if len(stripped) > max_length:
            stripped = stripped[:max_length] + "\n... [summary truncated]"
        return stripped

    # 最后手段：去除常见推理/草稿模式并截断
    result = _strip_reasoning_patterns(text)
    if not result:
        return "[Empty summary generated]"

    logger.warn("summary extraction fell back to stripped full text", length=len(text), stripped_length=len(result))
    if len(result) > max_length:
        result = result[:max_length] + "\n... [summary truncated]"
    return result


# 指示 LLM 推理/草稿的模式（作为摘要内容无用）
_REASONING_TAG_RE = re.compile(r"<(?:thinking|reasoning|scratchpad)>.*?</(?:thinking|reasoning|scratchpad)>", re.DOTALL)
_REASONING_LINE_RE = re.compile(
    r"^(?:Let me (?:think|analyze|consider|review)|I (?:need to|should|will)|"
    r"First,? |Next,? |Then,? |Finally,? |Step \d|OK,? so ).*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_reasoning_patterns(text: str) -> str:
    """从原始文本中去除常见的 LLM 推理/草稿模式。

    当模型未能输出正确的 ``<summary>``/``<analysis>`` 标签时，
    用作最后的清理手段。
    """
    result = _REASONING_TAG_RE.sub("", text)
    result = _REASONING_LINE_RE.sub("", result)
    # 折叠多个空行
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _build_compact_result(
    summary: str,
    recent: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """组装压缩后的消息列表。

    摘要作为 **用户消息** 注入，以便主代理的
    系统提示词前缀保持不变 → 前缀缓存命中。
    """
    user_summary_msg: dict[str, Any] = {
        "role": "user",
        "content": COMPACT_USER_MSG_TEMPLATE.format(summary=summary),
    }
    return [user_summary_msg, *recent]


async def _load_compaction_prompt() -> str:
    """加载压缩代理的提示词文本。

    如果无法加载代理，则回退到内置默认值。
    """
    try:
        from mycode.agent import agent as agentmod

        agent = await agentmod.get("compaction")
        if agent and agent.prompt:
            return agent.prompt
    except Exception:
        pass
    return _COMPACTION_PROMPT_FALLBACK


async def compact(
    messages: list[dict[str, Any]],
    *,
    system: list[str],
    tools: list[dict[str, Any]] | None,
    model: Model,
    api_key: str | None = None,
    api_base: str | None = None,
) -> tuple[list[dict[str, Any]], CompactionMetrics]:
    """使用滑动窗口 + 摘要策略压缩对话历史。

    缓存友好流水线：

    1. 首先修剪旧工具输出 — 可能仅此就足够了。
    2. 将消息拆分为旧回合和近期回合（保留最后 N 个回合原文）。
    3. 截断旧消息中的工具输出（深拷贝）以降低摘要成本。
    4. 使用与主代理 **相同的系统提示词 + 工具** 调用 LLM，
       以便共享 API 前缀缓存。
    5. 提取 ``<summary>`` 块，去除 ``<analysis>`` 草稿。
    6. 返回：``([user_summary_msg] + recent_turns, metrics)``。
    """
    _empty_metrics = CompactionMetrics(
        old_message_count=0, old_message_tokens=0, summary_length=0,
        removed_turn_count=0, old_messages=[], summary="",
    )

    # 步骤 1：修剪工具输出（在原始消息上就地操作）
    messages, freed = prune_tool_outputs(messages)
    if freed > PRUNE_MINIMUM:
        logger.info("修剪释放了足够的 token，跳过完整压缩", freed=freed)
        return messages, _empty_metrics

    # 步骤 2：拆分为旧 / 近期
    old, recent = _split_by_turns(messages, keep_turns=COMPACT_KEEP_TURNS)

    if not old:
        # 没有足够旧的内容可摘要 — 尝试更积极地修剪
        pruned_again, freed_again = prune_tool_outputs(messages)
        if freed_again > 0:
            logger.info("没有旧回合可压缩，但修剪了更多工具输出", freed=freed_again)
            return pruned_again, _empty_metrics
        logger.info("没有旧回合可压缩，按原样返回")
        return messages, _empty_metrics

    # 步骤 3：截断旧消息中的工具输出以供摘要调用
    truncated_old = _truncate_tool_outputs_for_summary(old)

    # 步骤 4：构建压缩请求
    compaction_prompt = await _load_compaction_prompt()

    summary_messages: list[dict[str, Any]] = list(truncated_old)
    summary_messages.append({"role": "user", "content": compaction_prompt})

    stream_input = llmmod.StreamInput(
        model=model,
        messages=summary_messages,
        system=system,  # 与主代理相同的系统提示词 → 缓存命中
        tools=tools,  # 与主代理相同的工具 → 缓存键匹配
        tool_choice="none",  # 在 API 层面阻止工具调用
        temperature=0.0,
        max_tokens=8196,
        api_key=api_key,
        api_base=api_base,
    )

    # 步骤 5：消费流并收集摘要文本（带重试，最多 3 次尝试）
    MAX_COMPACT_RETRIES = 3
    COMPACT_RETRY_DELAY = 1.0  # 重试之间的秒数

    summary_text = ""
    last_error: str | None = None
    for attempt in range(1, MAX_COMPACT_RETRIES + 1):
        try:
            summary_text = ""
            async for event in llmmod.stream(stream_input):
                if isinstance(event, llmmod.TextDelta):
                    summary_text += event.text
                elif isinstance(event, llmmod.ErrorEvent):
                    last_error = event.error
                    logger.error(
                        "压缩 LLM 流错误", attempt=attempt, max_retries=MAX_COMPACT_RETRIES,
                        error=last_error,
                    )
                    break  # 中断内层循环，将重试或继续执行
            else:
                # 流完成且没有 ErrorEvent → 成功（或为空）
                break  # 中断外层重试循环

            # 如果收到 ErrorEvent 且还有重试次数，等待并重试
            if attempt < MAX_COMPACT_RETRIES:
                await asyncio.sleep(COMPACT_RETRY_DELAY)
                continue
            # 没有更多重试次数，继续执行下面的降级方案
        except Exception as e:
            last_error = str(e)
            logger.error(
                "压缩 LLM 调用失败", attempt=attempt, max_retries=MAX_COMPACT_RETRIES,
                error=last_error,
            )
            if attempt < MAX_COMPACT_RETRIES:
                await asyncio.sleep(COMPACT_RETRY_DELAY)
                continue
    else:
        # 所有重试已耗尽 — 循环完成，未通过成功流中断
        logger.warn("压缩 LLM 调用在所有重试后失败", max_retries=MAX_COMPACT_RETRIES, error=last_error)
        return messages, _empty_metrics  # 降级方案：返回已修剪但未摘要的消息

    if not summary_text.strip():
        logger.warn("压缩产生了空摘要")
        return messages, _empty_metrics

    # 步骤 6：提取 <summary> 块，去除 <analysis> 草稿
    summary = _extract_summary(summary_text)
    logger.info(
        "压缩完成",
        summary_len=len(summary),
        old_msgs=len(old),
        kept_msgs=len(recent),
    )

    # 步骤 7：组装压缩后的对话
    metrics = CompactionMetrics(
        old_message_count=len(old),
        old_message_tokens=estimate_messages_tokens(old),
        summary_length=len(summary),
        removed_turn_count=sum(1 for m in old if m.get("role") == "user"),
        old_messages=list(old),
        summary=summary,
    )
    result = _build_compact_result(summary, recent)

    # 步骤 8：压缩后验证 — 如果结果仍然溢出则警告
    system_tokens_est = estimate_tokens("\n\n".join(system)) if system else 0
    tools_tokens_est = estimate_tokens(str(tools)) if tools else 0
    result_tokens = estimate_messages_tokens(result)
    total_est = result_tokens + system_tokens_est + tools_tokens_est
    context_limit = model.limit.context if model.limit.context > 0 else 32_000
    threshold = int(context_limit * OVERFLOW_RATIO)
    if total_est > threshold:
        logger.warn(
            "压缩后的结果仍然超出阈值 — 下一次迭代将重新压缩",
            result_tokens=result_tokens,
            total_est=total_est,
            threshold=threshold,
        )

    return result, metrics


def log_token_accuracy(estimated: int, actual: int, model_id: str) -> None:
    """记录 token 估计精度以进行调优。

    将启发式估计与 API 报告的实际输入 token 数进行比较。
    仅在偏差显著时（>2× 或 <0.5×）记录，以便正常操作不会产生噪音。

    参数:
        estimated: 启发式估计（来自 ``estimate_messages_tokens``）。
        actual: API 使用量元数据中的实际 input_tokens。
        model_id: 模型标识符（例如 ``"anthropic/claude-sonnet"``）。
    """
    if actual <= 0 or estimated <= 0:
        return
    ratio = estimated / actual
    if ratio > 2.0 or ratio < 0.5:
        logger.info(
            "token estimate divergence",
            estimated=estimated,
            actual=actual,
            ratio=f"{ratio:.2f}",
            model=model_id,
        )
