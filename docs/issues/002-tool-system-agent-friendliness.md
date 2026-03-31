# Issue #002: 工具系统 Agent 友好性与功能完善度改进

- **日期**: 2026-03-31
- **状态**: ✅ 已修复
- **影响范围**: `edit` / `task` / `bash` / `write` / `webfetch` / `websearch` / `glob_tool` / `batch` / `registry` / `base`
- **新增**: `listdir` 工具、`descriptions/listdir.md`

---

## 1. 问题总览

参考 Anthropic StrReplaceEditorTool 和主流 coding agent（Claude Code、Cursor 等）的最佳实践，对当前工具系统进行全面审计，发现了以下类别的问题：

| 类别 | 数量 | 严重度 |
|---|---|---|
| Agent 每次编辑后无法确认结果 | 1 | P0 |
| Sub-agent 不支持多步推理 | 1 | P0 |
| 缺少基础工具（目录浏览） | 1 | P1 |
| 工具描述文件未被使用（死代码） | 1 | P1 |
| HTML 解析过于简陋 | 1 | P2 |
| Bash 超时后进程未被杀死 | 1 | P2 |
| 各种小 bug 和缺失 | 3 | P2-P3 |

---

## 2. 修复详情

### 2.1 [P0] Edit 工具 — 返回修改上下文 + 新增 insert_after_line

**问题**：

修复前，edit 工具成功后只返回 `"Successfully edited xxx"`，agent 无法确认修改是否正确，**每次编辑后都必须额外调一次 read 来验证**，浪费一轮 LLM 调用。

对比 Anthropic StrReplaceEditorTool：编辑后返回修改区域前后的代码片段（带行号），agent 可以立即确认。

**修复**：

1. **编辑后返回上下文**：展示修改区域前后 4 行的代码片段，带行号和修改标记（`|`）

   ```
   Edited opencode/tool/edit.py (+2 lines)

       22| description = (
       23|     "Edit a file by replacing an exact string match..."
       24| )
       25  
       26      async def call(...):
   ```

2. **新增 `insert_after_line` 参数**：按行号插入内容，解决在空行区域或大段重复代码中无法精确定位的问题

3. **空 `old_string` 语义**：当 `old_string` 为空时追加到文件末尾，无需匹配

4. **行数变化提示**：返回 `(+3 lines)` 或 `(-2 lines)` 的增减信息

**修改文件**：`opencode/tool/edit.py`

### 2.2 [P0] Task 工具 — 支持多轮 Agentic Loop

**问题**：

修复前，task 工具只做**单轮 LLM 调用**：

```python
# Run a simple single-pass (no agentic loop for sub-agent to avoid deep recursion)
async for event in llmmod.stream(stream_input):
```

这意味着 sub-agent 只能做一次推理 + 一轮工具调用，不能进行多步推理。例如，它搜到了文件路径但不能进一步读取文件内容，严重限制实用性。

同时，tool call 的解析也有问题：在 stream 循环中把每个 `ToolCallDelta` 当作完整调用来执行，但在 stream 中 `ToolCallDelta` 是在 `finish_reason` 到达时才作为完整调用发出的，虽然碰巧能工作，但逻辑不清晰。

**修复**：

1. **多轮 agentic loop**：最多 `MAX_TURNS=8` 轮迭代，每轮：
   - 调用 LLM 获取响应
   - 如果有 tool calls，执行并将结果添加到 messages
   - 如果没有 tool calls（`finish_reason != "tool-calls"`），结束循环

2. **正确的消息格式**：每轮将 assistant 的 tool_calls 和对应的 tool results 以正确的 OpenAI 消息格式追加到 messages 中，保证多轮对话上下文连贯

3. **防止递归**：排除 `task`、`todo`、`question`、`batch` 工具

**修改文件**：`opencode/tool/task.py`

### 2.3 [P1] 新增 ListDir 工具

**问题**：

`opencode/file/file.py` 中有 `list_dir` 函数，但**没有注册为 LLM 可调用的工具**。Agent 要查看目录结构只能通过 `bash ls` 命令，但很多场景下 agent 不会想到这么做。

**修复**：

新增 `opencode/tool/listdir.py`：

- **flat 模式**（默认）：列出单层目录内容，显示类型标记 `[dir]`/`[file]` 和文件大小
- **recursive 模式**：tree 视图，限制深度 3 层，防止输出过多
- 自动过滤 `.venv`、`__pycache__`、`node_modules` 等忽略目录
- 结果超过 500 条时截断

**新增文件**：`opencode/tool/listdir.py`、`opencode/tool/descriptions/listdir.md`
**修改文件**：`opencode/tool/registry.py`（注册新工具）

### 2.4 [P1] descriptions/*.md 从死代码变为实际使用

**问题**：

`opencode/tool/base.py` 中已实现 `load_description(tool_id)` 函数，`descriptions/` 目录下有 13 个 .md 描述文件，但所有工具类的 `description` 都是硬编码字符串，**这些 .md 文件从未被实际加载**，是死代码。

.md 文件的描述比硬编码的字符串更详细（包含 Guidelines、使用注意事项等），对 LLM 更友好。

**修复**：

修改 `ToolInfo.to_llm_tool()` 方法，优先使用 `descriptions/{tool_id}.md` 文件：

```python
def to_llm_tool(self) -> dict[str, Any]:
    desc = load_description(self.id) or self.description  # .md 优先，fallback 到硬编码
    return {"type": "function", "function": {"name": self.id, "description": desc, ...}}
```

**修改文件**：`opencode/tool/base.py`

### 2.5 [P2] WebFetch — HTML 解析从正则剥标签升级为保留结构

**问题**：

修复前的 `_html_to_text()` 只做了简单的正则标签剥离：

```python
text = re.sub(r"<[^>]+>", "\n", text)  # 所有标签替换为换行
```

这导致所有结构信息丢失：标题变成普通文本、链接 URL 丢失、代码块和正文混在一起、列表结构消失。

**修复**：

新增 `_html_to_markdown()` 函数，按优先级处理：

| HTML 元素 | 转换为 |
|---|---|
| `<h1>`-`<h6>` | `# ` - `###### ` Markdown 标题 |
| `<pre><code>` | ` ``` ` 代码块 |
| `<code>` | `` ` `` 内联代码 |
| `<a href="url">text</a>` | `[text](url)` Markdown 链接 |
| `<li>` | `- ` 列表项 |
| `<b>`/`<strong>` | `**bold**` |
| `<i>`/`<em>` | `*italic*` |
| `<script>`/`<style>`/`<nav>` | 直接删除 |

**修改文件**：`opencode/tool/webfetch.py`

### 2.6 [P2] WebSearch — 增强解析稳定性

**问题**：

DuckDuckGo HTML 页面结构随时可能变化，单一正则解析容易失效。

**修复**：

实现三层 fallback 解析策略：

1. **Strategy 1**：解析 `result__a` + `result__snippet` 完整结果块
2. **Strategy 2**：只解析 `result__a` 链接（无 snippet）
3. **Strategy 3**：广泛 fallback——提取所有非 DDG 的链接

新增 `_extract_url()` 处理 DDG 的重定向 URL 解码。

**修改文件**：`opencode/tool/websearch.py`

### 2.7 [P2] Bash — 超时后 Kill 进程 + 支持 cwd 参数

**问题**：

1. **超时后进程残留**：`asyncio.wait_for` 超时后只是放弃等待，进程本身可能还在运行（占用 CPU/内存/端口）
2. **无工作目录参数**：agent 不能指定命令在哪个子目录执行

**修复**：

1. **超时 kill**：使用 `start_new_session=True` 创建进程组，超时后 `os.killpg(SIGTERM)` → 等待 0.2s → `os.killpg(SIGKILL)` 双保险
2. **新增 `cwd` 参数**：支持相对路径（基于项目根目录）和绝对路径，并验证目录存在

**修改文件**：`opencode/tool/bash.py`

### 2.8 [P2] Write — 覆盖信息提示

**问题**：

修复前覆盖已有文件时返回 `"Wrote N lines to xxx"`，与新建文件信息完全一样，agent 不知道自己是新建了还是覆盖了。

**修复**：

- 新建：`"Created xxx (42 lines)"`
- 覆盖：`"Overwrote xxx (38 → 42 lines)"`，明确显示行数变化

**修改文件**：`opencode/tool/write.py`

### 2.9 [P3] Glob — 修复截断后 count 值 bug

**问题**：

```python
matches = matches[:500]
output = "..." + f"500 of {len(matches)} matches"  # len(matches) 已经是 500 了！
```

截断后 `len(matches)` 已经变成 500，无法反映真实总数。

**修复**：截断前保存 `total_count = len(matches)`。

**修改文件**：`opencode/tool/glob_tool.py`

### 2.10 [P3] Batch — 修复成功判断的误判

**问题**：

```python
succeeded = sum(1 for r in results if "Error:" not in r)
```

用字符串 `"Error:" not in r` 判断成功/失败，如果工具的正常输出中包含 `"Error:"` 文本（如搜索代码中的错误处理代码），会被误判为失败。

**修复**：

`_execute_one()` 返回 `tuple[bool, str]`，用 `result.is_error` 结构化判断。

**修改文件**：`opencode/tool/batch.py`

---

## 3. 修改清单

| 文件 | 修改类型 | 说明 |
|---|---|---|
| `opencode/tool/edit.py` | 重写 | 返回上下文 + insert_after_line + 追加模式 |
| `opencode/tool/task.py` | 重写 | 多轮 agentic loop (MAX_TURNS=8) |
| `opencode/tool/listdir.py` | 新增 | 目录浏览工具（flat + tree 模式） |
| `opencode/tool/descriptions/listdir.md` | 新增 | listdir 描述文件 |
| `opencode/tool/base.py` | 修改 | to_llm_tool() 使用 .md 描述 |
| `opencode/tool/registry.py` | 修改 | 注册 listdir 工具 |
| `opencode/tool/webfetch.py` | 重写 | HTML→Markdown 保留结构 |
| `opencode/tool/websearch.py` | 重写 | 三层 fallback 解析 |
| `opencode/tool/bash.py` | 重写 | 超时 kill + cwd 参数 |
| `opencode/tool/write.py` | 修改 | 覆盖/新建区分提示 |
| `opencode/tool/glob_tool.py` | 修复 | 截断后 count 值 |
| `opencode/tool/batch.py` | 修复 | 结构化成功判断 |

**共 12 个文件，其中 5 个重写、5 个修改、2 个新增。**

---

## 4. 经验教训

1. **工具的返回值对 agent 行为影响巨大**：edit 工具不返回上下文，直接导致 agent 每次都要多调一次 read，浪费 50% 的工具调用。工具设计时必须从"agent 拿到这个结果后能做什么"的角度思考。

2. **Sub-agent 必须支持多轮推理**：单轮 LLM 调用的 sub-agent 几乎没有实用价值。真实任务（如"分析这个模块的实现"）需要搜索→读取→分析的多步链条。

3. **死代码要么删除要么用起来**：descriptions/*.md 文件占用了维护成本（要和代码同步更新）但从未被使用，是典型的"好意图、坏执行"。

4. **HTML 解析不能太简陋**：正则剥标签在 agent 场景下不够用——agent 需要理解网页结构来找到有用信息。转 Markdown 是最佳平衡点。

5. **超时 ≠ 停止**：`asyncio.wait_for` 超时只是放弃等待，不会终止进程。必须显式 kill 进程和进程组。

6. **字符串匹配做状态判断是反模式**：`"Error:" not in result` 这种判断在正常输出包含 `"Error:"` 时会误判。应使用结构化的 `is_error` 标志。
