# OpenCode MCP Server

将 opencode AI 编程助手暴露为 MCP (Model Context Protocol) 工具，支持外部 AI Agent（如 CodeBuddy、Claude Desktop、Cursor 等）与 opencode 进行多轮对话交互。

## 功能概述

| 工具 | 功能 | 用途 |
|------|------|------|
| `server_status` | 检查服务器状态 | 验证连接 |
| `create_session` | 创建对话会话 | 开始新对话 |
| `send_message` | 发送消息 | **核心工具，支持多轮对话** |
| `abort_session` | 中止会话 | 停止长时间运行的操作 |
| `list_sessions` | 列出历史会话 | 查看/恢复历史对话 |
| `delete_session` | 删除会话 | 清理历史 |
| `list_models` | 列出可用模型 | 查看支持的 LLM |
| `get_config` | 获取配置 | 查看当前设置 |
| `read_file` | 读取文件 | 获取项目文件内容 |
| `list_files` | 列出文件 | 浏览目录结构 |
| `search_files` | 搜索文件 | 按名称搜索文件 |

## 快速开始

### 1. 启动 OpenCode HTTP 服务器

```bash
cd /path/to/your/project
uv run opencode serve --port 4096
```

### 2. 启动 MCP Server

**方式 A: CLI 子命令**
```bash
uv run opencode mcp serve --directory /path/to/project
```

**方式 B: 脚本入口**
```bash
OPENCODE_DIRECTORY=/path/to/project uv run opencode-mcp
```

**方式 C: Python 模块**
```bash
OPENCODE_DIRECTORY=/path/to/project uv run python -m opencode.mcp_server
```

### 3. 在 CodeBuddy 中配置

编辑 `~/.codebuddy/mcp.json` 或项目的 `.codebuddy/mcp.json`:

```json
{
  "mcpServers": {
    "opencode": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/Users/your-username/Desktop/code-agent/opencode_py",
        "opencode-mcp"
      ],
      "env": {
        "OPENCODE_URL": "http://127.0.0.1:4096",
        "OPENCODE_DIRECTORY": "/path/to/your/project"
      }
    }
  }
}
```

## 多轮对话工作流

```
┌─────────────────────────────────────────────────────────────┐
│  CodeBuddy / Claude Desktop / Cursor                        │
│  (调用方 Agent)                                              │
└─────────────────┬───────────────────────────────────────────┘
                  │ MCP Protocol (stdio)
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  OpenCode MCP Server                                         │
│  (opencode/mcp_server/server.py)                            │
└─────────────────┬───────────────────────────────────────────┘
                  │ HTTP + SSE
                  ▼
┌─────────────────────────────────────────────────────────────┐
│  OpenCode HTTP Server                                        │
│  (opencode serve --port 4096)                               │
└─────────────────────────────────────────────────────────────┘
                  │
                  ▼
          ┌──────────────┐
          │   LLM API    │
          │ (DeepSeek等) │
          └──────────────┘
```

### 典型对话流程

```python
# 1. 检查服务器状态
result = server_status()
# {"status": "ok", "version": "0.1.0"}

# 2. 创建会话
result = create_session(title="修复登录Bug")
# {"session_id": "abc123", "title": "修复登录Bug", "created": 1774855095402}

# 3. 第一轮对话
result = send_message(session_id="abc123", message="请帮我分析 auth.py 的代码")
# 返回 opencode agent 的分析结果

# 4. 第二轮对话（上下文自动保持）
result = send_message(session_id="abc123", message="请修复其中的安全漏洞")
# opencode 记得之前分析的内容，直接进行修复

# 5. 第三轮对话
result = send_message(session_id="abc123", message="添加单元测试")
# 继续在同一上下文中工作
```

## 测试结果

### 测试环境
- macOS Darwin
- Python 3.14
- opencode 0.1.0
- MCP SDK 1.x
- 模型: openai/deepseek-v3.2

### 测试用例

| 测试项 | 状态 | 说明 |
|--------|------|------|
| 服务器连接 | ✅ PASS | `server_status()` 返回 `{"status": "ok"}` |
| 会话创建 | ✅ PASS | 成功创建并返回 session_id |
| 多轮对话 | ✅ PASS | 4 轮对话全部成功，上下文保持 |
| 上下文保留 | ✅ PASS | 后续问题能正确引用前文内容 |
| 工具调用 | ✅ PASS | opencode agent 成功调用 read/search 工具 |
| 会话清理 | ✅ PASS | 成功删除测试会话 |

### 测试输出示例

```
============================================================
  TEST 3: Multi-turn Conversation
============================================================

--- Turn 1: Ask about the project ---
📌 send_message() - Turn 1:
{
  "model": "openai/deepseek-v3.2",
  "agent": "build",
  "response": "opencode 是一个开源的 AI 编程助手...",
  "tokens": {"input": 7818, "output": 93},
  "context": {"used": 7911, "limit": 65536}
}

--- Turn 2: Follow-up question (context test) ---
📌 send_message() - Turn 2:
{
  "model": "openai/deepseek-v3.2",
  "response": "opencode 支持以下 LLM 提供商：1. OpenAI...",
  "tokens": {"input": 32744, "output": 205}
}
// 注意 input tokens 增加，说明包含了对话历史

--- Turn 4: Request tool usage (file read) ---
📌 send_message() - Turn 4 (with tool):
{
  "response": "项目版本号：0.1",
  "tool_calls": [
    {"tool": "read", "status": "completed", "output": "[project]\nname = \"opencode\"\nversion = \"0.1.0\"..."}
  ]
}
// opencode agent 自动调用了 read 工具

📊 Conversation Statistics:
   Turn 1: 7818 in / 93 out tokens
   Turn 2: 32744 in / 205 out tokens  ← 上下文累积
   Turn 3: 39241 in / 346 out tokens  ← 上下文继续累积
   Turn 4: 8098 in / 58 out tokens    ← 新话题，上下文重置
   Total: 88603 tokens
```

## 与 ACP 方案对比

| 维度 | MCP（当前方案） | ACP（未实现） |
|------|----------------|--------------|
| **通信模式** | 🔴 单向：调用方驱动 | ✅ 双向对等通信 |
| **协作关系** | 🔴 主从：opencode 是工具 | ✅ 对等：两个独立 agent |
| **主动通知** | 🔴 需轮询 | ✅ 事件推送 |
| **任务编排** | 🔴 线性调用链 | ✅ 多 agent 网络 |
| **实现复杂度** | ✅ 简单（仅包装 HTTP） | 🔴 需双端实现 ACP |
| **生态支持** | ✅ CodeBuddy/Cursor 原生支持 | 🔴 需自建 |
| **够用程度** | ✅ 满足多轮对话需求 | — |

### MCP 的局限

1. **单向驱动**：opencode 无法主动向 CodeBuddy 请求信息
2. **无事件推送**：长任务需要阻塞等待或轮询
3. **工具级集成**：opencode 被降级为"高级工具"，不是独立 agent

### 何时考虑 ACP

- 需要 opencode 主动回调（如请求额外文件）
- 需要多 agent 协作编排
- 需要实时进度推送

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENCODE_URL` | `http://127.0.0.1:4096` | OpenCode HTTP 服务器地址 |
| `OPENCODE_DIRECTORY` | `.` | 项目目录路径 |

## 常见问题

### Q: 为什么 `list_models()` 返回空列表？

A: `opencode serve` 命令需要在正确的项目目录下启动，且需要设置 API Key 环境变量：
```bash
export OPENAI_API_KEY=xxx  # 或其他 provider 的 key
cd /path/to/project
uv run opencode serve --port 4096
```

### Q: 如何在不同项目间切换？

A: 修改 `OPENCODE_DIRECTORY` 环境变量，指向目标项目目录。每个项目可以有独立的 session 历史。

### Q: 长时间操作会超时吗？

A: MCP Server 默认 timeout 为 300 秒（5 分钟）。如果 opencode agent 的操作超过此时间，需要调整 `httpx.AsyncClient` 的 timeout 参数。

## 文件结构

```
opencode/mcp_server/
├── __init__.py          # 包初始化
├── __main__.py          # python -m 入口
└── server.py            # MCP Server 实现
```

## 参考

- [MCP Protocol Specification](https://modelcontextprotocol.io/)
- [FastMCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [OpenCode HTTP API](../opencode/server/)
