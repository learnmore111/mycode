# Issue #008: 模型使用工具后不生成文本回复

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **提交**: `3f9ee35`
- **影响范围**: `opencode/session/prompts/*.txt`、`opencode/cli/main.py`

---

## 1. 现象

用户要求"分析 edit 工具的实现原理"，模型调用了 skill、listdir、grep 三个工具，结果正常返回，但之后**没有任何文本输出**：

```
✗ skill
  Skill 'opencode' not found. Searched in .opencode/skills/
✓ listdir
  [dir]  agent-mem-note/
✓ Grep class Edit|def edit
  ./opencode/tool/edit.py:nParams(BaseModel):
─ 50.8s · in:15.8K out:148 · reasoning:146
```

`out:148` 只有 148 个 output token，全部被工具调用的 JSON 参数消耗。

## 2. 根因分析

### Agentic Loop 工作正常

1. 第 1 轮：LLM 一次性请求 3 个工具调用（skill + listdir + grep）
2. 工具执行完毕，processor 返回 `"continue"`
3. `prompt.py` 把工具结果追加到 messages，发起第 2 轮 LLM 调用
4. 第 2 轮：LLM 收到工具结果，进行了 reasoning（146 tokens），但**选择不生成文本就结束**（`finish_reason=stop`，output=0）

### 根本原因：模型行为

某些模型（尤其是 reasoning 模型）在收到工具结果后，可能认为：
- 工具输出本身就是答案（"用户问了项目中有哪些工具，listdir 结果就是答案"）
- 不需要额外总结

但 CLI 只显示工具输出的第一行预览，**用户实际上看不到完整的工具结果**，因此需要模型生成文本来传达信息。

### 50.8s 的耗时说明

50.8s ≈ 第 1 轮 LLM 调用 + 工具执行 + 第 2 轮 LLM 调用。第二轮确实发生了（耗时最长的部分），但模型没有产出文本。

## 3. 修复方案

### 3.1 系统提示强化

在 `default.txt`、`anthropic.txt`、`trinity.txt` 三个系统提示中添加明确指令：

```
IMPORTANT: After receiving tool results, you MUST always provide a text
response to the user summarizing what you found or did. Never end your
turn with only tool calls and no text output. The user cannot see raw
tool results — only your text responses are displayed to them.
```

关键信息："**The user cannot see raw tool results**"——让模型理解用户看不到工具的原始输出。

### 3.2 CLI 兜底提示

如果 agentic loop 结束后 `full_text` 为空但有工具调用，显示：

```
(No text response — model returned only tool calls. Use /history to view tool results.)
```

这样用户至少知道发生了什么，并可以用 `/history` 查看详情。

## 4. 经验教训

1. **系统提示必须明确告知模型"用户能看到什么"**：模型不知道 CLI 只展示第一行预览。如果模型以为用户能看到完整的工具输出，就可能不生成总结。

2. **代码层兜底不可少**：系统提示只是"建议"，模型不保证遵守。CLI 层必须处理"无文本输出"的边界情况。

3. **这是 Issue #003 的同类问题**：#003 是 listdir 调用后无输出，本次是更复杂的多工具调用场景。根因相同——模型认为工具输出即答案。
