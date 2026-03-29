"""Tests for session compaction."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from opencode.session.compaction import (
    COMPACT_KEEP_TURNS,
    estimate_tokens, estimate_messages_tokens, should_compact,
    is_overflow, prune_tool_outputs, _split_by_turns, compact,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_estimate_messages_tokens():
    msgs = [
        {"role": "user", "content": "a" * 400},
        {"role": "assistant", "content": "b" * 800},
    ]
    est = estimate_messages_tokens(msgs)
    assert est == 300  # 100 + 200


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


# ── compact() integration tests ──


def _make_mock_response(summary_text):
    choice = MagicMock()
    choice.message.content = summary_text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


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

    mock_resp = _make_mock_response("Summary of old turns")

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
        result = asyncio.run(
            compact(msgs, model_name="test-model")
        )

    # First msg is summary system message
    assert result[0]["role"] == "system"
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
    result = asyncio.run(
        compact(list(msgs), model_name="test-model")
    )
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

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("fail")):
        result = asyncio.run(
            compact(msgs, model_name="test-model")
        )

    assert len(result) == original_len
