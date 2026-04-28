"""Tests for the three-layer loop guard system."""
from mycode.session.loop_guard import (
    GuardAction,
    LoopGuard,
    LoopGuardConfig,
    ToolResultCache,
)


class TestHardLimit:
    def test_within_limit(self):
        guard = LoopGuard(LoopGuardConfig(max_iterations=10))
        v = guard.check(0)
        assert v.action == GuardAction.CONTINUE

    def test_warn_at_threshold(self):
        guard = LoopGuard(LoopGuardConfig(max_iterations=10, warn_at=0.8))
        v = guard.check(8)
        assert v.action == GuardAction.WARN
        assert "remaining" in v.reason

    def test_force_stop_at_limit(self):
        guard = LoopGuard(LoopGuardConfig(max_iterations=10))
        v = guard.check(10)
        assert v.action == GuardAction.FORCE_STOP


class TestPatternDetection:
    def test_repeat_detection(self):
        guard = LoopGuard(LoopGuardConfig(repeat_threshold=3))
        for _ in range(3):
            guard.record_tool_call("read", {"file_path": "foo.py"}, output="content")
        v = guard.check(3)
        assert v.action == GuardAction.STOP
        assert "Repeat" in v.reason

    def test_no_false_positive_different_input(self):
        guard = LoopGuard(LoopGuardConfig(repeat_threshold=3))
        for i in range(3):
            guard.record_tool_call("read", {"file_path": f"file{i}.py"}, output="content")
        v = guard.check(3)
        assert v.action == GuardAction.CONTINUE

    def test_ping_pong_detection(self):
        guard = LoopGuard(LoopGuardConfig(ping_pong_threshold=3))
        for _ in range(3):
            guard.record_tool_call("grep", {"pattern": "foo"}, output="match")
            guard.record_tool_call("read", {"file_path": "bar.py"}, output="content")
        v = guard.check(6)
        assert v.action == GuardAction.STOP
        assert "Ping-pong" in v.reason

    def test_stall_detection(self):
        # Set repeat_threshold higher than stall to isolate stall detection
        guard = LoopGuard(LoopGuardConfig(stall_threshold=4, repeat_threshold=10))
        for _ in range(4):
            guard.record_tool_call("bash", {"command": "check"}, output="pending")
        v = guard.check(4)
        assert v.action == GuardAction.STOP
        assert "Stall" in v.reason


class TestNearLimitIntelligence:
    def test_stop_on_empty_text_streak(self):
        guard = LoopGuard(LoopGuardConfig(
            max_iterations=10,
            near_limit_ratio=0.5,
            empty_text_streak_limit=2,
        ))
        # Simulate 3 iterations with no text output
        for i in range(3):
            step = guard.begin_step(i)
            guard.complete_step(step, text_length=0)

        v = guard.check(5)  # 50% = near limit
        assert v.action == GuardAction.STOP
        assert "no text" in v.reason

    def test_continue_with_text_production(self):
        guard = LoopGuard(LoopGuardConfig(
            max_iterations=10,
            near_limit_ratio=0.5,
            empty_text_streak_limit=2,
        ))
        step = guard.begin_step(0)
        guard.complete_step(step, text_length=100)

        v = guard.check(5)
        # Should warn (near limit) but not stop
        assert v.action == GuardAction.WARN


class TestToolResultCache:
    def test_cache_hit(self):
        cache = ToolResultCache()
        cache.put("read", {"file_path": "test.py"}, "file content")
        result = cache.get("read", {"file_path": "test.py"})
        assert result == "file content"

    def test_cache_miss(self):
        cache = ToolResultCache()
        result = cache.get("read", {"file_path": "test.py"})
        assert result is None

    def test_non_cacheable_tool(self):
        cache = ToolResultCache()
        cache.put("edit", {"file_path": "test.py"}, "edited")
        result = cache.get("edit", {"file_path": "test.py"})
        assert result is None  # edit is not cacheable

    def test_cache_eviction(self):
        cache = ToolResultCache(max_size=4)
        for i in range(5):
            cache.put("read", {"file_path": f"file{i}.py"}, f"content{i}")
        assert cache.size <= 4

    def test_invalidation(self):
        cache = ToolResultCache()
        cache.put("read", {"file_path": "test.py"}, "content")
        cache.invalidate()
        assert cache.get("read", {"file_path": "test.py"}) is None

    def test_targeted_invalidation_keeps_unrelated(self):
        """Editing one file should NOT wipe caches for unrelated files."""
        cache = ToolResultCache()
        cache.put("read", {"file_path": "a.py"}, "a")
        cache.put("read", {"file_path": "b.py"}, "b")
        cache.invalidate(files={"a.py"})
        assert cache.get("read", {"file_path": "a.py"}) is None
        assert cache.get("read", {"file_path": "b.py"}) == "b"

    def test_targeted_invalidation_matches_dir_prefix(self):
        """Editing src/foo.py invalidates a grep cached under scope 'src'."""
        cache = ToolResultCache()
        cache.put("grep", {"path": "src"}, "match")
        cache.invalidate(files={"src/foo.py"})
        assert cache.get("grep", {"path": "src"}) is None

    def test_targeted_invalidation_reverse_prefix(self):
        """Editing the parent dir invalidates cached reads of its files."""
        cache = ToolResultCache()
        cache.put("read", {"file_path": "src/foo.py"}, "content")
        cache.invalidate(files={"src"})
        assert cache.get("read", {"file_path": "src/foo.py"}) is None

    def test_lru_eviction(self):
        """Oldest-used entries are evicted first, not randomly."""
        cache = ToolResultCache(max_size=3)
        cache.put("read", {"file_path": "a.py"}, "a")
        cache.put("read", {"file_path": "b.py"}, "b")
        cache.put("read", {"file_path": "c.py"}, "c")
        # Touch `a` so it becomes the most recently used.
        assert cache.get("read", {"file_path": "a.py"}) == "a"
        # Adding `d` should evict the oldest remaining (which is now `b`).
        cache.put("read", {"file_path": "d.py"}, "d")
        assert cache.get("read", {"file_path": "a.py"}) == "a"
        assert cache.get("read", {"file_path": "b.py"}) is None
        assert cache.get("read", {"file_path": "c.py"}) == "c"
        assert cache.get("read", {"file_path": "d.py"}) == "d"


class TestStepState:
    def test_step_lifecycle(self):
        guard = LoopGuard()
        step = guard.begin_step(0)
        assert step.status.value == "running"

        guard.complete_step(step, text_length=42)
        assert step.status.value == "completed"
        assert step.text_produced is True
        assert step.duration > 0

    def test_checkpoint(self):
        guard = LoopGuard()
        step = guard.begin_step(0)
        guard.complete_step(step, text_length=10)

        cp = guard.checkpoint
        assert len(cp["steps"]) == 1
        assert cp["steps"][0]["text_produced"] is True
        assert cp["empty_text_streak"] == 0


class TestRetryLogic:
    def test_retry_on_timeout(self):
        guard = LoopGuard(LoopGuardConfig(max_retries=2))
        assert guard.should_retry("bash", "Command timed out", retry_count=0) is True

    def test_no_retry_on_validation(self):
        guard = LoopGuard(LoopGuardConfig(max_retries=2))
        assert guard.should_retry("edit", "Validation failed for tool", retry_count=0) is False

    def test_no_retry_after_max(self):
        guard = LoopGuard(LoopGuardConfig(max_retries=2))
        assert guard.should_retry("bash", "Connection error", retry_count=2) is False

    def test_retry_on_rate_limit(self):
        guard = LoopGuard(LoopGuardConfig(max_retries=2))
        assert guard.should_retry("webfetch", "HTTP 429 rate limit", retry_count=0) is True


class TestCacheInvalidationOnMutation:
    def test_write_invalidates_cache(self):
        guard = LoopGuard()
        guard.cache.put("read", {"file_path": "test.py"}, "old content")
        assert guard.cache.get("read", {"file_path": "test.py"}) == "old content"

        # Simulate a write operation
        guard.record_tool_call("edit", {"file_path": "test.py"}, output="edited")

        # Cache should be invalidated
        assert guard.cache.get("read", {"file_path": "test.py"}) is None
