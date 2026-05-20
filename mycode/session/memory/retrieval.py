"""记忆检索 — 为当前上下文查找相关记忆。

- 基于关键词的快速匹配（不需要 LLM）
- 针对复杂查询的 LLM 辅助检索（sideQuery 从清单中选择）
- 可配置的最大结果数
- 新鲜度感知（较新的记忆优先）
"""
from __future__ import annotations

import re
from pathlib import Path

from mycode.session.memory.memdir import (
    MemoryEntry,
    format_memory_manifest,
    scan_memory_files,
)
from mycode.util import log as logmod

logger = logmod.create(service="session.memory.retrieval")

MAX_RELEVANT_MEMORIES = 5
MAX_QUERY_TERMS = 80
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def find_relevant_memories(
    project_path: str,
    query: str,
    *,
    max_results: int = MAX_RELEVANT_MEMORIES,
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
) -> list[MemoryEntry]:
    """查找与用户查询相关的记忆。

    使用名称 + 描述 + 内容上的关键词匹配。
    结果按相关性分数排序（越高 = 越相关），
    平局时按新鲜度（较新的优先）。

    参数：
        project_path: 项目根路径
        query: 用户查询文本
        max_results: 返回的最大记忆数
        recent_tools: 最近使用的工具名称列表（用于排除信号）
        already_surfaced: 本次会话中已注入的记忆路径/文件名
    """
    entries = scan_memory_files(project_path)
    if not entries:
        return []
    surfaced = already_surfaced or set()
    entries = [entry for entry in entries if not _is_already_surfaced(entry, surfaced)]

    # 为每条记忆针对查询评分
    scored: list[tuple[float, MemoryEntry]] = []
    query_lower = query.lower()
    query_words = _query_terms(query_lower)

    for entry in entries:
        score = _compute_relevance(entry, query_lower, query_words)
        if score > 0:
            scored.append((score, entry))

    # 按分数降序排列，然后按新鲜度（较新的优先）
    scored.sort(key=lambda x: (-x[0], -x[1].mtime_ms))

    return [entry for _, entry in scored[:max_results]]


def build_memory_context(
    project_path: str,
    query: str,
    *,
    max_results: int = MAX_RELEVANT_MEMORIES,
    already_surfaced: set[str] | None = None,
    recent_tools: list[str] | None = None,
) -> tuple[str, list[MemoryEntry]]:
    """Build user-message memory recall context for selected detailed memories."""
    memories = find_relevant_memories(
        project_path,
        query,
        max_results=max_results,
        recent_tools=recent_tools,
        already_surfaced=already_surfaced,
    )
    if not memories:
        return "", []

    from mycode.session.memory.memdir import format_memories_for_context

    return format_memories_for_context(memories, include_freshness=True), memories


async def find_relevant_memories_llm(
    project_path: str,
    query: str,
    *,
    model_name: str | None = None,
    api_key: str | None = None,
    max_results: int = MAX_RELEVANT_MEMORIES,
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
) -> list[MemoryEntry]:
    """使用 LLM 辅助检索查找相关记忆。

    流程：
    1. 扫描所有记忆文件 → 构建清单（名称 + 描述）
    2. 要求 LLM 从清单中选择相关记忆
    3. 返回包含完整内容的选定记忆条目

    如果 LLM 不可用，则回退到关键词匹配。
    """
    entries = scan_memory_files(project_path)
    if not entries:
        return []
    surfaced = already_surfaced or set()
    entries = [entry for entry in entries if not _is_already_surfaced(entry, surfaced)]
    if not entries:
        return []

    # 如果未配置模型，则回退到关键词匹配
    if not model_name or not api_key:
        return find_relevant_memories(
            project_path,
            query,
            max_results=max_results,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
        )

    # 构建清单
    manifest = format_memory_manifest(entries)

    # 要求 LLM 选择
    tools_section = ""
    if recent_tools:
        tools = ", ".join(sorted(set(recent_tools)))
        tools_section = f"""

Recently used tools: {tools}
Do not select usage reference docs for these tools. Do select warnings, gotchas, or known issues about them.
"""

    prompt = f"""You are selecting memories that will be useful to an AI coding agent as it processes a user's query.
Return a list of filenames for the memories that will clearly be useful (up to {max_results}).
- Be selective and discerning.
- Prefer memories whose descriptions are semantically relevant to the query.
- Consider timestamps: stale project facts may be less useful than recent memories.

User query: {query}

Available memories:
{manifest}{tools_section}

Reply with ONLY the filenames of relevant memories, one per line.
If no memories are relevant, reply with "NONE".
"""

    try:
        import litellm
        response = await litellm.acompletion(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
            temperature=0.0,
            api_key=api_key,
        )
        raw = response.choices[0].message.content or ""

        if "NONE" in raw.upper():
            return []

        # Parse selected filenames
        selected_names = set()
        for line in raw.strip().split("\n"):
            name = line.strip().strip("- ")
            if name:
                selected_names.add(name)

        # Match selected names to entries
        result: list[MemoryEntry] = []
        for entry in entries:
            if entry.filename in selected_names or entry.name in selected_names:
                result.append(entry)
                if len(result) >= max_results:
                    break

        return result

    except Exception as e:
        logger.warn("LLM retrieval failed, falling back to keywords", error=str(e))
        return find_relevant_memories(
            project_path,
            query,
            max_results=max_results,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
        )


# ---------------------------------------------------------------------------
# 评分辅助函数
# ---------------------------------------------------------------------------


def _compute_relevance(
    entry: MemoryEntry,
    query_lower: str,
    query_words: set[str],
) -> float:
    """计算记忆条目针对查询的相关性分数。

    评分：
    - 名称匹配：每个词 3.0
    - 描述匹配：每个词 2.0
    - 内容匹配：每个词 1.0（上限）
    - 类型加成：user/feedback 获得 +0.5（更持久的知识）
    - 新鲜度加成：今天的条目获得 +0.3
    """
    score = 0.0

    name_lower = entry.name.lower()
    desc_lower = entry.description.lower()
    content_lower = entry.content.lower()[:2000]  # Cap content search
    matched = False

    for word in query_words:
        if len(word) < 2:  # 跳过单字符词
            continue
        if word in name_lower:
            score += 3.0
            matched = True
        if word in desc_lower:
            score += 2.0
            matched = True
        if word in content_lower:
            score += 1.0
            matched = True

    # 精确短语匹配奖励
    if query_lower in name_lower:
        score += 5.0
        matched = True
    if query_lower in desc_lower:
        score += 3.0
        matched = True

    if not matched:
        return 0.0

    # 类型加成（user/feedback 记忆往往更普遍相关）
    if entry.memory_type in ("user", "feedback"):
        score += 0.5

    # 新鲜度加成
    from mycode.session.memory.memory import memory_age_days
    if entry.mtime_ms > 0:
        age = memory_age_days(entry.mtime_ms)
        if age == 0:
            score += 0.3
        elif age == 1:
            score += 0.1

    return score


def _query_terms(query_lower: str) -> set[str]:
    """Extract query terms for lightweight multilingual memory matching."""
    terms = {word for word in re.findall(r"\w+", query_lower) if len(word) >= 2}

    # Python's \w+ treats Chinese text as one long token. Add overlapping
    # character bigrams/trigrams so short Chinese queries can still match
    # memory names/descriptions without a full tokenizer dependency.
    cjk_text = "".join(_CJK_RE.findall(query_lower))
    if cjk_text:
        for size in (2, 3):
            for i in range(0, max(len(cjk_text) - size + 1, 0)):
                terms.add(cjk_text[i:i + size])

    return set(list(terms)[:MAX_QUERY_TERMS])


def _is_already_surfaced(entry: MemoryEntry, surfaced: set[str]) -> bool:
    if not surfaced:
        return False
    path = Path(entry.path)
    candidates = {entry.path, entry.name, entry.filename, path.name, path.stem}
    return bool(candidates & surfaced)
