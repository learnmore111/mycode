# TODO

本文档列出 mycode 仍待完善的改进项。已完成事项见各 PR / 变更记录,此处只保留「尚未着手」或「仅做了骨架、需补完」的条目。

---

## 高价值(P0 — 直接影响用户体感 / 可靠性)

### 1. Alembic 生成基线 revision
**状态**: 骨架已就位(`alembic.ini` + `mycode/storage/alembic/`),但没有任何 revision 脚本;生产环境仍走 `_migrate()` 的手写路径。

**待办**:
- [ ] 在干净分支上跑 `MYCODE_ALEMBIC=1 alembic -c alembic.ini revision --autogenerate -m "baseline"` 生成 v1 脚本。
- [ ] 将现有手写迁移(`visible` 列、`turn_number`、`snapshot_ref`、`uq_part_session_tool_call` 等)转为 Alembic 版本化脚本。
- [ ] `_migrate()` 增加一次性"if alembic_version 表存在就跳过手写迁移"的分支,避免重复执行。
- [ ] 在 CI 中加入 `alembic upgrade head` 冒烟步骤。

**参考**: `mycode/storage/database.py:_migrate()`、`mycode/storage/alembic/env.py`

---

### 2. 多模态输入的完整 provider 适配
**状态**: `prompt.py` 只识别 `{type: "image"}` 并组装成 OpenAI content-list 格式。litellm 下游对 Anthropic/Gemini 的转换已经存在,但我们没验证 PDF / audio / video 通路。

**待办**:
- [ ] `UserMessage` schema 扩展 `attachments: list[Attachment]`,Attachment 含 `kind` ∈ {image, pdf, audio, video}、`url` | `bytes_ref`、`mime`、`size`。
- [ ] `provider/transform.py` 针对不同 provider 做一次适配层(Anthropic 的 `source.type=base64` vs OpenAI 的 `image_url`)。
- [ ] `server/routes/session.py` 的 message POST 接受 multipart upload;将文件落盘到 `.mycode/attachments/<session>/<hash>` 并返回 URL。
- [ ] Web UI:`MessageInput.tsx` 增加文件拖拽 + 粘贴图片;`MessageBubble.tsx` 渲染 image/pdf 缩略图。
- [ ] 在 `model.capabilities.input` 中做能力守门 — 不支持图片的模型拒绝附件,给出明确错误。

**参考**: `mycode/session/prompt.py:_normalize_image_url`、`mycode/provider/transform.py`

---

### 3. mypy 基线清理(252 条)
**状态**: 已修 provider/tool 层数个高杠杆错误,剩余主要在 CLI 与 server routes。

**待办**:
- [ ] `server/routes/*.py`:给所有嵌套的 `async def _fn()` 和 `@router.*` 路由加返回类型注解。
- [ ] `cli/main.py`:给 Click 命令函数加 `None` 返回;给 `ps` / `history` / `extra` 字段补 `dict[str, Any]` 参数化。
- [ ] `server/app.py:114` 的 `Unused "type: ignore"` 清理。
- [ ] `tool/base.py:311` 的 `BaseModel` vs `Params` 返回类型 — 用 TypeVar 收紧。
- [ ] 在 pyproject 里配置 `mypy` 的 per-module override,强制 session/prompt/processor/llm 严格零错误。

**参考**: `uv run mypy mycode/ 2>&1 | head -40`

---

### 4. Alembic 之外的「session 导出 / 备份」
**状态**: ✅ 已完成。`mycode/session/archive.py` 实现完整的 export/import 往返；格式为 `mycode-session-archive` v1 JSON。

**已实现**:
- [x] `GET /session/{id}/export` 返回 JSON 归档（messages + parts + compaction_events）。
- [x] `POST /session/import` 接受 JSON 归档,校验完整性后重建。
- [x] CLI 镜像命令:`mycode session export <id> [-o file]` / `mycode session import file.json`。
- [ ] 文档说明如何合并快照 git 仓库（snapshot_ref 仅引用 hash,blob 不含在归档中）。

---

## 中等价值(P1 — 可观测性、安全、体验)

### 5. 模型"声明即执行"循环自我校正
**状态**: 端到端复现发现,当 prompt 让模型"先检查历史再继续"时,模型会只回复"让我先检查一下:"然后在没有调用任何工具的情况下直接结束本轮,导致用户请求没有实际推进。对照测试表明,若 prompt 中含明确的工具执行指令(如"先 listdir 再 read pyproject.toml")则工具链正常。属于 prompt/processor 层的行为缺陷,不是数据库或调度 bug。

**复现**:
```
uv run mycode run -p "继续处理我上一个被暂停的请求。

上一个请求:再新建一个

请先检查当前会话历史和工作区里已经完成的代码修改,再从中断处继续,不要重复已经做完的步骤。"
```
输出: `我需要查看当前工作区的状态和最近的修改记录来了解您之前的请求内容。让我先检查一下:` → 无 tool_call 直接 Done。

**待办**:
- [ ] 在系统 prompt 中加一条硬约束:当助手声明"让我 / 我需要 / 我来…看看 / 检查 / 读取 / 查看"等意图时,**必须**在同一轮产出对应 tool_call,否则即为失败输出。
- [ ] `mycode/session/processor.py` 在 assistant 文本以意图结尾且本轮无 tool_call 时,自动再推一次 LLM(类似 loop_guard 的反向信号);阈值和次数需防止死循环。
- [ ] 或者在检测到这种情况时,以 SystemMessage(info) 注入"Reminder: you declared an intent but did not call any tool; either call the tool or state that you are done."再让模型继续。
- [ ] 给"恢复被暂停请求"的系统 prompt 模板也加一条:显式列出应先调用的工具(read session history, list workspace diff 等)。
- [ ] 加 regression 测试: headless 模式跑上面的复现 prompt,断言至少触发一次工具调用。

**参考**: `mycode/session/processor.py`、`mycode/session/prompt.py`、`mycode/agent/build/PROMPT.md`(系统提示模板所在位置)

---

### 6. 结构化 OpenTelemetry tracing
**状态**: 已经有 in-process metrics counter + histogram;缺 span/trace。

**待办**:
- [ ] 在 `prompt()` 入口、每次 `llm.stream()`、每个 tool 执行、permission ask 加 span(使用 `opentelemetry-api` 软依赖)。
- [ ] span 上挂 `session_id` / `agent` / `model` / `tool_name` 属性。
- [ ] docs 加一节"可观测性"说明如何配 OTLP exporter 到 Honeycomb / Jaeger / Tempo。
- [ ] 计划 TPS / error rate 相关 RED 指标。

**参考**: `mycode/util/metrics.py`

---

### 6. 对话分叉(fork)
**状态**: ✅ 已完成。`fork_session()` 基于 export→prune→import 模式实现，`parent_id` 记录谱系。

**已实现**:
- [x] `POST /session/{id}/fork` body `{turn: N}`: 创建新 session,拷贝前 N 轮 messages + parts。
- [x] `SessionTable.parent_id` 字段存谱系（fork 源 session ID）。
- [ ] Web 侧 MessageBubble 上右键菜单加"从这里分叉"（前端待实现）。
- [x] CLI `mycode session fork <id> -t N`。

---

### 7. apply_patch 工具(GPT-5 风格多文件 diff)
**状态**: ✅ 已完成。`mycode/tool/apply_patch.py` 实现 Add/Update/Delete + 原子性应用 + 回滚。已注册到 tool registry，所有写入通过 `atomic_write` 自动触发 LSP `didChange`。

---

### 8. LSP didChange 增量通知
**状态**: ✅ 已完成。`atomic_write` → `_fire_post_write` → `LspManager.notify_changed` → `client.did_change` 钩子链自动工作。edit/write/apply_patch 所有文件修改都触发 LSP 更新。当前使用 Full sync 模式。

---

### 9. 敏感信息扫描 pre-commit 钩子
**状态**: 日志脱敏已做,但 worktree / snapshot 里可能意外提交密钥。

**待办**:
- [ ] `snapshot.track()` 前跑 detect-secrets / gitleaks,发现密钥时阻止提交并发 warning 事件。
- [ ] bash 工具输出做二次脱敏,避免把刚 `cat /etc/something` 的泄漏再发给 LLM。
- [ ] 在 `.mycode/config` 支持自定义白名单 regex。

---

### 10. 权限规则 UI 管理
**状态**: 权限改了 deny-wins,但前端没有办法查看 / 增删已批准的 `always` 规则。

**待办**:
- [ ] `GET /permission/rules` 返回当前会话的 approved + base ruleset。
- [ ] Web 侧新增 settings 面板:按 permission (edit/read/bash…) 分组展示,支持撤销。
- [ ] CLI 对应 `mycode permission list/revoke`。

---

### 11. 流式取消精度
**状态**: `abort_event` 只能在 generator 的下一次 yield 时生效,已进入 `litellm.acompletion` 的 chunk 拉取不会立刻中断。

**待办**:
- [ ] 在 `llm.stream` 中 select `abort_event.wait()` 与 chunk 读取,触发后 cancel 上游 HTTP response。
- [ ] 上游 cancel 后尽量回收已累计的 usage 统计。
- [ ] 在 processor 层区分「用户主动 abort」与「LLM 错误」。

---

### 12. 前端 a11y 与暗色对比度
**状态**: 当前 UI 无 aria-live、permission 弹窗无 focus trap。

**待办**:
- [ ] `PermissionModal` / `CommandPalette` 加 `role="dialog"`、`aria-modal`,Tab 循环聚焦。
- [ ] 聊天区加 `aria-live="polite"` 的 region 朗读新消息。
- [ ] 对比度审计,改高 `text-gray-500` 等低对比文字。

---

## 可做可不做(P2 — 打磨 / 生态)

### 13. Python SDK `mycode-sdk`
**状态**: 路线图项。对外暴露 programmatic interface(而非 HTTP)。

**待办**:
- [ ] 抽出 `mycode.sdk.Session` / `Agent`,封装 prompt/bus/permission 三件套。
- [ ] 发 PyPI 包,文档 + demo notebook。

---

### 14. provider 注册表模式
**状态**: `provider/transform.py` 里按模型名前缀 if/elif 堆叠,新增 provider 需改代码。

**待办**:
- [ ] 引入 `ProviderTransform` 接口(`applies_to(model)` + `transform(kwargs)`),按顺序遍历匹配。
- [ ] 每个 provider 独立文件:`provider/transforms/anthropic.py`、`openai.py`、`gemini.py`…
- [ ] 第三方可通过 entry points 注册新 transform。

---

### 15. CLI 搜索 / 历史检索
**状态**: prompt_toolkit 的 history 只支持上下翻阅,不能 fuzzy 搜索。

**待办**:
- [ ] 绑定 Ctrl+R 触发 FZF 风格检索。
- [ ] `/history search <keyword>` 在会话历史里找某一轮。

---

### 16. CommandPalette 扩展为工具调色板
**状态**: 当前只能跳转 session。

**待办**:
- [ ] 支持二级命令:`>` 前缀切换到命令模式(`> /reload-plugin foo`、`> /memory`)。
- [ ] 最近使用命令置顶。

---

### 17. 测试覆盖补齐
**状态**: 428 passed,但下列关键路径仍无覆盖。

**待办**:
- [ ] `permission/permission.py` 的 cascade reject 在真实并发下的行为(多 task 同时 ask)。
- [ ] `subagent` parallel / isolated 模式的竞态 smoke test(含 worktree cleanup)。
- [ ] `snapshot.restore()` 在 merge 冲突下的行为。
- [ ] SSE 断开 / 重连的 FastAPI `TestClient` 集成测试。
- [ ] `compaction` 在跨 provider 切换时 cache TTL 的判断。

---

## 多 Agent 架构优化（新增）

**状态**: `task` 子代理体系与 `orchestration` 编排体系都已可用,但当前更像“能力骨架已齐、控制面与体验仍待补完”的阶段。

<!--
暂不执行：统一运行入口
- 现状：`task` / `subagent` / `orchestration` 仍是并行三套入口。
- 问题：普通会话里没有全局 `orchestrate` 工具,主 Agent 不能像调 `task` 一样直接发起 flow。
- 影响：轻量委派与正式编排长期割裂,用户与模型都难形成稳定心智模型。
- 后续方向：简单任务走 `task`,多节点任务走 `orchestrate`,或把 `orchestration` 收敛进 `subagent` 的某种 mode。
-->

### 18. Orchestration run 控制面与持久化
**状态**: `/orchestration/run` 已支持后台启动、详情查询、取消请求，以及基于 SQLite 的 run 历史持久化；服务重启后仍可查看 run 摘要与结果。当前剩余缺口主要在更细粒度的执行历史与运行时隔离。

**待办**:
- [ ] 将 stage / spawn / transcript / mailbox 时间线摘要持久化到 SQLite,支持历史回放与排障。
- [ ] 为历史 run 补充事件回放或分页查询接口,避免 SSE 只覆盖 live 运行期。
- [ ] 评估将编排执行与 API 主事件循环解耦,避免高并发 run 影响服务响应。

---

### 19. Swarm Web 工作台闭环
**状态**: 后端与 CLI 已支持 swarm 运行,但 Web 工作台仍缺任务输入交互;当前前端会直接报“Swarm 需要任务描述（TODO: 弹窗输入）”。

**待办**:
- [ ] 给 swarm run 增加任务输入弹窗或启动面板。
- [ ] 在工作台展示 transcript / mailbox 时间线,便于观察 peer 间通信。
- [ ] 展示 lead / peer 输出、终止原因、turn 统计等 run 结果摘要。
- [ ] 补一组前端集成测试,覆盖 swarm 发起与事件展示流程。

---

### 20. 前后端工具能力对齐
**状态**: 前端 `COMMON_TOOLS` 仍是硬编码清单,其中包含 `send_message`;但该能力实际是 swarm 运行时动态注入工具,并非普通全局工具。

**待办**:
- [ ] 由后端暴露“全局静态工具”与“运行时专属工具”的能力描述。
- [ ] 前端按 flow mode 动态展示可选工具,避免误配 `send_message` 一类运行时工具。
- [ ] 校验 Agent 编辑器提交内容,提前拦截无效工具组合。
- [ ] 将当前硬编码工具清单替换为后端驱动配置。

---

### 21. Coordinator 动态调度能力
**状态**: 当前 `Coordinator` 已能稳定执行声明式 DAG,但尚不具备根据中间结果动态拆任务、改派 worker、失败重试的“总控代理”能力。

**待办**:
- [ ] 在保留 DAG 模式的前提下,设计可选的 agentic coordinator 模式。
- [ ] 支持根据 worker 输出继续派发新任务,而不是完全依赖静态 `stages`。
- [ ] 增加失败重试、改派、预算感知等调度策略。
- [ ] 为动态调度模式补充可复现实验流与专项测试。

---

### 22. 真实隔离与 Hybrid 语义补完
**状态**: `agent.isolation` 目前主要体现在 fresh tool context 层面,尚未真正形成 worktree / process 级隔离;`hybrid` 也已有 schema 与校验入口,但缺少清晰独立语义。

**待办**:
- [ ] 为编排节点补上真正的 worktree isolation。
- [ ] 评估 process 级隔离或独立执行环境,降低多 agent 并发改代码风险。
- [ ] 明确 `hybrid` 的产品语义与运行时边界;若短期不做,则降级为实验特性。
- [ ] 为 isolation / hybrid 增加端到端测试与故障回收策略。

---

### 23. 工具策略、可观测性与长生命周期能力
**状态**: 当前 `task` / `subagent` / `spawn` / `swarm` 各自持有部分工具过滤规则;运行事件已具备骨架,但成本、质量与长期团队能力仍偏弱。

**待办**:
- [ ] 抽出统一的 tool policy layer,统一递归禁用、交互工具禁用、运行时工具注入策略。
- [ ] 为 run / stage / peer 增加 token、cost、latency、retry、失败分类等观测指标。
- [ ] 补充 reviewer / validator / 交叉审阅等结果质量控制机制。
- [ ] 设计团队级记忆、scratchpad、可恢复 run 等长生命周期协作能力。
- [ ] 引入 token / walltime / turn budget 感知的调度策略。

---

## 待归档(超出当前项目范围但被多次提出)

- SaaS 部署 / 多租户 / 计费
- Web UI 主题切换(浅色)
- 非英语 / 非中文界面的 i18n
- VS Code 扩展集成 mycode 作为 backend

---

## 当前进度快照

| 类别 | 状态 |
|------|------|
| 并发 / 竞态修复(第一轮 15 项) | ✅ 完成 |
| 可靠性 / 体验(第二轮 15 项) | ✅ 完成 |
| Orchestration M1 (topology schema/loader/validator) | ✅ 完成 |
| Orchestration M2 (agent registry + .md frontmatter) | ✅ 完成 |
| Orchestration M3 (registry ↔ agent loader + subagent tools/max_turns) | ✅ 完成 |
| Orchestration M4 (flow AgentSpec.extends → registry resolver) | ✅ 完成 |
| Orchestration M5 (coordinator runtime: DAG + parallel + fan-out + synthesis) | ✅ 完成 |
| Orchestration M6 (swarm runtime: mailbox-driven peer agents, inprocess backend) | ✅ 完成 |
| Orchestration M7 (event bus integration + HTTP routes + SSE streaming) | ✅ 完成 |
| Orchestration M6.5 (file / tmux / iterm mailbox backends) | ✅ 完成 |
| Alembic 基线 revision | ✅ 完成 |
| 多模态 end-to-end | ✅ 完成 (后端) |
| mypy 零错误目标 | ✅ 完成 (286→0) |
| 会话导出 / 分叉 / fork | ✅ 完成 |
| OTel tracing | 🟡 仅 metrics |
| apply_patch / LSP didChange | ✅ 完成 |

*Last updated: 2026-04-22*
