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
**状态**: 没有导出接口,唯一方式是直接拷贝 `~/.local/share/mycode/mycode.db`。

**待办**:
- [ ] `GET /session/{id}/export` 返回 JSONL 归档(messages + parts + snapshot_refs)。
- [ ] `POST /session/import` 接受 JSONL,校验完整性后重建。
- [ ] CLI 镜像命令:`mycode session export <id> > out.jsonl` / `mycode session import out.jsonl`。
- [ ] 文档说明如何合并快照 git 仓库。

---

## 中等价值(P1 — 可观测性、安全、体验)

### 5. 结构化 OpenTelemetry tracing
**状态**: 已经有 in-process metrics counter + histogram;缺 span/trace。

**待办**:
- [ ] 在 `prompt()` 入口、每次 `llm.stream()`、每个 tool 执行、permission ask 加 span(使用 `opentelemetry-api` 软依赖)。
- [ ] span 上挂 `session_id` / `agent` / `model` / `tool_name` 属性。
- [ ] docs 加一节"可观测性"说明如何配 OTLP exporter 到 Honeycomb / Jaeger / Tempo。
- [ ] 计划 TPS / error rate 相关 RED 指标。

**参考**: `mycode/util/metrics.py`

---

### 6. 对话分叉(fork)
**状态**: 已实现线性回滚,但用户有时想从某一轮分叉成新对话来对比两种方案,不想覆盖原历史。

**待办**:
- [ ] `POST /session/{id}/fork?turn=N`:创建新 session,拷贝前 N 轮 messages + parts 到新 session_id。
- [ ] `SessionTable.parent_id` + `fork_point_turn` 字段存谱系。
- [ ] Web 侧 MessageBubble 上右键菜单加"从这里分叉"。
- [ ] CLI `mycode session fork <id> -t N`。

---

### 7. apply_patch 工具(GPT-5 风格多文件 diff)
**状态**: README 标注"待实现"。Edit/Write 当前是单文件语义,大规模重构时 LLM 要发十几次 edit,token 浪费。

**待办**:
- [ ] 新增 `mycode/tool/apply_patch.py`:接受统一 diff 格式(`*** Update File: path\n@@...\n+...\n-...`),原子性应用。
- [ ] 引入 `patch_preview` 字段在执行前回显;失败时给出冲突 hunk 的行号。
- [ ] 支持 rename / delete / new file 三种操作。
- [ ] 与 changes 暂存 / 回退面板集成。

---

### 8. LSP didChange 增量通知
**状态**: LSP 集成存在,但 Edit/Write 后不通知 LSP 服务,导致 diagnostics 陈旧。

**待办**:
- [ ] `tool/edit.py`、`tool/write.py` 在 atomic_write 后发送 `textDocument/didChange` 到相关 LSP 客户端。
- [ ] `lsp/client.py` 增加增量/全量切换的 capability 协商。
- [ ] 在 Web UI 的 MessageInput 旁显示 LSP diagnostics 的 badge。

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
| Orchestration M6.5 (file / tmux / iterm mailbox backends) | ❌ 未开始 |
| Alembic 基线 revision | ❌ 未开始 |
| 多模态 end-to-end | 🟡 骨架 |
| mypy 零错误目标 | 🟡 252 条残留 |
| 会话导出 / 分叉 / fork | ❌ 未开始 |
| OTel tracing | 🟡 仅 metrics |
| apply_patch / LSP didChange | ❌ 未开始 |

*Last updated: 2026-04-22*
