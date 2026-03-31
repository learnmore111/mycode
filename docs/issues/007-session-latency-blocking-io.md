# Issue #007: 模型回复后卡顿 + 退出时 Session 保存超时

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **提交**: `41129d1`
- **影响范围**: `opencode/session/prompt.py`、`opencode/cli/main.py`

---

## 1. 现象

用户观察到两个延迟：

1. **模型思考完后卡一会**：LLM 流结束后，最终文本已经显示，但状态行（`─ 3.2s · in:12K out:500`）迟迟不出来，中间有明显的空等
2. **退出 CLI 时保存 session 很慢**：`Ctrl+D` 后显示 `Saving session...` 然后等待好几秒

## 2. 根因分析

### 卡顿点 1：同步 SQLite 阻塞事件循环

`prompt.py` 在发出 `done` 事件**之前**执行了三次同步 SQLite 写操作：

```python
save_message(assistant_msg)   # 同步 db.commit()
save_parts(all_parts)         # 同步 db.commit()（可能有几十个 parts）
touch(session_id)             # 同步 db.commit()
```

这些用的是 SQLAlchemy 的同步 Session（`get_session()` → `db.merge()` → `db.commit()`），在 async 事件循环中**直接阻塞**。

CLI 在 `async for event in prompt(...)` 中等待 `done` 事件来显示统计行和 context bar，但 `done` 被这些 IO 操作卡住了。

**延迟估算**：每次 `db.commit()` 约 5-50ms（取决于磁盘），3 次操作 + 多个 parts 的 merge，总计可能 100-500ms 的阻塞。

### 卡顿点 2：session memory finalize 发起 LLM 调用

`main.py` 退出时：

```python
note_path = await session_memory.finalize(
    messages=conversation_history,
    start_time=session_start_time,
)
```

`finalize()` → `_llm_update(force=True)` → `_call_llm_combined()` → **发起一次完整的 LLM API 请求**生成 session 总结。

这是一个网络请求，耗时 **2-10 秒**（取决于 provider 和 conversation 长度）。

同时，每轮交互的 `record_turn()` 在第 3/6/9... 轮时（`SUMMARY_INTERVAL=3`）也会同步触发 `_llm_update()`，在主循环中等待 LLM 响应。

## 3. 修复方案

### 3.1 持久化后移：先 yield done，再异步写 SQLite

```python
# 修复前：先写 DB，再 yield done
save_message(assistant_msg)    # ← 阻塞
save_parts(all_parts)          # ← 阻塞
touch(session_id)              # ← 阻塞
yield PromptEvent(type="done", ...)  # ← CLI 终于收到

# 修复后：先 yield done，再在后台线程写 DB
yield PromptEvent(type="done", ...)  # ← CLI 立即收到
await asyncio.to_thread(save_message, assistant_msg)     # 后台线程
await asyncio.to_thread(save_parts, all_parts)           # 后台线程
await asyncio.to_thread(touch, session_id)               # 后台线程
```

`asyncio.to_thread()` 将同步函数放到线程池执行，不阻塞事件循环。

### 3.2 record_turn 改为 fire-and-forget

```python
# 修复前：await 等待完成（可能触发 LLM 调用）
await session_memory.record_turn(...)

# 修复后：fire-and-forget，不阻塞主循环
asyncio.ensure_future(_bg_record())
```

### 3.3 finalize 加 5 秒超时保护

```python
# 修复前：无限等待
note_path = await session_memory.finalize(...)

# 修复后：最多等 5 秒
note_path = await asyncio.wait_for(
    session_memory.finalize(...),
    timeout=5.0,
)
```

超时后输出 `⚠ Save timed out (skipped LLM summary)` 并正常退出。

## 4. 效果

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 模型回复 → 状态行显示 | 100-500ms 延迟 | 即时 |
| 每 3 轮交互的额外延迟 | 2-5s（LLM 调用） | 0s（后台执行） |
| 退出保存 session | 2-10s（可能更久） | 最多 5s（超时保护） |

## 5. 经验教训

1. **在 async 事件循环中，任何同步 IO 都是阻塞**：即使是"很快"的 SQLite 写操作，在高频交互中也会累积成明显的延迟。应该用 `asyncio.to_thread()` 或异步数据库驱动。

2. **网络请求必须有超时**：session memory 的 LLM 总结调用没有超时保护，如果 API 慢或不可用，用户会被无限卡住。

3. **非关键路径用 fire-and-forget**：session memory 的 per-turn 更新不应阻塞用户的下一次输入。即使偶尔丢失一条记录，也比卡住用户好。

4. **先给用户反馈，再做后台工作**：`yield done` 应该在所有 IO 之前，让用户立即看到结果。持久化是"后台清理"，不应阻塞前台展示。
