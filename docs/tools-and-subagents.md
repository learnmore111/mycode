# OpenCode Tools 与 Subagent 完整文档

> 本文档详细记录了 opencode 项目中所有内置 Tools 和 Subagent 的实现细节。

---

## 目录

- [一、架构概览](#一架构概览)
- [二、Tools 系统](#二tools-系统)
  - [基础架构](#基础架构)
  - [工具注册机制](#工具注册机制)
  - [内置工具列表](#内置工具列表)
- [三、Subagent 系统](#三subagent-系统)
  - [Agent 类型](#agent-类型)
  - [Subagent 详解](#subagent-详解)
  - [启动机制](#启动机制)
- [四、文件路径索引](#四文件路径索引)

---

## 一、架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenCode 架构                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Session / Processor (会话处理器)                            │
│       │                                                      │
│       ├──► Tools (工具系统)                                   │
│       │       ├── bash (执行命令)                             │
│       │       ├── read/edit/write (文件操作)                  │
│       │       ├── glob/grep (搜索)                           │
│       │       ├── task (启动 subagent)                       │
│       │       ├── webfetch/websearch (网络)                   │
│       │       └── ...                                        │
│       │                                                      │
│       └──► Agents (代理系统)                                  │
│               ├── build (默认主代理)                          │
│               ├── plan (计划模式)                             │
│               ├── general (通用 subagent)                    │
│               ├── explore (探索 subagent)                    │
│               └── compaction/title/summary (内部代理)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、Tools 系统

### 基础架构

**目录位置**: `/opencode/tool/`

**核心文件**:

| 文件 | 作用 |
|------|------|
| `base.py` | 工具基类 `ToolInfo`、上下文 `ToolContext`、结果 `ToolResult` |
| `registry.py` | 工具注册中心，管理所有已注册工具 |
| `*.py` | 各个具体工具实现 |

### 核心数据结构

#### ToolResult - 工具执行结果

```python
@dataclass
class ToolResult:
    title: str              # 工具执行标题（显示用）
    output: str             # 输出内容
    metadata: dict[str, Any] = field(default_factory=dict)  # 元数据
```

#### ToolContext - 工具执行上下文

```python
@dataclass
class ToolContext:
    session_id: str         # 会话 ID
    message_id: str         # 消息 ID
    agent: str              # 当前代理名称
    abort: Any = None       # 中止信号（asyncio.Event）
    call_id: str = ""       # 调用 ID
    messages: list[Any] = field(default_factory=list)  # 消息历史
```

#### ToolInfo - 工具基类

```python
class ToolInfo(ABC):
    id: str = ""            # 工具唯一标识
    description: str = ""   # 工具描述
    
    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]:
        """返回 JSON Schema 参数定义"""
        ...
    
    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行工具"""
        ...
    
    def to_llm_tool(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": self.parameters_schema(),
            },
        }
```

### 工具注册机制

```python
# registry.py
_tools: dict[str, ToolInfo] = {}  # 全局工具存储

def register(tool: ToolInfo) -> None:
    """注册单个工具"""
    _tools[tool.id] = tool

def register_builtins() -> None:
    """注册所有内置工具"""
    from opencode.tool import bash, edit, glob_tool, grep, question, read, skill, task, todo, webfetch, websearch, write
    for mod in [bash, read, edit, write, glob_tool, grep, task, webfetch, websearch, question, todo, skill]:
        if hasattr(mod, "tool"):
            register(mod.tool)
    
    # 实验性 batch 工具（需配置启用）
    if cfg.experimental and cfg.experimental.batch_tool:
        from opencode.tool import batch
        register(batch.tool)

def get(tool_id: str) -> ToolInfo | None:
    """获取工具"""
    return _tools.get(tool_id)

def all_tools() -> list[ToolInfo]:
    """获取所有工具"""
    return list(_tools.values())

def to_llm_tools() -> list[dict[str, Any]]:
    """转换为 LLM 工具格式"""
    return [t.to_llm_tool() for t in _tools.values()]
```

---

### 内置工具列表

共 **13 个** 内置工具（含 1 个实验性工具）：

| # | 工具 ID | 文件 | 功能 |
|---|---------|------|------|
| 1 | bash | `bash.py` | 执行 Shell 命令 |
| 2 | read | `read.py` | 读取文件内容 |
| 3 | edit | `edit.py` | 编辑文件（字符串替换） |
| 4 | write | `write.py` | 写入/创建文件 |
| 5 | glob | `glob_tool.py` | 文件模式搜索 |
| 6 | grep | `grep.py` | 内容搜索（正则） |
| 7 | task | `task.py` | 启动子代理 |
| 8 | webfetch | `webfetch.py` | 获取 URL 内容 |
| 9 | websearch | `websearch.py` | 网络搜索 |
| 10 | question | `question.py` | 向用户提问 |
| 11 | todo | `todo.py` | 任务列表管理 |
| 12 | skill | `skill.py` | 加载技能文件 |
| 13 | batch | `batch.py` | 批量并行执行 ⚠️ 实验性 |

---

### 1. bash - Shell 命令执行

**文件**: `/opencode/tool/bash.py`

**功能**: 执行 shell 命令，用于运行命令、安装包或与系统交互。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `command` | string | ✅ | 要执行的 shell 命令 |
| `timeout` | integer | ❌ | 超时时间（毫秒），默认 120000 |

**实现核心**:

```python
class BashTool(ToolInfo):
    id = "bash"
    description = "Execute a shell command. Use this to run commands, install packages, or interact with the system."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = args.get("timeout", 120000) / 1000  # 转换为秒

        # 获取工作目录
        inst = current_or_none()
        cwd = inst.directory if inst else os.getcwd()

        # 选择 Shell（避免 fish/nu 兼容性问题）
        shell = os.environ.get("SHELL", "/bin/sh")
        if os.path.basename(shell) in ("fish", "nu"):
            shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            shell, "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env={**os.environ, "AGENT": "1"},  # 标记 Agent 环境
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")

        # 输出超过 100KB 时截断
        if len(output) > 100_000:
            output = output[:50_000] + f"\n\n... truncated ({len(output)} chars total) ...\n\n" + output[-50_000:]

        return ToolResult(
            title=command[:80],
            output=f"Exit code: {code}\n{output}" if code != 0 else output,
            metadata={"exit_code": code},
        )
```

**特性**:
- 自动检测并使用兼容的 Shell
- 支持超时控制
- 输出超过 100KB 时自动截断（保留头尾各 50KB）
- 设置 `AGENT=1` 环境变量标识 Agent 执行

---

### 2. read - 读取文件

**文件**: `/opencode/tool/read.py`

**功能**: 读取文件内容，支持行范围读取。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `file_path` | string | ✅ | 文件路径（相对于项目根目录） |
| `line_offset` | integer | ❌ | 起始行号（0-based） |
| `line_count` | integer | ❌ | 读取行数 |

**实现核心**:

```python
class ReadTool(ToolInfo):
    id = "read"
    description = "Read the contents of a file. Use line_offset and line_count for partial reads."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path = args["file_path"]
        offset = args.get("line_offset", 0)
        count = args.get("line_count")

        # 解析路径
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        # 读取文件
        content = Path(full).read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        total = len(lines)

        # 处理行范围
        if offset or count:
            end = min(offset + count, total) if count else total
            lines = lines[offset:end]

        # 添加行号（6 位右对齐）
        numbered = "\n".join(f"{i + offset + 1:6d}:{line}" for i, line in enumerate(lines))
        
        return ToolResult(
            title=f"Read {file_path}",
            output=numbered,
            metadata={"lines": len(lines), "total": total},
        )
```

**特性**:
- 支持相对路径和绝对路径
- 支持部分读取（指定行范围）
- 输出带行号（格式：`    1:code`）

---

### 3. edit - 文件编辑

**文件**: `/opencode/tool/edit.py`

**功能**: 通过精确字符串替换来编辑文件。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `file_path` | string | ✅ | 文件路径 |
| `old_string` | string | ✅ | 要查找替换的精确字符串 |
| `new_string` | string | ✅ | 替换内容 |

**实现核心**:

```python
class EditTool(ToolInfo):
    id = "edit"
    description = "Edit a file by replacing an exact string with new content. The old_string must match exactly (including whitespace)."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path, old_string, new_string = args["file_path"], args["old_string"], args["new_string"]
        
        # 读取文件
        content = Path(full).read_text(encoding="utf-8")
        count = content.count(old_string)
        
        # 检查唯一性
        if count == 0:
            return ToolResult(
                title=f"Edit {file_path}",
                output="old_string not found in file. Make sure it matches exactly including whitespace.",
                metadata={"success": False},
            )
        if count > 1:
            return ToolResult(
                title=f"Edit {file_path}",
                output=f"old_string found {count} times. It must be unique. Add more surrounding context.",
                metadata={"success": False},
            )

        # 替换并写入
        new_content = content.replace(old_string, new_string, 1)
        Path(full).write_text(new_content, encoding="utf-8")
        
        return ToolResult(
            title=f"Edit {file_path}",
            output=f"Successfully edited {file_path}",
            metadata={"success": True},
        )
```

**特性**:
- `old_string` 必须精确匹配（包括空格）
- `old_string` 必须在文件中唯一
- 如果不唯一，需要添加更多上下文

---

### 4. write - 写入文件

**文件**: `/opencode/tool/write.py`

**功能**: 写入文件内容，自动创建目录，覆盖已有内容。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `file_path` | string | ✅ | 文件路径 |
| `content` | string | ✅ | 要写入的内容 |

**实现核心**:

```python
class WriteTool(ToolInfo):
    id = "write"
    description = "Write content to a file. Creates the file and parent directories if they don't exist. Overwrites existing content."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path, content = args["file_path"], args["content"]
        
        # 解析路径
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        
        # 创建目录并写入
        Path(full).parent.mkdir(parents=True, exist_ok=True)
        Path(full).write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        
        return ToolResult(
            title=f"Write {file_path}",
            output=f"Wrote {lines} lines to {file_path}",
            metadata={"success": True, "lines": lines}
        )
```

**特性**:
- 自动创建父目录
- 覆盖已有文件
- 返回写入行数

---

### 5. glob - 文件模式搜索

**文件**: `/opencode/tool/glob_tool.py`

**功能**: 使用 glob 模式查找文件，返回相对路径。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `pattern` | string | ✅ | Glob 模式 (如 `**/*.py`, `src/**/*.ts`) |
| `path` | string | ❌ | 搜索目录（默认：项目根目录） |

**实现核心**:

```python
class GlobTool(ToolInfo):
    id = "glob"
    description = "Find files matching a glob pattern. Returns relative file paths."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        search_path = args.get("path", "")
        
        # 执行搜索
        matches = sorted(globmod.glob(pattern, root_dir=base, recursive=True))
        
        # 限制结果数量
        if len(matches) > 500:
            matches = matches[:500]
            output = "\n".join(matches) + f"\n\n... truncated (500 of {len(matches)} matches)"
        else:
            output = "\n".join(matches) if matches else "No files found."
        
        return ToolResult(title=f"Glob {pattern}", output=output, metadata={"count": len(matches)})
```

**特性**:
- 支持递归搜索 (`**`)
- 最多返回 500 个匹配结果
- 返回相对路径

---

### 6. grep - 内容搜索

**文件**: `/opencode/tool/grep.py`

**功能**: 使用正则表达式搜索文件内容（优先使用 ripgrep）。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `pattern` | string | ✅ | 正则表达式 |
| `path` | string | ❌ | 搜索目录 |
| `include` | string | ❌ | 文件过滤 (如 `*.py`) |

**实现核心**:

```python
class GrepTool(ToolInfo):
    id = "grep"
    description = "Search file contents using a regex pattern. Uses ripgrep."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        path = args.get("path", ".")
        include = args.get("include")
        
        # 优先使用 ripgrep
        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-rn", "--no-heading", "-m", "100"]
            if include:
                cmd += ["-g", include]
            cmd.append(pattern)
            cmd.append(".")
        else:
            cmd = ["grep", "-rn", "-m", "100", pattern, cwd]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=exec_cwd)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace").strip()
        
        return ToolResult(title=f"Grep {pattern}", output=output or "No matches found.", metadata={"matches": lines})
```

**特性**:
- 优先使用 ripgrep（更快）
- 回退到系统 grep
- 限制每文件最多 100 个匹配
- 30 秒超时

---

### 7. task - 子代理任务

**文件**: `/opencode/tool/task.py`

**功能**: 启动子代理处理复杂任务，独立上下文运行。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `description` | string | ✅ | 任务描述 |
| `agent` | string | ❌ | 代理类型，默认 `general`，可选 `explore` |

**实现核心**:

```python
class TaskTool(ToolInfo):
    id = "task"
    description = (
        "Launch a sub-agent to handle a complex task. The sub-agent runs independently with its own context. "
        "Use this when you need to research, explore, or execute multi-step work in parallel."
    )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = args["description"]
        agent_name = args.get("agent", "general")

        # 加载 Agent
        agent = await agentmod.get(agent_name)
        
        # 加载模型
        provider_id, model_id = await providermod.default_model()
        model = await providermod.get_model(provider_id, model_id)

        # 构建 system prompt
        system = build_system(agent_prompt=agent.prompt)
        messages = [{"role": "user", "content": description}]
        tools = tool_registry.to_llm_tools()

        # ⚠️ 关键：过滤危险工具（防止递归）
        tools = [t for t in tools if t["function"]["name"] not in ("task", "todo", "question")]

        # 执行单次 LLM 调用
        async for event in llmmod.stream(stream_input):
            if isinstance(event, llmmod.TextDelta):
                output_parts.append(event.text)
            elif isinstance(event, llmmod.ToolCallDelta):
                # 执行工具调用
                tool_impl = tool_registry.get(event.tool_name)
                if tool_impl:
                    tool_args = json.loads(event.args) if event.args else {}
                    result = await tool_impl.execute(tool_args, ctx)
                    tool_results.append(f"[{event.tool_name}] {result.output[:500]}")

        return ToolResult(
            title=f"Task: {description[:60]}",
            output=output or "No output from sub-agent.",
            metadata={"agent": agent_name, "tool_calls": len(tool_results)},
        )
```

**特性**:
- 子代理独立上下文
- **不能使用** `task`（防止递归）、`todo`、`question`
- 执行单次 LLM 调用（避免深度递归）

---

### 8. webfetch - URL 内容获取

**文件**: `/opencode/tool/webfetch.py`

**功能**: 获取 URL 内容，HTTP 自动升级为 HTTPS。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `url` | string | ✅ | 要获取的 URL |
| `extract` | string | ❌ | 要提取的信息 |

**实现核心**:

```python
class WebFetchTool(ToolInfo):
    id = "webfetch"
    description = "Fetch content from a URL. Returns the page content as text. HTTP URLs are upgraded to HTTPS."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = args["url"]
        
        # HTTP → HTTPS
        if url.startswith("http://"):
            url = "https://" + url[7:]

        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url, headers={"User-Agent": "OpenCode/1.0"})
            resp.raise_for_status()

        # HTML → 纯文本
        content_type = resp.headers.get("content-type", "")
        text = _html_to_text(resp.text) if "text/html" in content_type else resp.text

        # 截断超长内容
        if len(text) > 50_000:
            text = text[:50_000] + f"\n\n... truncated ({len(text)} chars total)"

        return ToolResult(
            title=f"Fetch {url[:60]}",
            output=text or "(empty page)",
            metadata={"url": url, "status": resp.status_code, "length": len(text)},
        )
```

**特性**:
- 自动升级 HTTP → HTTPS
- HTML 自动转纯文本（去除 script/style 标签）
- 超过 50KB 截断
- 30 秒超时

---

### 9. websearch - 网络搜索

**文件**: `/opencode/tool/websearch.py`

**功能**: 搜索网络，返回搜索结果（使用 DuckDuckGo，无需 API key）。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `query` | string | ✅ | 搜索查询 |
| `max_results` | integer | ❌ | 最大结果数，默认 5 |

**实现核心**:

```python
class WebSearchTool(ToolInfo):
    id = "websearch"
    description = "Search the web for information. Returns search results with titles, URLs, and snippets."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = args["query"]
        max_results = args.get("max_results", 5)

        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "OpenCode/1.0"},
            )

        # 解析 DuckDuckGo HTML 结果
        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            resp.text, re.DOTALL,
        )
        
        results = []
        for url, title, snippet in blocks[:max_results]:
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet_clean = re.sub(r"<[^>]+>", "", snippet).strip()
            if title_clean and url:
                results.append(f"**{title_clean}**\n{url}\n{snippet_clean}\n")

        return ToolResult(
            title=f"Search: {query[:50]}",
            output="\n".join(results) if results else "No results found.",
            metadata={"query": query, "results": len(results)},
        )
```

**特性**:
- 使用 DuckDuckGo（无需 API key）
- 返回标题、URL、摘要
- 15 秒超时

---

### 10. question - 向用户提问

**文件**: `/opencode/tool/question.py`

**功能**: 向用户提问以获取澄清或额外信息。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `question` | string | ✅ | 要询问的问题 |
| `options` | array | ❌ | 可选的选项列表 |

**实现核心**:

```python
class QuestionTool(ToolInfo):
    id = "question"
    description = (
        "Ask the user a question to get clarification or additional information. "
        "Use this when you need user input to proceed."
    )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        question = args["question"]
        options = args.get("options", [])

        output = question
        if options:
            output += "\n\nOptions:\n" + "\n".join(f"  {i+1}. {o}" for i, o in enumerate(options))

        return ToolResult(
            title="Question",
            output=output,
            metadata={"question": question, "options": options, "awaiting_response": True},
        )
```

**特性**:
- 支持选项列表
- 设置 `awaiting_response: True` 元数据
- 客户端负责展示并收集用户回复

---

### 11. todo - 任务列表管理

**文件**: `/opencode/tool/todo.py`

**功能**: 创建和管理任务列表以跟踪多步骤任务进度。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `todos` | array | ✅ | 任务项数组 |
| `merge` | boolean | ❌ | 是否合并（默认 true） |

**任务项结构**:

```json
{
  "id": "string",
  "content": "string",
  "status": "pending" | "in_progress" | "completed" | "cancelled"
}
```

**实现核心**:

```python
# 内存存储（按会话）
_todos: dict[str, list[dict[str, Any]]] = {}

class TodoTool(ToolInfo):
    id = "todo"
    description = "Create and manage a todo list to track progress on multi-step tasks."

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        items = args["todos"]
        merge = args.get("merge", True)

        if merge and ctx.session_id in _todos:
            existing = {t["id"]: t for t in _todos[ctx.session_id]}
            for item in items:
                existing[item["id"]] = item
            _todos[ctx.session_id] = list(existing.values())
        else:
            _todos[ctx.session_id] = list(items)

        # 格式化输出
        current = _todos.get(ctx.session_id, [])
        lines = []
        for t in current:
            icon = {"pending": "⬜", "in_progress": "🔶", "completed": "✅", "cancelled": "⬛"}.get(t["status"], "⬜")
            lines.append(f"{icon} [{t['id']}] {t['content']}")

        return ToolResult(
            title="Todo list updated",
            output="\n".join(lines) if lines else "Empty todo list.",
            metadata={"count": len(current)},
        )
```

**特性**:
- 按会话存储
- 支持合并模式
- 状态图标：⬜ pending, 🔶 in_progress, ✅ completed, ⬛ cancelled

---

### 12. skill - 加载技能文件

**文件**: `/opencode/tool/skill.py`

**功能**: 加载技能文件获取专业指导，技能文件位于 `.opencode/skills/`。

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `name` | string | ✅ | 技能名称（不含 `.md` 扩展名） |

**实现核心**:

```python
class SkillTool(ToolInfo):
    id = "skill"
    description = (
        "Load a skill file to get specialized instructions. "
        "Skills are markdown files in .opencode/skills/ that provide domain-specific knowledge."
    )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = args["name"]
        
        # 搜索路径
        search_dirs = [
            os.path.join(base, ".opencode", "skills"),
            os.path.join(base, ".opencode", "skill"),
        ]

        for d in search_dirs:
            for ext in [".md", ".txt", ""]:
                p = os.path.join(d, name + ext)
                if os.path.isfile(p):
                    content = Path(p).read_text(encoding="utf-8")
                    return ToolResult(
                        title=f"Skill: {name}",
                        output=content,
                        metadata={"path": p, "found": True},
                    )

        return ToolResult(
            title=f"Skill: {name}",
            output=f"Skill '{name}' not found. Searched in .opencode/skills/",
            metadata={"found": False},
        )
```

**特性**:
- 支持 `.md`, `.txt`, 无扩展名
- 搜索 `.opencode/skills/` 和 `.opencode/skill/`

---

### 13. batch - 批量并行执行（实验性）

**文件**: `/opencode/tool/batch.py`

**功能**: 在单个请求中并行执行多个工具调用。

**状态**: ⚠️ **实验性**，需配置 `experimental.batch_tool: true`

**参数**:

| 参数 | 类型 | 必填 | 描述 |
|------|------|------|------|
| `description` | string | ❌ | 批次描述 |
| `calls` | array | ✅ | 工具调用数组（最多 25 个） |

**调用项结构**:

```json
{
  "tool": "string",  // 工具名称
  "args": {}         // 工具参数
}
```

**实现核心**:

```python
MAX_BATCH_SIZE = 25
_EXCLUDED_TOOLS = frozenset({"batch", "task", "todo", "question"})

class BatchTool(ToolInfo):
    id = "batch"
    description = (
        "Execute multiple tool calls in parallel within a single request. "
        "Use this when you need to run several independent operations simultaneously..."
    )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        calls = args.get("calls", [])
        description = args.get("description", "batch execution")

        # 验证数量
        if len(calls) > MAX_BATCH_SIZE:
            return ToolResult(output=f"Too many calls: {len(calls)} exceeds maximum of {MAX_BATCH_SIZE}.")

        # 验证工具
        validated = []
        errors = []
        for i, call in enumerate(calls):
            tool_name = call.get("tool", "")
            if tool_name in _EXCLUDED_TOOLS:
                errors.append(f"[{i}] Tool '{tool_name}' is not allowed in batch")
                continue
            tool_impl = tool_registry.get(tool_name)
            if not tool_impl:
                errors.append(f"[{i}] Unknown tool: {tool_name}")
                continue
            validated.append((call, tool_impl))

        # 并行执行
        async def _execute_one(idx, call, tool_impl):
            tool_name = call.get("tool", "")
            tool_args = call.get("args", {})
            result = await tool_impl.execute(tool_args, ctx)
            return f"[{idx}:{tool_name}] {result.output}"

        tasks = [_execute_one(i, call, impl) for i, (call, impl) in enumerate(validated)]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        return ToolResult(
            title=f"Batch: {description} ({succeeded}/{len(validated)} succeeded)",
            output="\n".join(results),
            metadata={"total": len(calls), "succeeded": succeeded, "failed": failed},
        )
```

**特性**:
- 最多 25 个调用
- 使用 `asyncio.gather` 并行执行
- **不能包含**: `batch`, `task`, `todo`, `question`
- 只支持内置工具

---

## 三、Subagent 系统

### Agent 类型

系统定义了 **7 个内置 Agent**：

| Agent 名称 | 模式 | 描述 | 是否隐藏 |
|-----------|------|------|---------|
| **build** | primary | 默认 agent，根据配置权限执行工具 | 否 |
| **plan** | primary | 计划模式，禁止所有编辑工具 | 否 |
| **general** | **subagent** | 通用 agent，用于研究复杂问题和执行多步骤任务 | 否 |
| **explore** | **subagent** | 快速 agent，专门用于探索代码库 | 否 |
| **compaction** | primary | 压缩/总结对话内容 | 是 |
| **title** | primary | 生成会话标题 | 是 |
| **summary** | primary | 生成对话摘要 | 是 |

### Agent 模式说明

| 模式 | 说明 |
|------|------|
| `primary` | 主代理模式，直接处理用户请求 |
| `subagent` | 子代理模式，由主代理通过 `task` 工具启动 |
| `all` | 可作为主代理或子代理使用 |

---

### Subagent 详解

#### 1. general - 通用子代理

**文件**: `/opencode/agent/agent.py`

**用途**: 研究复杂问题和执行多步骤任务

**配置**:

```python
"general": AgentInfo(
    name="general",
    description="General-purpose agent for researching complex questions and executing multi-step tasks in parallel.",
    mode="subagent",
    native=True,
    permission=[
        *base,  # 默认权限（允许大部分操作）
        {"permission": "todowrite", "pattern": "*", "action": "deny"},  # 禁止写 todo
    ],
)
```

**权限**: 
- ✅ 允许大部分工具
- ❌ 禁止 `todowrite`

---

#### 2. explore - 探索子代理

**文件**: `/opencode/agent/agent.py`

**用途**: 快速探索代码库，文件搜索，代码关键词搜索

**配置**:

```python
"explore": AgentInfo(
    name="explore",
    description="Fast agent specialized for exploring codebases. Use for file searches, code keyword searches, or codebase Q&A.",
    mode="subagent",
    native=True,
    prompt=_load_prompt("explore"),  # 专用 prompt
    permission=[
        {"permission": "*", "pattern": "*", "action": "deny"},  # 默认禁止所有
        {"permission": "grep", "pattern": "*", "action": "allow"},
        {"permission": "glob", "pattern": "*", "action": "allow"},
        {"permission": "list", "pattern": "*", "action": "allow"},
        {"permission": "bash", "pattern": "*", "action": "allow"},
        {"permission": "read", "pattern": "*", "action": "allow"},
        {"permission": "webfetch", "pattern": "*", "action": "allow"},
        {"permission": "websearch", "pattern": "*", "action": "allow"},
        {"permission": "codesearch", "pattern": "*", "action": "allow"},
    ],
)
```

**权限**:
- ✅ grep, glob, list, bash, read, webfetch, websearch, codesearch
- ❌ 其他所有工具（不能修改文件）

**专用 Prompt** (`/opencode/agent/prompts/explore.txt`):

```
You are a file search specialist. You excel at thoroughly navigating and exploring codebases.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path you need to read
- Use Bash for file operations like copying, moving, or listing directory contents
- Adapt your search approach based on the thoroughness level specified by the caller
- Return file paths as absolute paths in your final response
- For clear communication, avoid using emojis
- Do not create any files, or run bash commands that modify the user's system state in any way

Complete the user's search request efficiently and report your findings clearly.
```

---

### 启动机制

Subagent 通过 `task` 工具启动：

```
┌─────────────────────────────────────────────────────────┐
│  主 Agent (build)                                        │
│      │                                                   │
│      │ 调用 task 工具                                     │
│      │ task(description="分析代码库", agent="explore")    │
│      ▼                                                   │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Subagent (explore)                              │    │
│  │      - 独立上下文                                 │    │
│  │      - 可用工具被过滤（无 task/todo/question）    │    │
│  │      - 执行单次 LLM 调用                          │    │
│  │      - 返回结果给主 Agent                         │    │
│  └─────────────────────────────────────────────────┘    │
│      │                                                   │
│      ▼                                                   │
│  继续处理...                                              │
└─────────────────────────────────────────────────────────┘
```

**防止递归**: Subagent 无法调用以下工具：
- `task` - 防止无限递归
- `todo` - 任务管理由主 Agent 处理
- `question` - 用户交互由主 Agent 处理

---

### Agent 数据结构

```python
@dataclass
class AgentInfo:
    name: str                       # 名称
    description: str = ""           # 描述
    mode: Literal["subagent", "primary", "all"] = "primary"  # 模式
    native: bool = False            # 是否内置
    hidden: bool = False            # 是否隐藏
    prompt: str | None = None       # 专用提示词
    temperature: float | None = None
    top_p: float | None = None
    color: str | None = None
    model: dict[str, str] | None = None  # {"providerID": ..., "modelID": ...}
    variant: str | None = None
    permission: list[dict[str, Any]] = field(default_factory=list)  # 权限规则
    options: dict[str, Any] = field(default_factory=dict)
    steps: int | None = None
```

---

### 辅助 Agent Prompts

#### compaction.txt - 对话压缩

```
You are a helpful AI assistant tasked with summarizing conversations.

When asked to summarize, provide a detailed but concise summary of the conversation.
Focus on information that would be helpful for continuing the conversation, including:
- What was done
- What is currently being worked on
- Which files are being modified
- What needs to be done next
- Key user requests, constraints, or preferences that should persist
- Important technical decisions and why they were made
```

#### title.txt - 生成标题

```
You are a title generator. You output ONLY a thread title. Nothing else.

Rules:
- A single line
- ≤50 characters
- No explanations
- Use the same language as the user message
```

#### summary.txt - 生成摘要

```
Summarize what was done in this conversation. Write like a pull request description.

Rules:
- 2-3 sentences max
- Describe the changes made, not the process
- Write in first person (I added..., I fixed...)
```

---

## 四、文件路径索引

### Tools 文件

| 文件 | 路径 |
|------|------|
| 基础类 | `/opencode/tool/base.py` |
| 注册中心 | `/opencode/tool/registry.py` |
| bash | `/opencode/tool/bash.py` |
| read | `/opencode/tool/read.py` |
| edit | `/opencode/tool/edit.py` |
| write | `/opencode/tool/write.py` |
| glob | `/opencode/tool/glob_tool.py` |
| grep | `/opencode/tool/grep.py` |
| task | `/opencode/tool/task.py` |
| webfetch | `/opencode/tool/webfetch.py` |
| websearch | `/opencode/tool/websearch.py` |
| question | `/opencode/tool/question.py` |
| todo | `/opencode/tool/todo.py` |
| skill | `/opencode/tool/skill.py` |
| batch | `/opencode/tool/batch.py` |

### Agent 文件

| 文件 | 路径 |
|------|------|
| Agent 系统核心 | `/opencode/agent/agent.py` |
| Agent 模块导出 | `/opencode/agent/__init__.py` |
| Agent 配置模型 | `/opencode/config/models.py` |
| explore prompt | `/opencode/agent/prompts/explore.txt` |
| compaction prompt | `/opencode/agent/prompts/compaction.txt` |
| summary prompt | `/opencode/agent/prompts/summary.txt` |
| title prompt | `/opencode/agent/prompts/title.txt` |

---

## 总结

### Tools 系统

| 类别 | 工具 | 数量 |
|------|------|------|
| 文件操作 | read, edit, write | 3 |
| 搜索 | glob, grep | 2 |
| 执行 | bash | 1 |
| 网络 | webfetch, websearch | 2 |
| 任务 | task, todo | 2 |
| 交互 | question, skill | 2 |
| 实验性 | batch | 1 |
| **总计** | | **13** |

### Agent 系统

| 类别 | Agent | 数量 |
|------|-------|------|
| 主代理 | build, plan | 2 |
| 子代理 | general, explore | 2 |
| 内部代理 | compaction, title, summary | 3 |
| **总计** | | **7** |
