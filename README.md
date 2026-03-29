# OpenCode (Python)

**Open source AI coding agent — Python edition.**

Python 重写版 [OpenCode](https://github.com/anomalyco/opencode)，一个不绑定特定 AI 提供商的开源编程 Agent 平台。

---

## 快速开始

```bash
# 安装
uv sync

# 运行 CLI
uv run opencode --help

# Headless 模式（需要设置 API Key）
export ANTHROPIC_API_KEY=sk-xxx
uv run opencode run --message "列出当前目录的文件"

# 启动 API Server
uv run opencode serve --port 4096

# 运行测试
uv run pytest tests/ -v
```

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                        Clients                          │
│  CLI (click)  │  HTTP API (FastAPI)  │  Future: TUI     │
└──────┬────────┴──────────┬───────────┴──────────────────┘
       │                   │
       ▼                   ▼
┌─────────────────────────────────────────────────────────┐
│              Session Layer (prompt.py)                   │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Agent 选择   │  │ System Prompt│  │  Tool 加载     │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
│         └────────────────┼──────────────────┘           │
│                          ▼                              │
│              ┌─── Agentic Loop ───┐                     │
│              │                    │                     │
│              │  Processor ──────► LLM Stream (litellm)  │
│              │     │              │  ↓                   │
│              │     ▼              │  AI Provider API     │
│              │  Tool 执行         │                     │
│              │     │              │                     │
│              │  Permission 检查   │                     │
│              │     │              │                     │
│              │  continue/stop ◄──┘                     │
│              └────────────────────┘                     │
└──────┬──────────────────┬───────────────────┬──────────┘
       │                  │                   │
┌──────▼──────┐  ┌────────▼────────┐  ┌──────▼──────────┐
│  Storage    │  │  Event Bus      │  │  Project        │
│  (SQLite +  │  │  (asyncio       │  │  (git discovery │
│   JSON)     │  │   pub/sub)      │  │   + context)    │
└─────────────┘  └─────────────────┘  └─────────────────┘
```

## 模块说明

### 核心 (Core)

| 模块 | 文件数 | 说明 |
|------|--------|------|
| **`session/`** | 6 | **核心 agentic loop**。`prompt.py` 为消息入口，`processor.py` 驱动 LLM→Tool 循环，`llm.py` 封装 litellm 流式调用 |
| **`provider/`** | 4 | AI 提供商管理。自动发现环境变量/配置/auth 中的 provider，通过 litellm 统一调用 14+ 种 LLM |
| **`agent/`** | 1+4txt | Agent 系统。内置 7 个 agent：`build`(默认)、`plan`(只读)、`general`(子任务)、`explore`(搜索)、`compaction`/`title`/`summary`(辅助) |
| **`tool/`** | 13 | 工具系统。12 个内置工具 + 注册表：bash、read、edit、write、glob、grep、task、webfetch、websearch、question、todo、skill |

### 基础设施 (Infrastructure)

| 模块 | 文件数 | 说明 |
|------|--------|------|
| **`config/`** | 3 | JSONC 配置解析 + Pydantic 模型 + 多层合并（全局→环境→项目→.opencode） |
| **`storage/`** | 3 | SQLAlchemy 表定义（5 表：Project/Session/Message/Part/Permission）+ SQLite + JSON 文件存储 |
| **`bus/`** | 2 | asyncio pub/sub 事件总线，支持类型化订阅和全局广播 |
| **`permission/`** | 3 | 权限系统。Wildcard 规则评估 + ask/reply 阻塞流（allow/deny/ask） |
| **`project/`** | 2 | 项目发现（git root commit → ID）+ contextvars 实例管理 |
| **`auth/`** | 1 | API Key / OAuth 认证持久化 |

### 集成 (Integrations)

| 模块 | 文件数 | 说明 |
|------|--------|------|
| **`lsp/`** | 3 | LSP 集成。JSON-RPC 客户端 + 6 种预定义语言服务器 + 自动 spawn |
| **`mcp/`** | 1 | MCP 协议。支持 stdio/HTTP 传输，通过 Python `mcp` SDK 连接 |
| **`plugin/`** | 1 | 插件系统。Python 模块加载 + hook 注册/触发 |

### 应用层 (Application)

| 模块 | 文件数 | 说明 |
|------|--------|------|
| **`server/`** | 2 | FastAPI 应用。27 个 API 端点 + SSE 流式消息响应 |
| **`cli/`** | 1 | Click CLI。`serve`/`run --message`/`providers`/`models` 命令 |
| **`shell/`** | 1 | Shell 检测（排除 fish/nu）+ 进程树 kill |
| **`file/`** | 2 | 文件读取/搜索/列目录 + ripgrep 集成 |
| **`snapshot/`** | 1 | Shadow git repo，支持 track/diff/patch/restore |
| **`util/`** | 9 | 通用工具：log、filesystem、error、hash、ids、wildcard、context、paths、slug |

## 项目统计

```
Python 文件:     89
代码行数:       6,137
单元测试:        42 (全部通过)
内置工具:        12
API 路由:        27
Git 提交:         9
```

## 技术栈

| 用途 | 选择 |
|------|------|
| LLM 调用 | **litellm** (支持 100+ provider) |
| HTTP API | **FastAPI** + SSE |
| 数据库 | **SQLAlchemy** + SQLite |
| Schema | **Pydantic v2** |
| CLI | **Click** |
| 日志 | **structlog** |
| 包管理 | **uv** |
| 文件搜索 | **ripgrep** |
| ID 生成 | **python-ulid** |
| MCP | **mcp** (Python SDK) |

## API 端点

```
GET    /                           # 服务信息
GET    /health                     # 健康检查
GET    /session                    # 列出会话
POST   /session                    # 创建会话
GET    /session/{id}               # 获取会话
DELETE /session/{id}               # 删除会话
PUT    /session/{id}/title         # 设置标题
POST   /session/{id}/message       # 发送消息 (SSE 流式)
POST   /session/{id}/abort         # 中止会话
GET    /provider                   # 列出 provider
GET    /provider/{id}              # 获取 provider
GET    /agent                      # 列出 agent
GET    /config                     # 获取配置
POST   /config                     # 更新全局配置
GET    /file?path=...              # 读取文件
GET    /file/list                  # 列出目录
GET    /file/search?query=...      # 搜索文件
GET    /permission                 # 权限请求列表
POST   /permission/{id}            # 回复权限
GET    /mcp                        # MCP 状态
POST   /mcp/{name}/connect         # 连接 MCP
POST   /mcp/{name}/disconnect      # 断开 MCP
POST   /log                        # 写日志
```

---

## 已知问题与待优化

### 🔴 严重 Bug (5)

| # | 位置 | 问题 |
|---|------|------|
| 1 | `provider/provider.py` | **Provider 不加载内置模型目录** — `models_dev.py` 存在但从未被 `_init_state()` 调用。设置 `ANTHROPIC_API_KEY` 后因 `models` 为空无法使用 |
| 2 | `mcp/mcp.py` | **MCP 连接立即关闭** — `async with` 退出后 session 失效，`self._client` 指向已关闭的连接 |
| 3 | `lsp/client.py:113` | **LSP 消息路由缺陷** — `if "id" in msg and "id" in msg` 重复条件，无法区分 response 和 server request |
| 4 | `lsp/client.py:78` | **LSP 消息可能不发送** — `stdin.write()` 后缺少 `await drain()` |
| 5 | `session/prompt.py:175` | **不可靠的变量检测** — `'iteration' in dir()` 不是检查循环变量是否存在的正确方式 |

### 🟡 功能缺失 (14)

| # | 模块 | 缺失功能 |
|---|------|----------|
| 1 | `session/` | **消息不持久化** — `MessageTable`/`PartTable` 已定义但未使用，消息只在内存中 |
| 2 | `session/` | **无 Compaction** — 返回 `"compact"` 时只 break，无上下文压缩逻辑 |
| 3 | `session/` | **无 Abort 机制** — LLM 流不可取消 |
| 4 | `session/` | **无会话恢复** — 无法从数据库加载历史继续对话 |
| 5 | `session/` | **无 Snapshot 集成** — 文件变更前后不创建快照 |
| 6 | `session/` | **Permission 未连接** — `PermissionManager` 已实现但从未在 processor 中调用 |
| 7 | `provider/` | **transform 未应用** — `build_litellm_kwargs()` 从未被调用 (max_tokens/reasoning/headers) |
| 8 | `provider/` | **litellm 缺少 `stream_options`** — 流式模式下 usage 可能全零 |
| 9 | `tool/edit.py` | **编辑后无 LSP 通知/Snapshot/事件广播** |
| 10 | `tool/task.py` | **子 agent 无 agentic loop** — 只运行单次 LLM 调用 |
| 11 | `tool/question.py` | **不阻塞等待用户回复** — 直接返回问题文本 |
| 12 | `server/app.py` | **Permission/MCP 路由是 stub** — 返回空数据 |
| 13 | `agent/prompts/` | **4 个 prompt 模板全是 placeholder** — 需从原版迁移 |
| 14 | `processor.py` | **工具串行执行** — 原版支持并行 |

### 🟢 设计优化 (5)

| # | 问题 | 建议 |
|---|------|------|
| 1 | **同步 SQLAlchemy + 异步 FastAPI** | 迁移到 `create_async_engine` + `aiosqlite` |
| 2 | **全局可变状态过多** | `_state`/`_cached`/`_cached_agents` 等 — 改为依赖注入 |
| 3 | **配置缓存不区分 directory** | 不同项目目录会共享同一份缓存配置 |
| 4 | **`prompt.py` 访问 `providermod._state`** | 通过 provider 公共 API 获取 key |
| 5 | **`ids.py` descending 算法** | 字符反转不保证正确排序 — 改用时间戳补码 |

### 📊 测试覆盖

| 有测试 | 无测试 |
|--------|--------|
| util (hash/wildcard/ids/slug/error) | session/prompt, processor, llm |
| permission (evaluate/from_config) | provider, agent, config |
| session (message, session DB CRUD) | server, storage, bus |
| tool (read/write/edit/glob/question/todo/skill/registry) | lsp, mcp, snapshot, plugin, auth |
| file (read/list_dir), shell, project | tool/bash, webfetch, websearch, task |

### 📋 TODO 清单

| 文件 | 描述 |
|------|------|
| `server/app.py:102` | 实现 abort（共享 signal） |
| `server/app.py:167` | 连接 PermissionManager |
| `server/app.py:173` | 连接 PermissionManager.reply |
| `lsp/lsp.py:95` | 从运行的 LSP server 收集 diagnostics |
| `tool/base.py:26` | `ask_permission()` 连接到 PermissionManager |
| `agent/prompts/*.txt` | 从原版迁移 prompt 模板 |

---

## 与原版 TypeScript 的对比

| 功能 | 原版 (TS) | Python 版 | 差距 |
|------|-----------|-----------|------|
| Agentic Loop | ✅ 完整 | ✅ 基础可用 | 缺 compaction/abort/snapshot |
| 工具 | 20+ | 12 | 缺 apply_patch/multiedit/ls/lsp_tool |
| Provider | 20+ (Vercel AI SDK) | 14 (litellm) | litellm 覆盖更广但无内置模型目录 |
| 消息持久化 | SQLite (完整) | Session SQLite + 消息内存 | 需补全 Message/Part 持久化 |
| LSP | 20+ 语言 + 完整通信 | 6 语言 + 基础 JSON-RPC | 需补全 didChange/diagnostics |
| MCP | 完整 (stdio/HTTP/SSE) | 框架就绪 (连接不持久) | 需修复 async with 生命周期 |
| TUI | 完整 (opentui/SolidJS) | ❌ 未实现 | 计划用 Textual |
| 权限 | 完整 (集成到工具执行) | 已实现但未集成 | 需在 processor 中调用 |

---

## 开发指南

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# Lint
uv run ruff check opencode/

# 类型检查
uv run mypy opencode/
```

## License

MIT

---

*完整重构计划和进度追踪见 [PLAN.md](./PLAN.md)*
