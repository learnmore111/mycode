# MyCode 多 Agent 编排系统 — 用户使用指南

> **适用版本**: MyCode 0.1+  
> **最后更新**: 2026-04-30

---

## 目录

- [一、系统架构总览](#一系统架构总览)
- [二、Agent 系统](#二agent-系统)
- [三、Flow 编排流](#三flow-编排流)
- [四、三类多 Agent 模式](#四三类多-agent-模式)
- [五、Subagent 工具（传统路径）](#五subagent-工具传统路径)
- [六、内置 Flow 示例](#六内置-flow-示例)
- [七、运行时操作](#七运行时操作)
- [八、Web 工作台使用](#八web-工作台使用)
- [九、常见问题与最佳实践](#九常见问题与最佳实践)

---

## 一、系统架构总览

```
CLI / HTTP API / Web 工作台
        │
        ▼
  Flow / Agent Registry
        │
  Loader + Validator + Resolver
        │
  ┌─────┴──────────┐
  │                  │
Coordinator        Swarm
(DAG 执行器)     (邮箱驱动 P2P)
  │                  │
  └─────┬──────────┘
        ▼
 LLM Runner + Tool + Permission
```

本项目存在 **两套并行的多 Agent 机制**：

| 路径 | 入口 | 特点 |
|------|------|------|
| **subagent 工具** | `task` / `subagent` 工具 | 主会话内一次性委派（3 种模式） |
| **编排子系统** | `mycode/orchestration/` | 基于 Flow YAML 的独立执行框架 |

编排子系统面向用户定位为 **三类多 Agent 模式**。底层 YAML 仍使用 `mode` 字段，以兼容现有实现：

| 产品定位 | 底层 `mode` | 核心心智 | 适合场景 |
|----------|-------------|----------|----------|
| **工作流式** | `coordinator` | 明确阶段 / DAG / coordinator 分派与汇总 | 研究流水线、批量探索、分阶段综合 |
| **主管协作式** | `hybrid` | supervisor / orchestrator 接收任务、组织专家协同并最终综合 | 评审会、方案共创、需要单一负责人拍板的多角色校验 |
| **Swarm 式** | `swarm` | 去中心化 peer-to-peer，成员通过消息自由推进 | 动态协作、开放式 review、无需预设阶段的讨论 |

### 如何选择？

| 场景 | 推荐方式 |
|------|---------|
| 单个复杂多步骤任务 | `subagent` delegate 模式 |
| 多个独立的搜索/研究任务 | `subagent` parallel 模式 |
| 需要安全隔离的文件修改 | `subagent` isolated 模式 |
| 已知拓扑的多阶段流水线（研究→综合） | 工作流式 Flow YAML |
| 有主管 / 协调者组织专家产出 | 主管协作式 Flow YAML |
| 多角色动态协作（代码审查、头脑风暴） | Swarm 式 Flow YAML |

---

## 二、Agent 系统

### 2.1 内置 Agent（8 个）

来源文件：`mycode/agent/agent.py`

| 名称 | 模式 (mode) | 隐藏 | 角色定位 | 用途 |
|------|------------|------|---------|------|
| `build` | **primary** | 否 | 默认主代理 | 全权限默认入口，执行工具 |
| `plan` | **primary** | 否 | 计划模式 | 只读规划，**禁止编辑** |
| `general` | **subagent** | 否 | 通用子代理 | 多步骤研究/执行任务 |
| `explore` | **subagent** | 否 | 探索子代理 | 只读代码搜索专家 |
| `coder` | **subagent** | 否 | 写入子代理 | 在 git worktree 中安全修改文件 |
| `compaction` | primary | **是** | 内部 | 对话压缩 |
| `title` | primary | **是** | 内部 | 生成标题 |
| `summary` | primary | **是** | 内部 | 生成摘要 |

### 2.2 Agent 模式说明

| 模式 | 值 | 说明 |
|------|---|------|
| `primary` | 主代理 | 直接处理用户请求，可选作入口 |
| `subagent` | 子代理 | 由主代理通过 task/subagent 启动，不可直接选择 |
| `all` | 双模式 | 可作为主代理或子代理 |

### 2.3 Agent 角色类型 (role)

在编排 Flow 中，Agent 可声明以下角色：

| role | 适用模式 | 含义 |
|------|---------|------|
| `coordinator` | coordinator | **中央协调者** — 汇总 worker 输出，撰写最终报告（Orchestrator-Worker 模式） |
| `worker` | coordinator | **工作节点** — 执行具体研究/编码任务 |
| `teammate` | swarm | **队友** — P2P 协作的平等参与者 |
| `entry` / `lead` | swarm | **入口 Agent** — 接收初始任务的 agent（非中央控制器！只是第一个接收者） |
| `fork` | — | 分支角色 |

> **重要区别**：`coordinator` 是真正的中央控制器；而 `entry/lead` 只是初始任务接收者，swarm 中所有 peer 地位平等。

### 2.4 Agent 继承机制 (`extends`)

Agent 可以继承其他 Agent 的定义：

```yaml
# .mycode/agents/my-reviewer.md
---
extends: explore                  # 继承 explore 的所有配置
role: reviewer                   # 覆盖角色
tools: [read, grep, glob]        # 替换父级 tools 列表
temperature: 0.2                 # 覆盖温度
permission:
  - { permission: edit, pattern: "*", action: deny } # 追加权限规则
---
你是一个代码审查专家...
```

**合并规则**：
- 标量字段：子级显式设置则覆盖父级
- `permission`：父级规则在前，子级追加（先匹配先生效）
- `options`：浅合并 dict
- `tools`：子级整体替换父级

### 2.5 Isolation（隔离级别）

| 值 | 含义 |
|----|------|
| `none` | 无隔离，默认行为 |
| `worktree` | 在 git worktree 中执行（隔离文件修改） |
| `container` | 容器隔离（未来支持） |

### 2.6 自定义 Agent 定义

自定义 Agent 使用 **Markdown + YAML frontmatter** 格式，存储在以下目录之一：

1. **全局**: `~/.mycode/agents/*.md` （跨项目共享）
2. **项目级**: `<project>/.mycode/agents/*.md` （项目专属）
3. **配置层**: `mycode.json` 中的 `agent:` 字段

完整示例：

```markdown
---
description: 安全审计专家
mode: subagent                    # subagent / primary / all
role: security-auditor            # 自定义角色标识
extends: explore                  # 继承内置 explore agent
tools: [read, grep, glob]         # 允许的工具白名单
max_turns: 20                     # 最大轮次
isolation: none                   # none / worktree / container
model: anthropic/claude-sonnet-4   # 指定模型
temperature: 0.2                 # 温度参数
omit_claudemd: false              # 是否省略 CLAUDE.md
color: red                       # UI 颜色
permission:
  - { permission: edit, pattern: "*", action: deny }
  - { permission: bash, pattern: "*", action: "ask" }
---
你是一个安全审计专家。专注于：
- SQL 注入、XSS、CSRF
- 认证与授权缺陷
- 敏感数据泄露
请用中文输出报告。
```

---

## 三、Flow 编排流

### 3.1 两种运行模式对比

| 特性 | Coordinator 模式 | Swarm 模式 |
|------|-----------------|------------|
| 执行模型 | DAG 拓扑排序，中央调度 | P2P 邮箱消息驱动 |
| 适用场景 | 结构化的多阶段流水线 | 动态团队协作 |
| 核心概念 | stages + spawns + depends_on | agents + send_message |
| Agent 数量 | >= 1（需一个 coordinator） | >= 2 |
| 需要 stages? | **必须** | **禁止** |
| 需要 entry? | 可选（用 coordinator 替代） | 可选（默认首个 agent） |
| 依赖关系 | 显式 depends_on + 隐式顺序 | 通过消息动态协调 |
| 并行控制 | `parallel: true` + `max_concurrency` | 异步 Task 自然并行 |
| 典型用途 | 研究→综合、代码审查流水线 | 多角色 code review、头脑风暴 |

### 3.2 Coordinator 模式的 Stage 类型

#### 类型 1：普通 Spawn Stage（最常用）

```yaml
stages:
  - id: research
    parallel: true              # 并行执行所有 spawns
    max_concurrency: 4          # 最大并发数
    spawn:
      - agent: explorer
        task: "分析 API 层的认证机制"
      - agent: explorer
        task: "检查数据库查询性能"
    depends_on: []              # 无依赖（第一stage）
```

#### 类型 2：Fan-out Stage（动态扩展）

将上游 stage 输出拆分为 N 个独立任务：

```yaml
  - id: deep-dive
    fan_out_from: research      # 从 research stage 的每个成功输出发起
    spawn:
      - agent: coder
        task: "深入修复以下问题：{{ $item }}"
```

- `{{ $item }}` = 上游每个 spawn 的 output 文本
- `{{ $index }}` = 从 0 开始的索引

#### 类型 3：Coordinator Stage（runs_on）

由 coordinator agent 综合前序结果：

```yaml
  - id: synthesize
    runs_on: coordinator       # 由 coordinator agent 执行
    depends_on: [research]     # 依赖 research stage
    inputs: [research.*]       # 收集 research 所有成功的 spawn 输出
    prompt: |
      将以上研究发现整合为一份结构化报告。
      保持各视角独立，不要合并矛盾观察。
```

### 3.3 Stage 完整字段参考

```typescript
interface StageSpec {
  id: string                          // 唯一 ID
  description?: string                // 描述
  parallel: boolean                   // 是否并行执行 spawns
  max_concurrency?: number            // 并行上限（默认 5）
  runs_on?: string                    // 设为 agent 名 → 变为 coordinator stage
  fan_out_from?: string               // 设为上游 stage ID → 变为 fan-out stage
  depends_on: string[]                // 依赖的前序 stage IDs（默认依赖前一个）
  inputs: string[]                    // 输入来源（如 ["research.*"]）
  spawn: Array<{                     // 任务列表
    agent: string                    // 执行该任务的 agent 名
    task: string                     // 任务描述（支持 {{ vars.xxx }})
    vars?: Record<string, any>       // 传递给 agent 的变量
    timeout_seconds?: number          // 超时
  }>
  prompt?: string                     // runs_on stage 的 prompt
}
```

### 3.4 依赖关系与 DAG

**隐式顺序依赖**：未声明 `depends_on` 的 stage 自动依赖前一个 stage。

**显式依赖**：
```yaml
stages:
  - id: fetch          # 第一阶段
    spawn: [{ agent: explorer, task: "获取数据" }]
  
  - id: process        # 自动依赖 fetch（隐式）
    spawn: [{ agent: coder, task: "处理数据" }]
  
  - id: report         # 显式依赖 fetch（跳过 process）
    depends_on: [fetch]
    runs_on: coordinator
    inputs: [fetch.*]
```

**DAG 校验**：系统自动检测 `depends_on` + `fan_out_from` 形成的环。

### 3.5 Vars 变量系统

在 flow 顶层定义变量，在 spawn task / prompt 中引用：

```yaml
name: my-flow
vars:
  target_dir: "src/api"
  query_pattern: "TODO|FIXME"

stages:
  - id: search
    spawn:
      - agent: explore
        task: "在 {{ target_dir }} 中搜索 {{ query_pattern }}"
```

**运行时覆盖**：
```bash
# CLI
mycode orchestrate run my-flow --vars target_dir="src/core"

# HTTP API
POST /orchestration/run
{ "flow": "my-flow", "vars": { "target_dir": "src/core" } }
```

**模板语法**：支持 `{{ vars.key }}` 和简写 `{{ key }}`，也支持嵌套属性访问。

### 3.6 extends 流程继承

Flow 也可以继承其他 Flow：

```yaml
name: my-research
extends: research          # 继承内置 research.yaml
vars:
  q1: "我的自定义问题1"    # 覆盖父级变量
stages:
  - id: extra-step        # 追加新阶段
    spawn: [{ agent: explorer, task: "额外检查" }]
```

合并规则：dict 深合并，keyed list 按 name/id 合并。

---

## 四、三类多 Agent 模式

### 4.1 工作流式

工作流式对应底层 `mode: coordinator`。它强调明确阶段、DAG 拓扑和 coordinator 汇总，适合“先并行探索，再 fan-out 深挖，最后综合报告”这类确定流程。

### 4.2 主管协作式

主管协作式对应底层 `mode: hybrid`。它由一个 supervisor / orchestrator Agent 接收初始任务，通过 `send_message` 组织多个专家协作，专家可以用 `main` 回到主管，最后优先展示主管的综合输出。它不再复用工作流式的 stage DAG。

### 4.3 Swarm 式

Swarm 式对应底层 `mode: swarm`。它是去中心化 P2P 邮箱协作，没有预设 stage DAG；`entry` 只是初始任务接收者，不是中央控制器。

### 4.4 Swarm 细节

### 4.1 核心概念

Swarm 是**去中心化 P2P** 架构，没有中央控制器：

```
用户任务
  │
  ▼
Entry Agent (reviewer-starter)
  │  ├─ send_message ──► security-reviewer ( teammate )
  │  │                      ├─ send_message ──► reviewer-starter
  │  │                      └─ ...
  │  │
  │  └─ send_message ──► perf-reviewer ( teammate )
  │                         ├─ send_message ──► reviewer-starter
  │                         └─ ...
  │
  ▼
 最终报告（由 entry agent 产出）
```

### 4.2 send_message 工具

Swarm 中每个 agent 都有一个特殊的 `send_message` 工具：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 否 | `message`(默认) / `broadcast` / `shutdown_request` / `shutdown_response` |
| `recipient` | string | message 时必填 | 目标 agent 名，`'main'` = entry agent |
| `content` | string | 否 | 消息正文 |
| `summary` | string | 否 | 5–10 词摘要（UI 用） |

**消息类型**：
- **message**: 点对点消息
- **broadcast**: 广播给除自己外的所有 peer
- **shutdown_request**: 请求结束协作（对方可拒绝）
- **shutdown_response**: 同意/拒绝 shutdown

### 4.3 终止条件

Swarm 在以下任一条件满足时结束：

1. **和平退出**：entry agent 不再有 tool call + inbox 为空（静默超时 ~2s）
2. **协商关闭**：某 peer 发 shutdown_request，所有人同意
3. **预算耗尽**：达到 `max_turns` 或 `walltime_seconds` 上限

### 4.4 Mailbox 后端

| 后端 | 值 | 说明 |
|------|---|------|
| `auto` / `inprocess` | 默认 | asyncio.Queue，同进程内存 |
| `file` | 文件系统 | JSONL + 文件锁，可跨进程 |
| `tmux` | Tmux 终端 | 通过 tmux pane 通信 |
| `iterm` | iTerm2 | 通过 iTerm2 API 通信 |

```yaml
backend:
  prefer: auto             # auto / inprocess / file / tmux / iterm
  root_dir: /tmp/swarm    # file/tmux/iterm 模式需要
```

---

## 五、Subagent 工具（传统路径）

`subagent` 工具提供 3 种执行模式，适合不需要完整 Flow 编排的简单场景：

| 模式 | 适用场景 | 默认轮次上限 | 可用 Agent |
|------|---------|-------------|-----------|
| `delegate` | 单个复杂多步骤任务 | 12 (max 30) | general, explore, coder |
| `parallel` | 多个独立研究/搜索任务 | 8 (max 15) | **仅 explore, general** |
| `isolated` | 需要安全修改文件的实验性改动 | 20 (max 30) | coder, build, general |

### 调用示例

```python
# Delegate 模式
subagent(description="重构 auth 模块", agent="coder", context="当前使用 JWT...", max_turns=20)

# Parallel 模式
subagent(
    mode="parallel",
    agent="explore",
    tasks=["分析 API 层", "检查数据库层", "审查认证逻辑"],
    max_concurrency=3
)

# Isolated 模式（git worktree 隔离）
subagent(
    mode="isolated",
    agent="coder",
    description="优化数据库查询",
    auto_merge=True  # 完成后自动合入主分支
)
```

> **被禁止的工具**：`subagent`, `todo`, `question`, `batch`（防止递归和交互问题）

---

## 六、内置 Flow 示例

### 6.1 research.yaml（Coordinator 模式）

```yaml
name: research
mode: coordinator
vars:
  q1: "描述代码库结构与主要子系统"
  q2: "识别最值得关注的维护风险或 TODO 热点"

agents:
  - name: coordinator
    extends: build
    role: coordinator
    tools: [read, grep]
    prompt: |
      你是协调者。不要重复 worker 的探索工作，
      而是比较、归纳、指出分歧，并给出后续建议。

  - name: explorer
    extends: explore          # 继承内置 explore
    role: worker

stages:
  - id: research
    parallel: true
    max_concurrency: 4
    spawn:
      - agent: explorer
        task: "{{ vars.q1 }}"
      - agent: explorer
        task: "{{ vars.q2 }}"

  - id: deep-dive
    fan_out_from: research
    spawn:
      - agent: explorer
        task: |
          基于以下前序发现，补充一个最值得继续深挖的点，并解释原因：

          {{ $item }}

  - id: synthesize
    runs_on: coordinator
    depends_on: [deep-dive]
    inputs: [research.*, deep-dive.*]
    prompt: |
      输出一份 markdown 研究简报，包含：
      1. 系统概览
      2. 关键风险 / 热点
      3. 重要未知项
      4. 建议下一步
```

**执行流程**：
```
[research stage] ─┬─► explorer: 结构与子系统
                  └─► explorer: 风险与 TODO 热点
                        │
                        ▼
             [deep-dive stage] ─► explorer: 补充后续调查点
                        │
                        ▼ (depends_on + inputs)
              [synthesize stage] ─► coordinator: 综合简报
```

### 6.2 supervised-review.yaml（主管协作式）

```yaml
name: supervised-review
mode: hybrid
coordinator: review-supervisor

agents:
  - name: review-supervisor
    extends: build
    role: coordinator
    tools: [send_message, read, grep, glob]
    prompt: |
      你是主管。先把任务分派给架构专家和风险专家，
      等两者都回复后，再输出 ship / follow-up / block 决策。

  - name: architecture-reviewer
    extends: explore
    role: teammate
    tools: [read, grep, glob, send_message]
    prompt: |
      你是架构专家，关注模块边界、耦合、数据流和扩展性。
      将发现发送给 main。

  - name: risk-reviewer
    extends: explore
    role: teammate
    tools: [read, grep, glob, bash, send_message]
    prompt: |
      你是风险专家，关注回归、缺失测试、权限边界和发布风险。
      将发现发送给 main。
```

**协作流程**：
```
用户任务 ─► review-supervisor
              ├─► architecture-reviewer ─┐
              └─► risk-reviewer ─────────┤
                    ▲                     │
                    └── 可互相询问 ───────┘
                         │
                         ▼
              review-supervisor: 最终决策与综合
```

### 6.3 pair-review.yaml（Swarm 模式）

```yaml
name: pair-review
mode: swarm
entry: reviewer-starter

agents:
  - name: reviewer-starter
    extends: build
    role: entry
    tools: [send_message, read, grep, glob]
    prompt: |
      你是 swarm code review 的起始 peer。
      先把任务拆给不同专家，并要求专家之间直接交叉验证。
      注意：entry 不是中央主管，只是初始任务接收者。

  - name: security-reviewer
    extends: explore
    role: teammate
    tools: [read, grep, glob, send_message]
    prompt: |
      你是安全专家。关注 auth、secrets、输入校验、
      注入与权限绕过；必要时可直接联系 perf-reviewer 交叉确认。

  - name: perf-reviewer
    extends: explore
    role: teammate
    tools: [read, grep, glob, bash, send_message]
    prompt: |
      你是性能专家。关注不必要的 I/O、热循环、广域扫描、
      启动成本和并发瓶颈；必要时与 security-reviewer 互相确认假设。
```

---

## 七、运行时操作

### 7.1 CLI 命令

```bash
# 列出所有 flows
mycode orchestrate list

# 查看 flow 详情
mycode orchestrate inspect research

# 运行 coordinator flow
mycode orchestrate run research --vars q1="我的问题" --dry-run  # 试运行
mycode orchestrate run research --json                     # JSON 输出

# 运行 swarm flow
mycode orchestrate run pair-review --task "审查 src/auth/" \
    --max-turns 15 --walltime 600

# 运行主管协作式 flow
mycode orchestrate run supervised-review --task "评审最近的 API 改动" \
    --max-turns 15 --walltime 600

# 列出 agents
mycode orchestrate list-agents
```

### 7.2 HTTP API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/orchestration/flow` | 列出 flows |
| GET | `/orchestration/flow/{name}` | 获取 flow 详情 |
| POST | `/orchestration/flow` | 创建 flow |
| PUT | `/orchestration/flow/{name}` | 更新 flow |
| DELETE | `/orchestration/flow/{name}` | 删除 flow |
| GET | `/orchestration/agent` | 列出 agents |
| POST | `/orchestration/agent` | 创建 agent (.md) |
| PUT | `/orchestration/agent/{name}` | 更新 agent |
| DELETE | `/orchestration/agent/{name}` | 删除 agent |
| POST | `/orchestration/run` | **启动运行**（异步） |
| GET | `/orchestration/run` | 列出运行记录 |
| GET | `/orchestration/run/{id}` | 获取运行详情 |
| POST | `/orchestration/run/{id}/cancel` | 取消运行 |
| GET | `/orchestration/events` | **SSE 事件流** |

**启动运行请求体**：
```json
{
  "flow": "research",
  "task": "审查用户认证模块",
  "vars": { "q1": "自定义问题" },
  "max_turns": 10,
  "walltime_seconds": 300.0,
  "directory": "/path/to/project"
}
```

### 7.3 SSE 事件类型

| 事件类型 | 触发时机 | 关键字段 |
|---------|---------|---------|
| `orchestration.flow.started` | flow 开始执行 | mode, agents, stage_count |
| `orchestration.flow.finished` | flow 执行完毕 | ok, duration_seconds |
| `orchestration.stage.started` | stage 开始 | stage_id, parallel, runs_on |
| `orchestration.stage.finished` | stage 结束 | is_error, spawn_count, ok_count |
| `orchestration.spawn.started` | 单个 spawn 开始 | agent, task |
| `orchestration.spawn.finished` | 单个 spawn 结束 | is_error, turns, tool_calls |
| `orchestration.swarm.started` | swarm 开始 | lead, peers, user_task |
| `orchestration.swarm.finished` | swarm 结束 | terminated_reason, peer_count |
| `orchestration.message.sent` | swarm 消息发送 | sender, recipient, kind |

前端可通过 `GET /orchestration/events?run_id=xxx` 订阅过滤后的事件流。

---

## 八、Web 工作台使用

1. 启动开发服务器：`uv run mycode dev`
2. 浏览器打开前端界面
3. 左侧导航进入 **Orchestration** 功能区
4. **Agent 管理**：创建/编辑/删除自定义 agent（Markdown frontmatter 编辑器）
5. **Flow 管理**：创建/编辑/删除编排 flow（YAML 编辑器 + 表单视图）
6. **DAG 图视图**：Coordinator 模式的 stages 可切换为可视化 DAG 编辑器（表单/DAG 图按钮），支持：
   - 节点拖拽定位
   - 连线创建与编辑（从输出节点拖拽到输入节点）
   - 节点类型区分（普通 Stage / Fan-out / Coordinator）
   - 双向数据同步（DAG 操作 ↔ YAML 数据）
7. **运行**：填写 vars 参数后点击运行，实时查看 SSE 事件流和 stage 进度

---

## 九、常见问题与最佳实践

### Q1: Coordinator 和 Swarm 该如何选择？

- 如果你的任务可以**预先分解为固定阶段** → 用 **Coordinator**
- 如果你的任务需要 agents 之间**动态协商和多次交互** → 用 **Swarm**
- 如果只是**一次性派发子任务** → 用 **subagent** 工具即可

### Q2: 如何避免 Agent 死循环？

系统内置三层防护：
1. **硬限制**：`max_turns` / `walltime_seconds` 上限
2. **模式检测**：检测重复工具调用序列
3. **LLM 智能**：LLM 自检是否陷入循环

建议合理设置 `max_turns`（通常 10-30 足够）。

### Q3: 如何复用已有的 Agent 定义？

使用 `extends` 关键字继承内置或自定义 Agent：

```yaml
agents:
  - name: my-coder
    extends: coder           # 继承内置 coder
    model: gpt-4o            # 覆盖模型
    temperature: 0.1         # 覆盖温度
```

### Q4: Fan-out Stage 的典型用途？

当上游 Stage 的输出数量**不确定**时使用：
- 代码审查：每个文件一个 review task
- Bug 修复：每个发现的问题一个 fix task
- 数据处理：每条记录一个处理 task

### Q5: Swarm 中 Entry Agent 和 Coordinator 有什么区别？

| | Coordinator | Swarm Entry |
|--|------------|-------------|
| 控制权 | 中央调度所有 stage | 仅接收初始任务 |
| 生命周期 | 贯穿整个 flow | 可能提前退出 |
| worker 关系 | 主从关系 | 平等 P2P 关系 |
| 消息模式 | 单向分派+收集 | 双向任意通信 |

### Q6: 生产环境推荐配置

```yaml
# 推荐的生产环境 Flow 配置骨架
name: production-pipeline
mode: coordinator

vars:
  max_retries: 3
  timeout: 300

agents:
  - name: coordinator
    role: coordinator
    tools: [task, send_message, read]
    model: anthropic/claude-sonnet-4   # 使用稳定模型

stages:
  - id: analyze
    parallel: true
    max_concurrency: 4               # 控制并发防过载
    spawn:
      - agent: explorer
        task: "分析 {{ target_module }}"
        timeout_seconds: 120          # 单任务超时

  - id: validate
    runs_on: coordinator
    depends_on: [analyze]
    inputs: [analyze.*]
    prompt: "验证所有发现的有效性..."
```
