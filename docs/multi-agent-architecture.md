# Multi-Agent 架构说明

> 为避免文档继续混入“设计目标 / 规划项 / 尚未落地能力”，本文件现在**只保留当前仓库已经存在的多 Agent 架构入口说明**。
>
> 需要看实现细节时，请以 `docs/multi-agent-current-implementation.md` 为准。

---

## 当前架构的真实入口

当前仓库里的多 Agent 能力，主要通过下面三类入口使用：

- **CLI**：`mycode orchestrate list`、`mycode orchestrate inspect`、`mycode orchestrate run`
- **HTTP API**：`mycode/server/routes/orchestration.py` 提供 flow、agent、run、event 相关接口
- **Web 工作台**：`OrchestrationWorkbench.tsx` 提供 Agent / Flow / Run 的可视化管理界面

---

## 当前已经存在的核心模块

### Agent 与注册

- `mycode/agent/agent.py`：定义内置 Agent 与 `AgentInfo`
- `mycode/orchestration/registry/agent_registry.py`：发现并加载全局 / 项目级 Agent
- 支持 `.md` Agent、frontmatter、`extends` 继承

### Flow 与拓扑

- `mycode/orchestration/registry/flow_registry.py`：发现内置 / 全局 / 项目级 flow
- `mycode/orchestration/topology/schema.py`：定义 flow schema
- `mycode/orchestration/topology/loader.py`：负责加载、变量渲染、`extends` 合并
- `mycode/orchestration/topology/validator.py`：负责拓扑与字段校验

### 运行时

- `mycode/orchestration/runtime/coordinator.py`：执行声明式 DAG flow
- `mycode/orchestration/runtime/swarm.py`：执行 mailbox 驱动的 swarm 协作与主管协作
- `mycode/orchestration/runtime/context.py`：保存 stage 输出和变量上下文
- `mycode/orchestration/runtime/spawn.py`：执行单个 agent 节点
- `mycode/orchestration/runtime/mailbox.py` / `mailbox_file.py`：提供消息后端抽象

---

## 当前三种主要运行模型

### Coordinator

Coordinator 当前的真实语义是：

- 按 `stages` 拓扑顺序执行 flow
- 支持 `spawn`、`fan_out_from`、`runs_on` 三类阶段
- 在综合阶段把上游结果整理后交给指定 agent 生成输出

它更接近**声明式 DAG 执行器**，而不是一个能够自由拆解任务并持续派发 worker 的总控代理。

### Swarm

Swarm 当前具备较完整的协作运行时能力：

- 多个 peer 并行运行
- 通过 mailbox 交换消息
- 在运行时动态绑定 `send_message`
- 支持 `inprocess`、`file`、`tmux`、`iterm`、`auto` 等 backend

从协作模型上看，Swarm 是当前仓库里最接近“多 Agent 团队协作”的实现。

### Supervisor Collaboration

主管协作式对应 `mode: hybrid`。它复用 mailbox 协作能力，但语义上有稳定主管：

- `coordinator` 是主管 / facilitator
- 主管接收初始任务并通过 `send_message` 分派专家
- 专家可以用 `recipient: main` 回到主管
- 最终结果优先展示主管输出

---

## 内置示例

当前仓库内置了三类示例 flow：

- `research.yaml`：并行研究 + 综合
- `supervised-review.yaml`：主管组织架构与风险专家做评审
- `pair-review.yaml`：基于 swarm 的多人 peer review 示例

这些 flow 用于展示 registry、loader、runtime、CLI 与 UI 的联动方式。

---

## 与传统 `task` 子代理的关系

仓库里仍然保留原有的 `task` 子代理体系：

- `task` 适合主会话里的一次性委派
- `orchestration` 适合通过 flow 驱动的编排执行

二者共享部分基础设施，但当前仍然是**并行存在的两套入口**。

---

## 建议阅读顺序

如果你要继续看代码，建议按下面顺序：

1. `docs/multi-agent-current-implementation.md`
2. `mycode/orchestration/registry/`
3. `mycode/orchestration/topology/`
4. `mycode/orchestration/runtime/`
5. `mycode/server/routes/orchestration.py`
6. `web/src/components/OrchestrationWorkbench.tsx`

---

## 说明

旧版文档中涉及的规划项、未来工具名、尚未统一的设计路径，已经从本文件中移除，避免与当前代码现状混淆。
