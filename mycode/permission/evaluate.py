"""Permission rule evaluation.

Evaluates permission rules using wildcard matching.

Resolution order:
1. The most specific matching rule wins. This lets agents express a
   deny-by-default policy such as ``* deny`` followed by ``read allow``.
2. If equally-specific matching rules disagree, ``deny`` wins. This keeps
   sensitive project rules (for example ``read *.env deny``) from being
   overridden by a broad runtime approval.
3. Among equally-specific non-deny matches, the LAST one wins — so the
   caller order (``ruleset, self._approved``) keeps its "later takes
   priority" semantics for allow/ask.
4. If nothing matches, the default is ``ask``.
"""

from __future__ import annotations

from mycode.permission.schema import Rule, Ruleset
from mycode.util.wildcard import match


def _specificity(value: str) -> tuple[int, int]:
    """Rank exact/glob patterns by how narrowly they match.

    The first element favours fewer wildcard characters; the second favours
    longer literal text. This is intentionally small but handles the policy
    shapes used throughout the app: ``*`` < ``read`` and ``*`` < ``*.env``.
    """
    wildcard_count = value.count("*") + value.count("?")
    literal_len = len(value.replace("*", "").replace("?", ""))
    return (-wildcard_count, literal_len)


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

    best: tuple[tuple[int, int], tuple[int, int], int, Rule] | None = None

    for idx, rule in enumerate(merged):
        if not match(permission, rule.permission):
            continue
        if not match(pattern, rule.pattern):
            continue
        candidate = (_specificity(rule.permission), _specificity(rule.pattern), idx, rule)
        if best is None:
            best = candidate
            continue

        best_perm, best_pattern, best_idx, best_rule = best
        cand_perm, cand_pattern, cand_idx, cand_rule = candidate
        candidate_rank = (cand_perm, cand_pattern)
        best_rank = (best_perm, best_pattern)
        tied_deny_wins = cand_rule.action == "deny" and best_rule.action != "deny"
        tied_non_deny_last_wins = cand_rule.action != "deny" and best_rule.action != "deny" and cand_idx > best_idx
        should_replace = candidate_rank > best_rank or (
            candidate_rank == best_rank and (tied_deny_wins or tied_non_deny_last_wins)
        )
        if should_replace:
            best = candidate

    if best is not None:
        return Rule(permission=permission, pattern=pattern, action=best[3].action)
    return Rule(permission=permission, pattern=pattern, action="ask")
