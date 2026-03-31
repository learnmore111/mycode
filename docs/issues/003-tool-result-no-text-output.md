# Issue #003: 工具调用后无文本输出 + conversation_history 丢失工具上下文

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **影响范围**: `opencode/cli/main.py`、`opencode/session/prompt.py`

---

## 1. 现象

用户输入后，agent 调用了 `listdir` 工具两次并显示了结果，但之后没有任何文本回复：

```
  ✓ listdir
    [dir]  agent-mem-note/
  ✓ listdir
    ├── agent/
  ─ 11.6s · in:12.7K out:221 · reasoning:298
  Context ▐██░░░░░░░░░░░░░░░░░░░░░░░░░░░░▌ 12.9K/131K (10%)
```

工具执行过程中也没有 "Thinking" spinner 提示。

## 2. 根因分析

### 问题 A：工具执行完后没有 Thinking Spinner

`main.py` 第 615 行：

```python
# Prepare fresh live for next events
live = Live(Spinner("dots", ""), console=console, refresh_per_second=10, transient=True)
# 注意：没有 live.start()！
```

工具执行完后创建了新的 `Live` 对象但**没有启动**。当 agentic loop 进入下一轮 LLM 调用时，用户看不到任何 "Thinking" 提示，可能误以为程序已停止或已完成。

### 问题 B：模型在第二轮返回空文本

从 token 统计 `out:221` 和 `reasoning:298` 可以看出，LLM 确实进行了第二轮推理（298 reasoning tokens），但 221 个 output tokens 几乎全部被工具调用的 JSON 参数消耗，没有产生可见的文本内容。

Agentic loop 的设计是正确的（`prompt.py` 第 212-216 行）：

```python
if result == "continue":
    tool_messages = proc.build_tool_results_messages(iteration_parts)
    messages.extend(tool_messages)
    continue  # → 下一轮 LLM 调用
```

但某些模型在收到展示型工具结果（如目录列表）后，可能认为"工具输出就是答案"而不再生成额外文本。这是**模型行为**，不是代码 bug，但 CLI 层应该提供更好的用户体验。

### 问题 C（重要）：conversation_history 丢失工具调用上下文

`main.py` 第 682-684 行：

```python
conversation_history.append({"role": "user", "content": text})
if full_text:
    conversation_history.append({"role": "assistant", "content": full_text})
```

**只记录了纯文本的 user/assistant 消息**，完全丢弃了：
- assistant 的 `tool_calls` 字段
- `role: "tool"` 的工具结果消息

这意味着用户下一次发消息时，LLM 看到的 history 中**没有之前的工具调用上下文**，不知道自己之前做了什么。虽然不影响当前轮次的 agentic loop（因为 `prompt.py` 内部维护了完整的 `messages`），但**跨轮次的上下文连贯性被破坏**了。

## 3. 修复方案

### 3.1 工具完成后自动启动 Thinking Spinner

```python
# 修复前
live = Live(Spinner("dots", ""), console=console, ...)
# 没有 start!

# 修复后
live = Live(Spinner("dots", ""), console=console, ...)
live.start()
spinner = Spinner("dots", "")
spinner.text = Text("Thinking...", style="dim italic")
live.update(spinner)
```

当下一轮事件到来时（text_delta 或 tool_start），spinner 会自动被停止或替换。

### 3.2 prompt.py 在 done event 中携带完整 messages

```python
yield PromptEvent(type="done", data={
    ...
    "messages": messages,  # 包含完整的 tool_calls 和 tool results
})
```

### 3.3 CLI 使用完整 messages 更新 history

```python
if done_data.get("messages"):
    conversation_history.clear()
    conversation_history.extend(done_data["messages"])
else:
    # Fallback
    conversation_history.append({"role": "user", "content": text})
    if full_text:
        conversation_history.append({"role": "assistant", "content": full_text})
```

## 4. 修改文件

| 文件 | 修改 |
|---|---|
| `opencode/cli/main.py` | 工具完成后启动 Thinking spinner + 用完整 messages 更新 history |
| `opencode/session/prompt.py` | done event 携带完整 messages 列表 |

## 5. 经验教训

1. **UI 反馈不能断档**：agentic loop 中每次等待 LLM 响应都应有 spinner/indicator。用户在 11.6s 内看不到任何提示就会以为程序挂了。

2. **conversation_history 必须保留完整的 tool calling 上下文**：只保留 user/assistant 纯文本会破坏跨轮次的连贯性，LLM 不知道自己之前调用了什么工具、得到了什么结果。

3. **模型可能不为工具结果生成总结文本**：对于展示型工具（如 listdir），某些模型会认为工具输出本身就是答案。CLI 应该考虑在这种情况下自动展示更多工具输出内容（不仅仅是第一行预览）。
