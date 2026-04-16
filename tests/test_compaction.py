"""Tests for session compaction."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from opencode.session.compaction import (
    COMPACT_KEEP_TURNS,
    _build_compact_result,
    _extract_summary,
    _truncate_tool_outputs_for_summary,
    compact,
    estimate_messages_tokens,
    estimate_tokens,
    is_overflow,
    prune_tool_outputs,
    should_compact,
    _split_by_turns,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    # 400 ASCII chars = 400 bytes / 3 ≈ 133
    assert estimate_tokens("a" * 400) == 133


def test_estimate_messages_tokens():
    msgs = [
        {"role": "user", "content": "a" * 400},
        {"role": "assistant", "content": "b" * 800},
    ]
    est = estimate_messages_tokens(msgs)
    # 400 bytes / 3 = 133, 800 bytes / 3 = 266 → 399 (integer division)
    assert est == 399


def test_estimate_messages_tokens_with_tools():
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "bash", "arguments": '{"command": "ls"}'}}
        ]},
    ]
    est = estimate_messages_tokens(msgs)
    assert est > 0


def test_should_compact_under_limit():
    msgs = [{"role": "user", "content": "short message"}]
    assert should_compact(messages=msgs, model_context=100000) is False


def test_should_compact_over_limit():
    msgs = [{"role": "user", "content": "x" * 400_000}]  # ~100k tokens
    assert should_compact(messages=msgs, model_context=100_000) is True


def test_should_compact_zero_context():
    msgs = [{"role": "user", "content": "x" * 999999}]
    assert should_compact(messages=msgs, model_context=0) is False


def test_is_overflow_true():
    assert is_overflow(tokens={"input": 90000, "output": 10000}, model_context=100000) is True


def test_is_overflow_false():
    assert is_overflow(tokens={"input": 50000, "output": 10000}, model_context=100000) is False


def test_prune_tool_outputs_no_tools():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    pruned, freed = prune_tool_outputs(msgs)
    assert freed == 0
    assert pruned == msgs


def test_prune_tool_outputs_protects_recent():
    """Tool outputs in the last 2 user turns should not be pruned."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    _, freed = prune_tool_outputs(msgs)
    assert freed == 0  # All within last 2 turns, protected


def test_prune_tool_outputs_prunes_old():
    """Tool outputs beyond PRUNE_PROTECT in old turns should be pruned."""
    msgs = [
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "z" * 200_000},  # 50K tokens, exceeds PRUNE_PROTECT
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]
    pruned_msgs, freed = prune_tool_outputs(msgs)
    assert freed > 0
    assert pruned_msgs[2]["content"] == "[Old tool result content cleared]"


# ── _split_by_turns tests ──


def test_split_few_turns_keeps_all():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    old, recent = _split_by_turns(msgs, keep_turns=3)
    assert old == []
    assert recent == msgs


def test_split_exact_boundary():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    old, recent = _split_by_turns(msgs, keep_turns=3)
    assert old == []
    assert recent == msgs


def test_split_correctly():
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
        {"role": "user", "content": "q5"},
        {"role": "assistant", "content": "a5"},
    ]
    old, recent = _split_by_turns(msgs, keep_turns=3)
    assert len(old) == 4  # q1,a1,q2,a2
    assert old[0]["content"] == "q1"
    assert recent[0]["content"] == "q3"
    assert len(recent) == 6


def test_split_with_tool_messages():
    """Tool messages between assistant and next user stay with their turn."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "out1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]
    old, recent = _split_by_turns(msgs, keep_turns=3)
    # old = q1 + tool_call + tool_result + a1 (4 msgs)
    assert len(old) == 4
    assert old[0]["content"] == "q1"
    assert recent[0]["content"] == "q2"


# ── _truncate_tool_outputs_for_summary tests ──


def test_truncate_tool_outputs_does_not_modify_originals():
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "x" * 5000},
    ]
    original_content = msgs[0]["content"]
    truncated = _truncate_tool_outputs_for_summary(msgs, limit=100)
    # Original untouched
    assert msgs[0]["content"] == original_content
    # Truncated copy is shorter
    assert len(truncated[0]["content"]) < len(original_content)
    assert "[truncated" in truncated[0]["content"]


def test_truncate_preserves_short_content():
    msgs = [
        {"role": "tool", "tool_call_id": "1", "content": "short"},
        {"role": "user", "content": "hello"},
    ]
    truncated = _truncate_tool_outputs_for_summary(msgs, limit=1000)
    assert truncated[0]["content"] == "short"
    assert truncated[1]["content"] == "hello"


def test_truncate_tool_call_arguments():
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"function": {"name": "read", "arguments": "a" * 5000}}
        ]},
    ]
    truncated = _truncate_tool_outputs_for_summary(msgs, limit=100)
    assert len(truncated[0]["tool_calls"][0]["function"]["arguments"]) < 5000
    # Original untouched
    assert len(msgs[0]["tool_calls"][0]["function"]["arguments"]) == 5000


# ── _extract_summary tests ──


def test_extract_summary_with_both_tags():
    text = "<analysis>thinking...</analysis>\n<summary>The real summary.</summary>"
    assert _extract_summary(text) == "The real summary."


def test_extract_summary_only_summary_tag():
    text = "<summary>Just the summary.</summary>"
    assert _extract_summary(text) == "Just the summary."


def test_extract_summary_no_tags():
    text = "Plain text summary without tags."
    assert _extract_summary(text) == "Plain text summary without tags."


def test_extract_summary_strips_analysis_fallback():
    text = "<analysis>thinking...</analysis>\nLeftover content here."
    assert _extract_summary(text) == "Leftover content here."


# ── _build_compact_result tests ──


def test_build_compact_result_format():
    recent = [
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
    ]
    result = _build_compact_result("Summary text", recent)
    assert len(result) == 3  # 1 summary + 2 recent
    assert result[0]["role"] == "user"
    assert "Summary text" in result[0]["content"]
    assert result[1]["content"] == "q3"


def test_build_compact_result_user_not_system():
    """Summary must be a user message, not system, for cache preservation."""
    result = _build_compact_result("test", [])
    assert result[0]["role"] == "user"
    assert result[0]["role"] != "system"


# ── compact() integration tests ──


def _make_mock_model():
    """Create a minimal mock Model for compact() tests."""
    model = MagicMock()
    model.provider_id = "test"
    model.api.id = "test-model"
    model.api.npm = "@ai-sdk/openai"
    model.api.url = None
    model.limit.context = 100000
    model.limit.output = 4096
    model.capabilities.toolcall = True
    model.capabilities.reasoning = False
    model.headers = None
    model.options = None
    return model


def _make_compact_kwargs(model=None):
    """Build the kwargs dict that prompt.py passes to compact()."""
    if model is None:
        model = _make_mock_model()
    return {
        "system": ["You are a helpful assistant."],
        "tools": [{"type": "function", "function": {"name": "bash", "description": "Run bash", "parameters": {}}}],
        "model": model,
        "api_key": "test-key",
        "api_base": None,
    }


async def _fake_stream_with_summary(summary_text):
    """Create a fake async generator that yields TextDelta events."""
    from opencode.session.llm import TextDelta, FinishEvent
    yield TextDelta(text=f"<analysis>thinking</analysis>\n<summary>{summary_text}</summary>")
    yield FinishEvent(reason="stop", usage={}, cost=0.0)


def test_compact_preserves_recent_turns():
    """compact() should keep recent N turns and summarise old ones."""
    msgs = [
        {"role": "user", "content": "old q1"},
        {"role": "assistant", "content": "old a1"},
        {"role": "user", "content": "old q2"},
        {"role": "assistant", "content": "old a2"},
        {"role": "user", "content": "recent q3"},
        {"role": "assistant", "content": "recent a3"},
        {"role": "user", "content": "recent q4"},
        {"role": "assistant", "content": "recent a4"},
        {"role": "user", "content": "recent q5"},
        {"role": "assistant", "content": "recent a5"},
    ]

    kwargs = _make_compact_kwargs()

    with (
        patch("opencode.session.compaction.llmmod.stream", return_value=_fake_stream_with_summary("Summary of old turns")),
        patch("opencode.session.compaction._load_compaction_prompt", new_callable=AsyncMock, return_value="Summarize"),
    ):
        result, _ = asyncio.run(compact(msgs, **kwargs))

    # First msg is summary user message (not system!)
    assert result[0]["role"] == "user"
    assert "Summary of old turns" in result[0]["content"]
    # Recent 3 turns preserved (6 messages)
    assert len(result) == 7  # 1 summary + 6 recent
    assert result[1]["content"] == "recent q3"


def test_compact_no_old_turns():
    """When not enough turns to split, return as-is."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    kwargs = _make_compact_kwargs()
    result, _ = asyncio.run(compact(list(msgs), **kwargs))
    assert result == msgs


def test_compact_llm_failure_returns_original():
    """On LLM error, return pruned but unsummarised messages."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]
    original_len = len(msgs)

    kwargs = _make_compact_kwargs()

    with (
        patch("opencode.session.compaction.llmmod.stream", side_effect=Exception("fail")),
        patch("opencode.session.compaction._load_compaction_prompt", new_callable=AsyncMock, return_value="Summarize"),
    ):
        result, _ = asyncio.run(compact(msgs, **kwargs))

    assert len(result) == original_len


# ── Compaction Metrics tests ──

def test_compact_returns_metrics():
    """compact() should return (messages, metrics) tuple with token counts."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]

    kwargs = _make_compact_kwargs()

    with (
        patch("opencode.session.compaction.llmmod.stream", return_value=_fake_stream_with_summary("Summary text")),
        patch("opencode.session.compaction._load_compaction_prompt", new_callable=AsyncMock, return_value="Summarize"),
    ):
        result, metrics = asyncio.run(compact(msgs, **kwargs))

    # Check that metrics is a named tuple with expected fields
    assert metrics.old_message_count == 2  # First 2 messages (1 turn)
    assert metrics.old_message_tokens >= 0  # short test strings may estimate to 0
    assert metrics.summary_length > 0
    assert metrics.removed_turn_count == 1  # One user message removed
    # Result should be [summary_msg] + recent_turns
    assert len(result) == 7  # 1 summary + 6 recent


def test_compact_metrics_zero_when_no_compaction():
    """When not enough turns to split, metrics should reflect no removal."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    kwargs = _make_compact_kwargs()
    result, metrics = asyncio.run(compact(list(msgs), **kwargs))
    # No compaction happened, but metrics tuple is still returned
    assert metrics.old_message_count == 0
    assert metrics.removed_turn_count == 0
    assert result == msgs


def test_compact_metrics_include_old_messages():
    """CompactionMetrics should include old_messages and summary for audit trail."""
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "q4"},
        {"role": "assistant", "content": "a4"},
    ]

    kwargs = _make_compact_kwargs()

    with (
        patch("opencode.session.compaction.llmmod.stream", return_value=_fake_stream_with_summary("Test summary")),
        patch("opencode.session.compaction._load_compaction_prompt", new_callable=AsyncMock, return_value="Summarize"),
    ):
        result, metrics = asyncio.run(compact(msgs, **kwargs))

    # Verify old_messages is included
    assert len(metrics.old_messages) == 2  # First 2 messages
    assert metrics.old_messages[0]["content"] == "q1"
    
    # Verify summary is included
    assert metrics.summary == "Test summary"
    assert metrics.summary_length == len("Test summary")
