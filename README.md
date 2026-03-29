# OpenCode (Python)

**Open source AI coding agent — Python edition.**

Python 重写版 [OpenCode](https://github.com/anomalyco/opencode)，一个不绑定特定 AI 提供商的开源编程 Agent 平台。

---

## 快速开始

```bash
# 安装
uv sync

# 查看帮助
uv run opencode --help

# 设置 API Key（任意 OpenAI 兼容接口）
export OPENAI_API_KEY="your-token"
export OPENAI_API_BASE="https://your-endpoint.com/v1"  # 可选，默认 OpenAI 官方

# 交互式模式（Rich UI + Markdown 渲染 + 上下文进度条）
uv run opencode run

# Headless 模式（单次执行，适合脚本/CI）
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
│  CLI (click)  │  HTTP API (FastAPI)  │  Interactive CLI   │
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
│              │  Compaction 检测 ──┤                     │
│              │     │              │                     │
│              │  continue/stop ◄──┘                     │
│              └────────────────────┘                     │
│                      │                                  │
│              Message 持久化 (SQLite)                     │
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

| 模块 | 说明 |
|------|------|
| **`session/`** | **核心 agentic loop**。`prompt.py` 消息入口 + 消息持久化，`processor.py` LLM→Tool 循环 + 权限检查 + doom loop 检测，`compaction.py` 上下文压缩（token 估算 + LLM 摘要），`llm.py` litellm 流式调用 + token 统计（input/output/reasoning/cache）+ cost 计算 |
| **`provider/`** | AI 提供商管理。自动发现环境变量/配置/auth 中的 provider，`transform.py` 按模型类型调整参数（temperature/reasoning/max_tokens），通过 litellm 统一调用 14+ 种 LLM |
| **`agent/`** | Agent 系统。内置 7 个 agent：`build`(默认全权限)、`plan`(只读)、`general`(子任务)、`explore`(搜索)、`compaction`/`title`/`summary`(辅助) |
| **`tool/`** | 工具系统。12 个内置工具 + 注册表：bash、read、edit、write、glob、grep、task、webfetch、websearch、question、todo、skill |

### 基础设施 (Infrastructure)

| 模块 | 说明 |
|------|------|
| **`config/`** | JSONC 配置解析 + Pydantic v2 模型 + 多层合并（全局→环境→项目→.opencode）|
| **`storage/`** | SQLAlchemy 表定义（5 表：Project/Session/Message/Part/Permission）+ SQLite + JSON 文件存储 |
| **`bus/`** | asyncio pub/sub 事件总线，17 种事件类型，支持类型化订阅、通配符订阅和全局广播 |
| **`permission/`** | 权限系统。Wildcard 规则评估 + ask/reply 阻塞流（allow/deny/ask），集成到 processor 的 tool 执行中 |
| **`project/`** | 项目发现（git root commit → ID）+ contextvars 实例管理 |
| **`auth/`** | API Key / OAuth / WellKnown 认证持久化 |
| **`snapshot/`** | Shadow git repo，支持 track/diff/patch/restore + commit 历史追踪 |

### 集成 (Integrations)

| 模块 | 说明 |
|------|------|
| **`lsp/`** | LSP 集成。JSON-RPC 客户端 + 26 种预定义语言服务器（TypeScript/Python/Go/Rust/C++/Java/C#/Ruby/PHP/Kotlin/Swift 等）+ 自动 spawn + diagnostics 收集 |
| **`mcp/`** | MCP 协议。支持 stdio/HTTP 传输，自动重连（最多 3 次），工具缓存刷新 |
| **`plugin/`** | 插件系统。Python 模块动态加载 + 7 种 hook 类型（before/after_tool、before/after_prompt 等）+ 链式传递 |

### 应用层 (Application)

| 模块 | 说明 |
|------|------|
| **`server/`** | FastAPI 应用。8 个路由模块（session/provider/config/file/permission/mcp/event/project），26 个 API 端点 + SSE 流式消息 + SSE 全局事件订阅 |
| **`cli/`** | Click CLI + Rich 交互式 REPL。欢迎面板 + Markdown 渲染 + Spinner 动画 + 上下文进度条 + Token/Cost 统计。命令：`serve`/`run`/`providers`/`models` + `config show/path/set` + `session list/delete` + `mcp list` + `snapshot track/diff` |
| **`shell/`** | Shell 检测（排除 fish/nu）+ 进程树 kill |
| **`file/`** | 文件读取/模糊搜索/列目录 + ripgrep 集成 |
| **`util/`** | 通用工具：log (structlog)、filesystem (aiofiles)、error、hash、ids (ULID)、wildcard、context、paths (XDG)、slug |

## 项目统计

```
Python 文件:      91
代码行数:       7,593
单元测试:        165 (全部通过)
内置工具:         12
API 路由:         26
LSP 语言:         26
CLI 命令:         13
Lint 错误:         0
```

## 技术栈

| 用途 | 选择 |
|------|------|
| LLM 调用 | **litellm** (支持 100+ provider) |
| HTTP API | **FastAPI** + **SSE** (sse-starlette) |
| 数据库 | **SQLAlchemy** + SQLite |
| Schema | **Pydantic v2** |
| CLI | **Click** + **Rich** (Markdown/Spinner/Panel) + **prompt_toolkit** |
| 日志 | **structlog** |
| 包管理 | **uv** |
| 文件搜索 | **ripgrep** (subprocess) |
| ID 生成 | **python-ulid** |
| MCP | **mcp** (Python SDK) |
| 配置解析 | **json5** |
| 模糊搜索 | **rapidfuzz** |

## CLI 命令

```bash
opencode --help                     # 查看所有命令
opencode serve [--port 4096]        # 启动 API 服务器
opencode run [DIR]                  # 交互式模式（默认）
opencode run [DIR] -p "message"     # Headless 模式运行
opencode run [DIR] -a plan          # 指定 agent 模式
opencode providers                  # 列出可用 AI 提供商
opencode models                     # 列出可用模型
opencode config show [DIR]          # 查看合并后配置
opencode config path                # 查看全局配置路径
opencode config set KEY VALUE       # 设置全局配置项
opencode session list [-n 20]       # 列出最近会话
opencode session delete ID          # 删除会话
opencode mcp list                   # 列出 MCP 服务器
opencode snapshot track [DIR]       # 创建快照
opencode snapshot diff HASH [DIR]   # 查看快照 diff
```

### 交互式模式特性

```
┌──────────────────────────────────────────────────┐
│ ▐█▛█▛█▌ Welcome to OpenCode v0.1.0!             │
│ ▐█████▌ Type /help for commands, Ctrl+D to exit. │
│                                                  │
│ Directory: .                                     │
│ Model: default                                   │
│ Agent: build                                     │
└──────────────────────────────────────────────────┘

✨ 你好
  你好！有什么可以帮你的吗？
  ─ 2.4s · in:3517 out:24
  Context ▐██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▌ 3.5K/96.0K (4%)
```

- **Rich Markdown 渲染** — AI 回复自动渲染代码高亮、列表、标题
- **Spinner 动画** — 等待 AI 时显示 dots spinner + 耗时
- **Token 统计** — 每轮显示 input/output/reasoning/cache token 数
- **Cost 计算** — 基于 litellm 定价数据自动计算费用
- **上下文进度条** — 颜色编码显示上下文窗口使用率（绿→黄→橙→红）
- **斜杠命令** — `/help` `/clear` `/history` `/quit`

## API 端点

```
GET    /                           # 服务信息
GET    /health                     # 健康检查

# Session
GET    /session                    # 列出会话
POST   /session                    # 创建会话
GET    /session/{id}               # 获取会话
DELETE /session/{id}               # 删除会话
PUT    /session/{id}/title         # 设置标题
POST   /session/{id}/message       # 发送消息 (SSE)
POST   /session/{id}/abort         # 中止会话

# Provider / Agent
GET    /provider                   # 列出 provider
GET    /provider/{id}              # 获取 provider
GET    /agent                      # 列出 agent

# Config
GET    /config                     # 获取配置
POST   /config                     # 更新全局配置

# File
GET    /file?path=...              # 读取文件
GET    /file/list                  # 列出目录
GET    /file/search?query=...      # 模糊搜索文件

# Permission
GET    /permission                 # 待处理权限列表
POST   /permission/{id}            # 回复权限请求

# MCP
GET    /mcp                        # MCP 服务器状态
POST   /mcp/{name}/connect         # 连接 MCP
POST   /mcp/{name}/disconnect      # 断开 MCP

# Event / Project / Log
GET    /event                      # SSE 全局事件订阅
GET    /project                    # 获取项目信息
GET    /project/current            # 当前项目上下文
POST   /log                        # 写日志
```

## 支持的 AI 提供商

通过环境变量自动发现：

| 提供商 | 环境变量 |
|--------|----------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Google | `GOOGLE_API_KEY` / `GEMINI_API_KEY` |
| xAI | `XAI_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepInfra | `DEEPINFRA_API_KEY` |
| Cohere | `COHERE_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| Together AI | `TOGETHERAI_API_KEY` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` |
| Azure | `AZURE_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Cerebras | `CEREBRAS_API_KEY` |

对于以上内置提供商，**只需设置对应环境变量即可**，litellm 会自动路由到正确的 API endpoint。

### 自定义 Provider

如果使用 OpenAI 兼容的第三方服务（Azure、国内中转、自部署 vLLM/Ollama 等），在项目根目录创建 `opencode.json`：

```jsonc
{
  // 使用自定义 endpoint
  "provider": {
    "my-provider": {
      "api": "https://your-api-endpoint.com/v1",   // 自定义 endpoint
      "models": {
        "my-model": {
          "id": "gpt-4o",                           // 实际模型 ID
          "name": "My Custom Model",
          "tool_call": true,
          "limit": {
            "context": 131072,                      // 上下文窗口大小（token）
            "output": 8192                          // 最大输出 token
          }
        }
      }
    }
  },
  // 设置默认模型
  "model": "my-provider/my-model"
}
```

> **注意**：自定义模型必须手动设置 `limit.context`，否则上下文进度条和自动 compaction 无法工作。内置 provider（Anthropic/OpenAI/Google 等）会从 models.dev 数据库自动获取。

也可以通过环境变量指定 base URL（litellm 原生支持）：

```bash
export OPENAI_API_BASE=https://your-proxy.com/v1
export OPENAI_API_KEY=sk-xxx
uv run opencode run --message "hello"
```

## 后续路线图

| 功能 | 状态 |
|------|------|
| 交互式 CLI (Rich + prompt_toolkit) | ✅ 已完成 |
| Token 统计 + Cost 计算 + 上下文进度条 | ✅ 已完成 |
| 会话恢复（从 DB 加载历史继续对话） | 待实现 |
| 工具并行执行 | 待实现 |
| apply_patch 工具 (GPT-5 格式) | 待实现 |
| LSP didChange 通知 | 待实现 |
| Python SDK (`opencode-sdk`) | 待评估 |

---

## 开发指南

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
uv run pytest tests/ -v

# 运行测试 (带覆盖率)
uv run pytest tests/ --cov=opencode --cov-report=term-missing

# Lint
uv run ruff check opencode/

# 自动修复 Lint
uv run ruff check opencode/ --fix

# 类型检查
uv run mypy opencode/
```

### 项目结构

```
opencode/
├── agent/          # Agent 定义 (7 内置 agent)
├── auth/           # API Key / OAuth 持久化
├── bus/            # 事件总线 (asyncio pub/sub)
├── cli/            # CLI 入口 (Click, 13 命令)
├── config/         # JSONC 配置 + Pydantic 模型
├── file/           # 文件操作 + ripgrep
├── lsp/            # LSP 集成 (26 语言)
├── mcp/            # MCP 协议 (stdio/HTTP, 自动重连)
├── permission/     # 权限系统 (allow/deny/ask)
├── plugin/         # 插件系统 (7 hook 类型)
├── project/        # 项目发现 + contextvars
├── provider/       # AI Provider (14+ provider, litellm)
├── server/         # FastAPI (8 路由模块, 26 端点)
│   └── routes/     # session/provider/config/file/permission/mcp/event/project
├── session/        # 核心 agentic loop + 消息持久化 + compaction
├── shell/          # Shell 检测
├── snapshot/       # Shadow git (track/diff/restore/history)
├── storage/        # SQLite + JSON 存储
├── tool/           # 12 内置工具 + 注册表
└── util/           # 通用工具 (9 模块)
```

## License

MIT

---

*完整重构计划和进度追踪见 [PLAN.md](./PLAN.md)*
