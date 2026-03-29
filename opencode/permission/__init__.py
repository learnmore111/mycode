"""Permission system — allow/deny/ask rules for tool execution."""
from opencode.permission.evaluate import evaluate
from opencode.permission.permission import PermissionManager, from_config, merge
from opencode.permission.schema import DeniedError, RejectedError, Rule, Ruleset

__all__ = [
    "evaluate", "PermissionManager", "from_config", "merge",
    "Rule", "Ruleset", "DeniedError", "RejectedError",
]
