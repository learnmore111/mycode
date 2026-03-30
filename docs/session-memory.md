# Session Memory — 会话记忆功能

## 概述

Session Memory 是 OpenCode 的一个可选功能，可以在每次对话结束时自动保存会话摘要笔记。这些笔记包含：

- 会话时间和时长
- 用户请求摘要
- 工具使用统计
- 修改/读取的文件
- AI 生成的会话总结
- 关键决策和待办事项

## 启用功能

在配置文件中添加以下配置来启用 Session Memory：

### 全局配置 (`~/.config/opencode/opencode.json`)

```json
{
  "sessionMemory": {
    "enabled": true,
    "noteLanguage": "zh",
    "minDurationMinutes": 1,
    "minUserPrompts": 1,
    "maxNotesPerProject": 50,
    "maxRecentForContext": 5,
    "model": {
      "provider": "openai",
      "name": "gpt-4o-mini",
      "apiKey": "your-api-key-here"
    }
  }
}
```

### 项目级配置 (`.opencode/opencode.json`)

```json
{
  "sessionMemory": {
    "enabled": true,
    "noteLanguage": "en"
  }
}
```

## 配置选项

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `false` | 是否启用会话记忆 |
| `noteLanguage` | string | `"en"` | 笔记语言 (`"zh"` 或 `"en"`) |
| `minDurationMinutes` | number | `1` | 最短会话时长（分钟），低于此值不保存 |
| `minUserPrompts` | number | `1` | 最少用户提问数，低于此值不保存 |
| `maxNotesPerProject` | number | `50` | 每个项目最多保存的笔记数 |
| `maxRecentForContext` | number | `5` | 加载的最近笔记数（用于上下文） |
| `model` | object | `null` | AI 模型配置（用于生成摘要） |

### 模型配置

```json
{
  "model": {
    "provider": "openai",       // "openai" 或 "anthropic"
    "name": "gpt-4o-mini",      // 模型名称
    "baseURL": "https://...",   // 可选：自定义 API 地址
    "apiKey": "sk-...",         // 直接提供 API Key
    "apiKeyEnv": "OPENAI_API_KEY"  // 或从环境变量读取
  }
}
```

支持的 Provider：
- `openai` - OpenAI API（支持兼容接口如火山引擎、智谱等）
- `anthropic` - Anthropic Claude API

## 存储位置

笔记存储在 `~/.local/share/opencode/memory/` 目录下：

```
~/.local/share/opencode/memory/
├── notes/
│   ├── 2024-01-15/
│   │   ├── 14-30-00_abc123.md
│   │   └── 16-45-00_def456.md
│   └── 2024-01-16/
│       └── ...
└── index.json
```

## 笔记格式

每个笔记是一个 Markdown 文件：

```markdown
# Session Note

- **Session ID**: abc123
- **Time**: 2024-01-15T14:30:00 → 2024-01-15T15:15:00
- **Duration**: 45 min
- **Project**: /path/to/project
- **Topics**: Python, Configuration
- **Tools**: read×12, edit×5, bash×3

## Summary
This session focused on implementing a new authentication module...

## Key Decisions
- Chose JWT over session tokens for stateless scaling
- Implemented middleware pattern for auth checking

## Open TODOs
- [ ] Add unit tests for auth middleware
- [ ] Update API documentation

## Files Modified
- `src/auth/middleware.py`
- `src/routes/user.py`

---
*auto-saved by opencode session-memory at 2024-01-15T15:15:32*
```

## CLI 命令

### 查看最近笔记

在交互模式下使用 `/memory` 命令：

```
✨ /memory
  Recent session notes (3):
    • 2024-01-15 (45min) - Python, Configuration
    • 2024-01-14 (30min) - Documentation
    • 2024-01-13 (60min) - Python, Testing
```

### 其他相关命令

- `/help` - 显示所有命令帮助
- `/clear` - 清除当前会话（不影响已保存的笔记）

## 工作流程

1. **会话开始**：系统记录会话开始时间，可选加载最近笔记作为上下文
2. **会话进行中**：正常的编码对话
3. **会话结束**（Ctrl+D 或 `/quit`）：
   - 解析对话历史，提取结构化数据
   - 调用 AI 生成摘要（如配置了模型）
   - 保存笔记到文件
   - 更新索引

## 注意事项

1. **API Key 安全**：建议使用 `apiKeyEnv` 从环境变量读取 API Key，避免在配置文件中明文存储
2. **存储空间**：笔记按项目限制数量，超出后会自动清理旧笔记
3. **最小阈值**：短会话或交互过少的会话不会被保存，避免产生无意义的笔记
4. **无 AI 模式**：如果未配置模型，系统会生成简单的统计摘要

## 示例配置

### 使用 OpenAI

```json
{
  "sessionMemory": {
    "enabled": true,
    "noteLanguage": "en",
    "model": {
      "provider": "openai",
      "name": "gpt-4o-mini",
      "apiKeyEnv": "OPENAI_API_KEY"
    }
  }
}
```

### 使用 Anthropic Claude

```json
{
  "sessionMemory": {
    "enabled": true,
    "noteLanguage": "zh",
    "model": {
      "provider": "anthropic",
      "name": "claude-haiku-3-5-20241022",
      "apiKeyEnv": "ANTHROPIC_API_KEY"
    }
  }
}
```

### 使用兼容 OpenAI 的第三方接口

```json
{
  "sessionMemory": {
    "enabled": true,
    "model": {
      "provider": "openai",
      "name": "doubao-pro-4k",
      "baseURL": "https://ark.cn-beijing.volces.com/api/v3",
      "apiKeyEnv": "VOLC_API_KEY"
    }
  }
}
```

## 文件索引

相关源代码文件：

| 文件 | 说明 |
|------|------|
| `opencode/session/memory/__init__.py` | 模块入口 |
| `opencode/session/memory/memory.py` | 核心实现 |
| `opencode/config/models.py` | 配置模型定义 |
| `opencode/cli/main.py` | CLI 集成 |
| `tests/test_session_memory.py` | 单元测试 |
