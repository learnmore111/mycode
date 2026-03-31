# Issue #006: CLI 对话回溯能力不足

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **提交**: `493bc13`
- **影响范围**: `opencode/cli/main.py`

---

## 1. 问题

用户无法在 CLI 界面有效回溯对话内容：

| 问题 | 说明 |
|---|---|
| `/history` 信息量不够 | 每条消息只显示前 80 个字符，工具调用消息显示为截断的 JSON 乱码 |
| 无法查看消息详情 | 没有 `/history N` 查看单条消息完整内容 |
| 工具调用不可见 | assistant 消息关联了哪些 tool_calls 看不到，tool 结果只显示为 `tool: {"content": ...}` |
| 无步骤状态 | loop_guard 已生成 checkpoint 数据，但 CLI 没有暴露 |

## 2. 修复

### `/history` 概览增强

按 Turn（用户+助手一轮）分组，区分三种消息角色：

```
Turn 1
[0] user: 查看项目中有哪些工具
[1] assistant: (no text)
     tools: listdir, webfetch
[2] tool (call_abc123): [dir]  agent-mem-note/
[3] tool (call_def456): - OpenCode | The open source...
[4] assistant: 项目中有以下工具...
```

### `/history N` 消息详情

Rich Panel 展示完整内容：
- **user**: Markdown 渲染的用户消息
- **assistant**: 文本内容 + 完整的 tool_calls 列表（含参数）
- **tool**: 工具结果输出（最多 2000 字符）

### `/steps` 步骤状态

展示上一轮 agentic loop 的 checkpoint：

```
Agentic Loop Steps (3 iterations)
Cache: 2 entries

✅ Step 0  1.2s  tools:[listdir✓, webfetch✓]
✅ Step 1  3.5s  tools:[read✓ 📦, grep✓]  cached:1
✅ Step 2  0.8s  text:342
```

### `/help` 更新

```
/history      Show conversation turns
/history N    Show full detail for message #N
/steps        Show agentic loop step states from last turn
```

## 3. 实现细节

- `_handle_command` 签名改为接受 `**extra`（含 `last_checkpoint`）
- 新增 `_print_message_detail()` 辅助函数处理各角色消息的 Rich Panel 渲染
- `last_checkpoint` 变量在 `done` 事件中从 `done_data["checkpoint"]` 获取并传入

## 4. 经验教训

Agent 的内部状态（conversation history、step state、cache stats）对用户调试非常有价值。CLI 应该提供低成本的方式让用户随时查看这些信息，而不是把它们隐藏在内部变量中。
