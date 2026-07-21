# MyCode 记忆系统升级计划

> 日期：2026-07-21  
> 状态：基线实现已完成（P0–P6）；P7 评测框架已落地，生产回放与渐进开启待执行  
> 范围：项目指导、会话记忆、长期记忆、自动提取、检索、维护、安全与评测

---

## 0. 实现进度（2026-07-21）

| 阶段 | 状态 | 主要落地点 |
|---|---|---|
| P0 | 已完成 | memdir 边界校验、Unicode 文件名、完整 session ID、JSONL 容错、任务跟踪和摘要串行化 |
| P1 | 已完成 | `MemoryService`、`ProjectGuidance`、SQLite 权威源和 Markdown 投影 |
| P2 | 已完成 | `memory_record`、`memory_audit`、`memory_extraction_state` 及 Alembic 0004 |
| P3 | 已完成 | 在 `prompt()` 核心生命周期统一召回；CLI/headless/API/resume 共用；编排入口继承项目指导开关 |
| P4 | 已完成 | 空闲会话资格检查、claim/version 去重、外部上下文排除、pending inbox、显式记住直接生效 |
| P5 | 已完成 | scope/status/validity/sensitivity 硬过滤、FTS5/BM25 + 多语言词法回退、转义证据包、项目内代码/Git 证据失效检查 |
| P6 | 已完成 | TTL、supersede、隐私清除 tombstone、完整导出/作用域删除、验证审计、重复整理、冲突组报告和投影重建 |
| P7 | 框架已完成 | 提供 Recall@K、MRR、禁用记忆采用率和证据完整性回放计算；实际上线门槛仍需真实任务集验证 |

运维、配置、API 与数据语义见 [`docs/memory-system.md`](memory-system.md)。

---

## 1. 结论

MyCode 需要升级记忆系统，但升级重点不是立即接入向量数据库或知识图谱，而是先完成以下闭环：

1. 修复现有实现中的安全与正确性问题；
2. 明确项目指导、原始事件、会话摘要和长期记忆的边界；
3. 建立带来源、作用域、版本和状态的记忆数据模型；
4. 将自动提取改为后台候选生成和审核生效；
5. 实现带硬过滤、来源证据和事实验证的检索；
6. 通过真实评测决定是否需要 embedding 或图检索。

本计划吸收了当前主流 Coding Agent 的可验证设计：

- Claude Code：轻量索引与主题文件按需读取；
- OpenAI Codex：空闲会话后台抽取、生成与使用分离、抽取与整合分离；
- Gemini CLI：候选 inbox、目标 allowlist、patch 校验和审核后原子应用；
- GitHub Copilot：代码来源引用、当前分支重新验证和自动过期；
- Cursor：sidecar 抽取与用户批准；
- Windsurf/Cascade：Memory 与 Rules 分离、按触发方式加载；
- Devin：带 trigger description 的相关性召回和 Knowledge 更新建议。

---

## 2. 当前项目指导层

### 2.1 已有能力

MyCode 已经具备独立于长期记忆的项目指导文件加载能力，当前由 `mycode/session/system.py` 的 `_load_project_guidance()` 实现。

当前查找顺序为：

```text
mycode.md
  ↓ 不存在或为空
codebuddy.md
  ↓ 不存在或为空
CLAUDE.md
  ↓ 不存在或为空
Claude.md
```

系统使用第一个存在且非空的文件，将内容包装为 `<project_guidance>` 注入上下文。

因此，本计划不再新增一个以 `AGENTS.md` 为唯一入口的“规则层”，而是统一使用以下术语：

> **项目指导层（Project Guidance）**：以 `mycode.md` 为 MyCode 原生入口，并通过兼容文件名读取其他 Agent 的项目指导。

### 2.2 与记忆系统的边界

项目指导层和记忆层必须保持分离：

| 类型 | 保存内容 | 写入方式 | 使用方式 |
|---|---|---|---|
| 项目指导 | 构建命令、编码规范、安全约束、固定工作流 | 用户或团队显式维护 | 启动或规则命中时确定性加载 |
| 长期记忆 | 用户偏好、历史反馈、阶段性项目事实、经验线索 | 显式记忆或后台候选审核 | 根据当前任务选择性召回 |
| 会话摘要 | 当前任务进展、未完成工作、恢复线索 | 会话运行时生成 | 会话恢复或压缩时使用 |
| 原始事件 | 用户消息、工具调用、环境结果 | 系统自动记录 | 审计、重建和事实证据 |

长期记忆不得复制可以从以下来源直接读取的内容：

- `mycode.md` 及当前兼容的项目指导文件；
- 代码库当前状态；
- Git 历史；
- 测试、构建和静态检查结果；
- 权威数据库或外部 API 的当前状态。

### 2.3 当前兼容层仍需整理的地方

本轮计划只承认当前代码已经实现的兼容范围，不把未来能力描述成现状：

- 当前只查找项目根目录，不做父目录到子目录的分层合并；
- 多个兼容文件同时存在时只读取第一个，不会合并；
- 当前加载列表不包含 `AGENTS.md`、`GEMINI.md` 或 `.cursor/rules`；
- `memory` 工具、自动提取提示词和注释中仍有硬编码的 `CLAUDE.md` 表述；
- Agent 配置中的 `omit_claudemd` 是历史命名，实际语义应逐步改为“省略项目指导”。

后续可以在不破坏兼容性的前提下增加 `omit_project_guidance`，并将旧字段保留为别名。

---

## 3. 目标架构

```text
mycode.md / compatible guidance files
  └─ 必须遵守的确定性项目指导

SQLite messages / parts / tool events
  └─ 不可变原始证据
       ↓ 空闲会话后台抽取
memory_candidate
  └─ pending / approved / rejected
       ↓ 审核、去重、冲突与安全检查
memory
  └─ active / superseded / expired / deleted
       ↓ 作用域硬过滤 + FTS/BM25 + 事实验证
带来源、时间和可信状态的记忆证据包

session summary
  └─ 只服务会话恢复与上下文压缩

memdir Markdown
  └─ 人类可读视图、导出和向后兼容层
```

核心原则：

1. 项目指导是规则，记忆是证据；
2. SQLite 原始事件是可追溯底座；
3. 自动抽取默认只产生候选；
4. 更新创建新版本，不无痕覆盖；
5. 项目事实使用前必须重新验证；
6. Markdown 不再是唯一事实源；
7. 高风险业务状态不得由记忆决定。

---

## 4. 数据模型

建议使用统一记忆记录和状态机，而不是继续让 session JSONL、memdir 和 extractor 各自维护状态。

```text
id
memory_type
scope_type
scope_id
subject
content
trigger_description

source_session_id
source_message_ids
source_kind
evidence_refs
confidence

observed_at
valid_from
valid_to
last_verified_at
expires_at

status
supersedes_id
sensitivity
extractor_version
created_by
time_created
time_updated
```

### 4.1 建议枚举

`memory_type`：

- `user_preference`
- `feedback`
- `project_fact`
- `episodic_experience`
- `reference`
- `procedure_candidate`

`scope_type`：

- `user`
- `project`
- `repository`
- `organization`
- `agent`

`source_kind`：

- `user_statement`
- `code_evidence`
- `git_evidence`
- `tool_output`
- `external_content`
- `agent_inference`

`status`：

- `pending`
- `active`
- `superseded`
- `expired`
- `rejected`
- `deleted`

### 4.2 更新与删除语义

- 修改长期记忆时创建新版本，并使用 `supersedes_id` 指向旧记录；
- 自动抽取不得直接覆盖现有 active 记忆；
- 删除创建 tombstone，并同步移除检索索引和 Markdown 投影；
- consolidation 只能提出合并、更新或失效候选；
- 所有派生记录都必须能够回溯到原始消息或工具事件。

---

## 5. 分阶段实施计划

### P0：安全与正确性修复

目标：先让现有记忆能力可安全启用。

任务：

- 修复 memory `update/delete` 的绝对路径逃逸；
- 使用完整 session ID 或无碰撞 ID 作为会话日志文件名；
- 中文记忆文件改用 Unicode slug 或 `类型_ID.md`，避免覆盖为 `unnamed`；
- 追踪后台 `record_turn` 任务，并在退出或 finalize 前有界等待；
- 禁止多个摘要任务并发重写同一会话文件；
- JSONL 读取遇到单条损坏记录时跳过并告警；
- 将所有硬编码的 `CLAUDE.md` 表述替换为“项目指导文件”或统一辅助函数；
- 修复当前记忆相关失败测试；
- 增加路径、多语言、并发和重复写入回归测试。

验收：

- 任意 memory 参数都不能访问记忆目录之外的文件；
- 不同 session 不会共享日志文件；
- 两条中文记忆不会相互覆盖；
- 相关测试和 Ruff 检查全部通过。

### P1：统一边界与服务入口

目标：消除会话记忆、长期记忆和项目指导之间的职责重叠。

任务：

- 建立统一 `MemoryService`；
- 将项目指导加载抽成明确的 `ProjectGuidance` 接口；
- session summary 仅用于恢复和 compaction；
- SQLite messages/parts/tool events 作为原始证据；
- memdir 改为长期记忆的人类可读投影；
- 建立旧 Markdown 记忆向新模型的迁移工具；
- 保留现有 memory 工具的兼容接口。

验收：

- 同一条信息只有一个权威存储位置；
- 任意长期记忆都可追溯到来源；
- 删除数据库记录后不会被旧 Markdown 再次导入复活。

### P2：版本化 SQLite 记忆模型

目标：支持来源、作用域、冲突、更新、过期和审计。

任务：

- 创建数据库迁移和 repository/service 层；
- 实现 candidate 与 active 状态流转；
- 实现 scope 硬隔离；
- 实现 supersede、expire、reject、delete；
- 保存来源消息、代码/Git 证据和 extractor 版本；
- 提供 list/read/history/export/delete 接口。

验收：

- 更新不会破坏历史版本；
- user/project/repository/agent 作用域测试零泄漏；
- 可以从任意 active 记忆定位原始证据。

### P3：统一接入所有运行入口

目标：记忆能力属于核心会话生命周期，而不是 CLI 附属功能。

接入范围：

- interactive CLI；
- headless；
- FastAPI/SSE；
- session resume；
- subagent；
- orchestration/swarm。

建议配置：

```json
{
  "memory": {
    "enabled": true,
    "useMemories": true,
    "generateMemories": false,
    "disableOnExternalContext": true
  }
}
```

其中：

- `useMemories` 控制是否召回已有长期记忆；
- `generateMemories` 控制当前会话是否成为自动抽取输入；
- `disableOnExternalContext` 默认排除使用 Web、MCP、第三方文档或其他 Agent 输出的会话。

验收：

- 相同请求在不同入口具有一致的记忆使用策略；
- 关闭生成不影响已有记忆召回；
- 关闭使用不影响候选生成和审计。

### P4：后台抽取与 Memory Inbox

目标：采用 Shadow Write，自动提取不直接污染 active memory。

任务：

- 只处理已空闲、达到最低轮数、没有活跃任务的会话；
- 使用 lock、processed version 和任务状态防止重复抽取；
- 抽取用户偏好、反馈、项目事实、经验和程序候选；
- 区分用户声明、代码证据、工具输出、外部内容和 Agent 推断；
- 自动执行 secret scan、近重复检测、冲突检测和 scope 校验；
- 所有自动结果写入 `pending`；
- 提供 inbox 的查看、编辑、批准、拒绝和批量处理；
- 使用目标 allowlist、patch 校验和原子写入；
- 用户明确要求“记住”时允许直接创建已批准记忆。

验收：

- 后台抽取不增加前台响应时间；
- 未批准候选不会进入未来上下文；
- 同一会话不会被重复抽取；
- 外部 prompt injection 不会成为 active memory。

### P5：检索、证据注入与事实验证

目标：先建立透明、可调试的简单强基线。

第一版流程：

1. 按 `scope/status/type/validity/sensitivity` 硬过滤；
2. 使用 SQLite FTS5/BM25 搜索 `subject/trigger_description/content`；
3. 加入类型、时间、可信度和最近验证信号；
4. 去重并限制在 3 至 5 条；
5. 返回内容、来源、观察时间、有效时间、验证状态和冲突状态；
6. 将结果标记为历史证据，而不是系统指令。

项目事实召回后必须检查：

- 证据文件是否仍存在；
- 相关代码片段是否变化；
- Git ref 或当前分支是否仍支持该结论；
- 构建、测试或 API 是否能重新确认。

无法验证时将记忆标记为 `stale`，不能静默作为当前事实使用。

验收：

- 检索结果能够解释“为什么被召回”；
- 项目事实具有来源和最后验证时间；
- 不相关记忆不会仅因新鲜度或类型加成进入上下文；
- 非 LLM 检索 p95 建议低于 150ms。

### P6：维护、遗忘与治理

目标：防止记忆无限增长、过时事实复活和敏感信息长期滞留。

建议按类型配置策略：

- 用户偏好：默认不自动删除，但支持查看、修改、导出和删除；
- 项目事实：长期未使用或验证失败后自动过期；
- 项目计划和事件：必须设置 `valid_to` 或 TTL；
- 外部引用：周期性检查目标是否仍有效；
- 失败经验：保留失败标签，不能自动晋升为推荐流程；
- 程序候选：通过测试或用户批准后才能晋升为 skill 或项目指导。

任务：

- consolidation、重复合并和冲突分组；
- TTL 与 last-used/last-verified 更新；
- tombstone 和派生索引删除；
- 用户数据导出与删除；
- repo/user/organization/agent 访问控制；
- 记忆生成、审批、召回、验证和删除审计日志。

验收：

- 删除后的记忆不会从索引、摘要或投影中复活；
- 过期项目事实不会被当作当前状态；
- 跨作用域访问测试零泄漏。

### P7：评测与渐进上线

上线顺序：

```text
关闭长期记忆
  → 只记录候选（shadow）
  → 用户审核后生效
  → 低风险用户偏好自动生效
  → 经验证的项目事实有限自动生效
```

至少比较以下基线：

1. 无记忆；
2. 最近窗口；
3. 滚动摘要；
4. 全历史长上下文；
5. FTS5/BM25；
6. 结构化当前状态查询。

核心指标：

- 应写召回率和不应写误写率；
- 自动候选精确率；
- 作用域正确率和敏感信息误存率；
- Recall@K、MRR 和证据完整性；
- 过期或错误记忆采用率；
- 端到端任务成功率；
- 删除后复活率；
- prompt injection 和 memory poisoning 成功率；
- 前台延迟、后台成本和索引增长。

建议门槛：

- 路径逃逸和跨 scope 泄漏为 0；
- 测试集敏感信息误存为 0；
- 自动候选精确率至少 90%；
- 错误或过期记忆被最终回答采用低于 1%；
- 后台抽取不增加主任务关键路径延迟；
- 相比“最近窗口 + 摘要”基线，未见任务上的端到端成功率有稳定提升。

只有 BM25 在真实回放中出现明确语义漏召回时，才增加 embedding。只有动态实体关系、历史状态和多跳查询成为核心任务时，才评估时间图谱。

---

## 6. 与上一版计划相比的调整

1. 将唯一的 `AGENTS.md` 规则入口改为已经存在的 **MyCode 项目指导层**；
2. 明确 `mycode.md` 是原生入口，其他文件名属于兼容入口；
3. 将清理硬编码 `CLAUDE.md` 文案加入 P0；
4. 增加 Project Guidance 与长期记忆的去重边界；
5. 自动抽取从“会话结束直接保存”改为“空闲会话 → pending → 审核”；
6. 将来源证据和当前分支验证提升为核心能力；
7. 将 TTL、tombstone、作用域和删除审计纳入正式阶段；
8. 将向量数据库和图谱从默认方案降为评测后的可选扩展；
9. 将 CLI 专用记忆逻辑迁移到所有运行入口共享的核心服务；
10. 将 memdir 从唯一存储调整为可读投影与兼容层。

---

## 7. 推荐实施顺序

```text
P0 安全与正确性
  → P1 统一边界
  → P2 版本化数据模型
  → P3 全入口接入
  → P4 Shadow Write 与 Inbox
  → P5 检索和事实验证
  → P6 遗忘与治理
  → P7 评测后逐步自动化
```

P0 完成前不建议开启自动长期记忆；P4 和 P5 完成前不建议让后台抽取结果自动影响 Agent 行为；P7 评测通过前不建议引入更重的向量或图存储。

---

## 8. 当前相关代码入口

- `mycode/session/system.py`：项目指导文件发现与注入；
- `mycode/session/prompt.py`：系统提醒和长期记忆索引注入；
- `mycode/session/memory/memory.py`：会话 JSONL 和滚动摘要；
- `mycode/session/memory/memdir.py`：Markdown 长期记忆与索引；
- `mycode/session/memory/retrieval.py`：关键词和 LLM 辅助检索；
- `mycode/session/memory/extractor.py`：会话长期记忆提取；
- `mycode/tool/memory.py`：长期记忆 CRUD 工具；
- `mycode/session/message.py`：消息与工具事件持久化；
- `mycode/storage/models.py`：SQLite 数据模型；
- `mycode/cli/main.py`：当前会话记忆和自动提取的主要运行入口。
