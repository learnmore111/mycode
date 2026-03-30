"""Session memory — auto-save conversation notes at session end.

Parses conversation history, generates AI summary, and saves as structured notes.
Inspired by claude-memory skill implementation.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from opencode.config import config as configmod
from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths

logger = logmod.create(service="session.memory")

# Default storage directory
MEMORY_DIR = GlobalPaths.data() / "memory"


@dataclass
class SessionNote:
    """A single session note."""

    session_id: str
    project_path: str
    start_time: str
    end_time: str
    duration_minutes: int
    summary: str
    key_decisions: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    tool_uses: dict[str, int] = field(default_factory=dict)
    open_todos: list[str] = field(default_factory=list)
    user_prompts: list[str] = field(default_factory=list)
    key_topics: list[str] = field(default_factory=list)


@dataclass
class ParsedConversation:
    """Parsed conversation data from message history."""

    session_id: str
    start_time: datetime
    end_time: datetime
    duration_minutes: int
    user_prompts: list[str]
    assistant_summaries: list[str]
    tool_uses: list[dict[str, Any]]
    files_read: list[str]
    files_modified: list[str]
    key_topics: list[str]


class SessionMemory:
    """Manages session memory notes."""

    def __init__(self, project_path: str, session_id: str | None = None):
        self.project_path = project_path
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.memory_dir = MEMORY_DIR
        self.notes_dir = self.memory_dir / "notes"
        self.index_path = self.memory_dir / "index.json"
        self._config = self._load_config()

    def _load_config(self) -> dict[str, Any]:
        """Load session memory config."""
        cfg = configmod.get()
        sm = cfg.session_memory
        if not sm:
            return {
                "enabled": False,
                "note_language": "en",
                "min_duration_minutes": 1,
                "min_user_prompts": 1,
                "max_notes_per_project": 50,
                "max_recent_for_context": 5,
            }
        return {
            "enabled": sm.enabled or False,
            "model": {
                "provider": sm.model.provider if sm.model else None,
                "name": sm.model.name if sm.model else None,
                "base_url": sm.model.base_url if sm.model else None,
                "api_key": sm.model.api_key if sm.model else None,
                "api_key_env": sm.model.api_key_env if sm.model else None,
            } if sm.model else None,
            "note_language": sm.note_language or "en",
            "min_duration_minutes": sm.min_duration_minutes or 1,
            "min_user_prompts": sm.min_user_prompts or 1,
            "max_notes_per_project": sm.max_notes_per_project or 50,
            "max_recent_for_context": sm.max_recent_for_context or 5,
        }

    @property
    def is_enabled(self) -> bool:
        """Check if session memory is enabled."""
        return self._config.get("enabled", False)

    def _ensure_dirs(self) -> None:
        """Ensure storage directories exist."""
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def parse_conversation(
        self,
        messages: list[dict[str, Any]],
        start_time: datetime | None = None,
    ) -> ParsedConversation:
        """Parse conversation messages into structured data."""
        user_prompts: list[str] = []
        assistant_summaries: list[str] = []
        tool_counter: Counter = Counter()
        tool_files: dict[str, set] = defaultdict(set)
        files_read: set[str] = set()
        files_modified: set[str] = set()

        write_tools = {"edit", "write", "write_file", "replace_in_file", "write_to_file"}
        read_tools = {"read", "read_file", "glob", "grep"}

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "user" and content:
                # Truncate very long prompts
                prompt_text = content[:500] + "..." if len(content) > 500 else content
                user_prompts.append(prompt_text.strip())

            elif role == "assistant" and content:
                # Keep substantial assistant responses
                if len(content.strip()) > 20:
                    summary = content[:300] + "..." if len(content) > 300 else content
                    assistant_summaries.append(summary.strip())

            elif role == "tool":
                tool_name = msg.get("name", "unknown")
                tool_counter[tool_name] += 1

                # Extract file paths from tool inputs
                tool_input = msg.get("input", {})
                if isinstance(tool_input, dict):
                    file_path = (
                        tool_input.get("file_path")
                        or tool_input.get("path")
                        or tool_input.get("filePath")
                        or ""
                    )
                    if file_path:
                        tool_files[tool_name].add(file_path)
                        if tool_name in write_tools:
                            files_modified.add(file_path)
                        elif tool_name in read_tools:
                            files_read.add(file_path)

        # Build tool uses list
        tool_uses = []
        for name, count in tool_counter.most_common():
            tool_uses.append({
                "name": name,
                "count": count,
                "files": sorted(tool_files.get(name, set())),
            })

        # Infer key topics from files
        key_topics = self._infer_topics(files_modified)

        # Calculate timing
        now = datetime.now()
        start = start_time or now
        duration = int((now - start).total_seconds() / 60)

        return ParsedConversation(
            session_id=self.session_id,
            start_time=start,
            end_time=now,
            duration_minutes=max(duration, 1),
            user_prompts=user_prompts,
            assistant_summaries=assistant_summaries[-5:],  # Keep last 5
            tool_uses=tool_uses,
            files_read=sorted(files_read),
            files_modified=sorted(files_modified),
            key_topics=key_topics,
        )

    def _infer_topics(self, files: set[str]) -> list[str]:
        """Infer key topics from modified file extensions."""
        ext_to_topic = {
            ".ts": "TypeScript",
            ".tsx": "React/TypeScript",
            ".js": "JavaScript",
            ".jsx": "React/JavaScript",
            ".py": "Python",
            ".rs": "Rust",
            ".go": "Go",
            ".java": "Java",
            ".css": "Styling",
            ".scss": "Styling",
            ".html": "HTML",
            ".md": "Documentation",
            ".json": "Configuration",
            ".yaml": "Configuration",
            ".yml": "Configuration",
            ".sql": "Database",
            ".sh": "Shell Scripts",
        }
        topics = set()
        for f in files:
            ext = Path(f).suffix
            if ext in ext_to_topic:
                topics.add(ext_to_topic[ext])
        return list(topics)

    async def generate_summary(
        self,
        parsed: ParsedConversation,
    ) -> str:
        """Generate AI summary of the conversation."""
        model_config = self._config.get("model")
        if not model_config:
            # Fallback: create simple summary without AI
            return self._create_simple_summary(parsed)

        # Get API key
        api_key = model_config.get("api_key")
        if not api_key:
            api_key_env = model_config.get("api_key_env")
            if api_key_env:
                api_key = os.environ.get(api_key_env)

        if not api_key:
            logger.warn("no API key configured for session memory, using simple summary")
            return self._create_simple_summary(parsed)

        # Build prompt for summary generation
        lang = self._config.get("note_language", "en")
        prompt = self._build_summary_prompt(parsed, lang)

        try:
            import litellm

            provider = model_config.get("provider", "openai")
            model_name = model_config.get("name", "gpt-4o-mini")
            base_url = model_config.get("base_url")

            # Build model string for litellm
            if provider == "anthropic":
                model_str = f"anthropic/{model_name}"
            elif base_url:
                model_str = f"openai/{model_name}"
            else:
                model_str = model_name

            kwargs: dict[str, Any] = {
                "model": model_str,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0.3,
                "api_key": api_key,
            }
            if base_url:
                kwargs["base_url"] = base_url

            response = await litellm.acompletion(**kwargs)
            summary = response.choices[0].message.content or ""
            logger.info("generated AI summary", length=len(summary))
            return summary

        except Exception as e:
            logger.error("failed to generate AI summary", error=str(e))
            return self._create_simple_summary(parsed)

    def _build_summary_prompt(self, parsed: ParsedConversation, lang: str) -> str:
        """Build the prompt for AI summary generation."""
        if lang == "zh":
            template = """请为以下编程会话生成一个结构化的笔记摘要：

## 会话信息
- 会话ID: {session_id}
- 时长: {duration} 分钟
- 项目: {project}

## 用户请求
{user_prompts}

## 工具使用
{tool_uses}

## 修改的文件
{files_modified}

## 读取的文件
{files_read}

## 助手回复摘要
{assistant_summaries}

---

请按以下格式输出笔记：

## 摘要
[一段话总结本次会话完成的工作]

## 关键决策
- [决策1及其原因]
- [决策2及其原因]

## 待办 / 遗留问题
- [ ] [如有未完成的任务或问题]

注意：
- 简洁明了，重点突出
- 不要重复列出已经在上面显示的文件列表
- 使用中文"""
        else:
            template = """Please generate a structured note summary for this coding session:

## Session Info
- Session ID: {session_id}
- Duration: {duration} minutes
- Project: {project}

## User Requests
{user_prompts}

## Tool Usage
{tool_uses}

## Files Modified
{files_modified}

## Files Read
{files_read}

## Assistant Response Summaries
{assistant_summaries}

---

Please output the note in this format:

## Summary
[One paragraph summarizing what was accomplished]

## Key Decisions
- [Decision 1 and reasoning]
- [Decision 2 and reasoning]

## Open TODOs
- [ ] [Any unfinished tasks or issues]

Notes:
- Be concise and highlight key points
- Don't repeat the file lists already shown above
- Use English"""

        return template.format(
            session_id=parsed.session_id,
            duration=parsed.duration_minutes,
            project=self.project_path,
            user_prompts="\n".join(f"- {p}" for p in parsed.user_prompts) or "- (none)",
            tool_uses="\n".join(
                f"- {t['name']}: {t['count']} times" for t in parsed.tool_uses
            ) or "- (none)",
            files_modified="\n".join(f"- {f}" for f in parsed.files_modified) or "- (none)",
            files_read="\n".join(f"- {f}" for f in parsed.files_read[:10]) or "- (none)",
            assistant_summaries="\n".join(
                f"- {s[:200]}" for s in parsed.assistant_summaries
            ) or "- (none)",
        )

    def _create_simple_summary(self, parsed: ParsedConversation) -> str:
        """Create a simple summary without AI."""
        lines = ["## Summary", ""]
        lines.append(f"Session lasted {parsed.duration_minutes} minutes.")
        if parsed.user_prompts:
            lines.append(f"User made {len(parsed.user_prompts)} requests.")
        if parsed.files_modified:
            lines.append(f"Modified {len(parsed.files_modified)} files.")
        if parsed.tool_uses:
            top_tools = [f"{t['name']}({t['count']})" for t in parsed.tool_uses[:5]]
            lines.append(f"Top tools: {', '.join(top_tools)}")
        return "\n".join(lines)

    async def save_note(
        self,
        messages: list[dict[str, Any]],
        start_time: datetime | None = None,
    ) -> Path | None:
        """Parse conversation, generate summary, and save note."""
        if not self.is_enabled:
            logger.debug("session memory disabled, skipping save")
            return None

        # Parse conversation
        parsed = self.parse_conversation(messages, start_time)

        # Check minimum thresholds
        min_duration = self._config.get("min_duration_minutes", 1)
        min_prompts = self._config.get("min_user_prompts", 1)

        if parsed.duration_minutes < min_duration:
            logger.debug(
                "session too short, skipping",
                duration=parsed.duration_minutes,
                min=min_duration,
            )
            return None

        if len(parsed.user_prompts) < min_prompts:
            logger.debug(
                "too few prompts, skipping",
                count=len(parsed.user_prompts),
                min=min_prompts,
            )
            return None

        # Generate summary
        summary = await self.generate_summary(parsed)

        # Create note
        note = SessionNote(
            session_id=parsed.session_id,
            project_path=self.project_path,
            start_time=parsed.start_time.isoformat(),
            end_time=parsed.end_time.isoformat(),
            duration_minutes=parsed.duration_minutes,
            summary=summary,
            files_modified=parsed.files_modified,
            files_read=parsed.files_read,
            tool_uses={t["name"]: t["count"] for t in parsed.tool_uses},
            user_prompts=parsed.user_prompts,
            key_topics=parsed.key_topics,
        )

        # Save to file
        note_path = self._save_note_file(note)

        # Update index
        self._update_index(note, note_path)

        logger.info("saved session note", path=str(note_path))
        return note_path

    def _save_note_file(self, note: SessionNote) -> Path:
        """Save note to markdown file."""
        self._ensure_dirs()

        # Create date directory
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_dir = self.notes_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # Create filename
        time_str = datetime.now().strftime("%H-%M-%S")
        session_prefix = note.session_id[:8] if len(note.session_id) > 8 else note.session_id
        filename = f"{time_str}_{session_prefix}.md"
        note_path = date_dir / filename

        # Build markdown content
        lang = self._config.get("note_language", "en")
        content = self._format_note_markdown(note, lang)

        note_path.write_text(content, encoding="utf-8")
        return note_path

    def _format_note_markdown(self, note: SessionNote, lang: str) -> str:
        """Format note as markdown."""
        if lang == "zh":
            lines = [
                "# 会话笔记",
                "",
                f"- **会话 ID**: {note.session_id}",
                f"- **时间**: {note.start_time} → {note.end_time}",
                f"- **时长**: {note.duration_minutes} 分钟",
                f"- **项目**: {note.project_path}",
            ]
            if note.key_topics:
                lines.append(f"- **主题**: {', '.join(note.key_topics)}")
            if note.tool_uses:
                tools_str = ", ".join(f"{k}×{v}" for k, v in note.tool_uses.items())
                lines.append(f"- **工具**: {tools_str}")
        else:
            lines = [
                "# Session Note",
                "",
                f"- **Session ID**: {note.session_id}",
                f"- **Time**: {note.start_time} → {note.end_time}",
                f"- **Duration**: {note.duration_minutes} min",
                f"- **Project**: {note.project_path}",
            ]
            if note.key_topics:
                lines.append(f"- **Topics**: {', '.join(note.key_topics)}")
            if note.tool_uses:
                tools_str = ", ".join(f"{k}×{v}" for k, v in note.tool_uses.items())
                lines.append(f"- **Tools**: {tools_str}")

        lines.extend(["", note.summary, ""])

        if note.files_modified:
            lines.append("## Files Modified" if lang == "en" else "## 修改的文件")
            for f in note.files_modified:
                lines.append(f"- `{f}`")
            lines.append("")

        # Footer
        lines.extend([
            "---",
            f"*auto-saved by opencode session-memory at {datetime.now().isoformat()}*",
        ])

        return "\n".join(lines)

    def _update_index(self, note: SessionNote, note_path: Path) -> None:
        """Update the notes index."""
        self._ensure_dirs()

        # Load existing index
        index: list[dict[str, Any]] = []
        if self.index_path.exists():
            try:
                index = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                index = []

        # Add new entry
        entry = {
            "path": str(note_path),
            "session_id": note.session_id,
            "project": note.project_path,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "duration_minutes": note.duration_minutes,
            "topics": note.key_topics,
            "indexed_at": datetime.now().isoformat(),
        }
        index.insert(0, entry)

        # Limit entries per project
        max_per_project = self._config.get("max_notes_per_project", 50)
        project_counts: Counter = Counter()
        filtered_index: list[dict[str, Any]] = []
        for e in index:
            proj = e.get("project", "")
            if project_counts[proj] < max_per_project:
                filtered_index.append(e)
                project_counts[proj] += 1

        # Save index
        self.index_path.write_text(
            json.dumps(filtered_index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_recent_notes(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Load recent notes for the current project."""
        if not self.index_path.exists():
            return []

        max_recent = limit or self._config.get("max_recent_for_context", 5)

        try:
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return []

        # Filter by project
        project_notes = [
            e for e in index
            if e.get("project") == self.project_path
        ]

        return project_notes[:max_recent]

    def format_notes_for_context(self, notes: list[dict[str, Any]]) -> str:
        """Format notes as context string for AI."""
        if not notes:
            return ""

        lines = ["## Recent Session History", ""]
        for i, note in enumerate(notes, 1):
            note_path = Path(note.get("path", ""))
            if note_path.exists():
                # Read summary from file
                content = note_path.read_text(encoding="utf-8")
                # Extract just the summary section
                summary_lines = []
                in_summary = False
                for line in content.split("\n"):
                    if line.startswith("## Summary") or line.startswith("## 摘要"):
                        in_summary = True
                        continue
                    if in_summary:
                        if line.startswith("## "):
                            break
                        summary_lines.append(line)
                summary = "\n".join(summary_lines).strip()
            else:
                summary = "(note file not found)"

            date = note.get("date", "?")
            duration = note.get("duration_minutes", 0)
            topics = ", ".join(note.get("topics", [])) or "general"
            lines.append(f"### Session {i} ({date}, {duration}min, {topics})")
            lines.append(summary[:500])
            lines.append("")

        return "\n".join(lines)


# --- Convenience functions ---


async def save_session_note(
    project_path: str,
    session_id: str,
    messages: list[dict[str, Any]],
    start_time: datetime | None = None,
) -> Path | None:
    """Convenience function to save a session note."""
    memory = SessionMemory(project_path, session_id)
    return await memory.save_note(messages, start_time)


def load_recent_notes(project_path: str, limit: int = 5) -> list[dict[str, Any]]:
    """Convenience function to load recent notes for a project."""
    memory = SessionMemory(project_path)
    return memory.load_recent_notes(limit)
