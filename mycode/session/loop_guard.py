"""循环守卫 — 带结果缓存和步骤状态的三层智能体循环保护。

架构灵感来自：
- PraisonAI 厄运循环检测（generic_repeat / poll_no_progress / ping_pong）
- reivo-guard（哈希匹配 + 滑动窗口 + EWMA 异常检测）
- Claude SDK 智能体循环（硬限制 + 优雅降级）

三层保护：
1. 硬限制守卫    — 绝对最大迭代次数，永不超出
2. 模式守卫       — 检测重复/乒乓/停滞模式
3. 接近限制守卫    — 当接近限制时智能提前终止

额外功能：
- ToolResultCache      — 内容可寻址缓存，跳过重复工具调用
- StepState            — 每步状态，用于检查点/恢复/重试
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mycode.util import log as logmod

logger = logmod.create(service="loop_guard")


# ---------------------------------------------------------------------------
# 配置
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
    """守卫可建议的操作。"""
    CONTINUE = "continue"      # Proceed normally
    WARN = "warn"              # Continue but emit a warning
    STOP = "stop"              # Stop the loop
    FORCE_STOP = "force_stop"  # Hard stop, non-negotiable


@dataclass
class GuardVerdict:
    """守卫检查的结果。"""
    action: GuardAction
    reason: str = ""
    layer: str = ""            # Which layer triggered this


# ---------------------------------------------------------------------------
# 工具调用历史条目
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """用于模式分析的单个工具调用记录。"""
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
# 用于检查点/恢复的步骤状态
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
    """智能体循环单次迭代的原子步骤状态。"""
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
# 工具结果缓存
# ---------------------------------------------------------------------------

class ToolResultCache:
    """工具调用结果的内容可寻址缓存。

    当使用相同输入调用相同工具时（例如读取同一文件两次），消除冗余的工具执行。
    仅缓存成功的只读工具调用。

    缓存条目标记了它们依赖的任何文件路径，以便当变异工具编辑特定文件时，
    我们只驱逐触及该文件的条目 — 避免在大型批次上进行过于宽泛的 `.clear()` 风暴，
    同时仍然防止过读取问题。
    """

    # 可以安全缓存的工具（只读、确定性）
    CACHEABLE_TOOLS = frozenset({
        "read", "glob", "grep", "listdir", "webfetch", "websearch", "skill",
    })

    # 工具输入中标识工具触及文件的关键字。
    _FILE_INPUT_KEYS = ("file_path", "path", "filePath", "pathname")

    # 类似 LRU 的逐出使用 OrderedDict；最近使用的在末尾，最旧的在前面。
    def __init__(self, max_size: int = 200):
        from collections import OrderedDict

        self._cache: OrderedDict[str, tuple[str, frozenset[str]]] = OrderedDict()
        self._max_size = max_size

    @staticmethod
    def _extract_files(tool_name: str, tool_input: dict[str, Any]) -> frozenset[str]:
        """Best-effort extract file paths a read-only call depends on.

        We only use this for invalidation hints — missing paths just mean
        the entry gets dropped by the blanket fallback in `invalidate()`.
        """
        files: set[str] = set()
        for key in ToolResultCache._FILE_INPUT_KEYS:
            val = tool_input.get(key)
            if isinstance(val, str) and val:
                files.add(val)
        # grep/glob carry their scope under "path" already handled above.
        return frozenset(files)

    def get(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """查找缓存结果。未命中时返回 None。"""
        if tool_name not in self.CACHEABLE_TOOLS:
            return None
        key = ToolCallRecord.hash_input(tool_name, tool_input)
        entry = self._cache.get(key)
        if entry is None:
            return None
        # LRU 更新 — 移到最近使用端。
        self._cache.move_to_end(key)
        return entry[0]

    def put(self, tool_name: str, tool_input: dict[str, Any], output: str) -> None:
        """缓存成功的工具结果。"""
        if tool_name not in self.CACHEABLE_TOOLS:
            return
        if len(self._cache) >= self._max_size:
            # LRU 逐出 — 删除最旧的条目。对空字典是竞态安全的，
            # 因为上面的调用者都是同步的。
            with contextlib.suppress(KeyError):  # pragma: no cover — 防御性
                self._cache.popitem(last=False)
        key = ToolCallRecord.hash_input(tool_name, tool_input)
        files = self._extract_files(tool_name, tool_input)
        self._cache[key] = (output, files)
        self._cache.move_to_end(key)

    def invalidate(self, tool_name: str | None = None, files: frozenset[str] | set[str] | None = None) -> None:
        """使受变异工具调用影响的缓存条目失效。

        如果提供了 `files`，则仅删除触及至少一条路径的条目 —
        精确匹配或前缀匹配。
        否则（未知范围 — 例如具有任意副作用的 `bash` 命令）
        清除整个缓存。

        前缀匹配的存在是为了编辑 ``src/foo.py`` 时能正确使缓存的 ``grep`` 调用失效，
        该调用的范围是 ``src``（grep 命中了目录，我们刚刚编辑了其中一个文件）。
        """
        if not files:
            self._cache.clear()
            return

        file_set: set[str] = set(files)

        def _affects(tagged: frozenset[str]) -> bool:
            for tag in tagged:
                if tag in file_set:
                    return True
                # Directory-scoped reads (e.g. grep on `src`) must be
                # invalidated when any file under that dir was mutated,
                # and vice versa (edit on `src` invalidates grep("src/x")).
                for mutated in file_set:
                    if mutated == tag:
                        return True
                    if mutated.startswith(tag.rstrip("/") + "/"):
                        return True
                    if tag.startswith(mutated.rstrip("/") + "/"):
                        return True
            return False

        stale = [
            key
            for key, (_out, tagged) in self._cache.items()
            # An entry with no file tags (e.g. websearch) is kept — those
            # outputs cannot be invalidated by file edits.
            if tagged and _affects(tagged)
        ]
        for key in stale:
            self._cache.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        return {"size": len(self._cache), "max_size": self._max_size}


# 修改状态的工具（触发缓存失效）
MUTATING_TOOLS = frozenset({"edit", "write", "bash"})


# ---------------------------------------------------------------------------
# 循环守卫 — 主三层保护引擎
# ---------------------------------------------------------------------------

class LoopGuard:
    """三层循环保护引擎。

    第 1 层 — 硬限制：绝对最大迭代次数，永不超出。
    第 2 层 — 模式检测：重复调用、乒乓、停滞。
    第 3 层 — 接近限制智能：接近限制时智能终止。
    """

    def __init__(self, config: LoopGuardConfig | None = None):
        self.config = config or LoopGuardConfig()
        self._history: deque[ToolCallRecord] = deque(maxlen=self.config.window_size)
        self._steps: list[StepState] = []
        self._empty_text_streak = 0
        self._total_text_length = 0
        self.cache = ToolResultCache(max_size=self.config.cache_max_size)

    # --- 步骤管理 ---

    def begin_step(self, iteration: int) -> StepState:
        """创建并注册一个新步骤。"""
        step = StepState(iteration=iteration)
        step.start()
        self._steps.append(step)
        return step

    def complete_step(self, step: StepState, text_length: int = 0) -> None:
        """标记步骤为完成并更新连续记录。"""
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
        """用于恢复的可序列化检查点。"""
        return {
            "steps": [s.to_dict() for s in self._steps],
            "empty_text_streak": self._empty_text_streak,
            "total_text_length": self._total_text_length,
            "history_size": len(self._history),
            "cache_stats": self.cache.stats,
        }

    # --- 工具调用记录 ---

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

        # 变异工具的缓存失效 — 即使在错误时也要失效，
        # 因为工具可能在失败前已部分修改了文件。
        # `edit`/`write` 针对特定文件，因此我们可以进行精确驱逐；
        # `bash` 是不透明的，我们回退到完全清除。
        if tool_name in MUTATING_TOOLS:
            touched = ToolResultCache._extract_files(tool_name, tool_input)
            if tool_name == "bash" or not touched:
                self.cache.invalidate()
            else:
                self.cache.invalidate(tool_name, touched)
        # Cache successful read-only results
        elif not is_error and output:
            self.cache.put(tool_name, tool_input, output)

    # --- 三层守卫检查 ---

    def check(self, iteration: int) -> GuardVerdict:
        """运行所有三层守卫。在每次迭代之前调用。

        返回任何层中最严格的裁决。
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

    # --- 第 1 层：硬限制 ---

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

    # --- 第 2 层：模式检测 ---

    def _check_patterns(self) -> GuardVerdict:
        if len(self._history) < 2:
            return GuardVerdict(action=GuardAction.CONTINUE, layer="pattern")

        history = list(self._history)

        # 2a. 通用重复：相同工具 + 相同输入 N 次
        repeat = self._detect_repeat(history)
        if repeat:
            return repeat

        # 2b. 乒乓：A→B→A→B 交替
        ping_pong = self._detect_ping_pong(history)
        if ping_pong:
            return ping_pong

        # 2c. 停滞：重复相同输出（无进展的轮询）
        stall = self._detect_stall(history)
        if stall:
            return stall

        return GuardVerdict(action=GuardAction.CONTINUE, layer="pattern")

    def _detect_repeat(self, history: list[ToolCallRecord]) -> GuardVerdict | None:
        """检测重复调用相同工具 + 相同输入。"""
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
        """检测相同输入的 A→B→A→B 交替模式。"""
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
        if not is_alternating:
            return None

        # 还要验证输入是否重复 — 使用不同输入的合法工作不是乒乓
        # 按工具分组，检查每个工具的 input_hash 在其所有调用中是否相同
        tool_inputs: dict[str, set[str]] = {}
        for r in recent:
            tool_inputs.setdefault(r.tool_name, set()).add(r.input_hash)
        if all(len(hashes) == 1 for hashes in tool_inputs.values()):
            t1, t2 = unique_tools
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Ping-pong detected: {t1} ↔ {t2} alternating {threshold} times with same inputs",
                layer="pattern.ping_pong",
            )
        return None

    def _detect_stall(self, history: list[ToolCallRecord]) -> GuardVerdict | None:
        """检测重复相同工具 + 输入 + 输出（无进展）。"""
        threshold = self.config.stall_threshold
        if len(history) < threshold:
            return None

        recent = history[-threshold:]
        first = recent[0]

        # 对于有输出的工具：检查工具 + 输入 + 输出是否全部匹配（真正的停滞）
        # 对于没有输出的工具（例如静默 bash）：检查工具 + 输入是否匹配（重复的空操作）
        if first.output_hash:
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
        else:
            # No output — still detect if same tool+input is called repeatedly
            if all(
                r.tool_name == first.tool_name
                and r.input_hash == first.input_hash
                and not r.output_hash
                for r in recent
            ):
                return GuardVerdict(
                    action=GuardAction.STOP,
                    reason=f"Stall detected: {first.tool_name} called {threshold} times with same input and no output",
                    layer="pattern.stall",
                )
        return None

    # --- 第 3 层：接近限制智能 ---

    def _check_near_limit(self, iteration: int) -> GuardVerdict:
        max_iter = self.config.max_iterations
        near_limit = int(max_iter * self.config.near_limit_ratio)

        if iteration < near_limit:
            return GuardVerdict(action=GuardAction.CONTINUE, layer="near_limit")

        # 接近限制：检查我们是否在取得进展
        remaining = max_iter - iteration

        # 如果我们已连续 N 次迭代没有文本输出，建议停止
        if self._empty_text_streak >= self.config.empty_text_streak_limit:
            return GuardVerdict(
                action=GuardAction.STOP,
                reason=f"Near limit ({remaining} left) with {self._empty_text_streak} consecutive iterations producing no text",
                layer="near_limit.no_progress",
            )

        # 如果最近的工具调用都是错误，提前停止
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

    # --- 重试逻辑 ---

    def should_retry(self, tool_name: str, error: str, retry_count: int) -> bool:
        """决定失败的工具调用是否应该重试。"""
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
