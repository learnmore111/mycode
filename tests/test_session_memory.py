"""Tests for session memory module."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from opencode.session.memory.memory import (
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
        """Test simple summary creation without AI."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)
        summary = memory._create_simple_summary(parsed)

        assert "## Summary" in summary
        assert "minutes" in summary.lower() or "min" in summary.lower()

    def test_format_note_markdown_english(self):
        """Test note formatting in English."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["note_language"] = "en"

        note = SessionNote(
            session_id="test-123",
            project_path="/test/project",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:30:00",
            duration_minutes=30,
            summary="## Summary\nThis is a test session.",
            files_modified=["/src/main.py"],
            tool_uses={"read": 5, "edit": 3},
            key_topics=["Python"],
        )

        markdown = memory._format_note_markdown(note, "en")
        assert "# Session Note" in markdown
        assert "test-123" in markdown
        assert "30 min" in markdown
        assert "Python" in markdown

    def test_format_note_markdown_chinese(self):
        """Test note formatting in Chinese."""
        memory = SessionMemory("/test/project", "test-session")
        memory._config["note_language"] = "zh"

        note = SessionNote(
            session_id="test-123",
            project_path="/test/project",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:30:00",
            duration_minutes=30,
            summary="## 摘要\n这是一个测试会话。",
            files_modified=["/src/main.py"],
            tool_uses={"read": 5, "edit": 3},
            key_topics=["Python"],
        )

        markdown = memory._format_note_markdown(note, "zh")
        assert "# 会话笔记" in markdown
        assert "test-123" in markdown
        assert "30 分钟" in markdown


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
        """Test summary generation falls back to simple summary without model config."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        summary = await memory.generate_summary(parsed)
        assert "## Summary" in summary


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

    def test_build_summary_prompt_english(self, sample_messages):
        """Test building English summary prompt."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        prompt = memory._build_summary_prompt(parsed, "en")
        assert "Session Info" in prompt
        assert "User Requests" in prompt
        assert "Tool Usage" in prompt

    def test_build_summary_prompt_chinese(self, sample_messages):
        """Test building Chinese summary prompt."""
        memory = SessionMemory("/test/project", "test-session")
        parsed = memory.parse_conversation(sample_messages)

        prompt = memory._build_summary_prompt(parsed, "zh")
        assert "会话信息" in prompt
        assert "用户请求" in prompt
        assert "工具使用" in prompt
