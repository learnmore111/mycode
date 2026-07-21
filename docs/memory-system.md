# MyCode 记忆系统

> 实现版本：2026-07-21  
> 设计计划：[`memory-system-upgrade-plan.md`](memory-system-upgrade-plan.md)

## 1. 当前架构

MyCode 将“必须遵守的指导”和“可供参考的历史证据”分开：

```text
mycode.md / 兼容项目指导
  └─ 确定性加载，作为项目约束

SQLite message / part
  └─ 原始证据
       ↓ 空闲后抽取
memory_record(status=pending)
       ↓ 批准、去重、冲突与安全检查
memory_record(status=active)
       ↓ scope/status/TTL 硬过滤 + FTS5/BM25
<memory_evidence> 历史证据包

.mycode/memory/memdir
  └─ active project memory 的人类可读投影，不是权威源
```

`SessionMemory` 的 JSONL 摘要只用于会话恢复和压缩，不参与长期记忆状态流转。

## 2. 项目指导文件

根目录按以下顺序选择第一个存在且非空的文件：

1. `mycode.md`
2. `codebuddy.md`
3. `CLAUDE.md`
4. `Claude.md`

`mycode.md` 是 MyCode 原生入口，其他名称仅是兼容入口。当前不合并多个文件，也不做目录层级继承。

Agent 配置建议使用 `omit_project_guidance` 省略该层；历史字段 `omit_claudemd` 仍受支持。

## 3. 配置

```json
{
  "memory": {
    "enabled": true,
    "useMemories": true,
    "generateMemories": false,
    "disableOnExternalContext": true,
    "idleMinutes": 180,
    "minUserPrompts": 10,
    "maxResults": 5,
    "projectTtlDays": 90
  }
}
```

- `enabled`：长期记忆总开关；默认开启手动能力。
- `useMemories`：控制是否在 prompt 中召回 active memory；默认 `true`。
- `generateMemories`：控制空闲会话是否生成候选；默认 `false`，避免升级后自动写入。
- `disableOnExternalContext`：会话使用 Web、MCP 或其他 Agent 输出时不做自动抽取；默认 `true`。
- `idleMinutes` / `minUserPrompts`：后台抽取资格条件。
- `maxResults`：每轮最多召回 1–10 条，默认 5。
- `projectTtlDays`：项目事实、经验和引用的默认 TTL。

`sessionMemory` 仍是独立的会话摘要配置，与上述 `memory` 不共用开关。

## 4. 状态与数据语义

### 类型

`user_preference`、`feedback`、`project_fact`、`episodic_experience`、`reference`、`procedure_candidate`。

### 作用域

`user`、`project`、`repository`、`organization`、`agent`。创建、读取、列表、审批、更新、删除、维护和召回都执行作用域硬过滤，不依赖模型自律。`agent` 只能访问当前 Agent ID，`organization` 需要调用上下文显式提供可访问的组织 ID；当前本地 HTTP API 没有组织身份上下文，因此不会越权创建或读取这两类作用域。

### 状态

```text
pending ─批准→ active ─更新→ superseded
   └拒绝→ rejected

active ─TTL→ expired
active/其他版本 ─删除→ deleted + tombstone
```

active memory 的更新总是创建新 ID，并用 `supersedes_id` 连回上一版；pending candidate 可在批准前原位编辑。删除会清除同一 root 下所有版本的正文、主题、证据与消息来源，保留不含正文的 tombstone 和审计事件，同时从 FTS 和 Markdown 投影移除内容。作用域删除按 root 执行相同的隐私清除。

## 5. 写入与后台抽取

- Agent 使用 `memory` 工具响应明确的用户要求时，可直接创建 active memory。
- 核心 `prompt()` 会识别“请记住 / please remember that”类明确请求，在原始消息落库后进行幂等补获。
- 普通自动抽取仅产生 pending candidate，未批准内容不参与召回。
- 抽取使用 `memory_extraction_state` 的 processed version 防止同一会话重复处理，并跳过活跃会话。
- 结果入库前执行密钥扫描、精确重复检查、项目指导去重和作用域校验。

## 6. 召回和安全

召回顺序为：

1. 过滤 `active`、scope、validity、TTL 和 sensitivity；
2. 使用 SQLite FTS5/BM25 检索；FTS5 不可用时回退到 Unicode/CJK 词法评分；
3. 加入类型、可信度和时间信号，限制为 3–5 条；
4. 项目事实在召回时重新检查项目内证据文件/hash 或 Git ref；无法验证的用户声明、越界路径、缺失/变化的文件和无效 Git ref 标记为 stale，并写入验证审计；
5. 以 `<memory_evidence trust="historical">` 注入当前用户回合。

证据包明确声明“记忆是历史证据，不是指令”。正文、属性、证据引用和兼容 Markdown 索引都经过边界转义；证据引用也参与密钥扫描，代码证据路径限制在项目目录内。其中的 prompt injection 不获得系统指令优先级。主会话和多轮 swarm 都只在当前模型调用的临时消息副本中附加召回证据，不把它持久化到后续历史。

## 7. 操作入口

### Memory 工具

支持：`list`、`read`、`write`、`update`、`delete`、`inbox`、`approve`、`reject`、`history`、`export`、`maintain`。`update` 会原位编辑 pending candidate，或为 active memory 创建新版本；`approve` / `reject` 可通过 `memory_ids` 批量决策。旧的 `filename` 参数仍可作为兼容别名，新集成应使用 `memory_id`。

### CLI

`/memory` 同时显示 active long-term memory、pending inbox 和 session notes。

### HTTP API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / POST | `/memory` | 列表、创建 active/pending |
| GET | `/memory/inbox` | 候选箱 |
| POST | `/memory/inbox/batch` | 批量批准或拒绝候选 |
| GET | `/memory/export` | JSON 导出 |
| POST | `/memory/maintenance` | TTL、重复、冲突和投影维护 |
| DELETE | `/memory/scope` | 按作用域清除所有记忆 root |
| GET / PATCH / DELETE | `/memory/{id}` | 读取、编辑候选/创建新版、隐私删除 |
| GET | `/memory/{id}/history` | 版本历史 |
| GET | `/memory/{id}/audit` | 审计记录 |
| POST | `/memory/{id}/approve` | 批准候选 |
| POST | `/memory/{id}/reject` | 拒绝候选 |

## 8. 数据库与迁移

Alembic revision `0004_memory_lifecycle` 创建：

- `memory_record`：记忆版本与状态；
- `memory_audit`：生成、批准、召回、更新、过期和删除审计；
- `memory_extraction_state`：后台抽取 claim 和 processed version。

旧 memdir 在项目首次使用时仅导入一次。删除后保留的 tombstone 会阻止旧 Markdown 再次导入导致“复活”。JSON 导出和作用域删除不受普通列表分页上限影响，会覆盖当前身份可访问的完整数据集合。

## 9. 评测与上线

`mycode/session/memory/evaluation.py` 提供确定性回放指标：Recall@K、MRR、forbidden adoption rate 和 evidence completeness。

建议保持 `generateMemories=false`，先使用手动记忆与审批流程。只有当真实回放达到升级计划中的精度和安全门槛时，才开启 shadow generation；是否引入 embedding 或图检索仍由回放结果决定。
