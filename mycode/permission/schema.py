"""Permission system data types.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Rule:
    """A single permission rule."""
    permission: str
    pattern: str
    action: Literal["allow", "deny", "ask"]


Ruleset = list[Rule]


@dataclass
class PermissionRequest:
    """A pending permission request."""
    id: str
    session_id: str
    permission: str
    patterns: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    always: list[str] = field(default_factory=list)
    tool: dict[str, str] | None = None  # {"messageID": ..., "callID": ...}


Reply = Literal["once", "always", "reject"]


class DeniedError(Exception):
    """Raised when a tool call is denied by permission rules."""
    def __init__(self, ruleset: Ruleset | None = None):
        self.ruleset = ruleset or []
        super().__init__(
            f"The user has specified a rule which prevents this tool call. Relevant rules: {self.ruleset}"
        )


class RejectedError(Exception):
    """Raised when the user manually rejects a permission request."""
    def __init__(self, message: str = "The user rejected permission for this tool call."):
        super().__init__(message)


class CorrectedError(Exception):
    """Raised when the user rejects with feedback."""
    def __init__(self, feedback: str):
        self.feedback = feedback
        super().__init__(f"The user rejected with feedback: {feedback}")
