"""Tests for session compaction."""
from opencode.session.compaction import (
    estimate_tokens, estimate_messages_tokens, should_compact,
    is_overflow, prune_tool_outputs,
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
