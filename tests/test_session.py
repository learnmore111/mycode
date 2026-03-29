"""Tests for session and message models."""
import pytest
from opencode.session.message import (
    create_user_message, create_assistant_message, create_text_part, TextPart, ToolPart,
)
from opencode.session.system import build
from opencode.session.processor import build_tool_results_messages

def test_create_user_message():
    msg = create_user_message("sess1")
    assert msg.session_id == "sess1"
    assert msg.role == "user"
    assert len(msg.id) == 26

def test_create_assistant_message():
    msg = create_assistant_message("sess1", "parent1", "anthropic", "claude-3", "build")
    assert msg.role == "assistant"
    assert msg.provider_id == "anthropic"
    assert msg.agent == "build"

def test_system_prompt():
    parts = build()
    assert len(parts) >= 1
    assert "AI coding assistant" in parts[0]

def test_system_prompt_with_agent():
    parts = build(agent_prompt="You are a code reviewer.")
    assert any("code reviewer" in p for p in parts)

def test_build_tool_results_messages():
    tp = ToolPart(id="p1", session_id="s1", message_id="m1", tool="bash",
                  tool_call_id="tc1", state={"input": {"command": "ls"}, "output": "file.py"})
    text = TextPart(id="p2", session_id="s1", message_id="m1", content="Let me check")
    msgs = build_tool_results_messages([text, tp])
    assert len(msgs) == 2
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["tool_calls"][0]["function"]["name"] == "bash"
    assert msgs[1]["role"] == "tool"
    assert msgs[1]["content"] == "file.py"
