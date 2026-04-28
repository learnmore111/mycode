"""Permission rule evaluation.

Evaluates permission rules using wildcard matching.

Resolution order:
1. An explicit `deny` rule always wins (cannot be overridden by later `allow` /
   `always` entries). This closes a footgun where an "always allow" reply at
   runtime would otherwise override a deny declared in project config or an
   agent's ruleset.
2. Among non-deny matches, the LAST one wins — so the caller order
   (`ruleset, self._approved`) keeps its "later takes priority" semantics
   for the allow/ask case.
3. If nothing matches, the default is `ask`.
"""

from __future__ import annotations

from mycode.permission.schema import Rule, Ruleset
from mycode.util.wildcard import match


def evaluate(permission: str, pattern: str, *rulesets: Ruleset) -> Rule:
    """Evaluate permission rules. Returns the most specific matching rule.

    Args:
        permission: The permission being checked (e.g., "bash", "edit", "read")
        pattern: The pattern being checked (e.g., file path, command)
        rulesets: One or more rulesets to evaluate, merged in order
    """
    merged: Ruleset = []
    for rs in rulesets:
        merged.extend(rs)

    last_match: Rule | None = None

    for rule in merged:
        if not match(permission, rule.permission):
            continue
        if not match(pattern, rule.pattern):
            continue
        # Explicit deny short-circuits — no later allow can override it.
        if rule.action == "deny":
            return Rule(permission=permission, pattern=pattern, action="deny")
        last_match = rule

    if last_match is not None:
        return Rule(permission=permission, pattern=pattern, action=last_match.action)
    return Rule(permission=permission, pattern=pattern, action="ask")
