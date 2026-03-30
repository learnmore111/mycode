"""Tests for session memory module."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from opencode.session.memory.memory import (
    InteractionEntry,
    InteractionLog,
    ParsedConversation,
    SessionMemory,
    SessionNote,
    load_recent_notes,
    save_session_note,
)


@pytest.fixture
def temp_memory_dir(tmp_path: Path):
    """Create a temporary memory directory."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return memory_dir


@pytest.fixture
def sample_messages():
    """Sample conversation messages for testing."""
    return [
        {"role": "user", "content": "Help me create a Python function"},
        {"role": "assistant", "content": "Sure, I'll help you create a Python function."},
        {"role": "tool", "name": "read", "input": {"path": "/src/main.py"}},
        {"role": "tool", "name": "edit", "input": {"file_path": "/src/main.py"}},
        {"role": "user", "content": "Now add tests"},
        {"role": "assistant", "content": "I'll add tests for the function."},
        {"role": "tool", "name": "write", "input": {"file_path": "/tests/test_main.py"}},
    ]


class TestSessionMemory:
    """Tests for SessionMemory class."""

    def test_init(self, tmp_path: Path):
        """Test SessionMemory initialization."""
        memory = SessionMemory("/test/project", "test-session-123")
        assert memory.project_path == "/test/project"
        assert memory.session_id == "test-session-123"

    def test_init_with_generated_session_id(self, tmp_path: Path):
        """Test SessionMemory generates session ID if not provided."""
        memory = SessionMemory("/test/project")
        assert memory.session_id is not None
        assert len(memory.session_id) > 0

    def test_is_enabled_default_false(self, tmp_path: Path):
        """Test session memory is disabled by default."""
        memory = SessionMemory("/test/project")
        # Without config, should be disabled
        assert memory.is_enabled is False

    def test_parse_conversation_basic(self, sample_messages):
        """Test parsing conversation messages."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        assert isinstance(parsed, ParsedConversation)
        assert parsed.session_id == "test-session"
        assert len(parsed.user_prompts) == 2
        assert "Help me create a Python function" in parsed.user_prompts[0]
        assert len(parsed.assistant_summaries) > 0

    def test_parse_conversation_extracts_files(self, sample_messages):
        """Test that file paths are extracted from tool calls."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        assert "/src/main.py" in parsed.files_read or "/src/main.py" in parsed.files_modified
        assert "/tests/test_main.py" in parsed.files_modified

    def test_parse_conversation_counts_tools(self, sample_messages):
        """Test that tool usage is counted."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        tool_names = [t["name"] for t in parsed.tool_uses]
        assert "read" in tool_names or "edit" in tool_names or "write" in tool_names

    def test_infer_topics_python(self):
        """Test topic inference for Python files."""
        memory = SessionMemory("/test/project")
        topics = memory._infer_topics({"/src/main.py", "/tests/test_main.py"})
        assert "Python" in topics

    def test_infer_topics_javascript(self):
        """Test topic inference for JavaScript files."""
        memory = SessionMemory("/test/project")
        topics = memory._infer_topics({"/src/app.js", "/src/component.tsx"})
        assert "JavaScript" in topics or "React/TypeScript" in topics

    def test_infer_topics_documentation(self):
        """Test topic inference for documentation files."""
        memory = SessionMemory("/test/project")
        topics = memory._infer_topics({"/README.md", "/docs/guide.md"})
        assert "Documentation" in topics

    def test_create_simple_summary(self, sample_messages):
        """Test simple summary creation without AI (agent-oriented format)."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)
        summary = memory._create_simple_summary(parsed)

        assert "## what_was_done" in summary
        assert "## technical_context" in summary
        assert "## file_changes" in summary

    def test_format_note_markdown_english(self):
        """Test note formatting — agent-oriented structured format."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["note_language"] = "en"

        note = SessionNote(
            session_id="test-123",
            project_path="/test/project",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:30:00",
            duration_minutes=30,
            summary="## what_was_done\n- Added retry logic\n\n## technical_context\n- Uses litellm",
            files_modified=["/src/main.py"],
            tool_uses={"read": 5, "edit": 3},
            key_topics=["Python"],
        )

        markdown = memory._format_note_markdown(note, "en")
        assert "# agent-memory" in markdown
        assert "## meta" in markdown
        assert "test-123" in markdown
        assert "30min" in markdown
        assert "Python" in markdown
        assert "## what_was_done" in markdown

    def test_format_note_markdown_chinese(self):
        """Test note formatting — same agent format regardless of language."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["note_language"] = "zh"

        note = SessionNote(
            session_id="test-123",
            project_path="/test/project",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:30:00",
            duration_minutes=30,
            summary="## what_was_done\n- 添加了重试逻辑\n\n## technical_context\n- 使用 litellm",
            files_modified=["/src/main.py"],
            tool_uses={"read": 5, "edit": 3},
            key_topics=["Python"],
        )

        markdown = memory._format_note_markdown(note, "zh")
        assert "# agent-memory" in markdown
        assert "test-123" in markdown
        assert "30min" in markdown


class TestSessionMemoryAsync:
    """Async tests for SessionMemory."""

    @pytest.mark.asyncio
    async def test_save_note_disabled(self, sample_messages):
        """Test that save_note returns None when disabled."""
        memory = SessionMemory("/test/project", "test-session")
        assert memory.is_enabled is False

        result = await memory.save_note(sample_messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_save_note_too_short(self, sample_messages, temp_memory_dir):
        """Test that short sessions are skipped."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory._config["min_duration_minutes"] = 60  # Require 60 minutes

        with patch.object(memory, "memory_dir", temp_memory_dir):
            result = await memory.save_note(sample_messages)
            # Should be skipped due to short duration
            assert result is None

    @pytest.mark.asyncio
    async def test_save_note_too_few_prompts(self, temp_memory_dir):
        """Test that sessions with too few prompts are skipped."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory._config["min_user_prompts"] = 10  # Require 10 prompts

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        with patch.object(memory, "memory_dir", temp_memory_dir):
            result = await memory.save_note(messages)
            # Should be skipped due to too few prompts
            assert result is None

    @pytest.mark.asyncio
    async def test_generate_summary_no_model(self, sample_messages):
        """Test summary generation falls back to structured format without model config."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        summary = await memory.generate_summary(parsed)
        assert "## what_was_done" in summary
        assert "## technical_context" in summary


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_load_recent_notes_empty(self, tmp_path: Path):
        """Test loading notes when no notes exist."""
        notes = load_recent_notes("/nonexistent/project")
        assert notes == []


class TestIndexManagement:
    """Tests for index management."""

    def test_update_index_creates_file(self, temp_memory_dir):
        """Test that update_index creates index file."""
        memory = SessionMemory("/test/project", "test-session")
        memory.memory_dir = temp_memory_dir
        memory.index_path = temp_memory_dir / "index.json"
        memory.notes_dir = temp_memory_dir / "notes"

        note = SessionNote(
            session_id="test-123",
            project_path="/test/project",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:30:00",
            duration_minutes=30,
            summary="Test summary",
            key_topics=["Python"],
        )
        note_path = temp_memory_dir / "notes" / "2024-01-01" / "10-00-00_test.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Test Note")

        memory._update_index(note, note_path)

        assert memory.index_path.exists()
        index = json.loads(memory.index_path.read_text())
        assert len(index) == 1
        assert index[0]["session_id"] == "test-123"

    def test_load_recent_notes_filters_by_project(self, temp_memory_dir):
        """Test that load_recent_notes filters by project path."""
        memory = SessionMemory("/test/project", "test-session")
        memory.memory_dir = temp_memory_dir
        memory.index_path = temp_memory_dir / "index.json"

        # Create index with notes from different projects
        index = [
            {"path": "/note1.md", "project": "/test/project", "date": "2024-01-01"},
            {"path": "/note2.md", "project": "/other/project", "date": "2024-01-01"},
            {"path": "/note3.md", "project": "/test/project", "date": "2024-01-02"},
        ]
        memory.index_path.write_text(json.dumps(index))

        notes = memory.load_recent_notes()
        assert len(notes) == 2
        assert all(n["project"] == "/test/project" for n in notes)


class TestBuildSummaryPrompt:
    """Tests for summary prompt building."""

    def test_build_summary_prompt_agent_oriented(self, sample_messages):
        """Test that prompt generates agent-oriented technical memo instructions."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        prompt = memory._build_summary_prompt(parsed, "en")
        # Should contain structured section names for the agent memo
        assert "## what_was_done" in prompt
        assert "## technical_context" in prompt
        assert "## problems_encountered" in prompt
        assert "## unfinished_work" in prompt
        assert "## file_changes" in prompt
        # Should contain raw session data
        assert "Raw Session Data" in prompt
        assert "/test/project" in prompt

    def test_build_summary_prompt_same_for_all_languages(self, sample_messages):
        """Test that prompt is the same regardless of language — agent doesn't need i18n."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        prompt_en = memory._build_summary_prompt(parsed, "en")
        prompt_zh = memory._build_summary_prompt(parsed, "zh")
        assert prompt_en == prompt_zh


class TestInteractionLog:
    """Tests for the InteractionLog (near-lossless per-turn record)."""

    def test_record_tool_call(self, tmp_path: Path):
        """Test that tool calls are buffered correctly."""
        log = InteractionLog("/test/project", "test-session")
        log.interactions_dir = tmp_path / "interactions"

        log.record_tool_call(
            tool_name="read_file",
            tool_input={"filePath": "/src/main.py", "offset": 10, "limit": 50},
            tool_output="def hello(): ...",
        )

        assert len(log._current_tool_calls) == 1
        tc = log._current_tool_calls[0]
        assert tc["tool"] == "read_file"
        assert tc["file"] == "/src/main.py"
        assert "offset=10" in tc["input"]
        assert tc["status"] == "completed"

    def test_record_turn_writes_jsonl(self, tmp_path: Path):
        """Test that record_turn writes a JSONL line."""
        log = InteractionLog("/test/project", "test-sess")
        log.interactions_dir = tmp_path / "interactions"

        log.record_tool_call(
            tool_name="read_file",
            tool_input={"filePath": "/src/main.py"},
            tool_output="def hello(): pass",
        )
        log.record_tool_call(
            tool_name="search_content",
            tool_input={"pattern": "def hello"},
            tool_output="Found 3 matches",
        )

        entry = log.record_turn(
            user_query="Show me the hello function",
            assistant_response="Here's the hello function defined in main.py...",
        )

        assert entry.turn == 1
        assert len(entry.tool_calls) == 2
        assert entry.user_query == "Show me the hello function"
        assert "hello function" in entry.assistant_summary

        # Verify JSONL file exists and has content
        log_path = log._log_path()
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["turn"] == 1
        assert data["q"] == "Show me the hello function"
        assert len(data["tools"]) == 2

    def test_record_multiple_turns(self, tmp_path: Path):
        """Test that multiple turns append to the same JSONL file."""
        log = InteractionLog("/test/project", "test-sess")
        log.interactions_dir = tmp_path / "interactions"

        # Turn 1
        log.record_tool_call("read_file", {"filePath": "/a.py"}, "content a")
        log.record_turn("Read file a", "Here's file a")

        # Turn 2
        log.record_tool_call("write_file", {"filePath": "/b.py"}, "ok")
        log.record_turn("Write file b", "Done writing")

        # Turn 3 — no tool calls
        log.record_turn("What did I do?", "You read a.py and wrote b.py")

        entries = log.load_log()
        assert len(entries) == 3
        assert entries[0]["turn"] == 1
        assert entries[1]["turn"] == 2
        assert entries[2]["turn"] == 3
        assert len(entries[2]["tools"]) == 0

    def test_format_for_context(self, tmp_path: Path):
        """Test that format_for_context produces structured output."""
        log = InteractionLog("/test/project", "test-sess")
        log.interactions_dir = tmp_path / "interactions"

        log.record_tool_call("read_file", {"filePath": "/src/main.py"}, "def foo(): pass")
        log.record_turn("Show me foo", "Here's foo defined in main.py")

        ctx = log.format_for_context()
        assert "<interaction_log>" in ctx
        assert "</interaction_log>" in ctx
        assert "<turn n=" in ctx
        assert "read_file" in ctx
        assert "Show me foo" in ctx

    def test_summarize_input_search(self):
        """Test input summarization for search tools."""
        result = InteractionLog._summarize_input("search_content", {"pattern": "def foo"})
        assert "pattern=" in result
        assert "def foo" in result

    def test_summarize_input_file(self):
        """Test input summarization for file tools."""
        result = InteractionLog._summarize_input("read_file", {"filePath": "/a.py", "offset": 5, "limit": 10})
        assert "offset=5" in result
        assert "limit=10" in result

    def test_summarize_input_command(self):
        """Test input summarization for command tools."""
        result = InteractionLog._summarize_input("bash", {"command": "ls -la"})
        assert "cmd=" in result
        assert "ls -la" in result

    def test_summarize_input_edit(self):
        """Test input summarization for edit tools."""
        result = InteractionLog._summarize_input("replace_in_file", {"old_str": "hello world"})
        assert "replacing:" in result

    def test_load_log_empty(self, tmp_path: Path):
        """Test loading from nonexistent log file."""
        log = InteractionLog("/test/project", "test-sess")
        log.interactions_dir = tmp_path / "interactions"
        entries = log.load_log()
        assert entries == []


class TestRollingUpdate:
    """Tests for session summary rolling update (every N turns)."""

    def test_tick_turn(self):
        """Test turn counter incrementing."""
        memory = SessionMemory("/test/project", "test-session")
        assert memory._turn_count == 0
        assert memory.tick_turn() == 1
        assert memory.tick_turn() == 2
        assert memory._turn_count == 2

    @pytest.mark.asyncio
    async def test_update_summary_skips_when_not_due(self, sample_messages):
        """Test that update_summary_if_due skips when turn is not a multiple of interval."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory._turn_count = 3  # Not a multiple of 5

        result = await memory.update_summary_if_due(sample_messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_update_summary_runs_when_due(self, sample_messages, temp_memory_dir):
        """Test that update_summary_if_due runs at interval multiples."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory._turn_count = 5  # Multiple of 5
        memory.memory_dir = temp_memory_dir
        memory.notes_dir = temp_memory_dir / "notes"
        memory.index_path = temp_memory_dir / "index.json"

        result = await memory.update_summary_if_due(sample_messages)
        assert result is not None
        assert result.exists()
        # Should have set _last_summary_path
        assert memory._last_summary_path == result

    @pytest.mark.asyncio
    async def test_update_summary_overwrites_same_file(self, sample_messages, temp_memory_dir):
        """Test that rolling update overwrites the same file."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory.memory_dir = temp_memory_dir
        memory.notes_dir = temp_memory_dir / "notes"
        memory.index_path = temp_memory_dir / "index.json"

        # First update at turn 5
        memory._turn_count = 5
        path1 = await memory.update_summary_if_due(sample_messages)
        assert path1 is not None

        # Second update at turn 10 — should overwrite same file
        memory._turn_count = 10
        path2 = await memory.update_summary_if_due(sample_messages)
        assert path2 is not None
        assert path1 == path2  # Same file path

    @pytest.mark.asyncio
    async def test_update_summary_force(self, sample_messages, temp_memory_dir):
        """Test that force=True triggers update regardless of turn count."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = True
        memory._turn_count = 3  # Not a multiple of 5
        memory.memory_dir = temp_memory_dir
        memory.notes_dir = temp_memory_dir / "notes"
        memory.index_path = temp_memory_dir / "index.json"

        result = await memory.update_summary_if_due(sample_messages, force=True)
        assert result is not None
        assert result.exists()

    @pytest.mark.asyncio
    async def test_update_summary_disabled(self, sample_messages):
        """Test that rolling update does nothing when disabled."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["enabled"] = False
        memory._turn_count = 5

        result = await memory.update_summary_if_due(sample_messages)
        assert result is None


class TestFormatFullContext:
    """Tests for format_full_context combining both memory types."""

    def test_format_full_context_empty(self):
        """Test with no notes and no interaction log."""
        memory = SessionMemory("/test/project", "test-session")
        result = memory.format_full_context([], None)
        assert result == ""

    def test_format_full_context_with_interaction_log(self, tmp_path: Path):
        """Test that interaction log is included in full context."""
        memory = SessionMemory("/test/project", "test-session")
        log = InteractionLog("/test/project", "test-sess")
        log.interactions_dir = tmp_path / "interactions"

        log.record_tool_call("read_file", {"filePath": "/a.py"}, "content")
        log.record_turn("Read a.py", "Here's the content")

        result = memory.format_full_context([], log)
        assert "<interaction_log>" in result
        assert "read_file" in result
