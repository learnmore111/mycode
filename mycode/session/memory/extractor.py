"""记忆提取器 — 从对话中自动提取记忆。

- 分析对话历史以提取值得记忆的信息
- 分类为四种记忆类型（user/feedback/project/reference）
- 当代理已写入记忆时跳过提取
- 如果 LLM 不可用，则回退到基于规则的提取
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mycode.session.memory.memdir import (
    scan_memory_files,
)
from mycode.util import log as logmod

logger = logmod.create(service="session.memory.extractor")


@dataclass
class ExtractionResult:
    """从对话中提取记忆的结果。"""
    extracted: list[dict[str, Any]]  # [{name, description, type, content}]
    skipped_reason: str | None = None  # Why extraction was skipped


async def extract_memories(
    project_path: str,
    messages: list[dict[str, Any]],
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    has_memory_writes_since: bool = False,
) -> ExtractionResult:
    """分析对话消息并提取要保存的记忆。

    参数:
        project_path: 项目根路径
        messages: 对话消息（OpenAI 格式）
        model_name: 用于提取的 LLM 模型名称
        api_key: 模型的 API 密钥
        has_memory_writes_since: 如果为 True，代理在本回合已写入记忆 — 跳过

    返回:
        包含提取记忆的结果
    """
    # 如果代理已写入记忆则跳过
    if has_memory_writes_since:
        return ExtractionResult(extracted=[], skipped_reason="agent already wrote memories this turn")

    # 跳过非常短的对话
    user_messages = [m for m in messages if m.get("role") == "user"]
    if len(user_messages) < 2:
        return ExtractionResult(extracted=[], skipped_reason="conversation too short")

    # 加载现有记忆以避免重复
    existing = scan_memory_files(project_path)
    existing_names = {e.name.lower() for e in existing}

    # 尝试 LLM 提取
    if model_name and api_key:
        return await _extract_with_llm(
            project_path, messages, model_name, api_key, existing_names,
        )

    # 回退：基于规则的提取
    return _extract_with_rules(project_path, messages, existing_names)


async def _extract_with_llm(
    project_path: str,
    messages: list[dict[str, Any]],
    model_name: str,
    api_key: str,
    existing_names: set[str],
) -> ExtractionResult:
    """使用 LLM 分析提取记忆。"""
    # 构建用于提取的对话摘要
    conversation_text = _format_conversation(messages, max_chars=8000)
    existing_list = ", ".join(sorted(existing_names)[:20]) if existing_names else "(none)"

    prompt = f"""You are a memory extraction system. Analyze this conversation and decide if any information should be saved as long-term memory.

## Memory Types
- **user**: User role, goals, preferences, knowledge level
- **feedback**: User guidance on work style (corrections + confirmations)
- **project**: Ongoing work, goals, events, deadlines
- **reference**: Pointers to information in external systems

## Rules
- ONLY save information that CANNOT be derived from current project state
- DO NOT save: code patterns, file paths, git history, debug solutions, temporary task details
- Existing memories: {existing_list}
- DO NOT create duplicates of existing memories

## Conversation
{conversation_text}

## Output Format
If you find memories to save, output them in this exact format (one per block):
```
MEMORY_START
name: <short name>
description: <one-line description for future relevance matching>
type: <user|feedback|project|reference>
content: <the memory content — for feedback/project, include **Why:** and **How to apply:**>
MEMORY_END
```

If no memories should be saved, output: NO_MEMORIES
"""

    try:
        import litellm
        response = await litellm.acompletion(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.3,
            api_key=api_key,
        )
        raw = response.choices[0].message.content or ""

        if "NO_MEMORIES" in raw:
            return ExtractionResult(extracted=[])

        extracted = _parse_extraction_response(raw, existing_names)
        return ExtractionResult(extracted=extracted)

    except Exception as e:
        logger.error("LLM extraction failed", error=str(e))
        return _extract_with_rules(project_path, messages, existing_names)


def _extract_with_rules(
    project_path: str,
    messages: list[dict[str, Any]],
    existing_names: set[str],
) -> ExtractionResult:
    """基于规则的记忆提取（LLM 不可用时的回退方案）。

    查找关于偏好和工作风格的明确用户指示。
    """
    extracted: list[dict[str, Any]] = []

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        # 检测明确的偏好声明
        preference_patterns = [
            r"(?:always|never|prefer|don'?t|please|make sure|remember)\s+(.{10,100})",
            r"(?:I|we)\s+(?:prefer|like|want|need|use)\s+(.{10,100})",
        ]

        for pattern in preference_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                name = _infer_memory_name(match)
                if name.lower() not in existing_names:
                    extracted.append({
                        "name": name,
                        "description": f"User preference: {match[:80]}",
                        "type": "feedback",
                        "content": f"{match}\n\n**Why:** User explicitly stated this preference.\n**How to apply:** Follow this preference in future interactions.",
                    })
                    existing_names.add(name.lower())

    return ExtractionResult(extracted=extracted[:3])  # 上限为 3


def save_extracted_memories(
    project_path: str,
    extraction: ExtractionResult,
) -> list[str]:
    """将兼容提取结果写入 SQLite pending inbox。返回 memory ID。"""
    from mycode.session.memory.service import MemoryService
    from mycode.session.memory.service import MemoryType as LifecycleMemoryType

    service = MemoryService(project_path)
    saved: list[str] = []
    for mem in extraction.extracted:
        try:
            type_map: dict[str, LifecycleMemoryType] = {
                "user": "user_preference",
                "feedback": "feedback",
                "project": "project_fact",
                "reference": "reference",
            }
            record = service.create(
                subject=mem["name"],
                trigger_description=mem["description"],
                memory_type=type_map.get(mem["type"], "project_fact"),
                content=mem["content"],
                source_kind="agent_inference",
                status="pending",
                extractor_version="legacy-extractor-v1",
                created_by="legacy_extractor",
            )
            saved.append(record.id)
        except Exception as e:
            logger.error("failed to save extracted memory", name=mem.get("name"), error=str(e))
    return saved


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _format_conversation(messages: list[dict[str, Any]], max_chars: int = 8000) -> str:
    """格式化对话消息以用于提取提示词。"""
    lines: list[str] = []
    total = 0
    # 取最后 N 条消息以适应预算
    for msg in reversed(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            # 内容块（例如 tool_use、来自多模态 API 的文本块）
            text_parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = " ".join(text_parts)[:500]
        elif not isinstance(content, str):
            content = ""
        text = f"[{role}] {content[:500]}"
        if total + len(text) > max_chars:
            break
        lines.insert(0, text)
        total += len(text)
    return "\n".join(lines)


def _parse_extraction_response(raw: str, existing_names: set[str]) -> list[dict[str, Any]]:
    """将 LLM 提取响应解析为记忆字典。"""
    blocks = raw.split("MEMORY_START")
    extracted: list[dict[str, Any]] = []

    for block in blocks[1:]:  # Skip text before first MEMORY_START
        end_idx = block.find("MEMORY_END")
        if end_idx < 0:
            continue
        block = block[:end_idx].strip()

        mem: dict[str, Any] = {}
        content_lines: list[str] = []
        in_content = False

        for line in block.split("\n"):
            if in_content:
                content_lines.append(line)
                continue

            for key in ("name", "description", "type"):
                if line.lower().startswith(f"{key}:"):
                    mem[key] = line[len(key) + 1:].strip()
                    break
            else:
                if line.lower().startswith("content:"):
                    in_content = True
                    rest = line[len("content:"):].strip()
                    if rest:
                        content_lines.append(rest)

        if content_lines:
            mem["content"] = "\n".join(content_lines).strip()

        # Validate
        if mem.get("name") and mem.get("type") and mem.get("content"):
            if mem["type"] not in ("user", "feedback", "project", "reference"):
                mem["type"] = "project"
            if mem["name"].lower() not in existing_names:
                extracted.append(mem)

    return extracted


def _infer_memory_name(text: str) -> str:
    """从文本片段推断短记忆名称。"""
    # 取前几个有意义的单词
    words = re.findall(r"[a-zA-Z]+", text)[:4]
    if words:
        return "_".join(w.lower() for w in words)
    return "user_preference"
