# Changelog: Context Compression & Memory System Fixes

**Date:** 2026-04-17  
**Scope:** `opencode/session/compaction.py`, `opencode/session/memory/`, `opencode/session/message.py`, `opencode/session/prompt.py`

---

## Summary

对上下文压缩（compaction）和会话记忆（session memory）系统进行了全面审查和修复，共解决 9 个问题，涵盖竞态条件、数据安全、性能优化、死代码清理和可观测性增强。

---

## Fix 1: Summary Extraction Fallback Hardening

**文件:** `opencode/session/compaction.py` — `_extract_summary()`  
**严重度:** Medium  
**问题:** 当 LLM 未输出 `<summary>` 或 `<analysis>` 标签时，整个原始响应（包括推理/scratchpad 内容）会被直接作为摘要使用，导致后续上下文中包含不相关的 LLM 内部思考过程。

**修复:**
- 新增 `_strip_reasoning_patterns()` 函数，在 last-resort fallback 中自动清理常见的 LLM 推理模式
- 匹配并移除 `<thinking>`/`<reasoning>`/`<scratchpad>` XML 标签块
- 匹配并移除以 "Let me think..."、"I need to..."、"First,"、"Step N" 等模式开头的行
- 添加 warning 级别日志，记录 fallback 触发时的原始文本长度和清理后长度

**涉及变更:**
- 重写 `_extract_summary()` 的 last-resort 分支
- 新增 `_REASONING_TAG_RE` 和 `_REASONING_LINE_RE` 编译正则
- 新增 `_strip_reasoning_patterns()` 辅助函数

---

## Fix 2: Post-Compaction Validation

**文件:** `opencode/session/compaction.py` — `compact()`  
**严重度:** Low  
**问题:** 压缩后的结果未校验是否仍然超出上下文窗口阈值。如果 LLM 生成的摘要过长，要到下一次迭代才能发现问题。

**修复:**
- 在 `compact()` 返回前增加 Step 8：估算压缩结果 + system prompt + tools 的总 token 数
- 如果总量仍超过 `context_limit * OVERFLOW_RATIO`，记录 warning 日志
- 信息性检查，不改变行为（下一次迭代会自动重新触发压缩）

---

## Fix 3: Reduce Deep Copy Cost in Truncation

**文件:** `opencode/session/compaction.py` — `_truncate_tool_outputs_for_summary()`  
**严重度:** Medium  
**问题:** 原实现使用 `copy.deepcopy(messages)` 对整个消息列表做深拷贝。在大上下文（10K+ 消息，每条含大量工具输出）场景下，可能导致 ~1GB 的临时内存分配。

**修复:**
- 改为 copy-on-write 策略：遍历消息列表，仅对需要截断的消息做 `copy.deepcopy(msg)`
- 不需要截断的消息直接引用原始对象（零分配）
- 典型场景下内存开销从 O(n) 降至 O(k)，其中 k 为需要截断的消息数

---

## Fix 4: Remove Dead Code `get_messages_after_compact_boundary`

**文件:** `opencode/session/message.py`, `opencode/session/__init__.py`  
**严重度:** Low  
**问题:** `get_messages_after_compact_boundary()` 函数已定义但从未被调用。`compact_boundary` subtype 在 `SystemMessage` 中声明但从未被创建。属于未完成的基础设施代码。

**修复:**
- 删除 `get_messages_after_compact_boundary()` 函数
- 从 `opencode/session/__init__.py` 的导出列表中移除
- `normalize_messages_for_api()` 中保留对 `compact_boundary` 的过滤（标记为 "reserved for future use"）
- `SystemMessage.subtype` 中保留 `compact_boundary` 枚举值
- 删除 `tests/test_module_enhancements.py` 中的相关测试（`test_get_messages_after_compact_boundary`, `test_get_messages_no_boundary`）

---

## Fix 5: Async Test Compatibility for TestFileIO

**文件:** `tests/test_session_memory.py`  
**严重度:** N/A (test fix)  
**问题:** `SessionMemory._append_record()` 和 `_rewrite_file()` 已被改为 async 方法（Fix 1/2 的前置改动），但 6 个测试仍以同步方式调用。

**修复:**
- 将以下测试转换为 async + `@pytest.mark.asyncio`：
  - `TestFileIO.test_append_and_load_records`
  - `TestFileIO.test_load_all_turns_filters`
  - `TestFileIO.test_load_latest_summary`
  - `TestFileIO.test_rewrite_file_updates_turns`
  - `TestContextFormatting.test_format_for_context_with_data`
  - `TestContextFormatting.test_format_for_context_limits_turns`
- 所有调用点添加 `await`

---

## Fix 6: LLM Call Retry for Memory System

**文件:** `opencode/session/memory/memory.py` — `_call_llm_combined()`  
**严重度:** Medium  
**问题:** 内存系统的 LLM 调用（用于生成滚动摘要和精炼 turn summary）在任何错误时立即 fallback 到启发式摘要，包括限流（429）、超时等瞬时错误。

**修复:**
- 在 `_call_llm_combined()` 中添加重试循环：最多重试 2 次（共 3 次尝试）
- 指数退避：1s → 2s
- 仅对瞬时错误重试（通过 `_is_transient_error()` 判断）
- 新增模块级 `_is_transient_error()` 辅助函数，匹配常见瞬时错误模式：
  - `rate limit`, `timeout`, `429`, `503`, `connection`, `temporary`, `overloaded`
- 非瞬时错误（认证失败、模型不存在等）仍立即 fallback

---

## Fix 7: Token Estimation Telemetry

**文件:** `opencode/session/compaction.py`, `opencode/session/prompt.py`  
**严重度:** Low  
**问题:** Token 估算使用 `byte_len // 3 + 15%` 的启发式方法，但缺乏与实际 API 用量的对比数据，无法判断估算是否准确或需要调优。

**修复:**
- 新增 `log_token_accuracy(estimated, actual, model_id)` 函数
- 仅在偏差显著时记录日志（ratio > 2.0 或 < 0.5），避免正常运行时的日志噪声
- 在 `prompt.py` 的每次迭代后调用，将启发式估算与 API 返回的 `input_tokens` 对比
- 纯可观测性改动，不影响任何行为逻辑

---

## Fix 8: Extractor `_format_conversation` Defensive Type Handling

**文件:** `opencode/session/memory/extractor.py` — `_format_conversation()`  
**严重度:** Low  
**问题:** 原实现对非字符串 `content` 使用 `str(content)[:200]`，可能将 dict/list 的 repr 注入提取 prompt。例如多模态 API 的 content blocks（`[{"type": "text", "text": "..."}, {"type": "image", ...}]`）会被序列化为不可读的字符串。

**修复:**
- 新增 `isinstance(content, list)` 分支：提取 `type="text"` 的 block，拼接文本内容
- 非 str 且非 list 的 content 设为空字符串（安全忽略）
- 同时由 ruff auto-fix 清理了未使用的导入（`os`, `MEMORY_EXCLUSIONS`, `MemoryEntry`, `MemoryType`）

---

## Fix 9: FileLock Fallback Lock Per-Path Isolation

**文件:** `opencode/session/memory/filelock.py`  
**严重度:** Medium  
**问题:** 每个 `FileLock` 实例创建自己的 `asyncio.Lock()` 作为 OS 锁失败时的 fallback。但同一文件路径的不同 `FileLock` 实例会使用不同的内存锁，导致 fallback 场景下无法实现互斥。

**修复:**
- 新增模块级 `_fallback_locks: dict[str, asyncio.Lock]`，以 resolved 路径为 key
- 新增 `_fallback_locks_mutex` 保护字典并发访问
- 新增 `_get_fallback_lock(path)` 异步函数，获取或创建 per-path 的共享锁
- `FileLock.acquire()` 中 fallback 改为从模块级池获取锁，而非使用实例属性
- `FileLock.release()` 中对应更新释放逻辑

---

## Test Results

```
394 passed, 1 failed (pre-existing: test_server.py::test_root)
```

唯一的失败测试 `test_server.py::test_root` 是服务器端的 JSON 解码问题，与本次改动无关。

## Files Changed

| File | Type | Lines Changed |
|------|------|---------------|
| `opencode/session/compaction.py` | Modified | +60 |
| `opencode/session/memory/memory.py` | Modified | +30 |
| `opencode/session/memory/filelock.py` | Modified | +20 |
| `opencode/session/memory/extractor.py` | Modified | +8, -3 |
| `opencode/session/message.py` | Modified | -20 |
| `opencode/session/__init__.py` | Modified | -2 |
| `opencode/session/prompt.py` | Modified | +7 |
| `tests/test_session_memory.py` | Modified | +12, -6 |
| `tests/test_module_enhancements.py` | Modified | -15 |
