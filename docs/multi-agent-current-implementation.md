# 当前多 Agent 架构实现分析

> 本文档基于当前仓库代码现状编写，**只描述已经存在并可从代码中确认的实现**。
>
> 如果只想快速理解结构，可以先看 `docs/multi-agent-architecture.md`；如果要继续读源码，这份文档更适合作为入口索引。

---

## 1. 一句话结论

当前仓库里的多 Agent 能力，实际上由两条并行路径组成：

1. **传统子代理路径**：主会话里的 `task` 工具拉起一个短生命周期子 Agent。
2. **独立编排子系统**：`mycode/orchestration/` 提供 flow、registry、runtime、CLI、HTTP API 与 Web 工作台。

因此，项目现在并不是“只有一个统一多 Agent 引擎”，而是：**`task` 子代理体系与 `orchestration` 编排体系并行存在**。

---

## 2. 顶层结构概览

从当前代码看，多 Agent 相关能力大致可以分为五层：

```text
CLI / HTTP API / Web Workbench
            │
            ▼
   Flow / Agent Registry
            │
            ▼
  Loader + Validator + Resolver
            │
            ▼
 Coordinator Runtime / Swarm Runtime
            │
            ▼
  LLM Runner + Tool Registry + Permission
```

对应代码位置：

- **Agent 基础定义**：`mycode/agent/agent.py`
- **编排系统**：`mycode/orchestration/`
- **HTTP 路由**：`mycode/server/routes/orchestration.py`
- **CLI 命令**：`mycode/cli/main.py`
- **前端工作台**：`web/src/components/OrchestrationWorkbench.tsx`
- **传统子代理工具**：`mycode/tool/task.py`

---

## 3. Agent 定义与加载机制

### 3.1 统一数据结构：`AgentInfo`

`mycode/agent/agent.py` 中的 `AgentInfo` 是整个系统共享的 Agent 数据结构。当前编排能力依赖的关键字段包括：

- `role`
- `tools`
- `extends`
- `max_turns`
- `isolation`
- `omit_claudemd`
- `source`
- `source_path`

这说明当前仓库已经把普通 Agent 与编排用 Agent 统一到了同一个数据模型里。

### 3.2 内置 Agent

内置 Agent 定义仍然来自 `mycode/agent/agent.py` 的 `_build_agents()`，主要包括：

- `build`
- `plan`
- `general`
- `explore`
- `coder`
- `compaction`
- `title`
- `summary`

其中：

- `build` / `plan` 是主代理入口
- `general` / `explore` / `coder` 更适合作为子代理或编排节点
- `compaction` / `title` / `summary` 是内部用途 Agent

### 3.3 Agent 的多层来源

当前 Agent 采用多层发现与覆盖模型：

1. **builtin**：内置 Agent
2. **config**：`mycode.json` 中的 Agent 配置
3. **global**：`~/.mycode/agents/*.md`
4. **project**：`<project>/.mycode/agents/*.md`

相关逻辑由两部分协同完成：

- `mycode/agent/agent.py`：提供系统最终可见的 Agent 列表
- `mycode/orchestration/registry/agent_registry.py`：发现、解析、继承和覆盖自定义 Agent

### 3.4 Markdown Agent 的解析与继承

`AgentRegistry` 支持将 `.md` 文件解析为带 frontmatter 的 Agent 定义，并支持 `extends` 继承。当前可确认的能力包括：

- `description`
- `mode`
- `role`
- `extends`
- `tools`
- `max_turns`
- `isolation`
- `omit_claudemd`
- `model`
- `permission`

继承合并规则也已在代码中落地：

- 标量字段：子级显式覆盖父级
- `permission`：父级在前，子级追加
- `options`：浅合并
- `tools`：子级整体替换父级

---

## 4. Flow 规格、加载与校验

### 4.1 Flow 的发现范围

`mycode/orchestration/registry/flow_registry.py` 会从三层目录发现 flow：

1. `mycode/orchestration/flows/`
2. `~/.mycode/orchestrations/`
3. `<project>/.mycode/orchestrations/`

后出现的来源覆盖前面的同名 flow。

### 4.2 Flow Schema

flow 的核心 schema 位于 `mycode/orchestration/topology/schema.py`，顶层是 `OrchestrationSpec`。当前实现中常用的关键字段包括：

- `mode`
- `agents`
- `stages`
- `lead`
- `backend`
- `vars`
- `extends`

### 4.3 Loader：变量替换与 `extends`

`mycode/orchestration/topology/loader.py` 负责：

1. 读取 YAML / JSON
2. 处理 `extends`
3. 渲染 `{{ vars.xxx }}` / `{{ xxx }}` 变量

它的实现特点包括：

- 不依赖 Jinja2
- 使用轻量模板替换
- 支持 keyed list merge
- 支持通过 `vars_override` 覆盖 flow 内部变量

### 4.4 Validator：声明层校验较完整

`mycode/orchestration/topology/validator.py` 已经实现了较完整的语义校验，包括：

- agent 名称唯一
- stage id 唯一
- `spawn.agent` 必须存在
- `runs_on` / `lead` / `fan_out_from` 必须存在
- `depends_on` 的 DAG 无环
- mode 相关约束
- 未解析变量占位符检查
- `agent.extends` 的解析校验

---

## 5. Coordinator 运行时

### 5.1 运行模型

`mycode/orchestration/runtime/coordinator.py` 中的 Coordinator 会：

1. 对 stage 做拓扑排序
2. 依次执行每个 stage
3. 把结果写入 `RunContext`
4. 返回最后一个 stage 的输出

当前支持的 stage 类型主要有三种：

- **普通 spawn stage**：执行 `spawn` 列表中的一个或多个任务
- **fan-out stage**：把上游输出扩展成多个任务
- **runs_on stage**：由指定 agent 执行综合或撰写任务

### 5.2 默认顺序依赖

`_topo_sort()` 除了显式 `depends_on` 外，还提供了一个默认语义：

- 当某个 stage 没写 `depends_on`
- 它默认依赖前一个 stage

这让 flow 在不手写所有边的情况下，也可以按声明顺序串行执行。

### 5.3 `RunContext`

`mycode/orchestration/runtime/context.py` 中的 `RunContext` 负责保存运行期状态，包括：

- `vars`
- 每个 stage 的 `StageOutput`
- stage 执行顺序

它还提供：

- `collect_inputs()`
- `collect_inputs_text()`

用于把前序 stage 的结果聚合成后续阶段输入。

### 5.4 单节点执行由 `LiteLLMAgentRunner` 完成

真正执行单个节点的是 `mycode/orchestration/runtime/spawn.py` 里的 `LiteLLMAgentRunner`。它负责：

- 为单个 agent 构建 system prompt
- 构建工具集
- 驱动 LLM → tool 调用循环
- 应用 loop guard
- 返回 `SpawnOutput`

也就是说，Coordinator 当前的职责主要是**按 flow 拓扑组织执行**，而不是替代单节点 runner。

### 5.5 内置 `research.yaml`

当前仓库内置的 `research.yaml` 展示的是一个典型的 coordinator 风格 flow：

1. `research` 阶段并行执行多个研究任务
2. `synthesize` 阶段汇总前序结果并生成最终文本

这个 flow 主要用于体现：registry、loader、validator、coordinator runtime 与 CLI/API 的协作方式。

---

## 6. Swarm 运行时

### 6.1 总体模型

`mycode/orchestration/runtime/swarm.py` 中的 swarm 采用消息驱动模型：

1. 创建 `MailboxSystem`
2. 给每个 agent 分配 inbox
3. 把用户任务投递给 lead
4. 为每个 peer 启动独立异步任务
5. 每个 peer 在自己的循环里处理 inbox、执行 LLM、调用工具并继续通信

### 6.2 `send_message` 在运行时动态绑定

`send_message` 并不是一个静态全局单例，而是在 swarm 运行时为当前 peer 动态构造并绑定运行态上下文。当前这种做法带来的直接效果包括：

- 不污染全局工具表
- 并发 swarm 之间互不串话
- `main` 别名可以映射到当前 lead
- 每次运行都绑定当前 mailbox system

### 6.3 Mailbox 抽象

`mycode/orchestration/runtime/mailbox.py` 将消息系统拆分为：

- `Envelope`
- `Mailbox`
- `MailboxSystem`

当前 backend 抽象包括：

- `inprocess`
- `file`
- `tmux`
- `iterm`
- `auto`

其中：

- `InprocessMailbox` 基于 `asyncio.Queue`
- `FileMailbox` 在 `mailbox_file.py` 中实现，使用 JSONL 与文件锁

### 6.4 终止与结果

Swarm 运行结束后会得到 `SwarmResult`，其中包含：

- `peers`
- `transcript`
- `lead_output`
- `terminated_reason`

这让 swarm 路径不仅能协作执行，也具备较清晰的运行结果结构。

---

## 7. 传统 `task` 工具与编排系统的关系

### 7.1 `task` 仍然是重要入口

`mycode/tool/task.py` 仍然实现了经典的子代理模式：

- 解析目标 agent
- 构建独立消息上下文
- 最多 8 轮执行
- 使用全局 `tool.registry` 调用工具
- 复用 loop guard
- 最终把结果作为当前会话的一次 tool 输出返回

### 7.2 两套入口并行存在

当前更合适的理解方式是：

- `task`：主会话内的一次性子代理委派
- `orchestration`：基于 flow 的独立编排执行框架

二者共享部分基础设施，但入口和运行模型不同。

---

## 8. CLI、HTTP API 与前端工作台

### 8.1 CLI

`mycode/cli/main.py` 已经提供：

- `mycode orchestrate list`
- `mycode orchestrate inspect`
- `mycode orchestrate run`

并支持：

- `--vars`
- `--dry-run`
- `--json`
- `--task`（swarm）
- `--max-turns`
- `--walltime`

### 8.2 HTTP API

`mycode/server/routes/orchestration.py` 提供了完整的 flow、agent、run、event 相关接口，足以支撑前端工作台进行增删改查与运行观察。

运行中的任务状态当前保存在进程内内存结构中，由后端统一对外暴露。

### 8.3 事件桥接

`mycode/orchestration/runtime/events.py` 负责将运行时事件桥接到全局 Bus；`mycode/server/app.py` 再把这些事件通过 SSE 路由暴露给前端。

### 8.4 Web 工作台

`web/src/App.tsx` 与 `web/src/components/Sidebar.tsx` 已经把 `orchestration` 作为独立功能区接入主界面。

`OrchestrationWorkbench.tsx` 当前承担了：

- Agent 列表与编辑
- Flow 列表与编辑
- Run 列表
- SSE 实时事件展示

这说明 orchestration 已经不是单纯的后端试验模块，而是有明确 UI 入口的功能区。

---

## 9. 测试覆盖

从测试文件可以看出，这个子系统已经有比较明确的专项覆盖：

- `test_orchestration_m5_coordinator.py`
- `test_orchestration_m6_swarm.py`
- `test_orchestration_m6_5_backends.py`
- `test_orchestration_m7_events.py`
- `test_server_orchestration_routes.py`

覆盖范围主要包括：

- Coordinator DAG 调度
- fan-out 与 inputs 传递
- Swarm mailbox 路由
- `send_message` 工具行为
- file / tmux / iterm backend 选择与降级
- orchestration 事件桥接
- HTTP route 与 SSE 过滤

---

## 10. 最准确的架构描述

如果只用一句较严谨的话概括当前实现，可以写成：

> 当前仓库的多 Agent 架构，是以 `AgentInfo`、工具注册、权限系统与 LLM runner 为共享基础设施，在其上并行演化出的两套机制：一套是主会话内的 `task` 子代理，一套是以 flow / registry / runtime / API / UI 为主体的独立 orchestration 子系统；其中 Coordinator 负责声明式 flow 执行，Swarm 负责 mailbox 驱动的团队协作。

---

## 11. 总结

当前仓库的多 Agent 能力已经具备比较完整的骨架：

- 有统一的 Agent 数据模型
- 有多层 Agent / Flow 注册能力
- 有 loader、validator 与 resolver
- 有 Coordinator 与 Swarm 两类运行时
- 有 CLI、HTTP API、SSE 与 Web 工作台入口
- 有相对成体系的测试覆盖

因此，现阶段对它最准确的理解不是“单一总控式多 Agent 平台”，而是：

> **一个以共享基础设施为底座、由 `task` 子代理体系和 `orchestration` 编排体系共同组成的多 Agent 实现。**
