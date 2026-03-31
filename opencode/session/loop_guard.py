"""Loop guard — three-layer agentic loop protection with result caching and step state.

Architecture inspired by:
- PraisonAI doom loop detection (generic_repeat / poll_no_progress / ping_pong)
- reivo-guard (hash match + sliding window + EWMA anomaly)
- Claude SDK agent loop (hard limit + graceful degradation)

Three protection layers:
1. Hard Limit Guard    — absolute max iterations, never exceeded
2. Pattern Guard       — detect repeated/ping-pong/stalled patterns
3. Near-Limit Guard    — intelligent early termination as limit approaches

Plus:
- ToolResultCache      — content-addressable cache to skip duplicate tool calls
- StepState            — per-step state for checkpoint/resume/retry
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opencode.util import log as logmod

logger = logmod.create(service="loop_guard")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class LoopGuardConfig:
    """Configuration for all three guard layers."""
    # Layer 1: Hard limit
    max_iterations: int = 50          # Absolute ceiling, never exceeded
    warn_at: float = 0.8             # Warn at 80% of max

    # Layer 2: Pattern detection
    window_size: int = 20             # Sliding window for pattern analysis
    repeat_threshold: int = 3         # Same tool+input N times → doom loop
    ping_pong_threshold: int = 4      # A→B→A→B alternation N times → ping-pong
    stall_threshold: int = 5          # N consecutive iterations with no text output → stalled

    # Layer 3: Near-limit intelligence
    near_limit_ratio: float = 0.9     # At 90% of max, start smart termination
    empty_text_streak_limit: int = 3  # N consecutive iterations with no text → suggest stop

    # Result cache
    cache_enabled: bool = True
    cache_max_size: int = 200         # Max cached entries

    # Retry
    max_retries: int = 2             # Max retries per tool call on transient failure


class GuardAction(Enum):
    """Actions the guard can recommend."""
    CONTINUE = "continue"      # Proceed normally
    WARN = "warn"              # Continue but emit a warning
    STOP = "stop"              # Stop the loop
    FORCE_STOP = "force_stop"  # Hard stop, non-negotiable


@dataclass
class GuardVerdict:
    """Result of a guard check."""
    action: GuardAction
    reason: str = ""
    layer: str = ""            # Which layer triggered this


# ---------------------------------------------------------------------------
# Tool call history entry
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """Record of a single tool call for pattern analysis."""
    tool_name: str
    input_hash: str           # SHA-256 of canonical JSON input
    output_hash: str = ""     # SHA-256 of output (set after execution)
    is_error: bool = False
    timestamp: float = 0.0

    @staticmethod
    def hash_input(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Content-addressable hash of tool name + input."""
        canonical = json.dumps({"tool": tool_name, "input": tool_input}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @staticmethod
    def hash_output(output: str) -> str:
        return hashlib.sha256(output.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Step state for checkpoint/resume
# ---------------------------------------------------------------------------

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"       # Served from cache
    RETRYING = "retrying"


@dataclass
class StepState:
    """Atomic step state for one iteration of the agentic loop."""
    iteration: int
    status: StepStatus = StepStatus.PENDING
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    text_produced: bool = False
    text_length: int = 0
    error: str | None = None
    started_at: float = 0.0
    completed_at: float = 0.0
    retry_count: int = 0
    cached_calls: int = 0     # How many tool calls served from cache

    def start(self) -> None:
        self.status = StepStatus.RUNNING
        self.started_at = time.time()

    def complete(self, text_length: int = 0) -> None:
        self.status = StepStatus.COMPLETED
        self.completed_at = time.time()
        self.text_length = text_length
        self.text_produced = text_length > 0

    def fail(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.completed_at = time.time()
        self.error = error

    @property
    def duration(self) -> float:
        if self.completed_at and self.started_at:
            return self.completed_at - self.started_at
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "status": self.status.value,
            "text_produced": self.text_produced,
            "text_length": self.text_length,
            "tool_calls": self.tool_calls,
            "error": self.error,
            "duration": round(self.duration, 2),
            "retry_count": self.retry_count,
            "cached_calls": self.cached_calls,
        }


# ---------------------------------------------------------------------------
# Tool result cache
# ---------------------------------------------------------------------------

class ToolResultCache:
    """Content-addressable cache for tool call results.

    Eliminates redundant tool executions when the same tool is called
    with identical input (e.g. reading the same file twice).
    Only caches successful, read-only tool calls.
    """

    # Tools that are safe to cache (read-only, deterministic)
    CACHEABLE_TOOLS = frozenset({
        "read", "glob", "grep", "listdir", "webfetch", "websearch", "skill",
    })

    def __init__(self, max_size: int = 200):
        self._cache: dict[str, str] = {}  # input_hash → output
        self._max_size = max_size

    def get(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Look up a cached result. Returns None on miss."""
        if tool_name not in self.CACHEABLE_TOOLS:
            return None
        key = ToolCallRecord.hash_input(tool_name, tool_input)
        return self._cache.get(key)

    def put(self, tool_name: str, tool_input: dict[str, Any], output: str) -> None:
        """Cache a successful tool result."""
        if tool_name not in self.CACHEABLE_TOOLS:
            return
        if len(self._cache) >= self._max_size:
            # Evict oldest 25%
            keys = list(self._cache.keys())
            for k in keys[:len(keys) // 4]:
                del self._cache[k]
        key = ToolCallRecord.hash_input(tool_name, tool_input)
        self._cache[key] = output

    def invalidate(self, tool_name: str | None = None) -> None:
        """Invalidate cache entries. Call after write/edit/bash operations."""
        if tool_name is None:
            self._cache.clear()
        # After file modifications, invalidate read cache
        # (we can't know exactly which files changed, so clear all)
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        return {"size": len(self._cache), "max_size": self._max_size}


# Tools that modify state (trigger cache invalidation)
MUTATING_TOOLS = frozenset({"edit", "write", "bash"})


# ---------------------------------------------------------------------------
# Loop Guard — the main three-layer protection engine
# ---------------------------------------------------------------------------

class LoopGuard:
    """Three-layer loop protection engine.

    Layer 1 — Hard Limit: Absolute max iterations, never exceeded.
    Layer 2 — Pattern Detection: Repeated calls, ping-pong, stall.
    Layer 3 — Near-Limit Intelligence: Smart termination approaching limit.
    """

    def __init__(self, config: LoopGuardConfig | None = None):
        self.config = config or LoopGuardConfig()
        self._history: deque[ToolCallRecord] = deque(maxlen=self.config.window_size)
        self._steps: list[StepState] = []
        self._empty_text_streak = 0
        self._total_text_length = 0
        self.cache = ToolResultCache(max_size=self.config.cache_max_size)

    # --- Step management ---

    def begin_step(self, iteration: int) -> StepState:
        """Create and register a new step."""
        step = StepState(iteration=iteration)
        step.start()
        self._steps.append(step)
        return step

    def complete_step(self, step: StepState, text_length: int = 0) -> None:
        """Mark a step as complete and update streaks."""
        step.complete(text_length)
        self._total_text_length += text_length
        if text_length > 0:
            self._empty_text_streak = 0
        else:
            self._empty_text_streak += 1

    @property
    def steps(self) -> list[StepState]:
        return list(self._steps)

    @property
    def checkpoint(self) -> dict[str, Any]:
        """Serializable checkpoint for resume."""
        return {
            "steps": [s.to_dict() for s in self._steps],
            "empty_text_streak": self._empty_text_streak,
            "total_text_length": self._total_text_length,
            "history_size": len(self._history),
            "cache_stats": self.cache.stats,
        }

    # --- Tool call recording ---

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        output: str = "",
        is_error: bool = False,
    ) -> None:
        """Record a tool call for pattern analysis."""
        record = ToolCallRecord(
            tool_name=tool_name,
            input_hash=ToolCallRecord.hash_input(tool_name, tool_input),
            output_hash=ToolCallRecord.hash_output(output) if output else "",
            is_error=is_error,
            timestamp=time.time(),
        )
        self._history.append(record)

        # Cache invalidation for mutating tools
        if tool_name in MUTATING_TOOLS and not is_error:
            self.cache.invalidate()
        # Cache successful read-only results
        elif not is_error and output:
            self.cache.put(tool_name, tool_input, output)

    # --- Three-layer guard check ---

    def check(self, iteration: int) -> GuardVerdict:
        """Run all three guard layers. Call BEFORE each iteration.

        Returns the most restrictive verdict from any layer.
        """
        # Layer 1: Hard limit (absolute, non-negotiable)
        v1 = self._check_hard_limit(iteration)
        if v1.action in (GuardAction.STOP, GuardAction.FORCE_STOP):
            return v1

        # Layer 2: Pattern detection
        v2 = self._check_patterns()
        if v2.action == GuardAction.STOP:
            return v2

        # Layer 3: Near-limit intelligence
        v3 = self._check_near_limit(iteration)
        if v3.action == GuardAction.STOP:
            return v3

        # Return the most restrictive non-stop verdict
        if v1.action == GuardAction.WARN or v3.action == GuardAction.WARN:
            return GuardVerdict(
                action=GuardAction.WARN,
                reason=v1.reason or v3.reason,
                layer="combined",
            )

        return GuardVerdict(action=GuardAction.CONTINUE, layer="all_clear")

    # --- Layer 1: Hard Limit ---

    def _check_hard_limit(self, iteration: int) -> GuardVerdict:
        max_iter = self.config.max_iterations

        if iteration >= max_iter:
            return GuardVerdict(
                action=GuardAction.FORCE_STOP,
                reason=f"Hard limit reached ({max_iter} iterations)",
                layer="hard_limit",
            )

        warn_at = int(max_iter * self.config.warn_at)
        if iteration >= warn_at:
            remaining = max_iter - iteration
            return GuardVerdict(
                action=GuardAction.WARN,
                reason=f"Approaching limit: {remaining} iterations remaining",
                layer="hard_limit",
            )

        return GuardVerdict(action=GuardAction.CONTINUE, layer="hard_limit")

    # --- Layer 2: Pattern Detection ---

    def _check_patterns(self) -> GuardVerdict:
        if len(self._history) < 2:
            return GuardVerdict(action=GuardAction.CONTINUE, layer="pattern")

        history = list(self._history)

        # 2a. Generic repeat: same tool + same input N times
        repeat = self._detect_repeat(history)
        if repeat:
            return repeat

        # 2b. Ping-pong: A→B→A→B alternation
        ping_pong = self._detect_ping_pong(history)
        if ping_pong:
            return ping_pong

        # 2c. Stall: same output repeated (poll with no progress)
        stall = self._detect_stall(history)
        if stall:
            return stall

        return GuardVerdict(action=GuardAction.CONTINUE, layer="pattern")

    def _detect_repeat(self, history: list[ToolCallRecord]) -> GuardVerdict | None:
        """Detect same tool+input called repeatedly."""
        threshold = self.config.repeat_threshold
        if len(history) < threshold:
            return None

        recent = history[-threshold:]
        first = recent[0]
        if all(r.tool_name == first.tool_name and r.input_hash == first.input_hash for r in recent):
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Repeat detected: {first.tool_name} called {threshold} times with same input",
                layer="pattern.repeat",
            )
        return None

    def _detect_ping_pong(self, history: list[ToolCallRecord]) -> GuardVerdict | None:
        """Detect A→B→A→B alternation pattern."""
        threshold = self.config.ping_pong_threshold
        needed = threshold * 2  # Need 2N entries for N ping-pong pairs
        if len(history) < needed:
            return None

        recent = history[-needed:]
        # Check if it alternates between exactly two tools
        tools = [r.tool_name for r in recent]
        unique_tools = set(tools)
        if len(unique_tools) != 2:
            return None

        # Check strict alternation
        is_alternating = all(tools[i] != tools[i + 1] for i in range(len(tools) - 1))
        if is_alternating:
            t1, t2 = unique_tools
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Ping-pong detected: {t1} ↔ {t2} alternating {threshold} times",
                layer="pattern.ping_pong",
            )
        return None

    def _detect_stall(self, history: list[ToolCallRecord]) -> GuardVerdict | None:
        """Detect same tool+input+output repeated (no progress)."""
        threshold = self.config.stall_threshold
        if len(history) < threshold:
            return None

        recent = history[-threshold:]
        first = recent[0]
        if not first.output_hash:
            return None

        if all(
            r.tool_name == first.tool_name
            and r.input_hash == first.input_hash
            and r.output_hash == first.output_hash
            for r in recent
        ):
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Stall detected: {first.tool_name} returning identical results {threshold} times",
                layer="pattern.stall",
            )
        return None

    # --- Layer 3: Near-Limit Intelligence ---

    def _check_near_limit(self, iteration: int) -> GuardVerdict:
        max_iter = self.config.max_iterations
        near_limit = int(max_iter * self.config.near_limit_ratio)

        if iteration < near_limit:
            return GuardVerdict(action=GuardAction.CONTINUE, layer="near_limit")

        # Near the limit: check if we're making progress
        remaining = max_iter - iteration

        # If we've had N consecutive iterations with no text output, suggest stopping
        if self._empty_text_streak >= self.config.empty_text_streak_limit:
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Near limit ({remaining} left) with {self._empty_text_streak} consecutive iterations producing no text",
                layer="near_limit.no_progress",
            )

        # If recent tool calls are all errors, stop early
        recent_errors = sum(1 for r in list(self._history)[-3:] if r.is_error)
        if recent_errors >= 3:
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Near limit ({remaining} left) with {recent_errors} consecutive tool errors",
                layer="near_limit.error_streak",
            )

        return GuardVerdict(
            action=GuardAction.WARN,
            reason=f"Near limit: {remaining} iterations remaining",
            layer="near_limit",
        )

    # --- Retry logic ---

    def should_retry(self, tool_name: str, error: str, retry_count: int) -> bool:
        """Decide if a failed tool call should be retried."""
        if retry_count >= self.config.max_retries:
            return False
        # Don't retry validation errors or permission errors
        if "validation" in error.lower() or "permission" in error.lower() or "denied" in error.lower():
            return False
        # Don't retry if it's a doom loop trigger
        if "doom loop" in error.lower():
            return False
        # Retry transient errors (timeout, network, etc.)
        transient_keywords = ["timeout", "timed out", "connection", "network", "rate limit", "429", "503"]
        return any(kw in error.lower() for kw in transient_keywords)
