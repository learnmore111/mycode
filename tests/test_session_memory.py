"""Tests for session memory module — unified JSONL single-file architecture."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mycode.session.memory.memory import (
    InteractionEntry,
    SessionMemory,
    SessionSummary,
    create_session_memory,
    load_recent_notes,
)


@pytest.fixture
def temp_memory_dir(tmp_path: Path):
    """Create a temporary memory directory."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return memory_dir


@pytest.fixture
def memory(tmp_path: Path):
    """Create a SessionMemory instance with temp directory."""
    m = SessionMemory("/test/project", "test-session-123")
    m.memory_dir = tmp_path / "memory"
    return m


class TestSessionMemoryInit:
    """Tests for SessionMemory initialization."""

    def test_init_basic(self):
        """Test SessionMemory initialization with explicit args."""
        m = SessionMemory("/test/project", "test-session-123")
        assert m.project_path == "/test/project"
        assert m.session_id == "test-session-123"

    def test_init_generates_session_id(self):
        """Test SessionMemory generates session ID if not provided."""
        m = SessionMemory("/test/project")
        assert m.session_id is not None
        assert len(m.session_id) > 0

    def test_is_enabled_default_false(self):
        """Test session memory is disabled when config says so."""
        m = SessionMemory("/test/project")
        m._config["enabled"] = False
        assert m.is_enabled is False


class TestToolCallBuffering:
    """Tests for tool call recording."""

    def test_record_tool_call(self, memory):
        """Test that tool calls are buffered correctly."""
        memory.record_tool_call(
            tool_name="read_file",
            tool_input={"filePath": "/src/main.py", "offset": 10, "limit": 50},
            tool_output="def hello(): ...",
        )
        assert len(memory._current_tool_calls) == 1
        tc = memory._current_tool_calls[0]
        assert tc["tool"] == "read_file"
        assert tc["file"] == "/src/main.py"
        assert "offset=10" in tc["input"]
        assert tc["status"] == "completed"

    def test_record_multiple_tool_calls(self, memory):
        """Test buffering multiple tool calls in a single turn."""
        memory.record_tool_call("read_file", {"filePath": "/a.py"}, "content a")
        memory.record_tool_call("search_content", {"pattern": "def foo"}, "Found 3 matches")
        assert len(memory._current_tool_calls) == 2
        assert memory._current_tool_calls[0]["tool"] == "read_file"
        assert memory._current_tool_calls[1]["tool"] == "search_content"


class TestTurnRecording:
    """Tests for per-turn recording to JSONL."""

    @pytest.mark.asyncio
    async def test_record_turn_writes_jsonl(self, memory):
        """Test that record_turn writes a JSONL line to file."""
        memory.record_tool_call("read_file", {"filePath": "/src/main.py"}, "def hello(): pass")
        memory.record_tool_call("search_content", {"pattern": "def hello"}, "Found 3 matches")

        entry = await memory.record_turn(
            user_query="Show me the hello function",
            assistant_response="Here's the hello function defined in main.py...",
        )

        assert entry.turn == 1
        assert len(entry.tool_calls) == 2
        assert entry.user_query == "Show me the hello function"
        assert "hello function" in entry.assistant_summary

        # Verify JSONL file
        path = memory._get_log_path()
        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "turn"
        assert data["turn"] == 1
        assert data["q"] == "Show me the hello function"
        assert len(data["tools"]) == 2

    @pytest.mark.asyncio
    async def test_record_multiple_turns(self, memory):
        """Test that multiple turns append to the same JSONL file."""
        # Turn 1
        memory.record_tool_call("read_file", {"filePath": "/a.py"}, "content a")
        await memory.record_turn("Read file a", "Here's file a")

        # Turn 2
        memory.record_tool_call("write_file", {"filePath": "/b.py"}, "ok")
        await memory.record_turn("Write file b", "Done writing")

        # Turn 3 — no tool calls
        await memory.record_turn("What did I do?", "You read a.py and wrote b.py")

        turns = memory._load_all_turns()
        assert len(turns) == 3
        assert turns[0]["turn"] == 1
        assert turns[1]["turn"] == 2
        assert turns[2]["turn"] == 3
        assert len(turns[2]["tools"]) == 0

    @pytest.mark.asyncio
    async def test_tool_calls_cleared_after_turn(self, memory):
        """Test that tool call buffer is cleared after recording a turn."""
        memory.record_tool_call("read_file", {"filePath": "/a.py"}, "content")
        await memory.record_turn("Read a", "Here's a")
        assert len(memory._current_tool_calls) == 0


class TestInputSummarization:
    """Tests for _summarize_input static method."""

    def test_summarize_search_pattern(self):
        result = SessionMemory._summarize_input("search_content", {"pattern": "def foo"})
        assert "pattern=" in result
        assert "def foo" in result

    def test_summarize_file_path_with_offset(self):
        result = SessionMemory._summarize_input("read_file", {"filePath": "/a.py", "offset": 5, "limit": 10})
        assert "offset=5" in result
        assert "limit=10" in result

    def test_summarize_command(self):
        result = SessionMemory._summarize_input("bash", {"command": "ls -la"})
        assert "cmd=" in result
        assert "ls -la" in result

    def test_summarize_edit(self):
        result = SessionMemory._summarize_input("replace_in_file", {"old_str": "hello world"})
        assert "replacing:" in result

    def test_summarize_empty_input(self):
        result = SessionMemory._summarize_input("unknown", {})
        assert result == ""


class TestFileIO:
    """Tests for JSONL file I/O."""

    @pytest.mark.asyncio
    async def test_append_and_load_records(self, memory):
        """Test appending and loading records."""
        await memory._append_record({"type": "turn", "turn": 1, "q": "hello"})
        await memory._append_record({"type": "turn", "turn": 2, "q": "world"})
        records = memory._load_all_records()
        assert len(records) == 2
        assert records[0]["q"] == "hello"
        assert records[1]["q"] == "world"

    @pytest.mark.asyncio
    async def test_load_all_turns_filters(self, memory):
        """Test that _load_all_turns only returns turn records."""
        await memory._append_record({"type": "summary", "text": "summary"})
        await memory._append_record({"type": "turn", "turn": 1, "q": "hello"})
        await memory._append_record({"type": "turn", "turn": 2, "q": "world"})
        turns = memory._load_all_turns()
        assert len(turns) == 2

    @pytest.mark.asyncio
    async def test_load_latest_summary(self, memory):
        """Test that latest summary is found."""
        await memory._append_record({"type": "summary", "text": "old summary"})
        await memory._append_record({"type": "turn", "turn": 1})
        await memory._append_record({"type": "summary", "text": "new summary"})
        s = memory._load_latest_summary()
        assert s is not None
        assert s["text"] == "new summary"

    def test_load_latest_summary_empty(self, memory):
        """Test loading summary when no records exist."""
        s = memory._load_latest_summary()
        assert s is None

    @pytest.mark.asyncio
    async def test_rewrite_file_updates_turns(self, memory):
        """Test that _rewrite_file updates turn summaries and replaces summary."""
        await memory._append_record({"type": "turn", "turn": 1, "a": "old summary 1"})
        await memory._append_record({"type": "turn", "turn": 2, "a": "old summary 2"})
        await memory._append_record({"type": "summary", "text": "old session summary"})

        memory._summary = SessionSummary(
            session_id="test", project_path="/test", start_time="", end_time="",
            duration_minutes=1, summary_text="new session summary",
        )
        await memory._rewrite_file({1: "refined summary 1"})

        records = memory._load_all_records()
        turns = [r for r in records if r["type"] == "turn"]
        summaries = [r for r in records if r["type"] == "summary"]
        assert len(summaries) == 1
        assert summaries[0]["text"] == "new session summary"
        assert turns[0]["a"] == "refined summary 1"
        assert turns[1]["a"] == "old summary 2"  # unchanged


class TestLLMTrigger:
    """Tests for LLM update triggering every SUMMARY_INTERVAL turns."""

    @pytest.mark.asyncio
    async def test_llm_triggers_at_interval(self, memory):
        """Test that LLM update triggers at SUMMARY_INTERVAL multiples."""
        memory._config["enabled"] = True
        # Mock _llm_update to track calls
        call_count = 0
        original_llm_update = memory._llm_update

        async def mock_llm_update(**kwargs):
            nonlocal call_count
            call_count += 1

        memory._llm_update = mock_llm_update

        # Record 3 turns (SUMMARY_INTERVAL=3)
        await memory.record_turn("q1", "a1")
        await memory.record_turn("q2", "a2")
        await memory.record_turn("q3", "a3")  # Should trigger LLM

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_llm_does_not_trigger_off_interval(self, memory):
        """Test that LLM update does NOT trigger at non-interval turns."""
        memory._config["enabled"] = True
        call_count = 0

        async def mock_llm_update(**kwargs):
            nonlocal call_count
            call_count += 1

        memory._llm_update = mock_llm_update

        await memory.record_turn("q1", "a1")
        await memory.record_turn("q2", "a2")
        # Only 2 turns, no trigger
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_llm_does_not_trigger_when_disabled(self, memory):
        """Test that LLM update is skipped when memory is disabled."""
        memory._config["enabled"] = False
        call_count = 0

        async def mock_llm_update(**kwargs):
            nonlocal call_count
            call_count += 1

        memory._llm_update = mock_llm_update

        await memory.record_turn("q1", "a1")
        await memory.record_turn("q2", "a2")
        await memory.record_turn("q3", "a3")
        assert call_count == 0


class TestFinalize:
    """Tests for session finalization."""

    @pytest.mark.asyncio
    async def test_finalize_when_disabled(self, memory):
        """Test that finalize returns None when disabled."""
        memory._config["enabled"] = False
        result = await memory.finalize()
        assert result is None

    @pytest.mark.asyncio
    async def test_finalize_when_no_turns(self, memory):
        """Test that finalize returns None when no turns recorded."""
        memory._config["enabled"] = True
        result = await memory.finalize()
        assert result is None

    @pytest.mark.asyncio
    async def test_finalize_calls_llm_update(self, memory):
        """Test that finalize triggers LLM update."""
        memory._config["enabled"] = True
        memory._turn_counter = 2  # Simulate 2 turns recorded

        called = False

        async def mock_llm_update(**kwargs):
            nonlocal called
            called = True

        memory._llm_update = mock_llm_update

        result = await memory.finalize()
        assert called


class TestContextFormatting:
    """Tests for format_for_context and format_history_context."""

    def test_format_for_context_empty(self, memory):
        """Test formatting when no records exist."""
        result = memory.format_for_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_for_context_with_data(self, memory):
        """Test formatting with turns and summary."""
        await memory._append_record({"type": "summary", "text": "## what_was_done\n- Added login"})
        await memory._append_record({
            "type": "turn", "turn": 1, "ts": "2024-01-01",
            "q": "Add login", "tools": [{"tool": "edit", "file": "/login.py", "input": "", "output": ""}],
            "a": "Added login functionality",
        })

        result = memory.format_for_context()
        assert "<session_memory>" in result
        assert "</session_memory>" in result
        assert "<summary>" in result
        assert "what_was_done" in result
        assert "<turns>" in result
        assert "<turn n=" in result
        assert "Add login" in result

    @pytest.mark.asyncio
    async def test_format_for_context_limits_turns(self, memory):
        """Test that format_for_context respects the limit parameter."""
        for i in range(10):
            await memory._append_record({
                "type": "turn", "turn": i + 1, "ts": "2024-01-01",
                "q": f"query {i}", "tools": [], "a": f"answer {i}",
            })

        result = memory.format_for_context(limit=3)
        # Should only contain the last 3 turns
        assert "query 7" in result
        assert "query 8" in result
        assert "query 9" in result
        assert "query 0" not in result

    def test_format_history_context(self, memory):
        """Test format_history_context with explicit sessions data."""
        sessions = [
            {"date": "2024-01-01", "duration_min": 30, "topics": ["Python"],
             "text": "## what_was_done\n- Added tests"},
        ]
        result = memory.format_history_context(recent_sessions=sessions)
        assert "<session_history>" in result
        assert "2024-01-01" in result
        assert "Added tests" in result


class TestFallbackSummary:
    """Tests for fallback (non-LLM) summary generation."""

    def test_fallback_combined_basic(self, memory):
        """Test fallback summary generation."""
        all_turns = [
            {"turn": 1, "q": "Read main.py", "tools": [{"tool": "read_file", "file": "/main.py"}], "a": "content"},
            {"turn": 2, "q": "Edit it", "tools": [{"tool": "edit", "file": "/main.py"}], "a": "done"},
        ]
        result = memory._fallback_combined(all_turns, all_turns, datetime.now())

        assert "summary" in result
        assert "## what_was_done" in result["summary"]
        assert "## technical_context" in result["summary"]
        assert "refined_turns" in result


class TestParseResponse:
    """Tests for _parse_llm_response."""

    def test_parse_response_basic(self, memory):
        """Test parsing LLM response with summary and refined turns."""
        raw = """## what_was_done
- Added login feature

## technical_context
- Python Flask app

TURN_1: Added login endpoint using Flask
TURN_2: Created login template"""

        result = memory._parse_llm_response(raw, [])
        assert "what_was_done" in result["summary"]
        assert "Added login feature" in result["summary"]
        assert 1 in result["refined_turns"]
        assert 2 in result["refined_turns"]
        assert "login endpoint" in result["refined_turns"][1]

    def test_parse_response_no_refinements(self, memory):
        """Test parsing response with no TURN_ lines."""
        raw = """## what_was_done
- Explored codebase"""

        result = memory._parse_llm_response(raw, [])
        assert "what_was_done" in result["summary"]
        assert len(result["refined_turns"]) == 0


class TestHelpers:
    """Tests for static helper methods."""

    def test_extract_files_write(self):
        turns = [
            {"tools": [{"tool": "edit", "file": "/a.py"}, {"tool": "read_file", "file": "/b.py"}]},
            {"tools": [{"tool": "write_to_file", "file": "/c.py"}]},
        ]
        files = SessionMemory._extract_files(turns, "write")
        assert "/a.py" in files
        assert "/c.py" in files
        assert "/b.py" not in files

    def test_extract_files_read(self):
        turns = [
            {"tools": [{"tool": "read_file", "file": "/b.py"}, {"tool": "edit", "file": "/a.py"}]},
        ]
        files = SessionMemory._extract_files(turns, "read")
        assert "/b.py" in files
        assert "/a.py" not in files

    def test_count_tools(self):
        turns = [
            {"tools": [{"tool": "read_file"}, {"tool": "read_file"}, {"tool": "edit"}]},
            {"tools": [{"tool": "bash"}]},
        ]
        counts = SessionMemory._count_tools(turns)
        assert counts["read_file"] == 2
        assert counts["edit"] == 1
        assert counts["bash"] == 1

    def test_infer_topics_python(self):
        topics = SessionMemory._infer_topics({"/src/main.py", "/tests/test_main.py"})
        assert "Python" in topics

    def test_infer_topics_javascript(self):
        topics = SessionMemory._infer_topics({"/src/app.js", "/src/component.tsx"})
        assert "JavaScript" in topics or "React/TypeScript" in topics

    def test_infer_topics_documentation(self):
        topics = SessionMemory._infer_topics({"/README.md", "/docs/guide.md"})
        assert "Documentation" in topics


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_create_session_memory(self):
        m = create_session_memory("/test/project", "sess-1")
        assert isinstance(m, SessionMemory)
        assert m.project_path == "/test/project"
        assert m.session_id == "sess-1"

    def test_load_recent_notes_empty(self):
        notes = load_recent_notes("/nonexistent/project")
        assert notes == []


class TestBuildCombinedPrompt:
    """Tests for _build_combined_prompt."""

    def test_prompt_contains_required_sections(self, memory):
        """Test that the combined prompt includes all required sections."""
        all_turns = [
            {"turn": 1, "q": "Help me", "tools": [{"tool": "read_file", "file": "/a.py", "input": "", "output": ""}], "a": "Sure"},
        ]
        recent_turns = all_turns
        prompt_text = memory._build_combined_prompt(all_turns, recent_turns, datetime.now())

        assert "## what_was_done" in prompt_text
        assert "## technical_context" in prompt_text
        assert "## problems_encountered" in prompt_text
        assert "## unfinished_work" in prompt_text
        assert "## key_files" in prompt_text
        assert "TASK 1" in prompt_text
        assert "TASK 2" in prompt_text
        assert "TURN_" in prompt_text

    def test_prompt_includes_existing_summary(self, memory):
        """Test that existing summary is included in the prompt."""
        memory._summary = SessionSummary(
            session_id="test", project_path="/test", start_time="", end_time="",
            duration_minutes=1, summary_text="Previous session work",
        )
        prompt_text = memory._build_combined_prompt([], [], datetime.now())
        assert "Previous session work" in prompt_text
