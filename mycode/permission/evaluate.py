"""Permission rule evaluation.

Evaluates permission rules using wildcard matching.
Last matching rule wins (bottom of ruleset has highest priority).
"""

from __future__ import annotations

from mycode.permission.schema import Rule, Ruleset
from mycode.util.wildcard import match


def evaluate(permission: str, pattern: str, *rulesets: Ruleset) -> Rule:
    """Evaluate permission rules. Returns the most specific matching rule.

    Rules are evaluated in order; the LAST matching rule wins.
    Default is "ask" if no rule matches.

    Args:
        permission: The permission being checked (e.g., "bash", "edit", "read")
        pattern: The pattern being checked (e.g., file path, command)
        rulesets: One or more rulesets to evaluate, merged in order
    """
    merged: Ruleset = []
    for rs in rulesets:
        merged.extend(rs)

    # Default: ask
    result = Rule(permission=permission, pattern=pattern, action="ask")

    for rule in merged:
        # Check if the rule's permission pattern matches
        if not match(permission, rule.permission):
            continue
        # Check if the rule's file/command pattern matches
        if not match(pattern, rule.pattern):
            continue
        result = Rule(permission=permission, pattern=pattern, action=rule.action)

    return result
