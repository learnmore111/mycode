# Multi-Agent 架构实现文档

> 本文档参考 [how-claude-code-works / 07-multi-agent](https://github.com/Windy3f3f3f3f/how-claude-code-works/blob/main/docs/07-multi-agent.md) 设计理念，
> 结合 mycode 仓库现状，给出 **Coordinator（协调器）** 与 **Swarm（群组）** 两种多 Agent 模式的落地方案，
> 并新增一个独立的「编排模块（orchestration）」，支持用户自定义架构、自定义 Agent（含提示词）、可视化/声明式编排、以及复用。

---

## 1. 目标与范围

### 1.1 目标

1. 在现有 `mycode.agent` + `mycode.tool.subagent/task` 基础上，增加**两种**多 Agent 协作模式：
   - **Coordinator 模式**：中心化调度，一个协调者分派 Worker，综合结果；适合单次复杂任务自动拆解。
   - **Swarm 模式**：弱中心化团队，成员之间通过命名信箱点对点通信；适合长生命周期的并行协作。
2. 新增独立的 `mycode/orchestration/` 模块，提供：
   - **声明式编排**（YAML/JSON）→ 运行时拓扑；
   - **自定义 Agent**（提示词、模型、工具白名单、权限）；
   - **复用机制**（Agent / 流程模板可在多个项目间共享）。
3. 与现有的 subagent/task 工具平滑兼容，不破坏 Fork 语义（`task` 保留为轻量 Fork）。

### 1.2 非目标

- 不替换现有单 Agent 主循环（`mycode.session.processor`）；多 Agent 只是其上层能力。
- 不引入分布式消息队列；信箱用「文件邮箱 + 内存队列」两套实现（参考 Claude Code）。

---

## 2. 当前实现对照

| 现有能力 | 对应 Claude Code 概念 | 位置 |
|---|---|---|
| `mycode.agent.AgentInfo` + 内置 build/plan/general/explore/coder | AgentDefinition | `mycode/agent/agent.py` |
| `task` 工具（8 turns，独立上下文） | 同步子 Agent | `mycode/tool/task.py` |
| `subagent` 工具的 `delegate/parallel/isolated` 三种模式 | 同步 / 并行 / worktree | `mycode/tool/subagent.py` |
| `session.worktree` | Git Worktree 隔离 | `mycode/session/worktree.py` |
| `loop_guard` | 子 Agent 递归/循环防护 | `mycode/session/loop_guard.py` |
| `bus` | 事件广播基础设施（复用做 Swarm 信箱后端） | `mycode/bus/` |
| `permission` | 权限规则（委托到 Worker 时复用） | `mycode/permission/` |

**缺口**：
- 缺少「中心编排者」角色；现有 `subagent.parallel` 只是 fan-out / gather，没有「综合 → 再派发」循环。
- 缺少可持久化的「命名 Agent 团队」与 **SendMessage / Mailbox** 通道。
- 缺少用户可编排的**声明式架构**层（现在硬编码在 `mode` 字段中）。

---

## 3. 顶层设计

```
┌───────────────────────────────────────────────────────────────────┐
│                     mycode/orchestration (新增)                   │
│                                                                   │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐  │
│   │  topology/   │   │  runtime/    │   │  registry/           │  │
│   │  声明解析     │   │  运行器       │   │  Agent/流程模板仓库    │  │
│   │  (YAML/JSON) │   │  (Coord/Swarm│   │  (本地+项目级合并)     │  │
│   └──────┬───────┘   └──────┬───────┘   └──────────┬───────────┘  │
│          │                  │                      │              │
│          └───────────┬──────┴──────────────────────┘              │
│                      ▼                                            │
│              ┌──────────────────┐                                 │
│              │  mailbox/        │   SendMessage + 信箱            │
│              │  (file / memory) │   (AsyncLocalStorage 上下文)    │
│              └─────────┬────────┘                                 │
│                        │                                          │
└────────────────────────┼──────────────────────────────────────────┘
                         ▼
       ┌────────────────────────────────────────────┐
       │  现有能力: agent/tool/session/bus/permission │
       └────────────────────────────────────────────┘
```

- **topology**：解析用户编排文件 → 内存中的拓扑图（DAG + 角色）。
- **runtime**：两类运行器：`CoordinatorRunner` / `SwarmRunner`，都基于同一个 `AgentExecutor`。
- **registry**：加载内置 + `~/.mycode/agents/*.md` + `<project>/.mycode/agents/*.md` + `<project>/.mycode/orchestrations/*.yaml`。
- **mailbox**：`SendMessage` 工具 + 信箱抽象，上层协议完全相同，底层按后端选最优实现。

---

## 4. 模块划分

```
mycode/orchestration/
├── __init__.py
├── topology/
│   ├── __init__.py
│   ├── schema.py          # Pydantic 模型：OrchestrationSpec / AgentSpec / StageSpec
│   ├── loader.py          # yaml/json 加载、变量插值、校验
│   └── validator.py       # 环依赖检测、工具权限检查
├── runtime/
│   ├── __init__.py
│   ├── executor.py        # AgentExecutor: 可被协调/群组复用的单 Agent 循环
│   ├── coordinator.py     # CoordinatorRunner
│   ├── swarm.py           # SwarmRunner (带 SendMessage + TeamLead)
│   └── fork.py            # (可选) Fork 路径，封装当前 task 工具
├── mailbox/
│   ├── __init__.py
│   ├── base.py            # Mailbox 协议
│   ├── memory.py          # InProcess 内存队列
│   ├── file.py            # 文件信箱：~/.mycode/teams/<name>/inboxes/<agent>.jsonl
│   └── router.py          # SendMessage 智能路由
├── registry/
│   ├── __init__.py
│   ├── agent_registry.py  # 合并 built-in + .mycode/agents/*.md
│   └── flow_registry.py   # 合并 .mycode/orchestrations/*.yaml
└── tools/
    ├── __init__.py
    ├── send_message.py    # SendMessage 工具（仅 Swarm 成员可见）
    ├── team_create.py     # TeamCreate / TeamList / TeamDelete
    └── task_stop.py       # TaskStop（强制终止 Worker）
```

### 4.1 集成到 CLI / API

- CLI 新增：
  ```
  mycode orchestrate run <flow_id> [--vars k=v ...]      # 运行一个编排
  mycode orchestrate list                                # 列出可用编排
  mycode orchestrate inspect <flow_id>                   # 展开拓扑预览
  mycode agent add <path-to-md>                          # 注册自定义 agent
  ```
- API 新增 `/orchestrations` 路由（复用现有 SSE 事件流）。
- Tool 层新增对 `orchestrate` 工具的暴露，使主 Agent 可以**动态选择编排**。

---

## 5. Agent 自定义与复用

### 5.1 声明格式

完全对齐 Claude Code 的 `.claude/agents/*.md` 用法，扩展一个 `mode` 字段标记用途：

```markdown
---
name: db-migrator
description: Database migration specialist
mode: worker            # primary / worker / teammate / fork
model: anthropic/claude-sonnet-4
temperature: 0.2
tools: [bash, read, edit, grep]         # 白名单（MCP 工具默认放行）
permission:
  - { permission: "edit", pattern: "migrations/**", action: "allow" }
  - { permission: "bash", pattern: "alembic *",      action: "allow" }
  - { permission: "*",    pattern: "*",              action: "ask" }
omit_claudemd: true
max_turns: 20
---
You are a database migration expert. Given a schema change request:
1. Inspect `migrations/` to understand the current state ...
2. Generate an Alembic migration ...
3. Never run `downgrade` automatically.
```

**加载优先级**（后者覆盖前者）：
1. 内置（`mycode/agent/prompts/*.txt`，现有）
2. 全局：`~/.mycode/agents/*.md`
3. 项目：`<project>/.mycode/agents/*.md`
4. 编排文件内联（最高，运行时局部有效）

### 5.2 `AgentInfo` 扩展

在 `mycode/agent/agent.py` 的 `AgentInfo` 上追加字段（向后兼容）：

```python
@dataclass
class AgentInfo:
    # ... 现有字段
    mode: Literal["primary", "subagent", "all", "worker", "teammate", "fork"] = "primary"
    tools: list[str] | None = None              # 工具白名单
    omit_claudemd: bool = False                 # 成本优化
    max_turns: int | None = None                # 默认 per-mode 回落
    isolation: Literal["none", "worktree", "process"] | None = None
    background: bool = False                    # 协调器下强制异步
```

### 5.3 复用策略

- **跨项目复用**：放到 `~/.mycode/agents/`。
- **模板化**：`.md` 中支持 Jinja2 占位符 `{{ vars.repo_root }}`，由编排引擎注入。
- **继承**：frontmatter 支持 `extends: other-agent`，深度 ≤3。

---

## 6. 编排（Orchestration）自定义

### 6.1 编排文件示例

`<project>/.mycode/orchestrations/refactor-and-verify.yaml`

```yaml
name: refactor-and-verify
description: 多 Worker 并行重构 + 独立验证
mode: coordinator        # coordinator | swarm | hybrid
model: anthropic/claude-sonnet-4

vars:
  target_module: "mycode/session"

agents:                   # 内联定义 or 引用 registry
  - name: coordinator
    role: coordinator
    prompt_file: prompts/refactor-coordinator.md
    tools: [task, send_message, task_stop]      # 协调者工具集（硬限制）

  - name: explorer
    extends: explore                            # 引用内置
    tools: [read, grep, glob]

  - name: refactorer
    extends: coder
    isolation: worktree                         # 每个 worker 独立 worktree
    max_turns: 25

  - name: verifier
    extends: general
    permission:
      - { permission: "bash", pattern: "pytest *", action: "allow" }

stages:
  - id: research
    parallel: true
    spawn:
      - { agent: explorer, task: "调研 {{ vars.target_module }} 的依赖方向" }
      - { agent: explorer, task: "列出 {{ vars.target_module }} 下所有 TODO" }

  - id: synthesize
    runs_on: coordinator                        # 强制由协调器执行（不可委托）
    inputs: [research.*]

  - id: implement
    parallel: true
    fan_out_from: synthesize                    # 协调器输出切分成 N 份
    spawn:
      - { agent: refactorer, task: "$item" }

  - id: verify
    depends_on: [implement]
    spawn:
      - { agent: verifier, task: "运行 tests/ 下所有测试，报告失败用例" }
```

### 6.2 编排文件示例（Swarm）

`<project>/.mycode/orchestrations/pair-review.yaml`

```yaml
name: pair-review
mode: swarm
lead: reviewer-lead

agents:
  - name: reviewer-lead
    extends: build
    tools: [send_message, team_create, read, grep, task_stop]
  - name: security-reviewer
    prompt_file: prompts/security.md
    tools: [read, grep, glob, send_message]
  - name: perf-reviewer
    prompt_file: prompts/perf.md
    tools: [read, grep, glob, bash, send_message]

backend:                    # 后端选择策略（可省略 → 自动检测）
  prefer: inprocess         # inprocess | tmux | iterm
```

### 6.3 Schema（核心字段）

```python
class AgentSpec(BaseModel):
    name: str
    role: Literal["coordinator", "worker", "teammate", "lead", "fork"] | None = None
    extends: str | None = None
    prompt: str | None = None
    prompt_file: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    permission: list[dict] = []
    isolation: Literal["none", "worktree"] = "none"
    max_turns: int | None = None
    background: bool = False

class StageSpec(BaseModel):
    id: str
    parallel: bool = False
    runs_on: str | None = None          # 强制某个 agent 执行
    depends_on: list[str] = []
    fan_out_from: str | None = None
    inputs: list[str] = []
    spawn: list[SpawnSpec] = []

class OrchestrationSpec(BaseModel):
    name: str
    description: str = ""
    mode: Literal["coordinator", "swarm", "hybrid"] = "coordinator"
    model: str | None = None
    vars: dict[str, Any] = {}
    agents: list[AgentSpec]
    stages: list[StageSpec] = []        # coordinator 模式必填
    lead: str | None = None             # swarm 模式必填
    backend: dict[str, Any] | None = None
```

---

## 7. Coordinator 模式实现细节

### 7.1 运行流程

```
load OrchestrationSpec
   │
   ▼
CoordinatorRunner.start(user_input)
   │
   ├── 构建 coordinator AgentExecutor（工具硬限制为 task/send_message/task_stop）
   ├── 注入 workerToolsContext 到 system prompt
   │     "Workers can use: read, grep, glob, edit, bash, ..."
   │     "Never write 'based on your findings' — be specific"
   │     "Parallelism is your superpower"
   │
   └── 循环：coordinator 产出 tool_call(task / send_message)
         │
         ├── task(description, agent=...) → 派发 Worker（异步）
         ├── send_message(worker_id, ...) → 续聊已有 Worker
         └── task_stop(worker_id) → 终止 Worker
```

### 7.2 关键约束（写入 coordinator 系统提示词）

照搬 Claude Code 的 6 条规则：
1. **You do not execute tools directly.** 仅 `task` / `send_message` / `task_stop` 可用。
2. **Never write "based on your findings"**：综合必须由你做。
3. **Every message is to the user**：`<task-notification>` 不是对话伙伴。
4. **Don't set model on workers**：让他们继承默认。
5. **Include a purpose statement** 在每条 worker prompt 前。
6. **Continue vs Spawn** 决策表（嵌入提示词示例）。

### 7.3 Worker 执行

- 复用 `AgentExecutor`，但 `_tools` 由 `AgentSpec.tools` + `ASYNC_AGENT_ALLOWED_TOOLS` 交集决定。
- **强制异步**：所有 worker 走「启动即返回 + `<task-notification>` 回调」；
  - 成功 / 失败 / 被 stop 三态。
  - 回调通过 `bus.global_emit("orchestration.task_notification", ...)` 广播，Runner 注入到 coordinator messages。
- 采用 `DONT_FORK_GUARD`：worker 不可再调用 `task`/`subagent`（工具过滤第二层）。

### 7.4 并发策略

| 阶段 | 策略 |
|---|---|
| research | 自由并行，受 `semaphore(max_concurrency)` 限流（默认 5） |
| synthesize | 协调器串行（`runs_on: coordinator` 标记） |
| implement | 按 `worktree` 分组，同 worktree 串行，跨 worktree 并行 |
| verify | 与非重叠 implement 可并行 |

### 7.5 结果格式

```xml
<task-notification>
  <task-id>b7e...</task-id>
  <agent>refactorer</agent>
  <status>completed|failed|killed</status>
  <summary>…</summary>
  <result>…</result>
  <usage><tokens>…</tokens><duration_ms>…</duration_ms></usage>
</task-notification>
```

coordinator 的 `messages` 里以 `role=user` 注入（符合 LLM API 约束）。

---

## 8. Swarm 模式实现细节

### 8.1 团队生命周期

```
team_create(name, members=[...]) → 写入 ~/.mycode/teams/<name>/config.json
   │
   ▼
SwarmRunner.bootstrap()
   │   为每个 member 起一个 AgentExecutor，注册到 mailbox router
   │   Leader 获得额外工具：team_create / team_delete / task_stop
   │
   ▼
循环：任一成员产出 tool_call(send_message)
   │
   └── Router.deliver(to, payload)
        ├── InProcess: asyncio.Queue.put_nowait()
        ├── Pane (tmux/iterm): 写文件信箱 + 唤醒对端
        └── Broadcast: 写所有成员信箱
```

### 8.2 SendMessage 工具（新增）

```python
class SendMessageParams(BaseModel):
    to: str                         # agent name or "*"（广播）
    content: str
    kind: Literal["message", "request", "response"] = "message"
    reply_to: str | None = None     # message_id

class SendMessageTool(CallableTool[SendMessageParams]):
    id = "send_message"
    # 仅在 swarm / coordinator 模式下注册
```

系统提示词硬追加约束：

> Plain text output is **not** visible to your teammates.
> You **must** use the `send_message` tool to communicate.

### 8.3 信箱后端选择

完全对齐参考文档：

```
已在 tmux 内 → Tmux 后端
在 iTerm2 内 + it2 可用 → iTerm2 后端
非交互式（pipe / server / ci） → InProcess
其他：tmux 可用 → Tmux，否则 InProcess
```

- **InProcess**：`asyncio.Queue` + `contextvars.ContextVar` 做上下文隔离（Python 等价 AsyncLocalStorage）。
  - 共享 `llm.client` / `mcp.client` 连接池（无状态复用）。
  - 每 member 独立 `asyncio.Event` 作 `AbortController`。
- **File Mailbox**（tmux/iTerm2）：
  - 路径 `~/.mycode/teams/<team>/inboxes/<agent>.jsonl`
  - 用 `fasteners.InterProcessLock` 做原子写入，重试 10 次，指数退避 5-100ms。
  - 文件格式：逐行 JSON，字段 `{id, from, to, kind, content, ts, read}`。

### 8.4 权限冒泡（Leader Permission Bridge）

- InProcess 下，member 的权限请求通过 `bus.emit("permission.request", ...)` 直通 Leader UI。
- 文件后端下，写 `~/.mycode/teams/<t>/permissions/<req_id>.json`，Leader 轮询 + 响应。
- **原则**：权限链终止于人类；无论嵌套多少层都冒泡到交互终端。

### 8.5 Scratchpad（跨 member 知识共享）

- 路径：`~/.mycode/teams/<team>/scratch/`
- 该目录下的 `read/write/edit` 默认 `allow`（在 Agent 权限规则中自动追加一条）。
- 用于在不经 Leader 综合的前提下，member 间共享详细发现。

---

## 9. 复用：Agent / 编排模板

| 来源 | 路径 | 是否内置 |
|---|---|---|
| 内置 Agent | `mycode/agent/prompts/*.txt` + `mycode/agent/agent.py` | 是 |
| 全局 Agent | `~/.mycode/agents/*.md` | 否 |
| 项目 Agent | `<project>/.mycode/agents/*.md` | 否 |
| 内置编排 | `mycode/orchestration/flows/*.yaml`（新增） | 是 |
| 全局编排 | `~/.mycode/orchestrations/*.yaml` | 否 |
| 项目编排 | `<project>/.mycode/orchestrations/*.yaml` | 否 |

**内置编排示例**（随库出厂）：
- `research.yaml`：多 explorer 并行研究 + coordinator 综合（无 implement）
- `refactor-and-verify.yaml`：4 阶段流水线
- `pair-review.yaml`：3 人 swarm code review

复用通过 `extends:` 与 `include:` 实现：

```yaml
# 基于内置，只覆盖部分字段
name: my-refactor
extends: refactor-and-verify
vars:
  target_module: "mycode/tool"
agents:
  - name: refactorer
    model: anthropic/claude-opus-4   # 仅覆盖模型
```

---

## 10. 安全与隔离

1. **工具过滤纵深防御**（四层，完全对照 Claude Code）：
   - L1 元工具黑名单：去掉 `question`、`todo`、`plan_enter/exit`、`team_create/delete`
   - L2 自定义 Agent 额外限制：`.md` 定义的 agent 不能获得 `task` 工具
   - L3 异步 Agent 白名单：强制 `ASYNC_AGENT_ALLOWED_TOOLS` 交集
   - L4 Agent 自身 `disallowed_tools`
2. **上下文隔离**（`createSubagentContext` Python 版）：
   - `readFileState` 克隆
   - `abortController` 新建子控制器（父中断→子中断，反之不传播）
   - `get_app_state` 包装为 `should_avoid_permission_prompts=True`（后台 agent）
   - `set_app_state_for_tasks` **始终共享**（否则后台 bash 变僵尸）
   - `query_tracking = {chain_id: uuid4(), depth: parent.depth+1}`
3. **深度限制**：`depth > 3` 直接拒绝，防止无限嵌套。
4. **Prompt Injection 分类器**：可选开关 `TRANSCRIPT_CLASSIFIER=1`，在把 Worker 结果注入 coordinator 前做一次轻量 LLM 分类（纵深防御）。

---

## 11. 事件与可观测性

复用 `mycode.bus`，新增事件：

| 事件 | 负载 | 用途 |
|---|---|---|
| `orchestration.started` | `{flow_id, run_id}` | UI 展开拓扑 |
| `orchestration.stage_started` | `{run_id, stage_id}` | 阶段进度 |
| `orchestration.worker_spawned` | `{run_id, agent, task_id}` | 实时 badge |
| `orchestration.worker_message` | `{task_id, delta}` | 子 agent 流式输出 |
| `orchestration.task_notification` | `<task-notification>` payload | coordinator 消费 |
| `orchestration.mailbox_sent` | `{from, to, kind}` | swarm 可视化 |
| `orchestration.finished` | `{run_id, status, cost}` | 汇总 |

API：`GET /orchestrations/{run_id}/events` → SSE 复用 `server/events.py`。

---

## 12. 持久化

- **运行记录**：新增 SQLAlchemy 表 `OrchestrationRun(id, flow_id, status, started_at, finished_at, cost, ...)`；子表 `OrchestrationTask(run_id, agent, status, input, output, tokens, duration_ms)`。
- **信箱**：运行期存内存/文件；结束时归档到 `session.archive`。
- **团队配置**：`~/.mycode/teams/<team>/config.json`（常驻，跨 session）。

---

## 13. 分阶段实施路线（TODO）

| 阶段 | 内容 | 产出 |
|---|---|---|
| M1 | `orchestration/topology` + schema + loader + CLI `orchestrate list/inspect` | 能加载 YAML 并打印拓扑 |
| M2 | `orchestration/registry.agent_registry` + `AgentInfo` 字段扩展 + `.md` frontmatter 支持 | `mycode agent add` 可用 |
| M3 | `orchestration/runtime.executor`（抽出公共子 agent loop） | 现有 `task`/`subagent` 迁移为薄封装 |
| M4 | **Coordinator 模式** 完整实现 + 内置 `research.yaml` + SSE 事件 | `mycode orchestrate run research` 跑通 |
| M5 | `mailbox.memory` + `SendMessage` 工具 + InProcess 后端 | `pair-review.yaml` 跑通（单进程） |
| M6 | `mailbox.file` + tmux 后端 + Leader Permission Bridge | 多窗格可视化 |
| M7 | 安全：工具过滤四层 + 上下文隔离 + 深度限制 + 分类器开关 | 通过 `tests/test_orchestration_security.py` |
| M8 | 持久化 + 成本统计 + 文档 & 示例 | v1 发布 |

每个 M 完成后做一次 `git commit`（遵循仓库规范）。

---

## 14. 与现有 `task` / `subagent` 工具的关系

| 工具 | 继续存在 | 语义 |
|---|---|---|
| `task` | ✅ | 等价于「Fork 子 Agent」的轻量形态（单一 agent，无编排） |
| `subagent.delegate` | ✅ | 同上，带 context |
| `subagent.parallel` | ⚠️ 折叠为 orchestration | 后续通过 `mycode orchestrate run parallel-research --vars tasks=[...]` 替代；保留一个 release 过渡期 |
| `subagent.isolated` | ✅ | 单 worker + worktree 场景仍然用它 |
| **`orchestrate`（新）** | ✅ | 主 Agent 可用的编排工具，参数为 `flow_id` + `vars` |

主 Agent 提示词里加一段选择指南：

```
选择多 Agent 模式：
- 独立小任务：task
- 单 worker + 隔离写：subagent(isolated)
- 多 worker 带综合：orchestrate(<coordinator flow>)
- 长期协作团队：orchestrate(<swarm flow>)
```

---

## 15. 设计洞察（摘自参考文档并本地化）

1. **协调器不执行是硬约束**——工具层强制，不靠模型自律。
2. **"Never write based on your findings"**——综合能力不可委托，这是协调器的全部价值。
3. **Continue vs Spawn 取决于上下文重叠度**——高重叠续聊，低重叠新开，避免认知锚定。
4. **AbortController 独立性**保证单 worker 崩溃不连锁。
5. **后端自动降级**：tmux > iTerm2 > InProcess，永不报错退出。
6. **Scratchpad 解决 worker 间的信息损耗**，跳过协调器的"理解和转述"。
7. **上下文隔离 deny-by-default**：共享必须显式 opt-in。
8. **四层工具过滤实现纵深防御**。
9. **Fork 本质是缓存优化**（Python 侧暂不做 Prompt Cache 字节共享，留后续优化点）。

---

## 附录 A：最小可用 YAML（M4 里程碑示例）

```yaml
name: research
mode: coordinator
agents:
  - { name: coordinator, role: coordinator, tools: [task, send_message, task_stop] }
  - { name: explorer, extends: explore }
stages:
  - id: research
    parallel: true
    spawn:
      - { agent: explorer, task: "{{ vars.q1 }}" }
      - { agent: explorer, task: "{{ vars.q2 }}" }
  - id: synthesize
    runs_on: coordinator
    inputs: [research.*]
```

运行：

```bash
uv run mycode orchestrate run research \
  --vars q1="数据库层依赖方向" \
  --vars q2="找出所有 N+1 查询"
```

## 附录 B：相关源文件锚点

- 参考原文：`docs/07-multi-agent.md` @ Windy3f3f3f3f/how-claude-code-works
- 关键 Claude Code 源文件引用：
  - `src/tools/AgentTool/AgentTool.tsx`
  - `src/coordinator/coordinatorMode.ts`
  - `src/utils/swarm/backends/*.ts`
  - `src/utils/forkedAgent.ts`
  - `src/tools/AgentTool/agentToolUtils.ts`
