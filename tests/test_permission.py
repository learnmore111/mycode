"""Tests for the permission system."""
from mycode.permission.evaluate import evaluate
from mycode.permission.permission import from_config, merge
from mycode.permission.schema import Rule


def test_evaluate_default_ask():
    result = evaluate("bash", "ls")
    assert result.action == "ask"

def test_evaluate_allow():
    rules = [Rule(permission="*", pattern="*", action="allow")]
    result = evaluate("bash", "ls", rules)
    assert result.action == "allow"

def test_evaluate_deny():
    rules = [Rule(permission="*", pattern="*", action="allow"), Rule(permission="edit", pattern="*", action="deny")]
    result = evaluate("edit", "file.py", rules)
    assert result.action == "deny"

def test_evaluate_deny_beats_equally_specific_later_allow():
    """Deny wins ties so runtime approvals cannot override exact denies."""
    rules = [
        Rule(permission="bash", pattern="*", action="deny"),
        Rule(permission="bash", pattern="*", action="allow"),
    ]
    result = evaluate("bash", "ls", rules)
    assert result.action == "deny"


def test_evaluate_specific_allow_overrides_broad_deny():
    """Supports deny-by-default agent rules such as explore's tool policy."""
    rules = [
        Rule(permission="*", pattern="*", action="deny"),
        Rule(permission="read", pattern="*", action="allow"),
    ]
    result = evaluate("read", "file.py", rules)
    assert result.action == "allow"


def test_evaluate_last_allow_wins_among_non_deny():
    """Among non-deny matches, the last rule still wins."""
    rules = [
        Rule(permission="bash", pattern="*", action="ask"),
        Rule(permission="bash", pattern="*", action="allow"),
    ]
    result = evaluate("bash", "ls", rules)
    assert result.action == "allow"

def test_evaluate_pattern_match():
    rules = [
        Rule(permission="read", pattern="*", action="allow"),
        Rule(permission="read", pattern="*.env", action="ask"),
    ]
    assert evaluate("read", "config.ts", rules).action == "allow"
    assert evaluate("read", ".env", rules).action == "ask"

def test_from_config():
    ruleset = from_config({"bash": "allow", "edit": {"*.py": "deny", "*": "allow"}})
    assert len(ruleset) == 3
    assert ruleset[0].permission == "bash"
    assert ruleset[0].action == "allow"

def test_evaluate_deny_beats_always_reply():
    """An 'always-allow' reply at runtime must not override a project deny.

    The runtime ruleset (simulated here as the second argument) is
    evaluated alongside the base ruleset by PermissionManager; whichever
    order they are merged in, a deny anywhere in the chain wins.
    """
    base = [Rule(permission="edit", pattern="*.env", action="deny")]
    approved = [Rule(permission="edit", pattern="*", action="allow")]
    result = evaluate("edit", ".env", base, approved)
    assert result.action == "deny"


def test_evaluate_specific_pattern_deny_beats_broad_allow():
    rules = [
        Rule(permission="read", pattern="*", action="allow"),
        Rule(permission="read", pattern="*.env", action="deny"),
    ]
    assert evaluate("read", ".env", rules).action == "deny"


def test_merge():
    r1 = [Rule(permission="*", pattern="*", action="allow")]
    r2 = [Rule(permission="edit", pattern="*", action="deny")]
    merged = merge(r1, r2)
    assert len(merged) == 2
