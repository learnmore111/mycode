"""记忆目录管理 — 结构化记忆系统。

- 四种记忆类型：user、feedback、project、reference
- 基于 frontmatter 的记忆文件（YAML 头部 + markdown 正文）
- MEMORY.md 索引文件作为入口点
- 路径安全验证（防止目录遍历）
- 带 frontmatter 解析的记忆文件扫描
- 记忆新鲜度跟踪
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from mycode.util import log as logmod

logger = logmod.create(service="session.memory.memdir")

# ---------------------------------------------------------------------------
# 记忆类型
# ---------------------------------------------------------------------------

MemoryType = Literal["user", "feedback", "project", "reference"]

MEMORY_TYPE_DESCRIPTIONS: dict[MemoryType, str] = {
    "user": "User role, goals, preferences, knowledge level",
    "feedback": "User guidance on work style (corrections + confirmations)",
    "project": "Ongoing work, goals, events, deadlines",
    "reference": "Pointers to information in external systems",
}

# 不应保存为记忆的内容（可以从代码库推导）
MEMORY_EXCLUSIONS = [
    "Code patterns, architecture, file paths (use grep/git/CLAUDE.md)",
    "Git history (git log/blame is authoritative)",
    "Debug solutions (fix is in code, context in commit message)",
    "Content already in CLAUDE.md / .mycode instructions",
    "Temporary task details",
]

# 限制
MAX_MEMORY_FILES = 200
MAX_MEMORY_INDEX_LINES = 200
MAX_MEMORY_INDEX_SIZE = 25 * 1024  # 25KB

# Frontmatter 正则表达式
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """带有解析 frontmatter 的单个记忆文件。"""
    path: str                  # Absolute path to memory file
    name: str                  # Memory name from frontmatter
    description: str           # One-line description
    memory_type: MemoryType    # user/feedback/project/reference
    content: str               # Full markdown body (after frontmatter)
    mtime_ms: float = 0.0     # File modification time in milliseconds
    size_bytes: int = 0        # File size

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def relative_path(self) -> str:
        """返回仅用于显示的不带扩展名的文件名。"""
        return Path(self.path).stem


@dataclass
class MemoryIndex:
    """包含所有发现记忆的 MEMORY.md 索引。"""
    entries: list[MemoryEntry] = field(default_factory=list)
    base_dir: str = ""

    @property
    def count(self) -> int:
        return len(self.entries)

    def by_type(self, memory_type: MemoryType) -> list[MemoryEntry]:
        return [e for e in self.entries if e.memory_type == memory_type]


# ---------------------------------------------------------------------------
# 路径管理
# ---------------------------------------------------------------------------


def memory_base_dir(project_path: str) -> str:
    """获取项目的记忆基础目录。

    优先级：
    1. OPENCODE_MEMORY_DIR 环境变量（完全覆盖）
    2. <project>/.mycode/memory/
    """
    override = os.environ.get("OPENCODE_MEMORY_DIR")
    if override and os.path.isabs(override):
        return override
    return os.path.join(project_path, ".mycode", "memory")


def memdir_path(project_path: str) -> str:
    """获取 memdir 目录（结构化记忆所在位置）。"""
    return os.path.join(memory_base_dir(project_path), "memdir")


def memory_index_path(project_path: str) -> str:
    """获取结构化记忆的 MEMORY.md 索引路径。"""
    return os.path.join(memdir_path(project_path), "MEMORY.md")


def validate_memory_path(path: str) -> str | None:
    """验证记忆文件路径的安全性。

    如果不安全则返回错误消息，如果安全则返回 None。
    拒绝：
    - 相对路径（../）
    - 根 / 近根路径
    - 空字节
    - 非绝对路径
    """
    if not path:
        return "Empty path"

    if "\x00" in path:
        return "Path contains null byte"

    if not os.path.isabs(path):
        return f"Path must be absolute: {path}"

    resolved = os.path.realpath(path)

    # 检查近根路径（例如 /a、/b）
    parts = Path(resolved).parts
    if len(parts) <= 2:
        return f"路径太接近根目录: {resolved}"

    # 检查目录遍历
    if ".." in Path(path).parts:
        return f"路径包含目录遍历: {path}"

    return None


def sanitize_memory_name(name: str) -> str:
    """清理记忆名称以用作文件名。

    转换为小写，将空格/特殊字符替换为下划线。
    """
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip().lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "unnamed"


# ---------------------------------------------------------------------------
# Frontmatter 解析
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """从记忆文件中解析 YAML frontmatter。

    返回 (metadata_dict, body_text)。
    如果未找到 frontmatter，则返回 ({}, full_content)。
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_block = match.group(1)
    body = match.group(2).strip()

    # 简单的 YAML 解析器（仅键值对）
    metadata: dict[str, str] = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                metadata[key] = value

    return metadata, body


def format_frontmatter(
    name: str,
    description: str,
    memory_type: MemoryType,
    body: str,
) -> str:
    """使用 YAML frontmatter 格式化记忆文件。"""
    return f"""---
name: {name}
description: {description}
type: {memory_type}
---

{body}
"""


# ---------------------------------------------------------------------------
# 记忆文件扫描
# ---------------------------------------------------------------------------


def scan_memory_files(project_path: str) -> list[MemoryEntry]:
    """扫描 memdir 目录中所有带 frontmatter 的记忆文件。

    扫描 markdown 记忆文件，解析 frontmatter 以提取名称、
    描述和类型，然后返回最新的 MAX_MEMORY_FILES 条目。
    """
    base = memdir_path(project_path)
    if not os.path.isdir(base):
        return []

    entries: list[MemoryEntry] = []
    for name in sorted(os.listdir(base)):
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            continue
        if not name.endswith(".md"):
            continue
        if name == "MEMORY.md":
            continue

        try:
            content = Path(fp).read_text(encoding="utf-8")
            stat = os.stat(fp)
            metadata, body = parse_frontmatter(content)

            mem_type = metadata.get("type", "project")
            if mem_type not in ("user", "feedback", "project", "reference"):
                mem_type = "project"

            entries.append(MemoryEntry(
                path=fp,
                name=metadata.get("name", Path(fp).stem),
                description=metadata.get("description", ""),
                memory_type=mem_type,  # type: ignore[arg-type]
                content=body,
                mtime_ms=stat.st_mtime * 1000,
                size_bytes=stat.st_size,
            ))
        except (OSError, UnicodeDecodeError) as e:
            logger.warn("failed to read memory file", path=fp, error=str(e))
            continue

    entries.sort(key=lambda e: e.mtime_ms, reverse=True)
    return entries[:MAX_MEMORY_FILES]


def scan_memory_index(project_path: str) -> MemoryIndex:
    """扫描并从项目的 memdir 构建 MemoryIndex。"""
    entries = scan_memory_files(project_path)
    return MemoryIndex(
        entries=entries,
        base_dir=memdir_path(project_path),
    )


# ---------------------------------------------------------------------------
# 记忆清单格式化（用于检索）
# ---------------------------------------------------------------------------


def format_memory_manifest(entries: list[MemoryEntry]) -> str:
    """将记忆条目格式化为基于 LLM 检索的清单。

    输出格式镜像 Claude Code 的选择器清单：
    - [type] filename (ISO timestamp): description
    """
    lines: list[str] = []
    for entry in entries:
        desc = entry.description or "(no description)"
        ts = datetime.fromtimestamp(entry.mtime_ms / 1000, tz=UTC).isoformat()
        lines.append(f"- [{entry.memory_type}] {entry.filename} ({ts}): {desc}")
    return "\n".join(lines)


def load_memory_index(project_path: str) -> str:
    """使用 Claude Code 风格的启动限制加载 MEMORY.md 索引。

    Claude Code 将顶级记忆文件视为轻量级入口点：
    始终安全地包含前 200 行或 25KB，而详细文件按需获取。
    在此处保持相同的限制，以便索引可以引导模型而不会挤占任务上下文。
    """
    path = memory_index_path(project_path)
    if not os.path.isfile(path):
        return ""

    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""

    lines = raw.splitlines()
    was_line_truncated = len(lines) > MAX_MEMORY_INDEX_LINES
    truncated = "\n".join(lines[:MAX_MEMORY_INDEX_LINES] if was_line_truncated else lines)

    was_byte_truncated = len(truncated.encode("utf-8")) > MAX_MEMORY_INDEX_SIZE
    if was_byte_truncated:
        kept: list[str] = []
        total = 0
        for line in truncated.splitlines():
            line_bytes = len((line + "\n").encode("utf-8"))
            if total + line_bytes > MAX_MEMORY_INDEX_SIZE:
                break
            kept.append(line)
            total += line_bytes
        if kept:
            truncated = "\n".join(kept)
        else:
            truncated = truncated.encode("utf-8")[:MAX_MEMORY_INDEX_SIZE].decode("utf-8", errors="ignore")

    if was_line_truncated or was_byte_truncated:
        reasons = []
        if was_line_truncated:
            reasons.append(f"超过 {MAX_MEMORY_INDEX_LINES} 行")
        if was_byte_truncated:
            reasons.append(f"超过 {MAX_MEMORY_INDEX_SIZE} 字节")
        reason = " and ".join(reasons)
        warning = (
            f"> 警告：MEMORY.md {reason}。仅加载了部分内容。"
            "将索引条目保持在约 200 字符以内的一行；将详细信息移至主题文件中。"
        )
        truncated = f"{truncated}\n\n{warning}" if truncated else warning

    return truncated.strip()


# ---------------------------------------------------------------------------
# MEMORY.md 索引管理
# ---------------------------------------------------------------------------


def build_memory_index_content(entries: list[MemoryEntry]) -> str:
    """构建 MEMORY.md 索引文件的内容。"""
    lines: list[str] = [
        "# Memory Index",
        "",
        f"Total memories: {len(entries)}",
        "",
    ]

    for mem_type in ("user", "feedback", "project", "reference"):
        typed = [e for e in entries if e.memory_type == mem_type]
        if not typed:
            continue
        type_desc = MEMORY_TYPE_DESCRIPTIONS.get(mem_type, mem_type)
        lines.append(f"## {mem_type.title()} ({type_desc})")
        lines.append("")
        for entry in typed:
            desc = entry.description or "(no description)"
            lines.append(f"- **{entry.name}**: {desc}")
        lines.append("")

    return "\n".join(lines[:MAX_MEMORY_INDEX_LINES])


def update_memory_index(project_path: str) -> str:
    """重建并写入 MEMORY.md 索引文件。返回索引路径。"""
    entries = scan_memory_files(project_path)
    content = build_memory_index_content(entries)

    base = memdir_path(project_path)
    os.makedirs(base, exist_ok=True)
    index_path = memory_index_path(project_path)
    Path(index_path).write_text(content, encoding="utf-8")
    logger.debug("updated memory index", path=index_path, count=len(entries))
    return index_path


# ---------------------------------------------------------------------------
# 记忆 CRUD 操作
# ---------------------------------------------------------------------------


def save_memory(
    project_path: str,
    name: str,
    description: str,
    memory_type: MemoryType,
    content: str,
) -> str:
    """将新记忆文件保存到 memdir。返回文件路径。"""
    base = memdir_path(project_path)
    os.makedirs(base, exist_ok=True)

    filename = f"{memory_type}_{sanitize_memory_name(name)}.md"
    filepath = os.path.join(base, filename)

    text = format_frontmatter(name, description, memory_type, content)
    Path(filepath).write_text(text, encoding="utf-8")
    logger.info("saved memory", name=name, type=memory_type, path=filepath)

    # Rebuild index
    update_memory_index(project_path)
    return filepath


def delete_memory(project_path: str, filename: str) -> bool:
    """删除记忆文件。如果已删除则返回 True。"""
    base = memdir_path(project_path)
    filepath = os.path.join(base, filename)

    # 安全检查
    error = validate_memory_path(filepath)
    if error:
        logger.warn("refused to delete memory", path=filepath, error=error)
        return False

    if not os.path.isfile(filepath):
        return False

    os.unlink(filepath)
    logger.info("deleted memory", path=filepath)

    # 重建索引
    update_memory_index(project_path)
    return True


def update_memory(
    project_path: str,
    filename: str,
    *,
    name: str | None = None,
    description: str | None = None,
    memory_type: MemoryType | None = None,
    content: str | None = None,
) -> str | None:
    """更新现有记忆文件。如果已更新则返回路径，如果未找到则返回 None。"""
    base = memdir_path(project_path)
    filepath = os.path.join(base, filename)

    if not os.path.isfile(filepath):
        return None

    # 读取现有内容
    existing_content = Path(filepath).read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(existing_content)

    # 合并更新
    final_name = name or metadata.get("name", Path(filepath).stem)
    final_desc = description or metadata.get("description", "")
    final_type = memory_type or metadata.get("type", "project")
    if final_type not in ("user", "feedback", "project", "reference"):
        final_type = "project"
    final_body = content if content is not None else body

    text = format_frontmatter(final_name, final_desc, final_type, final_body)  # type: ignore[arg-type]
    Path(filepath).write_text(text, encoding="utf-8")
    logger.info("updated memory", name=final_name, path=filepath)

    # Rebuild index
    update_memory_index(project_path)
    return filepath


# ---------------------------------------------------------------------------
# 记忆上下文格式化（用于代理提示词注入）
# ---------------------------------------------------------------------------


def format_memories_for_context(
    entries: list[MemoryEntry],
    *,
    include_freshness: bool = True,
) -> str:
    """格式化选定的记忆以注入代理系统提示词。

    用 XML 标签包装每条记忆，包含类型和新鲜度信息。
    """
    from mycode.session.memory.memory import memory_freshness_note

    if not entries:
        return ""

    lines: list[str] = ["<relevant_memories>"]

    for entry in entries:
        lines.append(f'<memory name="{entry.name}" type="{entry.memory_type}">')

        # 陈旧记忆的新鲜度警告
        if include_freshness and entry.mtime_ms > 0:
            note = memory_freshness_note(entry.mtime_ms)
            if note:
                lines.append(note)

        lines.append(entry.content)
        lines.append("</memory>")

    lines.append("</relevant_memories>")
    return "\n".join(lines)
