# 问答讲解：项目实现细节与代码位置

> 本文档以问答形式深入讲解 MyCode 的核心架构设计与具体代码实现。每个问题都标注了对应的源码文件路径和行号，方便直接定位阅读。

---

## Q1: MyCode 的核心 agentic loop（代理循环）是如何工作的？代码在哪里？

**答**: 核心循环由两个关键文件协作完成：

### `mycode/session/prompt.py` — 入口编排器（~1000 行）

`prompt()` 函数是整个 Agent 的主入口，负责：

- 解析模型和 Agent 配置（第 204-220 行）
- 构建静态 System Prompt（第 223 行）
- 加载工具注册表并转为 LLM 工具格式（第 231-232 行）
- **初始化三层 LoopGuard** 循环保护（第 346-348 行）
- **主循环** `for iteration in range(max_iterations)` （第 392 行开始）：
  1. 每轮开始检查 LoopGuard 是否应停止
  2. 检查是否需要 Context Compaction（上下文压缩）
  3. 构建 System-Reminder 增量注入（skills/memory/date → messages 尾部）
  4. 调用 `processor.process_stream()` 执行 LLM 调用 + 工具执行
  5. 收集结果，若为 `continue` 则将工具结果追加到 messages 继续下一轮
  6. 若为 `stop` 则跳出循环
- 最终持久化消息到 SQLite（第 687-697 行）

### `mycode/session/processor.py` — LLM→Tool 流式处理器（~700 行）

`process_stream()` 是 async generator，实时 yield 事件：

- `text_delta`: LLM 文本增量
- `reasoning_delta`: 推理内容增量
- `tool_start/tool_running/tool_done`: 工具调用生命周期
- `error`: 错误事件
- `finish`: 单轮完成（返回 `continue`/`stop`/`compact`）

其他关键机制：

- **LLM 重试机制**：最多 3 次，带指数退避（第 145-288 行）
- **工具执行阶段**（第 291 行开始）分为三个阶段：
  1. **Phase 1 Pre-flight**：权限检查 + Doom Loop 检测 + 缓存命中检查
  2. **Phase 1.5 Cache**：直接返回缓存结果
  3. **Phase 2 Execute**：**读写分离** — mutating 工具先执行（保证一致性），readonly 工具通过 `asyncio.gather()` 并行执行

### 数据流图

```
用户消息 → prompt.py:prompt()
           ├─ 模型/Agent 解析
           ├─ System Prompt 构建 (固定不变)
           ├─ Tool 注册表加载
           └─ [Agentic Loop]
               ├─ LoopGuard 检查
               ├─ Compaction 检查
               ├─ System-Reminder 注入 (增量)
               │
               ▼
         processor.py:process_stream()
              ├─ LLM 流式调用 (litellm)
              │   ├─ TextDelta → yield text_delta
              │   ├─ ReasoningDelta → yield reasoning_delta
              │   ├─ ToolCallDelta → 收集工具调用
              │   └─ FinishEvent → 统计 token/cost
              │
              ▼ [工具执行]
              ├─ 权限检查 (PermissionManager)
              ├─ Doom Loop 检测 (同工具+同输入×3次)
              ├─ 缓存命中检查
              ├─ [读写分离]
              │   ├─ Mutating 工具: 串行执行
              │   └─ ReadOnly 工具: asyncio.gather() 并行
              │
              ▼ yield "finish" ("continue" / "stop")
         [若 continue → 工具结果追加 messages → 下一轮]
```

---

## Q2: System-Reminder 增量注入是什么设计？为什么这么做？

**答**: 这是参考 Claude Code 的 **prefix cache 复用优化**。

### 问题背景

传统方式将 skills 列表、memory 等动态信息放入 system prompt。但 system prompt 每轮都变化（如 memory 更新），导致 API 的 prefix cache 失效，每次都要重新处理完整前缀。

### 解决方案 (`mycode/session/prompt.py` 第 462-470 行)

将动态信息以 `<system-reminder>` 标签包装，作为 **user message 追加到 messages 列表末尾**，而非嵌入 system prompt。这样：

- **system prompt 保持完全静态** → 跨 session/跨轮次复用 prefix cache
- **动态信息在消息尾部** → 不影响前缀匹配

### 增量策略 (`_build_system_reminders()`, 第 855-906 行)

| 信息类型 | 增量策略 | 实现位置 |
|---------|---------|---------|
| Skills 列表 | 首次发送全量；仅新增时发增量；无变化则省略 | `_build_skills_reminder()` (908-957行) |
| Date | 首次发送；日期变化时发更新；否则省略 | `_build_date_reminder()` (960-972行) |
| Memory | 每次根据当前 query 检索相关记忆 | `_build_memory_reminder()` (975-1006行) |

### 状态追踪

`_extract_reminder_state_from_history()` (770-852行) 从历史消息中解析上一次的 reminder 状态，避免重复发送。

---

## Q3: 三层循环保护（Loop Guard）是如何实现的？

**答**: 位于 `mycode/session/loop_guard.py`，提供三层递进式保护：

| 层级 | 保护机制 | 触发条件 |
|------|---------|---------|
| **Layer 1: Hard Limit** | 硬性迭代上限 | 达到 `max_iterations`（默认 50，可由 Agent 的 `steps` 字段配置） |
| **Layer 2: Pattern Detection** | 模式检测 | 检测重复工具调用模式（相同工具+相似输入连续出现） |
| **Layer 3: Intelligence** | LLM 智能判断 | 将最近步骤摘要发给 LLM，询问是否应继续 |

### 关键实现细节

- `LoopGuardConfig` 数据类配置保护参数（`max_iterations`, `pattern_window` 等）
- `LoopGuard.check(iteration)` 在每轮开始时调用，返回 `GuardAction`（`CONTINUE`/`STOP`/`WARN`/`FORCE_STOP`）
- `begin_step(iteration)` / `complete_step(step)` 记录每步的原子状态
- `record_tool_call()` 记录工具调用历史用于模式检测
- 结果缓存 `checkpoint` 保存最终状态供 UI 展示

### 集成位置

`prompt.py` 第 346-348 行初始化，第 396-418 行每轮检查，第 561-570 行记录步骤完成状态。

---

## Q4: 上下文压缩（Compaction）的完整流程是什么？

**答**: 位于 `mycode/session/compaction.py`（~630 行），采用 **滑动窗口 + LLM 摘要** 策略：

### 触发检测 (`should_compact()`, 162-178行)

- 当估算 token 数超过 context window 的 **85%** (`OVERFLOW_RATIO = 0.85`) 时触发
- Token 估算使用保守策略：UTF-8 字节数 ÷ 3 + 15% 安全余量

### 压缩流程 (`compact()`, 459-605行)

```
Step 1: prune_tool_outputs()  — 清理旧工具输出（保留最近 40K tokens）
        ↓ 若已释放 > 20K tokens，直接返回，跳过后续步骤
Step 2: _split_by_turns()     — 按 user turn 分割（保留最近 3 轮原文）
        ↓ old_messages | recent_messages
Step 3: _truncate_tool_outputs_for_summary() — 截断旧消息中的大工具输出
                                          （错误信息保留 2500 字符，普通输出 1000 字符）
Step 4: LLM 摘要调用          — 使用与主 agent 相同的 system prompt + tools
                               （复用 prefix cache！）
Step 5: _extract_summary()    — 提取 <summary> 块，丢弃 <analysis> 草稿
Step 6: _build_compact_result() — 组装为 [user_summary_msg] + recent_messages
                                （summary 作为 user message，不破坏前缀缓存）
Step 7: 后验证               — 检查压缩后是否仍在阈值内
```

### Cache 友好设计要点

1. 摘要以 **user message** 形式注入（非 system message）
2. 使用 **相同 system prompt + tools** 调用 compaction agent
3. 工具输出按类型差异化截断（错误信息保留更多）

### Token 估算缓存 (`estimate_tokens_cached()`, 126-144行)

使用 blake2b 内容指纹避免重复计算大型 tools JSON 的 token 估算。

---

## Q5: 两层记忆系统如何工作？代码结构是怎样的？

**答**: 位于 `mycode/session/memory/` 目录，共 5 个模块：

### 第一层：会话记忆 (`memory.py`)

- **用途**：短期、会话内的对话摘要
- **存储格式**：JSONL 滚动文件（每个会话一个文件）
- **工作机制**：每轮对话结束后，LLM 精炼该轮的关键信息，以追加方式写入 JSONL，支持高效读取最新条目。会话结束时生成最终摘要。

### 第二层：结构化长期记忆 (`memdir.py`)

- **用途**：跨会话持久化的长期知识
- **存储目录**：项目的 `.mycode/memories/` 目录
- **四种类别**：`user/`（用户偏好）、`feedback/`（反馈）、`project/`（项目知识）、`reference/`（参考资料）
- **Frontmatter 格式**：每个 `.md` 文件带有 YAML frontmatter（标签、创建时间、新鲜度等）
- **MEMORY.md 索引**：自动维护的记忆索引文件

### 记忆检索 (`retrieval.py`)

```python
# 使用方式 (见 prompt.py:997行)
memories = find_relevant_memories(directory, query, max_results=5)
```

两阶段检索：**关键词匹配** → **LLM 辅助排序**

### 自动提取 (`extractor.py`)

后台运行，监控对话内容；当检测到值得长期保存的信息时自动创建记忆条目；新鲜度管理定期清理过期记忆。

### 文件锁 (`filelock.py`)

使用 `fcntl.flock` 保证多进程安全，防止并发写入导致数据损坏。

---

## Q6: 工具系统的能力声明和读写分离是如何设计的？

**答**: 工具系统位于 `mycode/tool/` 目录，核心设计如下：

### 能力声明体系 (`base.py`)

每个工具基类声明三种能力标志：

```python
class ToolBase:
    is_read_only: bool          # 是否只读（不修改文件系统）
    is_destructive: bool        # 是否破坏性（不可逆操作）
    is_concurrency_safe: bool   # 是否并发安全（可并行执行）
```

### 15 个内置工具的能力矩阵

| 工具 | is_read_only | is_destructive | 执行方式 |
|------|:---:|:---:|---------|
| `bash` | 取决于命令 | 可能 | 动态判断 |
| `read` / `glob` / `grep` / `listdir` | ✅ | ❌ | **并行** |
| `edit` / `write` / `create_skill` | ❌ | ✅/❌ | **串行** |
| `task/subagent/question/todo/batch` | ❌ | 可能 | **串行** |
| `webfetch` / `websearch` / `skill` | ✅ | ❌ | **并行** |

### 读写分离执行逻辑 (`processor.py:417-493行`)

```python
# Phase 2: 分离 readonly 和 mutating 任务
for tp, tool_impl, tool_ctx in executable:
    if tool_impl.is_read_only(input) and tool_impl.is_concurrency_safe(input):
        readonly_tasks.append(...)
    else:
        mutating_tasks.append(...)

# 关键：mutating 先执行！
if mutating_first:
    await _run_mutating()   # 串行
    await _run_readonly()   # asyncio.gather 并行
```

**为什么 mutating 先执行？** 避免混合批处理 `[read(foo.py), edit(foo.py)]` 中 read 缓存了 edit 之前的旧内容。

### 其他工具特性

- **路径安全验证**：所有文件路径工具防止目录逃逸（`../` 攻击）
- **原子写入**：write/edit 使用写入临时文件 + rename 策略
- **变更暂存**：编辑操作可批量确认或回退（集成 snapshot 系统）

---

## Q7: 统一子代理工具 `subagent` 的三种模式有什么区别？

**答**: 位于 `mycode/tool/subagent.py`，提供三种子代理调度模式：

| 模式 | 用途 | 上下文传递 | 默认轮次 | 隔离级别 |
|------|------|-----------|---------|---------|
| **`delegate`** | 委托子任务 | 继承父会话完整上下文 | 可配置 | 低（共享 workspace） |
| **`parallel`** | 并行独立任务 | 仅传递任务描述 | 固定短轮次 | 中（asyncio.gather） |
| **`isolated`** | 隔离 risky 操作 | Git worktree 隔离 | 可配置 | **高**（独立工作目录） |

### Isolated 模式的特殊实现

1. 创建 git worktree 作为临时工作目录
2. 子代理在 worktree 中独立执行所有操作
3. 完成后可选择性将变更合并回主分支
4. 适用于高风险操作（大规模重构、实验性修改等）

**权限与 Loop Guard 贯穿**：子代理继承父会话的权限规则和循环保护配置。

---

## Q8: Provider 系统如何支持 14+ 种 AI 提供商？

**答**: 位于 `mycode/provider/` 目录：

### 自动发现机制

通过环境变量自动识别可用的 provider：`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY` 等 16 种。

### 参数转换 (`transform.py`)

不同提供商对参数有不同要求：

- **Reasoning 模型**（o1/deepseek-r1）：设置 `reasoning_effort`，移除 `temperature`
- **长上下文模型**：调整 `max_tokens` 上限
- **Vision 模型**：确保 image capability 正确声明
- **Provider 特殊处理**：Bedrock/Azure 的认证签名等

### 统一调用层

所有请求通过 **litellm** 库路由，自动处理不同 API 格式转换、流式响应标准化、Token 计数和费用统计、错误码统一映射、自动重试。

---

## Q9: 权限系统（Permission）的工作流程是什么？

**答**: 位于 `mycode/permission/` 目录：

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| PermissionManager | `permission.py` | 权限管理器，协调 ask/reply 流程 |
| Rule schema | `schema.py` | 权限规则定义（通配符模式） |
| Wildcard 匹配 | 通配符求值引擎 | `*`/`?` 模式匹配 |

### 规则格式

```python
Rule(permission="write", pattern="*.py", action="ask")  # allow / deny / ask
```

### 执行流程 (`processor.py:324-367行`)

```
工具调用触发
    ↓
PermissionManager.ask()
    ↓
遍历 Agent 规则集
    ↓
├─ action == "allow"  → 直接放行
├─ action == "deny"   → 拒绝执行
└─ action == "ask"    → 阻塞等待用户回复
                         ├─ CLI: 同步阻塞等待输入
                         └─ HTTP: SSE 推送前端 → 弹窗 → 用户点击 → reply
```

---

## Q10: Event Bus（事件总线）有哪些事件类型？

**答**: 位于 `mycode/bus/` 目录，基于 **asyncio.Queue** 实现发布-订阅模式的异步通信，17 种事件类型覆盖全系统关键节点：

| 类别 | 事件 | 触发时机 |
|------|------|---------|
| **Session** | `SESSION_STARTED`, `SESSION_ERROR` | 会话开始/出错 |
| **Message** | `PART_DELTA`, `PART_UPDATED` | 消息部分增量/更新 |
| **Tool** | `TOOL_START`, `TOOL_DONE` | 工具开始/完成 |
| **Compaction** | `COMPACTION_BEFORE/AFTER` | 上下文压缩前后 |
| **File** | `FILE_CREATED/MODIFIED/DELETED` | 文件变更 |
| **Permission** | `PERMISSION_ASK/REPLY` | 权限请求/回复 |
| **MCP** | `CONNECTED/DISCONNECTED/TOOL_CALL` | MCP 连接/调用 |
| **System** | `SHUTDOWN` | 系统关闭 |

支持类型化订阅、通配符订阅（如 `"session.*"`）、全局广播。

---

## Q11: Web 前端的技术栈和组件架构是什么？

**答**: 位于 `web/` 目录，React 18 + TypeScript + Vite + TailwindCSS：

### 核心组件树

```
<App>
 ├─ <Sidebar>            # 会话列表侧边栏（宽度可拖拽）
 ├─ <ChatArea>
 │    ├─ <ChatHeader>     # 标题栏 + Model/Agent 选择器
 │    ├─ <MessageList>    # 消息滚动容器（auto-scroll-to-bottom）
 │    │    └─ <MessageBubble>
 │    │         ├─ <TextContent>      # Markdown 渲染
 │    │         ├─ <ToolExecution>   # 工具调用卡片（可折叠）
 │    │         └─ <MessageMeta>     # Token/Cost 统计
 │    ├─ <StreamingIndicator>  # 流式动画
 │    └─ <MessageInput>       # 输入框 + 发送按钮
 └─ <PermissionModal>     # 权限弹窗
```

### SSE 流式通信 (`api/stream.ts`)

POST + ReadableStream 实现双向流，实时接收 LLM 输出和工具调用状态。

---

## Q12: 如何扩展 MyCode？有哪些扩展点？

**答**: 项目设计了多个层次的扩展机制：

| 扩展方式 | 位置 | 说明 |
|---------|------|------|
| **自定义工具** | `mycode/tool/` | 继承 `ToolBase`，实现 `execute()`，声明能力标志 |
| **插件 Hook** | `mycode/plugin/` | 7 种钩子：before/after_tool、before/after_prompt、on_compaction 等 |
| **MCP Server** | `mycode/mcp/` | stdio/HTTP 传输，外部工具自动注册 |
| **自定义 Agent** | `mycode/agent/` | `.txt` 文件定义 system prompt、工具集、权限规则 |
| **Skill 技能** | `.mycode/skills/` | Markdown 格式技能描述，`skill` 工具加载 |
| **自定义 Provider** | `mycode/provider/` | 环境变量自动发现 或 `mycode.json` 手动配置 |
