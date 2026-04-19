"""Tests for session and message models."""
import pytest
from mycode.session.message import (
    create_user_message, create_assistant_message, create_text_part, TextPart, ToolPart,
)
from mycode.session.system import build
from mycode.session.processor import build_tool_results_messages

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
    # Without model, should use fallback
    assert any("AI coding assistant" in p or "Working directory" in p for p in parts)

def test_system_prompt_with_agent():
    parts = build(agent_prompt="You are a code reviewer.")
    assert any("code reviewer" in p for p in parts)

def test_system_prompt_with_model():
    from mycode.provider.schema import Model, ModelApi
    model = Model(id="claude-sonnet-4", providerID="anthropic", api=ModelApi(id="claude-sonnet-4-20250514"), name="Sonnet")
    parts = build(model=model, agent_prompt="You are helpful.")
    # Should contain the Anthropic prompt (OpenCode)
    assert any("OpenCode" in p or "coding" in p.lower() for p in parts)
    # Should contain environment info
    assert any("Working directory" in p for p in parts)

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
