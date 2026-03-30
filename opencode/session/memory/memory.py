"""Session memory — dual-layer memory system for AI agent context.

Two types of memory:
1. Session Summary Note — high-level technical context memo, updated every N turns.
   Used to quickly restore working state on next session start.
2. Interaction Log — near-lossless per-turn record of user queries, tool calls
   (with parameters), and brief results. Enables the agent to recall exactly
   what it did and where it found things.
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


@dataclass
class InteractionEntry:
    """A single turn's interaction record — near-lossless."""

    turn: int  # 1-based turn number
    timestamp: str  # ISO format
    user_query: str  # what the user asked (truncated to 500 chars)
    tool_calls: list[dict[str, Any]]  # [{tool, input_summary, output_summary, file}]
    assistant_summary: str  # brief summary of assistant response (first 200 chars)


class InteractionLog:
    """Near-lossless per-turn interaction log.

    Records every user→agent→tool interaction with enough detail that
    a future agent can recall exactly what happened: "user asked about X,
    agent used read_file on /path/to/file.py with pattern='foo', found bar".

    Storage: one JSONL file per session under memory/interactions/<date>/<session>.jsonl
    Each line is one InteractionEntry serialized as JSON.
    """

    def __init__(self, project_path: str, session_id: str):
        self.project_path = project_path
        self.session_id = session_id
        self.interactions_dir = MEMORY_DIR / "interactions"
        self._turn_counter = 0
        self._current_tool_calls: list[dict[str, Any]] = []

    def _ensure_dirs(self) -> None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        (self.interactions_dir / date_str).mkdir(parents=True, exist_ok=True)

    def _log_path(self) -> Path:
        date_str = datetime.now().strftime("%Y-%m-%d")
        session_prefix = self.session_id[:8] if len(self.session_id) > 8 else self.session_id
        return self.interactions_dir / date_str / f"{session_prefix}.jsonl"

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str,
        status: str = "completed",
    ) -> None:
        """Record a tool call during the current turn.

        Call this for each tool event received from the agentic loop.
        The tool calls are buffered and flushed when record_turn() is called.
        """
        # Extract the most useful bits from input — file path, query, pattern, etc.
        input_summary = self._summarize_input(tool_name, tool_input)
        output_summary = tool_output[:200].strip() if tool_output else ""
        file_path = (
            tool_input.get("filePath")
            or tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("target_file")
            or ""
        )

        self._current_tool_calls.append({
            "tool": tool_name,
            "status": status,
            "input": input_summary,
            "output": output_summary,
            "file": file_path,
        })

    def record_turn(
        self,
        user_query: str,
        assistant_response: str,
    ) -> InteractionEntry:
        """Flush the current turn to disk.

        Call this after each user→agent exchange is complete.
        Returns the InteractionEntry for testing/inspection.
        """
        self._turn_counter += 1
        entry = InteractionEntry(
            turn=self._turn_counter,
            timestamp=datetime.now().isoformat(),
            user_query=user_query[:500],
            tool_calls=list(self._current_tool_calls),
            assistant_summary=assistant_response[:200].strip(),
        )
        self._current_tool_calls = []

        # Append to JSONL file
        self._ensure_dirs()
        log_path = self._log_path()
        with log_path.open("a", encoding="utf-8") as f:
            record = {
                "turn": entry.turn,
                "ts": entry.timestamp,
                "q": entry.user_query,
                "tools": entry.tool_calls,
                "a": entry.assistant_summary,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug("recorded interaction turn", turn=entry.turn, tools=len(entry.tool_calls))
        return entry

    def load_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Load recent interaction entries from the current session's JSONL file."""
        log_path = self._log_path()
        if not log_path.exists():
            return []
        entries = []
        try:
            for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
                if line:
                    entries.append(json.loads(line))
        except (json.JSONDecodeError, IOError):
            return []
        return entries[-limit:]

    def format_for_context(self, limit: int = 20) -> str:
        """Format recent interactions as agent-consumable context.

        Output is a compact record that the agent can scan to recall:
        'I searched for X in file Y and found Z'.
        """
        entries = self.load_log(limit=limit)
        if not entries:
            return ""

        lines = ["<interaction_log>"]
        for entry in entries:
            turn = entry.get("turn", "?")
            q = entry.get("q", "")
            tools = entry.get("tools", [])
            a = entry.get("a", "")

            lines.append(f"<turn n=\"{turn}\">")
            lines.append(f"  user: {q[:300]}")
            if tools:
                for tc in tools:
                    tool = tc.get("tool", "?")
                    inp = tc.get("input", "")
                    out = tc.get("output", "")[:100]
                    f = tc.get("file", "")
                    parts = [f"    {tool}"]
                    if f:
                        parts.append(f"file={f}")
                    if inp:
                        parts.append(f"({inp})")
                    if out:
                        parts.append(f"→ {out}")
                    lines.append(" ".join(parts))
            if a:
                lines.append(f"  assistant: {a[:200]}")
            lines.append("</turn>")

        lines.append("</interaction_log>")
        return "\n".join(lines)

    @staticmethod
    def _summarize_input(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Extract the most informative bits from tool input for the log.

        Goal: just enough to understand what the tool was asked to do,
        not the full verbose input.
        """
        if not tool_input:
            return ""

        # For search tools — capture the query/pattern
        for key in ("query", "pattern", "queryString", "search", "regex"):
            if key in tool_input:
                return f"{key}={tool_input[key]!r}"

        # For file tools — just the path (already captured in 'file' field)
        for key in ("filePath", "file_path", "path", "target_file"):
            if key in tool_input:
                # Also capture offset/limit if present
                extras = []
                if "offset" in tool_input:
                    extras.append(f"offset={tool_input['offset']}")
                if "limit" in tool_input:
                    extras.append(f"limit={tool_input['limit']}")
                if extras:
                    return ", ".join(extras)
                return ""

        # For edit tools — capture old_str snippet
        if "old_str" in tool_input:
            old = tool_input["old_str"][:80]
            return f"replacing: {old!r}"

        # For command tools
        if "command" in tool_input:
            return f"cmd={tool_input['command'][:100]!r}"

        # Generic: dump first key-value pair
        for k, v in tool_input.items():
            if k.startswith("_"):
                continue
            val = str(v)[:80]
            return f"{k}={val!r}"
        return ""


class SessionMemory:
    """Manages session memory notes.

    Supports two update modes:
    - Final save: save_note() at session end (original behavior).
    - Rolling update: update_summary_if_due() called every turn from the
      CLI loop — actually writes/overwrites the summary every N user turns
      (default 5). This ensures the summary stays fresh during long sessions
      and is always available even if the session crashes.
    """

    # How many user turns between summary updates
    SUMMARY_UPDATE_INTERVAL = 5

    def __init__(self, project_path: str, session_id: str | None = None):
        self.project_path = project_path
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.memory_dir = MEMORY_DIR
        self.notes_dir = self.memory_dir / "notes"
        self.index_path = self.memory_dir / "index.json"
        self._config = self._load_config()
        self._turn_count = 0  # tracks user turns for rolling update
        self._last_summary_path: Path | None = None  # path to current rolling summary file

    def _load_config(self) -> dict[str, Any]:
        """Load session memory config.
        
        If no model is specified in sessionMemory config, falls back to main model config.
        """
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
        
        # Build model config - use sessionMemory.model if specified, otherwise use main model
        model_config = None
        if sm.model and (sm.model.provider or sm.model.name):
            # Explicit model config in sessionMemory
            model_config = {
                "provider": sm.model.provider,
                "name": sm.model.name,
                "base_url": sm.model.base_url,
                "api_key": sm.model.api_key,
                "api_key_env": sm.model.api_key_env,
            }
        elif cfg.model:
            # Fallback to main model (e.g., "deepseek/deepseek-chat")
            model_config = self._parse_main_model(cfg)
        
        return {
            "enabled": sm.enabled or False,
            "model": model_config,
            "note_language": sm.note_language or "en",
            "min_duration_minutes": sm.min_duration_minutes or 1,
            "min_user_prompts": sm.min_user_prompts or 1,
            "max_notes_per_project": sm.max_notes_per_project or 50,
            "max_recent_for_context": sm.max_recent_for_context or 5,
        }
    
    def _parse_main_model(self, cfg: Any) -> dict[str, Any] | None:
        """Parse main model config (e.g., 'deepseek/deepseek-chat') into model config dict."""
        if not cfg.model:
            return None
        
        parts = cfg.model.split("/", 1)
        if len(parts) != 2:
            return None
        
        provider_id, model_id = parts
        
        # Get provider config to extract API key and base URL
        base_url = None
        api_key = None
        api_key_env = None
        
        if cfg.provider and provider_id in cfg.provider:
            pcfg = cfg.provider[provider_id]
            if pcfg.api:
                base_url = pcfg.api
            if pcfg.options:
                api_key = pcfg.options.get("apiKey")
            if pcfg.env:
                api_key_env = pcfg.env[0] if isinstance(pcfg.env, list) and pcfg.env else pcfg.env
        
        # Map well-known providers to their env vars
        if not api_key_env:
            provider_env_map = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
                "google": "GOOGLE_API_KEY",
                "groq": "GROQ_API_KEY",
            }
            api_key_env = provider_env_map.get(provider_id)
        
        return {
            "provider": provider_id,
            "name": model_id,
            "base_url": base_url,
            "api_key": api_key,
            "api_key_env": api_key_env,
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
            
            # Get base_url from provider config if not specified
            if not base_url:
                cfg = configmod.get()
                if cfg.provider and provider in cfg.provider:
                    pcfg = cfg.provider[provider]
                    base_url = pcfg.api

            # Build model string for litellm
            # For custom providers like deepseek, use openai-compatible format
            if provider == "anthropic":
                model_str = f"anthropic/{model_name}"
            elif provider == "openai" and not base_url:
                model_str = f"openai/{model_name}"
            elif provider == "deepseek":
                # DeepSeek uses OpenAI-compatible API
                model_str = f"openai/{model_name}"
                if not base_url:
                    base_url = "https://api.deepseek.com/v1"
            elif base_url:
                # Custom provider with base_url uses openai-compatible format
                model_str = f"openai/{model_name}"
            else:
                # Try provider/model format for known providers
                model_str = f"{provider}/{model_name}"

            kwargs: dict[str, Any] = {
                "model": model_str,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0.3,
                "api_key": api_key,
            }
            if base_url:
                kwargs["base_url"] = base_url

            logger.debug("calling LLM for summary", provider=provider, model=model_name, base_url=base_url)
            response = await litellm.acompletion(**kwargs)
            summary = response.choices[0].message.content or ""
            logger.info("generated AI summary", length=len(summary))
            return summary

        except Exception as e:
            logger.error("failed to generate AI summary", error=str(e))
            return self._create_simple_summary(parsed)

    def _build_summary_prompt(self, parsed: ParsedConversation, lang: str) -> str:
        """Build prompt for AI summary — generates agent-oriented technical context memo.

        The output is NOT for humans to read. It is structured technical context
        that an AI agent can consume on next session start to quickly restore
        working state: what was done, what changed, what broke, what's left.
        """
        # Use same prompt regardless of lang — the output is for the agent, not the user
        template = """You are an AI coding agent's memory system. Analyze this session and produce
a concise technical context memo that YOUR FUTURE SELF can read to resume work.

## Raw Session Data
- project: {project}
- duration: {duration} min
- user_prompts: {user_prompts}
- tool_uses: {tool_uses}
- files_modified: {files_modified}
- files_read: {files_read}
- assistant_responses: {assistant_summaries}

---

Output a memo in EXACTLY this format (no extra sections, no prose):

## what_was_done
[1-3 bullet points: concrete technical changes made. e.g. "Added retry logic to opencode/http/client.py with exponential backoff"]

## technical_context
[1-3 bullet points: key technical facts the agent needs to know. e.g. "Project uses uv as package manager, Python 3.14, litellm for LLM calls"]

## problems_encountered
[0-2 bullet points: bugs, errors, failed approaches. e.g. "cd command didn't work because subprocess doesn't persist cwd — fixed by tracking shell_cwd in main process"]

## unfinished_work
[0-2 bullet points: things left incomplete, with enough detail to resume. e.g. "TODO: add unit tests for the new ShellCompleter class in tests/test_cli.py"]

## file_changes
[list each modified file with a SHORT description of what changed. e.g. "opencode/cli/main.py: added cd handling, smart completers, bottom toolbar"]

Rules:
- Be extremely concise — every word must carry information
- Focus on TECHNICAL FACTS, not narrative
- No filler phrases like "In this session..." or "The user asked..."
- If a section has nothing, write "none"
- Output plain text, no code blocks"""

        return template.format(
            project=self.project_path,
            duration=parsed.duration_minutes,
            user_prompts="; ".join(p[:200] for p in parsed.user_prompts) or "(none)",
            tool_uses=", ".join(
                f"{t['name']}×{t['count']}" for t in parsed.tool_uses
            ) or "(none)",
            files_modified=", ".join(parsed.files_modified) or "(none)",
            files_read=", ".join(parsed.files_read[:10]) or "(none)",
            assistant_summaries=" | ".join(
                s[:150] for s in parsed.assistant_summaries
            ) or "(none)",
        )

    def _create_simple_summary(self, parsed: ParsedConversation) -> str:
        """Create a structured technical summary without AI (fallback)."""
        lines = []

        # what_was_done — derive from user prompts
        lines.append("## what_was_done")
        if parsed.user_prompts:
            for p in parsed.user_prompts[:5]:
                lines.append(f"- user request: {p[:200]}")
        else:
            lines.append("- none")

        # technical_context
        lines.append("\n## technical_context")
        lines.append(f"- duration: {parsed.duration_minutes}min, {len(parsed.user_prompts)} prompts")
        if parsed.tool_uses:
            top_tools = ", ".join(f"{t['name']}×{t['count']}" for t in parsed.tool_uses[:5])
            lines.append(f"- tools: {top_tools}")

        # problems_encountered
        lines.append("\n## problems_encountered")
        lines.append("- none (no AI analysis available)")

        # unfinished_work
        lines.append("\n## unfinished_work")
        lines.append("- none (no AI analysis available)")

        # file_changes
        lines.append("\n## file_changes")
        if parsed.files_modified:
            for f in parsed.files_modified:
                lines.append(f"- {f}: modified")
        elif parsed.files_read:
            for f in parsed.files_read[:5]:
                lines.append(f"- {f}: read only")
        else:
            lines.append("- none")

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

    def tick_turn(self) -> int:
        """Increment the turn counter. Returns new turn count."""
        self._turn_count += 1
        return self._turn_count

    async def update_summary_if_due(
        self,
        messages: list[dict[str, Any]],
        start_time: datetime | None = None,
        force: bool = False,
    ) -> Path | None:
        """Rolling update: rewrite the session summary every N turns.

        Call this after each user turn. It will only actually regenerate
        the summary when turn_count is a multiple of SUMMARY_UPDATE_INTERVAL,
        or when force=True (e.g. at session end).

        Unlike save_note(), this OVERWRITES the same file each time (no new
        file per update), and skips min_duration/min_prompts checks since
        we want rolling updates even for short sessions.
        """
        if not self.is_enabled:
            return None

        if not force and (self._turn_count % self.SUMMARY_UPDATE_INTERVAL != 0):
            return None

        # Parse conversation
        parsed = self.parse_conversation(messages, start_time)

        if not parsed.user_prompts:
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

        # Save — overwrite the same file for this session
        if self._last_summary_path and self._last_summary_path.exists():
            # Overwrite existing summary file
            lang = self._config.get("note_language", "en")
            content = self._format_note_markdown(note, lang)
            self._last_summary_path.write_text(content, encoding="utf-8")
            note_path = self._last_summary_path
        else:
            # First time — create new file
            note_path = self._save_note_file(note)
            self._last_summary_path = note_path
            # Update index only on first creation
            self._update_index(note, note_path)

        logger.info("rolling summary updated", path=str(note_path), turn=self._turn_count)
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
        """Format note as agent-oriented structured markdown.

        This format is designed for AI agent consumption, NOT human reading.
        It contains machine-parseable sections that an agent can use to
        restore working context on the next session.
        """
        lines = [
            "# agent-memory",
            "",
            "## meta",
            f"- session_id: {note.session_id}",
            f"- project: {note.project_path}",
            f"- time: {note.start_time} → {note.end_time}",
            f"- duration: {note.duration_minutes}min",
        ]
        if note.key_topics:
            lines.append(f"- topics: {', '.join(note.key_topics)}")
        if note.tool_uses:
            tools_str = ", ".join(f"{k}×{v}" for k, v in note.tool_uses.items())
            lines.append(f"- tools: {tools_str}")

        # The AI-generated (or fallback) summary — already in structured format
        lines.extend(["", note.summary, ""])

        # Always include file changes section for easy scanning
        if note.files_modified:
            # Only add if not already present in the summary
            if "## file_changes" not in note.summary:
                lines.append("## file_changes")
                for f in note.files_modified:
                    lines.append(f"- {f}")
                lines.append("")

        if note.files_read:
            lines.append("## files_read")
            for f in note.files_read[:10]:
                lines.append(f"- {f}")
            lines.append("")

        if note.user_prompts:
            lines.append("## user_prompts")
            for p in note.user_prompts[:10]:
                lines.append(f"- {p[:300]}")
            lines.append("")

        # Footer
        lines.extend([
            "---",
            f"*auto-saved at {datetime.now().isoformat()}*",
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
        """Format recent session summary notes as structured context for the AI agent.

        This produces a compact, machine-friendly context block that gets
        injected into the agent's system prompt. Every word must be useful
        for the agent to restore working state.
        """
        if not notes:
            return ""

        lines = ["<session_history>"]
        for i, note in enumerate(notes, 1):
            note_path = Path(note.get("path", ""))
            if note_path.exists():
                content = note_path.read_text(encoding="utf-8")
                # Extract the structured sections (what_was_done, technical_context, etc.)
                # Skip the meta header and footer, keep the meat
                useful_lines = []
                skip_sections = {"# agent-memory", "## meta", "---"}
                in_meta = False
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped == "## meta":
                        in_meta = True
                        continue
                    if in_meta:
                        if stripped.startswith("## "):
                            in_meta = False
                        else:
                            continue
                    if stripped in skip_sections or stripped.startswith("*auto-saved"):
                        continue
                    # Keep everything else
                    useful_lines.append(line)
                body = "\n".join(useful_lines).strip()
            else:
                body = "(note file missing)"

            date = note.get("date", "?")
            duration = note.get("duration_minutes", 0)
            topics = ", ".join(note.get("topics", [])) or "general"
            lines.append(f"<session date=\"{date}\" duration=\"{duration}min\" topics=\"{topics}\">")
            lines.append(body)
            lines.append("</session>")

        lines.append("</session_history>")
        return "\n".join(lines)

    def format_full_context(
        self,
        notes: list[dict[str, Any]],
        interaction_log: InteractionLog | None = None,
    ) -> str:
        """Format BOTH session summaries AND interaction log as agent context.

        This is the primary method for injecting memory into the agent's
        system prompt. It combines:
        - Session summaries (high-level what_was_done, technical_context, etc.)
        - Interaction log (per-turn detail: user asked X, agent used tool Y on file Z)
        """
        parts = []

        # 1. Session summaries from previous sessions
        summary_ctx = self.format_notes_for_context(notes)
        if summary_ctx:
            parts.append(summary_ctx)

        # 2. Current session's interaction log
        if interaction_log:
            log_ctx = interaction_log.format_for_context(limit=30)
            if log_ctx:
                parts.append(log_ctx)

        return "\n\n".join(parts)


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


def create_interaction_log(project_path: str, session_id: str) -> InteractionLog:
    """Convenience function to create an InteractionLog for a session."""
    return InteractionLog(project_path, session_id)
