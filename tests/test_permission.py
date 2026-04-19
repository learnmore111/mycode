"""Tests for the permission system."""
from mycode.permission.evaluate import evaluate
from mycode.permission.schema import Rule
from mycode.permission.permission import from_config, merge

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

def test_evaluate_last_wins():
    rules = [
        Rule(permission="bash", pattern="*", action="deny"),
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

def test_merge():
    r1 = [Rule(permission="*", pattern="*", action="allow")]
    r2 = [Rule(permission="edit", pattern="*", action="deny")]
    merged = merge(r1, r2)
    assert len(merged) == 2
