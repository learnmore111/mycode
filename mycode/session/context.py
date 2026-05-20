"""UI 上下文查看器的上下文快照构建器。

在智能体循环的每次迭代中，构建发送给 LLM 的完整上下文的结构化快照。
这驱动了前端 "context viewer" 面板，显示系统提示词、工具、消息、
预估 token 使用量以及可用时的 **真实 API 使用量**。

缓存状态 **绝不猜测** — 仅根据 LLM 提供商返回的实际
``usage`` 字段报告。如果提供商未返回缓存指标，
UI 会显示信息提示而非编造数字。
"""

from __future__ import annotations

import json
from typing import Any

from mycode.session.compaction import estimate_tokens
import re as _re

TOOL_OUTPUT_PREVIEW_LIMIT = 500  # chars — preview shown in UI for tool outputs
TOOL_ARGS_PREVIEW_LIMIT = 200    # chars — preview shown for tool call arguments

# Signature used to detect compaction summary messages.
_COMPACTION_MARKER = "continued from a previous conversation"

# Regex to extract <system-reminder>...</system-reminder> blocks from content.
_REMINDER_RE = _re.compile(r"<system-reminder>(.*?)</system-reminder>", _re.DOTALL)


def build_context_snapshot(
    *,
    system: list[str],
    tools: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    model_id: str,
    context_limit: int,
    iteration: int,
    has_history: bool,
    actual_usage: dict[str, int | float] | None = None,
    raw_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为 UI 上下文查看器构建上下文快照。

    参数
    ----------
    system:
        系统提示词部分（将使用 ``\\n\\n`` 连接）。
    tools:
        LLM 工具定义（OpenAI 函数调用格式），或 *None*。
    messages:
        即将发送给 LLM 的完整消息列表。
    model_id:
        完全限定的模型标识符（``provider/model``）。
    context_limit:
        模型上下文窗口大小（以 token 计）。
    iteration:
        当前智能体循环迭代（从 0 开始）。
    has_history:
        会话是否以预存历史启动。
    actual_usage:
        来自 **上一次** 迭代的 LLM API 响应的真实 token 使用量。
        包含 ``input_tokens``、``output_tokens``、``cache_read_tokens``、
        ``cache_write_tokens`` 等。为 *None* 时，前端应显示占位符 / 提示
        而非预估缓存数字。

    返回
    -------
    dict
        与 ``context_snapshot`` 事件模式匹配的结构化快照。
    """
    # --- 系统提示词 ---
    system_text = "\n\n".join(system)
    system_tokens = estimate_tokens(system_text)
    system_info: dict[str, Any] = {
        "content": system_text,
        "estimated_tokens": system_tokens,
    }

    # --- 工具 ---
    if tools:
        tool_names = [t.get("function", {}).get("name", "?") for t in tools]
        tools_json = json.dumps(tools, ensure_ascii=False)
        tools_tokens = estimate_tokens(tools_json)
    else:
        tool_names = []
        tools_json = ""
        tools_tokens = 0
    tools_info: dict[str, Any] = {
        "count": len(tool_names),
        "names": tool_names,
        "estimated_tokens": tools_tokens,
    }

    # --- 消息（不猜测缓存状态）---
    msg_count = len(messages)

    message_infos: list[dict[str, Any]] = []
    compaction_boundary_index: int | None = None

    for idx, msg in enumerate(messages):
        role: str = msg.get("role", "unknown")
        content: str = msg.get("content") or ""

        info: dict[str, Any] = {
            "index": idx,
            "role": role,
        }

        # --- role=tool ---
        if role == "tool":
            full_length = len(content)
            truncated = len(content) > TOOL_OUTPUT_PREVIEW_LIMIT
            preview = content[:TOOL_OUTPUT_PREVIEW_LIMIT] if truncated else content
            info["content"] = preview
            info["content_truncated"] = truncated
            info["full_length"] = full_length
            info["estimated_tokens"] = estimate_tokens(content)
            info["tool_call_id"] = msg.get("tool_call_id", "")
            info["tool_name"] = msg.get("name", "")

        # --- role=assistant ---
        elif role == "assistant":
            info["content"] = content
            info["estimated_tokens"] = estimate_tokens(content)
            # Tool calls summary
            raw_calls = msg.get("tool_calls") or []
            if raw_calls:
                tc_summaries: list[dict[str, Any]] = []
                for tc in raw_calls:
                    fn = tc.get("function", {})
                    args_str = fn.get("arguments", "")
                    tc_summaries.append({
                        "id": tc.get("id", ""),
                        "tool": fn.get("name", ""),
                        "args_preview": args_str[:TOOL_ARGS_PREVIEW_LIMIT],
                    })
                info["tool_calls"] = tc_summaries
                # Add tool call tokens to estimate
                for tc in raw_calls:
                    fn = tc.get("function", {})
                    info["estimated_tokens"] += estimate_tokens(fn.get("arguments", ""))
                    info["estimated_tokens"] += estimate_tokens(fn.get("name", ""))

        # --- role=user / role=system / 其他 ---
        else:
            info["content"] = content
            info["estimated_tokens"] = estimate_tokens(content)

        # 检测压缩摘要
        if role == "user" and _COMPACTION_MARKER in content.lower():
            info["is_compaction_summary"] = True
            compaction_boundary_index = idx

        # 检测并提取 <system-reminder> 注入。
        # 提醒文本由 _attach_reminder_to_last_user_message() 附加到用户消息内容中。
        # 我们提取它，以便前端可以清晰地渲染它（徽章 + 样式块），而不是显示原始 XML 标签。
        reminder_matches = _REMINDER_RE.findall(content)
        if reminder_matches:
            info["is_system_reminder"] = True
            info["system_reminder_content"] = "\n".join(
                r.strip() for r in reminder_matches if r.strip()
            )
            # 从显示内容中移除 <system-reminder> 块
            display_content = _REMINDER_RE.sub("", content).strip()
            info["content"] = display_content or "(系统提醒)"
            info["content_truncated"] = False
            info["full_length"] = len(display_content)

        message_infos.append(info)

    # --- 压缩信息 ---
    compaction_info: dict[str, Any] = {
        "has_boundary": compaction_boundary_index is not None,
        "boundary_index": compaction_boundary_index,
    }

    # --- 摘要（仅启发式总计，无虚假缓存拆分）---
    total_tokens = system_tokens + tools_tokens

    for mi in message_infos:
        total_tokens += mi.get("estimated_tokens", 0)

    usage_percent = round(100 * total_tokens / context_limit, 1) if context_limit > 0 else 0.0

    summary: dict[str, Any] = {
        "total_estimated_tokens": total_tokens,
        "context_limit": context_limit,
        "usage_percent": usage_percent,
    }

    # --- 来自上一次迭代的真实 API 使用量 ---
    if actual_usage:
        actual_info: dict[str, Any] = {
            "input_tokens": actual_usage.get("input_tokens", 0),
            "output_tokens": actual_usage.get("output_tokens", 0),
            "cache_read_tokens": actual_usage.get("cache_read_tokens", 0),
            "cache_write_tokens": actual_usage.get("cache_write_tokens", 0),
            "reasoning_tokens": actual_usage.get("reasoning_tokens", 0),
            "total_cost": actual_usage.get("total_cost", 0.0),
        }
        if raw_usage:
            actual_info["raw_usage"] = raw_usage
    else:
        actual_info = None

    return {
        "system": system_info,
        "tools": tools_info,
        "messages": message_infos,
        "compaction": compaction_info,
        "summary": summary,
        "actual_usage": actual_info,
        "iteration": iteration,
        "model": model_id,
    }
